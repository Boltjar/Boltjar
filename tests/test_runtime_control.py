"""The REST runtime-control routes: POST /api/runtime/{slug}/power, GET .../state,
POST .../fire, POST .../chat.

These are the HTTP twin of the /ws power actions (the same per-slug Hub methods),
so an agent can drive a live graph without a websocket. Everything runs in-process
via the FastAPI TestClient; no server is started. The live power-on tests use the
TestClient as a context manager so the runtime's background tasks stay on one loop
for the life of the block.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

import boltjar.nodes.core  # noqa: F401  registers the core nodes
import boltjar.server as srv
from boltjar.server import app, HUBS

# Manual -> Log: Manual fires once on power-on (emits `trigger`), so `latest`
# populates immediately; a validation-clean, network-free graph.
MANUAL_GRAPH = {
    "nodes": [
        {"id": "m", "type": "core.trigger.manual", "config": {}, "pos": [0, 0]},
        {"id": "lg", "type": "core.output.log", "config": {"label": "o"}, "pos": [200, 0]},
    ],
    "edges": [{"src": "m", "src_port": "trigger", "dst": "lg", "dst_port": "in"}],
}

# Chat Input -> Log: injected chat text should propagate to the log node.
CHAT_GRAPH = {
    "nodes": [
        {"id": "c", "type": "core.trigger.chat", "config": {}, "pos": [0, 0]},
        {"id": "lg", "type": "core.output.log", "config": {"label": "o"}, "pos": [200, 0]},
    ],
    "edges": [{"src": "c", "src_port": "trigger", "dst": "lg", "dst_port": "in"}],
}


def test_power_on_inline_then_state_fire_off():
    with TestClient(app) as client:
        try:
            r = client.post("/api/runtime/rc-a/power",
                            json={"action": "on", "graph": MANUAL_GRAPH})
            assert r.status_code == 200
            assert r.json() == {"power": "on", "problems": []}

            st = client.get("/api/runtime/rc-a/state").json()
            assert st["power"] == "on"
            ports = {(e["node"], e["port"]) for e in st["latest"]}
            # Manual emitted its trigger on start; the state reflects that live event.
            assert ("m", "trigger") in ports

            assert client.post("/api/runtime/rc-a/fire", json={"node": "m"}).json() == {"ok": True}

            off = client.post("/api/runtime/rc-a/power", json={"action": "off"}).json()
            assert off == {"power": "off", "problems": []}
            assert client.get("/api/runtime/rc-a/state").json()["power"] == "off"
        finally:
            HUBS.pop("rc-a", None)


def test_chat_injects_and_propagates_downstream():
    with TestClient(app) as client:
        try:
            client.post("/api/runtime/rc-chat/power",
                        json={"action": "on", "graph": CHAT_GRAPH})
            assert client.post("/api/runtime/rc-chat/chat",
                               json={"node": "c", "text": "hello there"}).json() == {"ok": True}

            latest: dict = {}
            deadline = time.time() + 3
            while time.time() < deadline:
                st = client.get("/api/runtime/rc-chat/state").json()
                latest = {(e["node"], e["port"]): e for e in st["latest"]}
                if ("lg", "trigger") in latest:  # reached the downstream Log
                    break
                time.sleep(0.05)
            assert latest.get(("c", "text"), {}).get("value") == "hello there"
            assert ("lg", "trigger") in latest  # the chat propagated through
        finally:
            client.post("/api/runtime/rc-chat/power", json={"action": "off"})
            HUBS.pop("rc-chat", None)


def test_ws_stream_replays_status_on_connect():
    """The /ws stream that the MCP `observe` tool consumes: on connect it replays
    the current power status (and cached values). Locks the server-side contract."""
    with TestClient(app) as client:
        try:
            client.post("/api/runtime/rc-ws/power",
                        json={"action": "on", "graph": MANUAL_GRAPH})
            with client.websocket_connect("/ws?slug=rc-ws") as ws:
                msg = ws.receive_json()
                assert msg["kind"] == "status" and msg["power"] == "on"
        finally:
            client.post("/api/runtime/rc-ws/power", json={"action": "off"})
            HUBS.pop("rc-ws", None)


def test_state_unknown_slug_is_off():
    with TestClient(app) as client:
        r = client.get("/api/runtime/never-seen-slug/state")
        assert r.status_code == 200
        assert r.json() == {"power": "off", "latest": []}


def test_fire_and_chat_404_when_not_running():
    with TestClient(app) as client:
        assert client.post("/api/runtime/ghost/fire", json={"node": "m"}).status_code == 404
        assert client.post("/api/runtime/ghost/chat",
                           json={"node": "c", "text": "x"}).status_code == 404


def test_power_unknown_action_is_400():
    with TestClient(app) as client:
        try:
            assert client.post("/api/runtime/rc-x/power",
                               json={"action": "spin"}).status_code == 400
        finally:
            HUBS.pop("rc-x", None)


def test_power_on_invalid_graph_returns_problems_and_stays_off():
    # a lone Log: required input `in` unwired AND no trigger node -> rejected.
    bad = {"nodes": [{"id": "lg", "type": "core.output.log", "config": {}, "pos": [0, 0]}],
           "edges": []}
    with TestClient(app) as client:
        try:
            j = client.post("/api/runtime/rc-bad/power",
                            json={"action": "on", "graph": bad}).json()
            assert j["power"] == "off"
            assert j["problems"]  # non-empty: validation blocked power-on
        finally:
            HUBS.pop("rc-bad", None)


def test_power_on_without_graph_missing_file_is_404(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "GRAPHS_DIR", tmp_path)
    with TestClient(app) as client:
        try:
            r = client.post("/api/runtime/no-such-graph/power", json={"action": "on"})
            assert r.status_code == 404
        finally:
            HUBS.pop("no-such-graph", None)


def test_power_on_loads_saved_graph_from_disk(monkeypatch, tmp_path):
    # the load-from-disk path: no inline graph, so the saved file for the slug loads.
    monkeypatch.setattr(srv, "GRAPHS_DIR", tmp_path)
    monkeypatch.setattr(srv, "AUTOSAVE_DIR", tmp_path / "auto")
    (tmp_path / "disk-slug.json").write_text(json.dumps(MANUAL_GRAPH), encoding="utf-8")
    with TestClient(app) as client:
        try:
            r = client.post("/api/runtime/disk-slug/power", json={"action": "on"})
            assert r.status_code == 200 and r.json()["power"] == "on"
            assert client.get("/api/runtime/disk-slug/state").json()["power"] == "on"
        finally:
            client.post("/api/runtime/disk-slug/power", json={"action": "off"})
            HUBS.pop("disk-slug", None)
