"""Tests for the Webhook IN trigger node (core.trigger.webhook) + the
`/hook/{slug}/{path}` server route.

The route looks up the live Hub for {slug}, finds a matching WebhookTrigger
(path + method match), enforces the optional shared-secret header, and emits
body/json/headers/query/trigger onto the node's outputs (trigger LAST). These
tests use the TestClient to drive the WS-based On power model end to end, then
hit the route and observe the live event stream (the WS multiplexes every
`value` and `node_status` event for the running graph) to verify the wired
chain fires.

Why we listen on the WS instead of polling node latches: the TestClient's
anyio portal only advances the runtime's event loop while we are awaiting on
that loop (an HTTP call or a WS read). A pure `time.sleep` from the sync test
thread starves the runtime consumer tasks, so the latch never updates.
Reading from the WS forces the loop to tick.
"""
from __future__ import annotations

from local_client import local_client

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.server import HUBS


client = local_client()


def _graph(path: str = "foo", method: str = "POST", secret: str = "",
           nodes_extra: list | None = None, edges_extra: list | None = None) -> dict:
    """A graph with one Webhook trigger -> Log sink. The Log node's `done` is
    irrelevant; we read what landed on the Log node's `in` latch by inspecting
    the runtime instead, but the route also broadcasts a `value` event."""
    nodes = [
        {"id": "hook", "type": "core.trigger.webhook",
         "config": {"path": path, "method": method, "secret": secret}},
        {"id": "log", "type": "core.output.log", "config": {"label": "hooked"}},
    ]
    edges = [
        {"src": "hook", "src_port": "trigger", "dst": "log", "dst_port": "in"},
    ]
    if nodes_extra:
        nodes.extend(nodes_extra)
    if edges_extra:
        edges.extend(edges_extra)
    return {"nodes": nodes, "edges": edges}


def _drain_status(ws, kind: str, max_frames: int = 16) -> dict:
    for _ in range(max_frames):
        evt = ws.receive_json()
        if evt.get("kind") == kind:
            return evt
    raise AssertionError(f"never received {kind!r} after {max_frames} frames")


def _power_on(slug: str, graph: dict) -> "tuple":
    """Open a WS for the slug, send `on`, wait for the on-status, return the
    open WS so the caller can keep the runtime alive and inspect later."""
    HUBS.pop(slug, None)
    ws = client.websocket_connect(f"/ws?slug={slug}").__enter__()
    # initial status frame (off)
    ws.receive_json()
    ws.send_json({"action": "on", "graph": graph})
    _drain_status(ws, "status")  # power=on (or invalid -> raises below)
    return ws


def _close(ws) -> None:
    try:
        ws.send_json({"action": "off"})
    except Exception:
        pass
    try:
        ws.__exit__(None, None, None)
    except Exception:
        pass


def _wait_for_value(ws, node_id: str, port: str, max_frames: int = 40) -> dict:
    """Drain the WS event stream until a `value` event arrives for (node, port).
    Reading the WS ticks the portal loop, which is what advances the runtime's
    consumer tasks under TestClient."""
    seen: list = []
    for _ in range(max_frames):
        evt = ws.receive_json()
        seen.append(evt)
        if (evt.get("kind") == "value"
                and evt.get("node") == node_id
                and evt.get("port") == port):
            return evt
    raise AssertionError(
        f"no value event for {node_id}.{port} after {max_frames} frames; saw {seen!r}"
    )


def _saw_value(ws, node_id: str, port: str, max_frames: int = 20) -> bool:
    """Return True if a value event for (node, port) appears within max_frames."""
    for _ in range(max_frames):
        try:
            evt = ws.receive_json()
        except Exception:
            return False
        if (evt.get("kind") == "value"
                and evt.get("node") == node_id
                and evt.get("port") == port):
            return True
    return False


# ---------------------------------------------------------------------------
# Registry: the node exists with the declared ports
# ---------------------------------------------------------------------------

def test_node_is_registered_with_expected_ports():
    from boltjar.sdk import NODE_REGISTRY
    spec = NODE_REGISTRY["core.trigger.webhook"]
    out_names = {p.name for p in spec.outputs}
    assert out_names == {"trigger", "body", "json", "headers", "query"}


# ---------------------------------------------------------------------------
# 404 when the workflow is NOT running
# ---------------------------------------------------------------------------

def test_404_when_workflow_not_running():
    slug = "wh-not-running"
    HUBS.pop(slug, None)
    resp = client.post(f"/hook/{slug}/foo", json={"hello": "world"})
    assert resp.status_code == 404
    assert resp.json() == {"error": "workflow not running"}


# ---------------------------------------------------------------------------
# Happy path: POST a json body, the Log node receives the trigger payload (1)
# and the data ports have latched the right values.
# ---------------------------------------------------------------------------

def test_200_and_payload_propagates_when_running():
    slug = "wh-happy"
    ws = _power_on(slug, _graph(path="foo", method="POST"))
    try:
        resp = client.post(f"/hook/{slug}/foo?x=1&y=two",
                           json={"name": "ada", "n": 7},
                           headers={"X-Custom": "value"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Drive the runtime loop by reading WS events; the route's emits surface
        # as live `value` events. Wait for the Log's `done` event (the Log fires
        # only after the trigger lands on its `in` port, so this proves the chain
        # ran end to end).
        _wait_for_value(ws, "log", "trigger")

        # the data ports latched the right values on the webhook node itself.
        # The body is the raw request bytes decoded; httpx may serialise the
        # json with no whitespace, so parse before comparing.
        import json as _json
        hook = HUBS[slug].runtime.nodes["hook"]
        assert _json.loads(hook.out_latch.get("body") or "") == {"name": "ada", "n": 7}
        assert hook.out_latch.get("json") == {"name": "ada", "n": 7}
        assert hook.out_latch.get("query") == {"x": "1", "y": "two"}
        headers = hook.out_latch.get("headers") or {}
        assert headers.get("x-custom") == "value"
        # safety: cookie/authorization are stripped from the emitted dict.
        assert "cookie" not in headers
        assert "authorization" not in headers
    finally:
        _close(ws)


# ---------------------------------------------------------------------------
# Secret enforcement: 401 when missing or wrong, 200 when matching
# ---------------------------------------------------------------------------

def test_401_when_secret_missing():
    slug = "wh-secret-missing"
    ws = _power_on(slug, _graph(secret="s3cret"))
    try:
        resp = client.post(f"/hook/{slug}/foo", json={})
        assert resp.status_code == 401
        assert resp.json() == {"error": "forbidden"}
    finally:
        _close(ws)


def test_401_when_secret_wrong():
    slug = "wh-secret-wrong"
    ws = _power_on(slug, _graph(secret="s3cret"))
    try:
        resp = client.post(f"/hook/{slug}/foo", json={},
                           headers={"X-Webhook-Secret": "nope"})
        assert resp.status_code == 401
    finally:
        _close(ws)


def test_200_when_secret_matches():
    slug = "wh-secret-ok"
    ws = _power_on(slug, _graph(secret="s3cret"))
    try:
        resp = client.post(f"/hook/{slug}/foo", json={"ok": 1},
                           headers={"X-Webhook-Secret": "s3cret"})
        assert resp.status_code == 200
        # The Log's `done` event proves the trigger fired end to end.
        _wait_for_value(ws, "log", "trigger")
    finally:
        _close(ws)


# ---------------------------------------------------------------------------
# Method filter: a node with method=GET rejects a POST with 404
# ---------------------------------------------------------------------------

def test_method_filter_rejects_mismatched_method():
    slug = "wh-method"
    ws = _power_on(slug, _graph(path="only-get", method="GET"))
    try:
        # POST against a GET-only webhook -> 404 (no matching webhook).
        resp = client.post(f"/hook/{slug}/only-get", json={})
        assert resp.status_code == 404
        # the matching GET goes through.
        resp = client.get(f"/hook/{slug}/only-get")
        assert resp.status_code == 200
    finally:
        _close(ws)


# ---------------------------------------------------------------------------
# Multiple webhook nodes with different paths in the same runtime
# ---------------------------------------------------------------------------

def test_multiple_webhooks_route_by_path():
    """Two webhook nodes in one graph, each wired to its own Log; a request to
    /hook/<slug>/<path> must fan out only onto the matching path's chain."""
    slug = "wh-multi"
    graph = {
        "nodes": [
            {"id": "hook_a", "type": "core.trigger.webhook",
             "config": {"path": "alpha", "method": "POST", "secret": ""}},
            {"id": "hook_b", "type": "core.trigger.webhook",
             "config": {"path": "beta", "method": "POST", "secret": ""}},
            {"id": "log_a", "type": "core.output.log", "config": {"label": "a"}},
            {"id": "log_b", "type": "core.output.log", "config": {"label": "b"}},
        ],
        "edges": [
            {"src": "hook_a", "src_port": "trigger", "dst": "log_a", "dst_port": "in"},
            {"src": "hook_b", "src_port": "trigger", "dst": "log_b", "dst_port": "in"},
        ],
    }
    ws = _power_on(slug, graph)
    try:
        # hit alpha; log_a fires (we see its `done`), log_b stays silent.
        resp = client.post(f"/hook/{slug}/alpha", json={"path": "alpha"})
        assert resp.status_code == 200
        _wait_for_value(ws, "log_a", "trigger")
        # log_b should not have latched anything on its `in` port.
        assert "in" not in HUBS[slug].runtime.nodes["log_b"].latch

        # hit beta; log_b fires now.
        resp = client.post(f"/hook/{slug}/beta", json={"path": "beta"})
        assert resp.status_code == 200
        _wait_for_value(ws, "log_b", "trigger")
    finally:
        _close(ws)
