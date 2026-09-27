"""Tests for the `live_graph` set: the server-authoritative record of which
nodes/edges the running graph is built from.

This exists to enforce a core rule: a structural edit made in the editor while
a graph is powered ON (adding a Preview node + wire) must NOT appear live until the
user does Save & Restart. The editor classifies each node/wire against this set, so
the server has to (a) stash the running graph, (b) broadcast the set on every power
change, and (c) replay it on connect. Draft-only edges are simply absent from it.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from boltjar.server import app, HUBS

client = TestClient(app)


# a minimal but real graph: Manual trigger -> Log. Its one edge is the live edge.
GRAPH = {
    "nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "log", "type": "core.output.log", "config": {"label": "t"}},
    ],
    "edges": [{"src": "m", "src_port": "trigger", "dst": "log", "dst_port": "in"}],
}


def _drain_until(ws, kind: str, max_frames: int = 16) -> dict:
    for _ in range(max_frames):
        evt = ws.receive_json()
        if evt.get("kind") == kind:
            return evt
    raise AssertionError(f"never received a {kind!r} event after {max_frames} frames")


def test_live_graph_broadcast_on_power_on() -> None:
    """Turning On broadcasts a live_graph carrying exactly the running graph's
    node ids + edge tuples, so the editor knows what is live."""
    HUBS.pop("lg-on", None)
    with client.websocket_connect("/ws?slug=lg-on") as ws:
        ws.receive_json()  # initial status
        ws.receive_json()  # initial (empty) live_graph
        ws.send_json({"action": "on", "graph": GRAPH})
        lg = _drain_until(ws, "live_graph")
        assert set(lg["nodes"]) == {"m", "log"}
        assert lg["edges"] == [["m", "trigger", "log", "in"]]
        ws.send_json({"action": "off"})


def test_connect_replays_current_live_set() -> None:
    """A second editor connecting to a live slug is replayed the live_graph, so a
    browser refresh / new tab reconstructs the live set (power persistence)."""
    HUBS.pop("lg-replay", None)
    with client.websocket_connect("/ws?slug=lg-replay") as ws_a:
        ws_a.receive_json()  # status
        ws_a.receive_json()  # empty live_graph
        ws_a.send_json({"action": "on", "graph": GRAPH})
        _drain_until(ws_a, "live_graph")  # the "on" broadcast

        # a fresh connection to the SAME slug must be told the live set on connect.
        with client.websocket_connect("/ws?slug=lg-replay") as ws_b:
            status = ws_b.receive_json()
            assert status["kind"] == "status" and status["power"] == "on"
            lg = _drain_until(ws_b, "live_graph")
            assert set(lg["nodes"]) == {"m", "log"}
            assert lg["edges"] == [["m", "trigger", "log", "in"]]

        ws_a.send_json({"action": "off"})


def test_off_broadcasts_empty_live_set() -> None:
    """Power off empties the live set: nothing is live, so no wire may paint/pulse."""
    HUBS.pop("lg-off", None)
    with client.websocket_connect("/ws?slug=lg-off") as ws:
        ws.receive_json()  # status
        ws.receive_json()  # empty live_graph
        ws.send_json({"action": "on", "graph": GRAPH})
        _drain_until(ws, "live_graph")
        ws.send_json({"action": "off"})
        lg = _drain_until(ws, "live_graph")
        assert lg["nodes"] == []
        assert lg["edges"] == []


def test_hub_forgets_graph_after_stop() -> None:
    """The Hub drops its stashed graph on stop, so a later live_graph_event() is
    empty even before a fresh power-on (no stale live set lingers)."""
    HUBS.pop("lg-forget", None)
    with client.websocket_connect("/ws?slug=lg-forget") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"action": "on", "graph": GRAPH})
        _drain_until(ws, "live_graph")
        hub = HUBS["lg-forget"]
        assert hub.graph is not None
        ws.send_json({"action": "off"})
        _drain_until(ws, "live_graph")
        assert hub.graph is None
        ev = hub.live_graph_event()
        assert ev["nodes"] == [] and ev["edges"] == []


def test_draft_edge_absent_from_live_set() -> None:
    """The heart of the bug: an edge NOT in the graph that was powered on is absent
    from the live set. (The editor tests membership by the same src:port->dst:port
    key, so a wire drawn after power-on classifies as draft and shows no live data.)"""
    HUBS.pop("lg-draft", None)
    with client.websocket_connect("/ws?slug=lg-draft") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"action": "on", "graph": GRAPH})
        lg = _drain_until(ws, "live_graph")
        live_edge_keys = {f"{s}:{sp}->{d}:{dp}" for s, sp, d, dp in lg["edges"]}
        # the live wire is present…
        assert "m:trigger->log:in" in live_edge_keys
        # …a hypothetical draft Preview wire off the same live source is NOT.
        assert "m:trigger->preview1:in" not in live_edge_keys
        ws.send_json({"action": "off"})
