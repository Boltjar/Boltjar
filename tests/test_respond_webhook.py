"""The Webhook's `reply` knob and the Respond to Webhook node.

A Webhook set to reply "from Respond to Webhook" holds its caller until the
Respond to Webhook of the same run writes, with its status, headers and content
type; a write with `last` off streams the response until one with `last` on
ends it. Each call is answered by its own run, however many are in flight.

Held calls need the handler and the runtime on ONE event loop, so most tests
run in asyncio: the graph is powered On through its Hub, and calls go through
the whole app (LocalGuard, middleware, route) over httpx's ASGI transport. One
test drives the same path through the TestClient.
"""
from __future__ import annotations

import asyncio
import contextlib
import json

import httpx

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar import replies
from boltjar.runtime import Runtime
from boltjar.server import HUBS, app, get_hub, validate_graph
from local_client import BASE_URL, LOOPBACK_PEER, local_client

WAIT = replies.REPLY_FROM_RESPOND
RIGHT_AWAY = replies.REPLY_RIGHT_AWAY


# ---------------------------------------------------------------- graphs
def _hook(reply: str = WAIT, timeout: float = 30, path: str = "ask") -> dict:
    return {"id": "hook", "type": "core.trigger.webhook",
            "config": {"path": path, "method": "ANY", "reply": reply, "timeout": timeout}}


def _respond(node_id: str = "respond", **config) -> dict:
    return {"id": node_id, "type": "core.output.respond_webhook", "config": config}


def _edge(src: str, sp: str, dst: str, dp: str) -> dict:
    return {"src": src, "src_port": sp, "dst": dst, "dst_port": dp}


def _single(body_port: str = "json", **respond_config) -> dict:
    """Webhook -> Respond to Webhook, answering with the call's own `body_port`."""
    return {"nodes": [_hook(), _respond(**respond_config)],
            "edges": [_edge("hook", "trigger", "respond", "trigger"),
                      _edge("hook", body_port, "respond", "body")]}


def _delayed(seconds: float, timeout: float = 30, then_log: bool = False) -> dict:
    """Webhook -> Wait(seconds) -> Respond to Webhook (body: the call's json)."""
    nodes = [_hook(timeout=timeout),
             {"id": "wait", "type": "core.flow.wait", "config": {"amount": seconds}},
             _respond()]
    edges = [_edge("hook", "trigger", "wait", "trigger"),
             _edge("wait", "trigger", "respond", "trigger"),
             _edge("hook", "json", "respond", "body")]
    if then_log:
        nodes.append({"id": "log", "type": "core.output.log", "config": {"label": "after"}})
        edges.append(_edge("respond", "trigger", "log", "in"))
    return {"nodes": nodes, "edges": edges}


def _stream(final_body: str | None = None, **piece_config) -> dict:
    """Webhook -> For-each over the call's json list: each item is written with
    `last` off, and after the last item a second Respond to Webhook ends the
    response (with `final_body` when given)."""
    nodes = [_hook(),
             {"id": "each", "type": "core.flow.for_each", "config": {}},
             _respond("piece", last=False, **piece_config),
             _respond("end")]
    edges = [_edge("hook", "trigger", "each", "trigger"),
             _edge("hook", "json", "each", "list"),
             _edge("each", "each", "piece", "trigger"),
             _edge("each", "item", "piece", "body"),
             _edge("piece", "trigger", "each", "loop"),
             _edge("each", "after_last", "end", "trigger")]
    if final_body is not None:
        nodes.append({"id": "final", "type": "core.value.text", "config": {"text": final_body}})
        edges.append(_edge("final", "out", "end", "body"))
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- harness
@contextlib.asynccontextmanager
async def _live(slug: str, graph: dict):
    """The graph On in its Hub, a client on the app, and the Hub's events."""
    HUBS.pop(slug, None)
    hub = get_hub(slug)
    events: asyncio.Queue = asyncio.Queue()
    hub.subscribers.add(events)
    problems = await hub.power_on(graph)
    assert not problems, problems
    assert hub.runtime is not None
    transport = httpx.ASGITransport(app=app, client=LOOPBACK_PEER)
    try:
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL, timeout=20) as client:
            yield hub, client, events
    finally:
        await hub.power_off()
        HUBS.pop(slug, None)


def _drained(events: asyncio.Queue) -> list[dict]:
    out = []
    while not events.empty():
        out.append(events.get_nowait())
    return out


def _logs(events: list[dict], node: str) -> list[str]:
    return [e["message"] for e in events if e.get("kind") == "log" and e.get("node") == node]


def _warnings(events: list[dict], node: str) -> list[str]:
    return [e["message"] for e in events if e.get("kind") == "warning" and e.get("node") == node]


def _fired(events: list[dict], node: str, port: str) -> bool:
    return any(e.get("kind") == "value" and e.get("node") == node and e.get("port") == port
               for e in events)


# ---------------------------------------------------------------- registry
def test_the_nodes_declare_their_knobs():
    from boltjar.sdk import NODE_REGISTRY
    hook = {w.name: w for w in NODE_REGISTRY["core.trigger.webhook"].widgets}
    assert hook["reply"].options == [RIGHT_AWAY, WAIT] and hook["reply"].default == RIGHT_AWAY
    assert (hook["timeout"].default, hook["timeout"].min, hook["timeout"].max) == (30, 1, 600)
    assert (hook["timeout"].op_field, hook["timeout"].op_values) == ("reply", (WAIT,))
    spec = NODE_REGISTRY["core.output.respond_webhook"]
    assert [p.name for p in spec.inputs] == ["trigger", "body"]
    assert spec.inputs[0].trigger and not spec.inputs[0].optional
    assert [p.name for p in spec.outputs] == ["trigger"]
    knobs = {w.name: w for w in spec.widgets}
    assert (knobs["status"].default, knobs["status"].min, knobs["status"].max) == (200, 100, 599)
    assert knobs["content_type"].options == list(replies.CONTENT_TYPES)
    assert knobs["last"].default is True
    assert all(knobs[k].promotable for k in ("status", "content_type", "headers", "last"))
    assert spec.subline == "respond · {status}" and spec.icon == "paper-plane-outline"


# ---------------------------------------------------------------- right away
def test_right_away_is_unchanged():
    async def scenario():
        graph = {"nodes": [_hook(reply=RIGHT_AWAY),
                           {"id": "log", "type": "core.output.log", "config": {}}],
                 "edges": [_edge("hook", "trigger", "log", "in")]}
        async with _live("rw-right-away", graph) as (hub, client, _events):
            resp = await client.post("/hook/rw-right-away/ask", json={"a": 1})
            assert (resp.status_code, resp.json()) == (200, {"ok": True})
            assert len(hub.replies) == 0
    asyncio.run(scenario())


def test_a_webhook_saved_without_the_knob_replies_right_away():
    graph = {"nodes": [{"id": "hook", "type": "core.trigger.webhook", "config": {"path": "ask"}},
                       {"id": "log", "type": "core.output.log", "config": {}}],
             "edges": [_edge("hook", "trigger", "log", "in")]}
    assert validate_graph(graph) == []

    async def scenario():
        async with _live("rw-old-graph", graph) as (_hub, client, _events):
            resp = await client.post("/hook/rw-old-graph/ask", json={})
            assert resp.json() == {"ok": True}
    asyncio.run(scenario())


# ---------------------------------------------------------------- single response
def test_a_json_body_answers_as_json_with_status_and_headers():
    async def scenario():
        graph = _single(status=201, headers='{"X-Reply": "yes", "Cache-Control": "no-store"}')
        async with _live("rw-json", graph) as (hub, client, _events):
            resp = await client.post("/hook/rw-json/ask", json={"name": "Ada", "n": 7})
            assert resp.status_code == 201
            assert resp.headers["content-type"] == "application/json"
            assert resp.headers["x-reply"] == "yes"
            assert resp.headers["cache-control"] == "no-store"
            assert resp.json() == {"name": "Ada", "n": 7}
            assert len(hub.replies) == 0
    asyncio.run(scenario())


def test_a_text_body_answers_as_plain_text():
    async def scenario():
        async with _live("rw-text", _single(body_port="body")) as (_hub, client, _events):
            resp = await client.post("/hook/rw-text/ask", content="hello there",
                                     headers={"content-type": "text/plain"})
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/plain")
            assert resp.text == "hello there"
    asyncio.run(scenario())


def test_the_content_type_knob_wins_over_auto():
    async def scenario():
        async with _live("rw-ct-plain", _single(content_type="text/plain")) as (_h, client, _e):
            resp = await client.post("/hook/rw-ct-plain/ask", json={"a": 1})
            assert resp.headers["content-type"].startswith("text/plain")
            assert json.loads(resp.text) == {"a": 1}
        async with _live("rw-ct-json", _single(body_port="body",
                                               content_type="application/json")) as (_h, client, _e):
            resp = await client.post("/hook/rw-ct-json/ask", content="plain words")
            assert resp.headers["content-type"] == "application/json"
            assert resp.json() == "plain words"  # plain text is quoted into JSON
            resp = await client.post("/hook/rw-ct-json/ask", content='{"already": "json"}')
            assert resp.json() == {"already": "json"}  # JSON text passes as it is
    asyncio.run(scenario())


def test_a_caller_asking_for_event_stream_gets_one_event():
    async def scenario():
        async with _live("rw-sse-one", _single()) as (_hub, client, _events):
            resp = await client.post("/hook/rw-sse-one/ask", json={"a": 1},
                                     headers={"accept": "text/event-stream"})
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.text == 'data: {"a": 1}\n\n'
    asyncio.run(scenario())


def test_an_unwired_body_answers_empty():
    graph = {"nodes": [_hook(), _respond(status=204)],
             "edges": [_edge("hook", "trigger", "respond", "trigger")]}

    async def scenario():
        async with _live("rw-empty", graph) as (_hub, client, _events):
            resp = await client.post("/hook/rw-empty/ask", json={})
            assert (resp.status_code, resp.content) == (204, b"")
    asyncio.run(scenario())


def test_refused_headers_are_left_out_with_a_warning():
    async def scenario():
        # a wired headers value (JSON text) is checked per fire; a knob is
        # checked by validation
        graph = _single()
        graph["nodes"][1]["config"]["promoted"] = ["headers"]
        graph["edges"].append(_edge("hook", "body", "respond", "headers"))
        async with _live("rw-refused", graph) as (_hub, client, events):
            resp = await client.post("/hook/rw-refused/ask",
                                     json={"Connection": "close", "X-Kept": "1"})
            assert resp.status_code == 200
            assert resp.json() == {"Connection": "close", "X-Kept": "1"}
            assert resp.headers["x-kept"] == "1"
            assert resp.headers.get("connection") != "close"
            warned = _warnings(_drained(events), "respond")
            assert warned == ["header 'Connection' cannot be set here, so it was left out"]
    asyncio.run(scenario())


def test_the_testclient_gets_the_same_single_response():
    graph = _single(status=202)
    with local_client() as client:
        HUBS.pop("rw-testclient", None)
        on = client.post("/api/runtime/rw-testclient/power", json={"action": "on", "graph": graph})
        assert on.json() == {"power": "on", "problems": []}
        try:
            resp = client.post("/hook/rw-testclient/ask", json={"who": "Alice"})
            assert (resp.status_code, resp.json()) == (202, {"who": "Alice"})
        finally:
            client.post("/api/runtime/rw-testclient/power", json={"action": "off"})
            HUBS.pop("rw-testclient", None)


# ---------------------------------------------------------------- timeout and stop
def test_no_answer_in_time_is_a_504_and_a_late_respond_does_nothing():
    async def scenario():
        async with _live("rw-timeout", _delayed(1.5, timeout=1, then_log=True)) as (hub, client, events):
            resp = await client.post("/hook/rw-timeout/ask", json={"a": 1})
            assert (resp.status_code, resp.json()) == (504, {"error": "no response within 1 s"})
            assert len(hub.replies) == 0
            await asyncio.sleep(0.8)  # the Wait ends and the Respond to Webhook fires late
            seen = _drained(events)
            assert "answered 504: nothing was written within 1 s" in _logs(seen, "hook")
            assert _logs(seen, "respond") == [
                "no webhook call is waiting for this response, so nothing was sent"]
            assert not _fired(seen, "respond", "trigger")
            assert not _fired(seen, "log", "trigger")
    asyncio.run(scenario())


def test_turning_the_graph_off_answers_503():
    async def scenario():
        async with _live("rw-off", _delayed(5)) as (hub, client, _events):
            call = asyncio.create_task(client.post("/hook/rw-off/ask", json={"a": 1}))
            await asyncio.sleep(0.2)
            assert len(hub.replies) == 1
            await hub.power_off()
            resp = await asyncio.wait_for(call, 2)
            assert (resp.status_code, resp.json()) == (503, {"error": "workflow stopped"})
            assert len(hub.replies) == 0
    asyncio.run(scenario())


def test_the_server_exiting_answers_503():
    async def scenario():
        async with _live("rw-exit", _delayed(5)) as (hub, client, _events):
            call = asyncio.create_task(client.post("/hook/rw-exit/ask", json={"a": 1}))
            await asyncio.sleep(0.2)
            await hub.stop_for_exit()
            resp = await asyncio.wait_for(call, 2)
            assert (resp.status_code, resp.json()) == (503, {"error": "workflow stopped"})
    asyncio.run(scenario())


def test_a_held_call_is_released_once():
    async def scenario():
        async with _live("rw-once", _delayed(0.1)) as (hub, client, _events):
            resp = await client.post("/hook/rw-once/ask", json={"a": 1})
            assert resp.json() == {"a": 1}
            assert len(hub.replies) == 0
        # the graph stopping afterwards finds nothing left to release
        assert len(hub.replies) == 0
    asyncio.run(scenario())


def test_every_release_path_happens_once():
    book = replies.ReplyBook()
    reply = book.hold(accepts_sse=False)
    assert reply.write("a", last=True, status=200, headers={}, content_type="auto").sent
    assert not reply.write("b", last=True, status=200, headers={}, content_type="auto").sent
    reply.abort(503, "workflow stopped")
    assert reply._events.qsize() == 1 and len(book) == 0


# ---------------------------------------------------------------- streaming
def test_pieces_stream_as_ndjson_and_the_last_fire_closes():
    async def scenario():
        async with _live("rw-ndjson", _stream()) as (hub, client, _events):
            resp = await client.post("/hook/rw-ndjson/ask", json=[{"n": 1}, {"n": 2}, {"n": 3}])
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/x-ndjson"
            assert resp.text == '{"n": 1}\n{"n": 2}\n{"n": 3}\n'
            assert [json.loads(line) for line in resp.text.splitlines()] == [
                {"n": 1}, {"n": 2}, {"n": 3}]
            assert len(hub.replies) == 0
    asyncio.run(scenario())


def test_text_pieces_stream_one_per_line_with_a_final_piece():
    async def scenario():
        async with _live("rw-lines", _stream(final_body="done")) as (_hub, client, _events):
            resp = await client.post("/hook/rw-lines/ask", json=["one", "two"])
            assert resp.headers["content-type"].startswith("text/plain")
            assert resp.text == "one\ntwo\ndone\n"
    asyncio.run(scenario())


def test_pieces_stream_as_server_sent_events_when_the_caller_asks():
    async def scenario():
        async with _live("rw-sse", _stream(final_body="end")) as (_hub, client, _events):
            resp = await client.post("/hook/rw-sse/ask", json=["a", {"b": 2}],
                                     headers={"accept": "text/event-stream"})
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.text == 'data: a\n\ndata: {"b": 2}\n\ndata: end\n\n'
    asyncio.run(scenario())


def test_the_content_type_knob_can_ask_for_server_sent_events():
    async def scenario():
        graph = _stream(content_type="text/event-stream")
        async with _live("rw-sse-knob", graph) as (_hub, client, _events):
            resp = await client.post("/hook/rw-sse-knob/ask", json=["x\ny"])
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.text == "data: x\ndata: y\n\n"
    asyncio.run(scenario())


def test_a_later_fire_cannot_change_the_status_and_warns_once():
    async def scenario():
        graph = _stream(final_body="end", status=207)
        graph["nodes"][3]["config"]["status"] = 404  # the closing Respond to Webhook
        async with _live("rw-head", graph) as (_hub, client, events):
            resp = await client.post("/hook/rw-head/ask", json=["a", "b"])
            assert resp.status_code == 207
            assert resp.text == "a\nb\nend\n"
            assert _warnings(_drained(events), "end") == [
                "the status, headers and content type of a response come from its first "
                "fire; this fire's differ and were ignored"]
    asyncio.run(scenario())


# ---------------------------------------------------------------- concurrency
def test_concurrent_calls_each_get_their_own_body():
    async def scenario():
        async with _live("rw-many", _delayed(0.05)) as (hub, client, _events):
            calls = [client.post("/hook/rw-many/ask", json={"i": i}) for i in range(6)]
            answers = await asyncio.gather(*calls)
            assert [r.json() for r in answers] == [{"i": i} for i in range(6)]
            assert len(hub.replies) == 0
    asyncio.run(scenario())


def test_concurrent_streams_never_cross():
    # a For-each runs one list at a time, so the calls line up in a Queue (the
    # closing Respond to Webhook acks the next); each keeps its own run
    graph = _stream(final_body="end")
    graph["nodes"].append({"id": "queue", "type": "core.flow.queue", "config": {}})
    graph["edges"][0] = _edge("hook", "trigger", "queue", "hook")
    graph["edges"] += [_edge("queue", "out", "each", "trigger"),
                       _edge("end", "trigger", "queue", "ack")]

    async def scenario():
        async with _live("rw-many-streams", graph) as (_hub, client, _events):
            calls = [client.post("/hook/rw-many-streams/ask", json=[f"{i}a", f"{i}b", f"{i}c"])
                     for i in range(4)]
            answers = await asyncio.gather(*calls)
            assert [r.text for r in answers] == [f"{i}a\n{i}b\n{i}c\nend\n" for i in range(4)]
    asyncio.run(scenario())


def test_calls_through_a_queue_keep_their_own_run():
    graph = {"nodes": [_hook(),
                       {"id": "queue", "type": "core.flow.queue", "config": {}},
                       {"id": "wait", "type": "core.flow.wait", "config": {"amount": 0.02}},
                       _respond()],
             "edges": [_edge("hook", "trigger", "queue", "hook"),
                       _edge("queue", "out", "wait", "trigger"),
                       _edge("wait", "trigger", "respond", "trigger"),
                       _edge("hook", "json", "respond", "body"),
                       _edge("respond", "trigger", "queue", "ack")]}

    async def scenario():
        async with _live("rw-queue", graph) as (_hub, client, _events):
            calls = [client.post("/hook/rw-queue/ask", json={"i": i}) for i in range(5)]
            answers = await asyncio.gather(*calls)
            assert [r.json() for r in answers] == [{"i": i} for i in range(5)]
    asyncio.run(scenario())


# ---------------------------------------------------------------- caps
def test_one_call_over_the_held_limit_is_a_429():
    async def scenario():
        async with _live("rw-limit", _delayed(0.5)) as (hub, client, _events):
            hub.replies.limit = 2
            held = [asyncio.create_task(client.post("/hook/rw-limit/ask", json={"i": i}))
                    for i in range(2)]
            await asyncio.sleep(0.1)
            over = await client.post("/hook/rw-limit/ask", json={"i": 9})
            assert (over.status_code, over.json()) == (
                429, {"error": "too many calls are waiting for a response"})
            assert [r.json() for r in await asyncio.gather(*held)] == [{"i": 0}, {"i": 1}]
    asyncio.run(scenario())


def test_the_default_caps():
    assert replies.MAX_HELD_PER_GRAPH == 64
    assert replies.MAX_RESPONSE_BYTES == 8 * 1024 * 1024
    assert replies.cap_text() == "8 MB"


def test_a_response_over_the_byte_cap_is_refused(monkeypatch):
    monkeypatch.setattr(replies, "MAX_RESPONSE_BYTES", 10)

    async def scenario():
        async with _live("rw-cap-one", _single(body_port="body")) as (_hub, client, events):
            resp = await client.post("/hook/rw-cap-one/ask", content="x" * 40)
            assert (resp.status_code, resp.json()) == (
                500, {"error": "response is larger than 10 bytes"})
            await asyncio.sleep(0.05)
            errors = [e for e in _drained(events) if e.get("kind") == "node_error"]
            assert [e["node"] for e in errors] == ["respond"]
            assert "went over 10 bytes" in errors[0]["error"]
        async with _live("rw-cap-stream", _stream(final_body="end")) as (_hub, client, _events):
            resp = await client.post("/hook/rw-cap-stream/ask", json=["abcd", "efgh", "ijkl"])
            assert resp.status_code == 200
            assert resp.text == "abcd\nefgh\n"  # the piece past the cap closes the stream
    asyncio.run(scenario())


# ---------------------------------------------------------------- no pending call
def test_a_respond_with_no_waiting_call_logs_one_line_and_does_nothing():
    events: list[dict] = []
    graph = {"nodes": [{"id": "go", "type": "core.trigger.manual", "config": {}},
                       _respond(),
                       {"id": "log", "type": "core.output.log", "config": {}}],
             "edges": [_edge("go", "trigger", "respond", "trigger"),
                       _edge("respond", "trigger", "log", "in")]}

    async def drive():
        rt = Runtime(observer=events.append)
        rt.build(graph)
        await rt.run()
        await asyncio.sleep(0.05)
        await rt.stop()
    asyncio.run(drive())
    assert _logs(events, "respond") == [
        "no webhook call is waiting for this response, so nothing was sent"]
    assert not _fired(events, "respond", "trigger")
    assert not _fired(events, "log", "trigger")
    assert not [e for e in events if e.get("kind") == "node_error"]


# ---------------------------------------------------------------- validation
def _kinds(problems: list[dict]) -> list[tuple]:
    return [(p["node"], p["kind"], p["message"]) for p in problems]


def test_a_waiting_webhook_needs_a_respond_downstream():
    graph = {"nodes": [_hook(), {"id": "log", "type": "core.output.log", "config": {}}],
             "edges": [_edge("hook", "trigger", "log", "in")]}
    assert _kinds(validate_graph(graph)) == [
        ("hook", "respond-missing", "Webhook waits for a Respond to Webhook that is not in the graph")]


def test_a_respond_needs_a_waiting_webhook_upstream():
    graph = _single()
    graph["nodes"][0]["config"]["reply"] = RIGHT_AWAY
    assert _kinds(validate_graph(graph)) == [
        ("respond", "respond-unused",
         "Respond to Webhook answers no Webhook: set one upstream to reply from "
         "Respond to Webhook")]
    graph = {"nodes": [{"id": "go", "type": "core.trigger.manual", "config": {}}, _respond()],
             "edges": [_edge("go", "trigger", "respond", "trigger")]}
    assert [p["kind"] for p in validate_graph(graph)] == ["respond-unused"]


def test_the_single_and_streaming_graphs_are_valid():
    assert validate_graph(_single()) == []
    assert validate_graph(_stream()) == []
    assert validate_graph(_delayed(1)) == []


def test_a_bypassed_respond_does_not_count():
    graph = _single()
    graph["nodes"][1]["disabled"] = True
    assert [p["kind"] for p in validate_graph(graph)] == ["respond-missing"]


def test_reach_passes_through_a_bypassed_passthrough():
    graph = {"nodes": [_hook(),
                       {"id": "tap", "type": "core.output.preview", "config": {}, "disabled": True},
                       _respond()],
             "edges": [_edge("hook", "trigger", "tap", "trigger"),
                       _edge("tap", "trigger", "respond", "trigger")]}
    assert validate_graph(graph) == []
    # a bypassed Wait with no passthrough for the wire breaks the reach
    graph["nodes"][1] = {"id": "tap", "type": "core.output.log", "config": {}, "disabled": True}
    graph["edges"][0] = _edge("hook", "trigger", "tap", "in")
    kinds = [p["kind"] for p in validate_graph(graph)]
    assert "respond-missing" in kinds and "respond-unused" in kinds


def test_a_bypassed_waiting_webhook_leaves_the_respond_unused():
    graph = _single()
    graph["nodes"][0]["disabled"] = True
    graph["nodes"].append({"id": "go", "type": "core.trigger.manual", "config": {}})
    kinds = [p["kind"] for p in validate_graph(graph)]
    assert "respond-unused" in kinds and "respond-missing" not in kinds


def test_bad_respond_knobs_are_problems_until_promoted():
    graph = _single(status=700, headers='{"Content-Length": "3", "X-Ok": "1"}')
    assert _kinds(validate_graph(graph)) == [
        ("respond", "bad-config", "status 700 is not an HTTP status (100 to 599)"),
        ("respond", "bad-config", "header 'Content-Length' cannot be set here")]
    graph = _single(headers="[1, 2]")
    assert [p["message"] for p in validate_graph(graph)] == ["headers must be a JSON object"]
    graph = _single(status=700)
    graph["nodes"][1]["config"]["promoted"] = ["status"]
    graph["edges"].append(_edge("hook", "json", "respond", "status"))
    assert validate_graph(graph) == []


def test_the_headers_filter():
    assert replies.clean_headers({"X-A": "1", "Transfer-Encoding": "chunked", "Upgrade": "h2c",
                                  "Bad\nName": "v", "X-B": "a\r\nInjected: 1"}) == (
        {"X-A": "1"},
        ["header 'Transfer-Encoding' cannot be set here", "header 'Upgrade' cannot be set here",
         "header 'Bad\\nName' is not a valid header", "header 'X-B' is not a valid header"])
    assert replies.clean_headers("{}") == ({}, [])
    assert replies.clean_headers({"X-N": 5}) == ({"X-N": "5"}, [])
