"""Every value event carries an id, and the replay on connect repeats it.

On connect the server replays the latest value of every wire, so an editor
reconnecting to the graph it already shows (a dropped connection) receives
values it holds. The id is how it tells a replay from a new value: without it
each replay landed in the port's history again, and a Chat viewer showed the
last message and its reply twice.
"""
from __future__ import annotations

from local_client import local_client

import boltjar.server as server
from boltjar.server import HUBS

client = local_client()

# Manual fires once at start, so `m:trigger` lands in the replay cache.
GRAPH = {
    "nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "log", "type": "core.output.log", "config": {"label": "t"}},
    ],
    "edges": [{"src": "m", "src_port": "trigger", "dst": "log", "dst_port": "in"}],
}


def _value(ws, max_frames: int = 16) -> dict:
    for _ in range(max_frames):
        evt = ws.receive_json()
        if evt.get("kind") == "value":
            return evt
    raise AssertionError(f"no value event in {max_frames} frames")


def test_replayed_value_keeps_its_id() -> None:
    HUBS.pop("ev-ids", None)
    with client.websocket_connect("/ws?slug=ev-ids") as ws_a:
        ws_a.send_json({"action": "on", "graph": GRAPH})
        live = _value(ws_a)
        assert isinstance(live.get("id"), str) and live["id"]
        # a second connection (the editor reconnecting) is replayed that value
        with client.websocket_connect("/ws?slug=ev-ids") as ws_b:
            replay = _value(ws_b)
        assert (replay["node"], replay["port"]) == (live["node"], live["port"])
        assert replay["id"] == live["id"]
        ws_a.send_json({"action": "off"})


def test_event_ids_never_repeat() -> None:
    hub = server.Hub()
    events = [{"kind": "value", "node": "n", "port": "p", "value": i} for i in range(3)]
    for evt in events:
        hub.broadcast(evt)
    other = server.Hub()
    other.broadcast(extra := {"kind": "value", "node": "n", "port": "p", "value": 0})
    ids = [e["id"] for e in events] + [extra["id"]]
    assert len(set(ids)) == 4  # unique across hubs, not only within one
