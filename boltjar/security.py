"""
boltjar.security: keep the local server local.

The server listens on the user's own machine, and any web page the user visits
can aim a request at it. Three checks stand between those pages and the API,
applied by one ASGI middleware (`LocalGuard`) to HTTP and WebSocket alike:

  - Host:   the Host header must name this machine (loopback, plus the names in
            BOLTJAR_ALLOWED_HOSTS). This is what stops DNS rebinding, where a
            page on evil.example re-resolves its own name to 127.0.0.1.
  - Origin: a browser stamps every WebSocket and every state-changing request
            with the page it came from. Only the editor's own origins pass; a
            request with no Origin is a non-browser client and passes.
  - Token:  a random per-install token (user/data/token) is required on /api,
            /ws, /stream and /audio, from a cookie (the editor) or a Bearer
            header (any other local client, e.g. the MCP server).

/hook/* skips all three: a webhook is meant to be reached from outside and is
guarded by its own shared secret instead.
"""
from __future__ import annotations

import hmac
import os
import pathlib
from secrets import token_urlsafe
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The per-install token. Module-level so the test suite can point it at a tmp dir.
TOKEN_PATH = ROOT / "user" / "data" / "token"
COOKIE_NAME = "boltjar_token"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# The endpoint a browser calls once at boot to receive its cookie.
SESSION_PATH = "/api/session"

_cached: tuple[pathlib.Path, str] | None = None


# ---------------------------------------------------------------- the token

def read_token() -> str | None:
    """The token on disk, or None when no server has created one yet. Never
    creates it: a client (the MCP server) reads, only the server writes."""
    try:
        return TOKEN_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def get_token() -> str:
    """The per-install token, created on first use (32 random bytes, url-safe)
    and kept for every later run. Cached per path."""
    global _cached
    if _cached is not None and _cached[0] == TOKEN_PATH:
        return _cached[1]
    token = read_token() or _create_token()
    _cached = (TOKEN_PATH, token)
    return token


def _create_token() -> str:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    token = token_urlsafe(32)
    # O_EXCL: two servers starting on a fresh install agree on one token. The
    # 0o600 mode makes the file owner-only on POSIX (Windows ignores it).
    try:
        fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = read_token()
        if existing:
            return existing
        # an empty leftover file: replace it.
        fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


def token_matches(provided: str | None) -> bool:
    """Constant-time comparison against the install token."""
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), get_token().encode("utf-8"))


def request_token(headers: Headers) -> str | None:
    """The token a request carries: a Bearer header wins, else the cookie."""
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return cookie_parser(headers.get("cookie", "")).get(COOKIE_NAME)


# ---------------------------------------------------------------- host + origin

def _host_name(host: str) -> str:
    """The name part of a Host header or netloc, lowercased, port dropped:
    '127.0.0.1:8770' -> '127.0.0.1', '[::1]:5173' -> '[::1]'."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def allowed_hosts() -> frozenset[str]:
    """Loopback plus every name in BOLTJAR_ALLOWED_HOSTS (a comma list the serve
    command fills from --host). Read per call so a changed env applies at once."""
    extra = set()
    for raw in os.environ.get("BOLTJAR_ALLOWED_HOSTS", "").split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if not name.startswith("[") and name.count(":") > 1:
            name = f"[{name}]"  # a bare IPv6 address, as a Host header writes it
        extra.add(_host_name(name))
    return LOOPBACK_HOSTS | extra


def host_allowed(host: str | None) -> bool:
    return bool(host) and _host_name(host) in allowed_hosts()


def origin_allowed(origin: str | None) -> bool:
    """No Origin means a non-browser client. Otherwise the page must be served
    from an allowed host, on any port (the Vite dev server runs on its own).
    An opaque origin ('null', a sandboxed frame or a file://) never passes."""
    if origin is None:
        return True
    parts = urlsplit(origin.strip())
    if parts.scheme not in ("http", "https"):
        return False
    return _host_name(parts.netloc) in allowed_hosts()


# ---------------------------------------------------------------- the middleware

def _is_hook(path: str) -> bool:
    return path.startswith("/hook/")


def _needs_token(path: str) -> bool:
    if path == SESSION_PATH:
        return False
    return any(path == p or path.startswith(p + "/") for p in ("/api", "/ws", "/stream", "/audio"))


def session_cookie(secure: bool = False) -> str:
    cookie = f"{COOKIE_NAME}={get_token()}; Path=/; HttpOnly; SameSite=Strict"
    return cookie + "; Secure" if secure else cookie


class LocalGuard:
    """ASGI middleware applying the Host, Origin and token checks (module doc)
    and handing the browser its session cookie on GET / and GET /api/session."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket") or _is_hook(scope["path"]):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        method = scope.get("method", "GET")
        headers = Headers(scope=scope)
        if not host_allowed(headers.get("host")):
            await _deny(scope, receive, send, 400, "invalid host header")
            return
        # the session endpoint hands out the cookie, so it gets the Origin check too.
        checks_origin = kind == "websocket" or method in STATE_CHANGING or path == SESSION_PATH
        if checks_origin and not origin_allowed(headers.get("origin")):
            await _deny(scope, receive, send, 403, "origin not allowed")
            return
        if _needs_token(path) and not token_matches(request_token(headers)):
            await _deny(scope, receive, send, 401, "missing or invalid token")
            return
        if kind == "http" and method == "GET" and path in ("/", SESSION_PATH):
            send = _with_cookie(send, session_cookie(secure=scope.get("scheme") == "https"))
        await self.app(scope, receive, send)


def _with_cookie(send, cookie: str):
    async def wrapped(message) -> None:
        if message["type"] == "http.response.start":
            MutableHeaders(scope=message).append("set-cookie", cookie)
        await send(message)
    return wrapped


async def _deny(scope, receive, send, status: int, message: str) -> None:
    if scope["type"] == "websocket" and "websocket.http.response" not in scope.get("extensions", {}):
        # a server without the denial-response extension: closing before accept
        # fails the handshake (uvicorn answers it with a 403).
        await send({"type": "websocket.close", "code": 1008})
        return
    await JSONResponse({"error": message}, status_code=status)(scope, receive, send)
