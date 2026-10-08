"""One Ctrl+C stops the server: every running graph stops cleanly and every
live connection (the editor websockets) ends, so nothing is left for the
server to wait on."""
from __future__ import annotations

import asyncio
import io
import logging

import pytest
from starlette.websockets import WebSocketDisconnect

from boltjar import console, server
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from local_client import local_client

MANUAL_LOG = {
    "nodes": [
        {"id": "m", "type": "core.trigger.manual", "config": {}},
        {"id": "lg", "type": "core.output.log", "config": {"label": "o"}},
    ],
    "edges": [{"src": "m", "src_port": "trigger", "dst": "lg", "dst_port": "in"}],
}


@pytest.fixture
def hubs(monkeypatch):
    """A registry of this test's Hubs only, so graphs other tests left behind
    never count toward a shutdown here."""
    fresh: dict = {}
    monkeypatch.setattr(server, "HUBS", fresh)
    return fresh


# ---------------------------------------------------------------- shutdown
def test_shutdown_stops_every_running_graph(hubs):
    with local_client() as client:
        for slug in ("sd-a", "sd-b"):
            r = client.post(f"/api/runtime/{slug}/power", json={"action": "on", "graph": MANUAL_LOG})
            assert r.json()["power"] == "on"
        assert client.portal.call(server.shutdown_all) == 2
        assert all(hub.runtime is None for hub in hubs.values())
        assert client.portal.call(server.shutdown_all) == 0  # safe to call again


def test_shutdown_closes_the_editor_websockets(hubs):
    with local_client() as client:
        with client.websocket_connect("/ws?slug=sd-ws") as ws:
            assert ws.receive_json()["kind"] == "status"
            assert server.open_connections() == 1
            client.portal.call(server.shutdown_all)
            with pytest.raises(WebSocketDisconnect) as closed:
                for _ in range(10):
                    ws.receive_json()
            assert closed.value.code == 1001  # going away


def test_a_graph_whose_stop_hangs_holds_up_none_of_the_others(hubs):
    async def scenario():
        stuck, fine = server.get_hub("sd-stuck"), server.get_hub("sd-fine")
        for hub in (stuck, fine):
            assert await hub.power_on(MANUAL_LOG) is None
        assert server.running_graphs() == ["sd-stuck", "sd-fine"]

        async def never_stops():
            await asyncio.Event().wait()

        stuck.stop_for_exit = never_stops  # first in line, and it never returns
        shutdown = asyncio.ensure_future(server.shutdown_all())
        for _ in range(100):
            if fine.runtime is None:
                break
            await asyncio.sleep(0.01)
        assert fine.runtime is None
        assert server.running_graphs() == ["sd-stuck"]
        shutdown.cancel()
        await stuck.runtime.stop()

    asyncio.run(scenario())


def test_a_stop_that_fails_shows_no_key_in_the_terminal(hubs, monkeypatch):
    # a custom node's store whose close raises with a resolved key in its message
    # (an httpx error URL, say): the terminal prints its token, never the key
    key = "sk-test-0123456789abcdef"  # gitleaks:allow (made-up test value)
    monkeypatch.setitem(server._secrets._store, "SHUTDOWN_TEST_KEY", key)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(console.LogFormatter(verbose=True, stream=stream))
    monkeypatch.setattr(server._log, "handlers", [handler])
    monkeypatch.setattr(server._log, "propagate", False)

    async def scenario():
        hub = server.get_hub("sd-key")
        assert await hub.power_on(MANUAL_LOG) is None

        async def refuses():
            raise RuntimeError(f"401 for https://api.example.com/v1/close?key={key}")

        hub.stop_for_exit = refuses
        assert await server.shutdown_all() == 0
        await hub.runtime.stop()

    asyncio.run(scenario())
    out = stream.getvalue()
    assert "could not stop graph sd-key cleanly" in out
    assert key not in out and "key={{secret.SHUTDOWN_TEST_KEY}}" in out


def test_leaving_the_app_lifespan_stops_the_graphs(hubs):
    with local_client() as client:
        client.post("/api/runtime/sd-life/power", json={"action": "on", "graph": MANUAL_LOG})
        assert hubs["sd-life"].runtime is not None
    assert hubs["sd-life"].runtime is None
