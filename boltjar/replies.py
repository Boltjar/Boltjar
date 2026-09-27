"""
boltjar.replies: webhook calls held open until a Respond to Webhook answers.

A Webhook set to reply "from Respond to Webhook" does not answer its caller
right away. The server holds the call in a HeldReply and fires the graph in a
TurnScope whose origin is that HeldReply, so the Respond to Webhook firing in
that run writes to exactly this call, however many calls are in flight.

The first write decides the response: its status, headers and content type
stand for the whole response. A write marked last answers with one plain
response; writes that are not last stream it (chunked), newline-delimited, or
as server-sent events when the caller asks for text/event-stream or the
content type says so, until a write marked last closes it.

A held call is released exactly once: by its last write, by the timeout (504
before anything was written), by the graph stopping (503), or by its caller
going away. Every write after that is refused, so a late Respond to Webhook
does nothing.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from starlette.responses import JSONResponse, Response, StreamingResponse

# how many calls one graph may hold open at once; one more is answered 429
MAX_HELD_PER_GRAPH = 64
# the most one response may carry, all its chunks together
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

REPLY_RIGHT_AWAY = "right away"
REPLY_FROM_RESPOND = "from Respond to Webhook"

CONTENT_TYPES = ("auto", "application/json", "text/plain", "text/event-stream")
SSE = "text/event-stream"
NDJSON = "application/x-ndjson"

# headers a response can never set: they describe the connection, not the
# response (hop-by-hop), or the server computes them (Content-Length)
REFUSED_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailer", "trailers", "transfer-encoding", "upgrade", "content-length",
})


def seconds_text(seconds: float) -> str:
    """30.0 as "30", 2.5 as "2.5"."""
    return str(int(seconds)) if float(seconds).is_integer() else f"{seconds:g}"


def cap_text() -> str:
    """MAX_RESPONSE_BYTES for a reader: "8 MB", or its bytes when not whole MB."""
    mb, rest = divmod(MAX_RESPONSE_BYTES, 1024 * 1024)
    return f"{mb} MB" if mb and not rest else f"{MAX_RESPONSE_BYTES} bytes"


def as_text(value: Any) -> str:
    """A body value as text: a string as it is, nothing as empty, anything else
    (a dict, a list, a number) as JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return json.dumps(value, ensure_ascii=False, default=str)


def as_json_text(value: Any) -> str:
    """A body value as JSON text: a string that already is JSON passes as it
    is, any other string is quoted."""
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except ValueError:
            return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bytes):
        return as_json_text(value.decode("utf-8", errors="replace"))
    return json.dumps(value, ensure_ascii=False, default=str)


def sse_event(text: str) -> str:
    """One server-sent event carrying `text`, one `data:` line per line."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "".join(f"data: {line}\n" for line in lines) + "\n"


def clean_headers(value: Any) -> tuple[dict[str, str], list[str]]:
    """The headers a response may set from `value` (a JSON object, or its
    text), and why anything was left out. Hop-by-hop headers, Content-Length
    and a name or value with a line break are refused."""
    problems: list[str] = []
    if value is None or value == "":
        return {}, problems
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}, ["headers must be a JSON object"]
    if not isinstance(value, dict):
        return {}, ["headers must be a JSON object"]
    out: dict[str, str] = {}
    for name, raw in value.items():
        name = str(name).strip()
        text = raw if isinstance(raw, str) else as_text(raw)
        if not name or any(c in name for c in " \t\r\n:") or "\r" in text or "\n" in text:
            problems.append(f"header {name!r} is not a valid header")
        elif name.lower() in REFUSED_HEADERS:
            problems.append(f"header {name!r} cannot be set here")
        else:
            out[name] = text
    return out, problems


@dataclass
class Written:
    """What a write did. `sent`: it reached the caller. Otherwise `reason` is
    "closed" (no call is waiting) or "too-large" (the response went over
    MAX_RESPONSE_BYTES and was cut off). `head_ignored`: this write's status,
    headers or content type differ from the first write's, reported once per
    response."""
    sent: bool
    reason: str = ""
    head_ignored: bool = False


@dataclass
class _Head:
    status: int
    headers: dict[str, str]
    content_type: str
    sse: bool
    media_type: str


class HeldReply:
    """One webhook call held open (see the module doc)."""

    def __init__(self, book: "ReplyBook", accepts_sse: bool,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self._book = book
        self._events: "asyncio.Queue[tuple]" = asyncio.Queue()
        self.accepts_sse = accepts_sse
        self._log = log or (lambda _msg: None)
        self.released = False
        self._head: Optional[_Head] = None
        self._first_head: Optional[tuple] = None
        self._head_warned = False
        self._bytes = 0
        # the TurnScope the run was started in, closed on release
        self.scope: Any = None

    @property
    def open(self) -> bool:
        return not self.released

    @property
    def started(self) -> bool:
        return self._head is not None

    # ------------------------------------------------------------ writing
    def write(self, body: Any, *, last: bool, status: int, headers: dict[str, str],
              content_type: str) -> Written:
        """Write `body` to the caller (see Written). The first write fixes the
        status, headers and content type of the whole response."""
        if self.released:
            return Written(False, "closed")
        head_ignored = False
        asked = (status, tuple(sorted(headers.items())), content_type)
        if self._head is None:
            sse = content_type == SSE or self.accepts_sse
            if sse:
                media = SSE
            elif content_type != "auto":
                media = content_type
            elif isinstance(body, (dict, list)):
                media = "application/json" if last else NDJSON
            else:
                media = "text/plain"
            head = _Head(status, dict(headers), content_type, sse, media)
        else:
            head = self._head
            if asked != self._first_head and not self._head_warned:
                self._head_warned = True
                head_ignored = True
        data = self._frame(head, body, single=last and self._head is None)
        if self._bytes + len(data) > MAX_RESPONSE_BYTES:
            # the cap ends the response: an error when nothing went out yet,
            # else the stream closes without this chunk
            self._abort(500, f"response is larger than {cap_text()}")
            return Written(False, "too-large")
        if self._head is None:
            self._head, self._first_head = head, asked
        self._bytes += len(data)
        self._events.put_nowait(("write", data, bool(last)))
        if last:
            self._release()
        return Written(True, head_ignored=head_ignored)

    @staticmethod
    def _frame(head: _Head, body: Any, *, single: bool) -> bytes:
        if head.sse:
            if body is None and not single:
                return b""  # a stream's closing fire with nothing to add
            text = as_json_text(body) if head.media_type == "application/json" else as_text(body)
            return sse_event(text).encode("utf-8")
        if head.media_type in ("application/json", NDJSON):
            text = as_json_text(body) if body is not None else ("" if not single else "null")
        else:
            text = as_text(body)
        if single:
            return text.encode("utf-8")
        if body is None:
            return b""
        return (text + "\n").encode("utf-8")

    def abort(self, status: int, message: str) -> None:
        """Release the call with an error: `status` and {"error": message}
        when nothing went out yet, else the stream closes."""
        self._abort(status, message)

    def _abort(self, status: int, message: str) -> None:
        if self.released:
            return
        self._events.put_nowait(("abort", status, message))
        self._release()

    def _release(self) -> None:
        if self.released:
            return
        self.released = True
        self._book._forget(self)
        if self.scope is not None:
            self.scope.close()

    # ------------------------------------------------------------ answering
    async def _next(self, timeout: float) -> Optional[tuple]:
        try:
            return await asyncio.wait_for(self._events.get(), timeout)
        except asyncio.TimeoutError:
            # a write that landed as the wait ran out still counts
            if not self._events.empty():
                return self._events.get_nowait()
            return None

    async def response(self, timeout: float) -> Response:
        """Wait for the first write and answer the call with it: a plain
        response, or a stream when the write is not the last. 504 when nothing
        is written within `timeout` seconds."""
        try:
            event = await self._next(timeout)
        except BaseException:
            self._release()  # the caller went away, or the server is cancelling
            raise
        if event is None:
            self._release()
            self._log(f"answered 504: nothing was written within {seconds_text(timeout)} s")
            return JSONResponse({"error": f"no response within {seconds_text(timeout)} s"},
                                status_code=504)
        if event[0] == "abort":
            return JSONResponse({"error": event[2]}, status_code=event[1])
        _, data, last = event
        head = self._head
        assert head is not None
        if last:
            return Response(content=data, status_code=head.status, headers=head.headers,
                            media_type=head.media_type)
        return StreamingResponse(self._stream(data, timeout), status_code=head.status,
                                 headers=head.headers, media_type=head.media_type)

    async def _stream(self, first: bytes, timeout: float):
        try:
            if first:
                yield first
            while True:
                event = await self._next(timeout)
                if event is None:
                    self._log(f"stream closed: nothing was written for {seconds_text(timeout)} s")
                    return
                if event[0] == "abort":
                    self._log(f"stream closed: {event[2]}")
                    return
                _, data, last = event
                if data:
                    yield data
                if last:
                    return
        finally:
            self._release()


class ReplyBook:
    """The calls one graph holds open, at most MAX_HELD_PER_GRAPH."""

    def __init__(self, limit: int = MAX_HELD_PER_GRAPH) -> None:
        self.limit = limit
        self._held: set[HeldReply] = set()

    def __len__(self) -> int:
        return len(self._held)

    def hold(self, accepts_sse: bool, log: Optional[Callable[[str], None]] = None) -> Optional[HeldReply]:
        """A new held call, or None when the graph already holds `limit`."""
        if len(self._held) >= self.limit:
            return None
        reply = HeldReply(self, accepts_sse, log)
        self._held.add(reply)
        return reply

    def close_all(self, status: int = 503, message: str = "workflow stopped") -> None:
        """Release every held call with an error (the graph stops)."""
        for reply in list(self._held):
            reply.abort(status, message)

    def _forget(self, reply: HeldReply) -> None:
        self._held.discard(reply)
