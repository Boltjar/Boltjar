"""One Ctrl+C stops the server: every running graph stops cleanly and every
live connection (the editor websockets, the media streams) ends, so nothing is
left for the server to wait on."""
from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from boltjar import server
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
            assert server.open_connections() == (1, 0)
            client.portal.call(server.shutdown_all)
            with pytest.raises(WebSocketDisconnect) as closed:
                for _ in range(10):
                    ws.receive_json()
            assert closed.value.code == 1001  # going away


def test_shutdown_ends_the_media_streams(hubs):
    async def scenario():
        hub = server.get_hub("sd-stream")
        response = await server.avatar_stream("sd-stream", "avatar")
        body = response.body_iterator
        first = asyncio.ensure_future(body.__anext__())
        await asyncio.sleep(0)
        hub.publish_stream("avatar", {"text": "hi"})
        assert (await first).startswith("data: ")
        assert server.open_connections() == (0, 1)
        waiting = asyncio.ensure_future(body.__anext__())
        await asyncio.sleep(0)
        await server.shutdown_all()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(waiting, 1)
        assert "avatar" not in hub.stream_subscribers

    asyncio.run(scenario())


def test_a_graph_whose_stop_hangs_holds_up_none_of_the_others(hubs):
    async def scenario():
        stuck, fine = server.get_hub("sd-stuck"), server.get_hub("sd-fine")
        for hub in (stuck, fine):
            assert await hub.power_on(MANUAL_LOG) is None
        assert server.running_graphs() == ["sd-stuck", "sd-fine"]

        async def never_stops():
            await asyncio.Event().wait()

        stuck.power_off = never_stops  # first in line, and it never returns
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


def test_leaving_the_app_lifespan_stops_the_graphs(hubs):
    with local_client() as client:
        client.post("/api/runtime/sd-life/power", json={"action": "on", "graph": MANUAL_LOG})
        assert hubs["sd-life"].runtime is not None
    assert hubs["sd-life"].runtime is None
