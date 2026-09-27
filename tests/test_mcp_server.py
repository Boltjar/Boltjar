"""The MCP tool functions (boltjar.mcp_server).

The HTTP-backed tools are exercised in-process by pointing the module's `_client`
factory at an httpx.ASGITransport against the FastAPI app, so no server runs. The
tool functions are plain coroutines (FastMCP's @tool leaves them callable), so we
await them directly.

The `observe` / `send_chat_and_observe` tools open a REAL websocket
(`websockets.connect`), which needs a listening TCP server. That cannot be driven
by the in-process ASGI transport, so the socket connect itself is not unit-tested
here; instead its collection logic (`_drain`) is tested directly, the server-side
/ws replay it consumes is locked in tests/test_runtime_control.py, and the
backend-down error path is tested by pointing at a dead port.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import boltjar.nodes.core  # noqa: F401  registers the core nodes
import boltjar.mcp_server as mcp_server
import boltjar.server as srv
from boltjar.server import app, HUBS

MANUAL_GRAPH = {
    "nodes": [
        {"id": "m", "type": "core.trigger.manual", "config": {}, "pos": [0, 0]},
        {"id": "lg", "type": "core.output.log", "config": {"label": "o"}, "pos": [200, 0]},
    ],
    "edges": [{"src": "m", "src_port": "trigger", "dst": "lg", "dst_port": "in"}],
}


def _asgi_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp-test")


@pytest.fixture(autouse=True)
def _point_client_at_app(monkeypatch):
    """Every HTTP tool call goes to the in-process app, not a live backend."""
    monkeypatch.setattr(mcp_server, "_client", _asgi_client)


def run(coro):
    return asyncio.run(coro)


def test_list_node_types_is_condensed_schema():
    cat = run(mcp_server.list_node_types())
    assert isinstance(cat, list) and cat
    # every entry carries the author-facing schema shape
    for n in cat:
        assert set(n) >= {"id", "title", "category", "ports", "knobs"}
    man = next(n for n in cat if n["id"] == "core.trigger.manual")
    assert man["title"] == "Manual"
    assert {"name": "trigger", "type": "event", "direction": "out"} in man["ports"]
    # a node with knobs exposes name/kind/default/options
    chat = next(n for n in cat if n["id"] == "core.trigger.chat")
    knob_names = {k["name"] for k in chat["knobs"]}
    assert "placeholder" in knob_names
    for k in chat["knobs"]:
        assert set(k) >= {"name", "kind", "default", "options"}


def test_list_models_returns_catalog():
    out = run(mcp_server.list_models())
    assert "models" in out


def test_graph_crud_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "GRAPHS_DIR", tmp_path)
    monkeypatch.setattr(srv, "AUTOSAVE_DIR", tmp_path / "auto")

    saved = run(mcp_server.save_graph("mcp-crud", MANUAL_GRAPH))
    assert saved["saved"] is True and saved["problems"] == []

    assert "mcp-crud" in run(mcp_server.list_graphs())["graphs"]
    got = run(mcp_server.get_graph("mcp-crud"))
    assert got["nodes"][0]["id"] == "m"

    assert run(mcp_server.validate_graph(MANUAL_GRAPH))["problems"] == []

    run(mcp_server.delete_graph("mcp-crud"))
    assert "mcp-crud" not in run(mcp_server.list_graphs())["graphs"]


def test_save_graph_reports_validation_problems(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "GRAPHS_DIR", tmp_path)
    monkeypatch.setattr(srv, "AUTOSAVE_DIR", tmp_path / "auto")
    bad = {"nodes": [{"id": "lg", "type": "core.output.log", "config": {}, "pos": [0, 0]}],
           "edges": []}
    saved = run(mcp_server.save_graph("mcp-bad", bad))
    assert saved["saved"] is True
    assert saved["problems"]  # non-empty: it saved but would not power on


def test_power_state_and_off_via_mcp_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "GRAPHS_DIR", tmp_path)
    monkeypatch.setattr(srv, "AUTOSAVE_DIR", tmp_path / "auto")
    (tmp_path / "mcp-rt.json").write_text(json.dumps(MANUAL_GRAPH), encoding="utf-8")

    async def go():
        try:
            on = await mcp_server.power("mcp-rt", "on")
            assert on == {"power": "on", "problems": []}
            st = await mcp_server.runtime_state("mcp-rt")
            assert st["power"] == "on"
            ports = {(e["node"], e["port"]) for e in st["latest"]}
            assert ("m", "trigger") in ports
            fired = await mcp_server.fire("mcp-rt", "m")
            assert fired == {"ok": True}
            off = await mcp_server.power("mcp-rt", "off")
            assert off["power"] == "off"
        finally:
            HUBS.pop("mcp-rt", None)

    run(go())


def test_fire_tool_errors_when_not_running():
    with pytest.raises(RuntimeError):
        run(mcp_server.fire("mcp-ghost", "m"))


def test_drain_collects_until_source_ends():
    seq = ['{"kind":"status","power":"on"}',
           {"kind": "value", "node": "m", "port": "trigger", "value": "1"}]
    it = iter(seq)

    async def recv():
        try:
            return next(it)
        except StopIteration:
            raise StopAsyncIteration

    async def go():
        loop = asyncio.get_event_loop()
        return await mcp_server._drain(recv, loop.time() + 2)

    events = run(go())
    assert len(events) == 2
    assert events[0]["kind"] == "status"
    assert events[1]["node"] == "m"


def test_drain_stops_at_deadline_without_hanging():
    async def recv():  # never yields; _drain must time out
        await asyncio.sleep(10)

    async def go():
        loop = asyncio.get_event_loop()
        return await mcp_server._drain(recv, loop.time() + 0.2)

    assert run(go()) == []


def test_clamp_seconds_bounds():
    assert mcp_server._clamp_seconds(99) == 30.0
    assert mcp_server._clamp_seconds(-5) == 0.0
    assert mcp_server._clamp_seconds(7) == 7.0


def test_ws_url_derivation(monkeypatch):
    monkeypatch.setenv("BOLTJAR_URL", "http://127.0.0.1:8770")
    assert mcp_server._ws_url("foo") == "ws://127.0.0.1:8770/ws?slug=foo"


def test_observe_reports_backend_down(monkeypatch):
    # a closed port: the socket connect fails and the tool surfaces the clear
    # "start uvicorn" instruction rather than a raw connection error.
    monkeypatch.setenv("BOLTJAR_URL", "http://127.0.0.1:1")
    with pytest.raises(RuntimeError) as exc:
        run(mcp_server.observe("x", 1))
    assert "uvicorn" in str(exc.value)
