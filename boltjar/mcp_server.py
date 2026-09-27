"""boltjar.mcp_server: an MCP server that gives an AI engineer first-class,
programmatic access to a running Boltjar backend.

It is a thin MCP (FastMCP, stdio transport) client over the Boltjar REST + WS
API. Point it at a backend with the BOLTJAR_URL env var (default
http://127.0.0.1:8770); the backend must be running (uvicorn on 8770) for any
tool to work. Nothing here starts a server.

The tools let the AI: read the node catalog (the schema source of truth, so it
never guesses ports/knobs), list models, do full graph CRUD + validation, power
a graph On/Off/Restart, fire triggers, inject chat, and OBSERVE the live event
stream to watch data actually flow.

Run (registered via .mcp.json):
    .venv/Scripts/python.exe -m boltjar.mcp_server
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
import websockets

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("boltjar")

# How the AI is told to fix the one failure mode it will hit most: the backend
# is not up. Every HTTP/WS helper surfaces this same instruction.
_BACKEND_DOWN = (
    "Cannot reach the Boltjar backend at {url}. Start it first (in a foreground "
    "terminal): .venv\\Scripts\\python.exe -m uvicorn boltjar.server:app --port "
    "8770 . Override the address with the BOLTJAR_URL env var if it runs "
    "elsewhere."
)


def _base_url() -> str:
    """The backend base URL (http). Read per call so the env can be set late
    (and so tests can point it at an in-process transport)."""
    return os.environ.get("BOLTJAR_URL", "http://127.0.0.1:8770").rstrip("/")


def _ws_url(slug: str) -> str:
    """The /ws endpoint for a slug, derived from the base URL (http->ws)."""
    base = _base_url()
    ws = "ws" + base[len("http"):] if base.startswith("http") else base
    return f"{ws}/ws?slug={slug}"


def _client() -> httpx.AsyncClient:
    """A fresh HTTP client bound to the backend. Factored out so a test can
    monkeypatch it to an httpx.ASGITransport against the FastAPI app in-process."""
    return httpx.AsyncClient(base_url=_base_url(), timeout=30.0)


async def _get(path: str) -> dict:
    try:
        async with _client() as c:
            r = await c.get(path)
    except httpx.ConnectError as exc:  # backend down
        raise RuntimeError(_BACKEND_DOWN.format(url=_base_url())) from exc
    return _unwrap(r)


async def _send(method: str, path: str, payload: dict | None = None) -> dict:
    try:
        async with _client() as c:
            r = await c.request(method, path, json=payload)
    except httpx.ConnectError as exc:  # backend down
        raise RuntimeError(_BACKEND_DOWN.format(url=_base_url())) from exc
    return _unwrap(r)


def _unwrap(r: httpx.Response) -> dict:
    """Return the JSON body, raising a clear error for a non-2xx (surfacing the
    backend's own {"error": ...} message when it sent one)."""
    try:
        body = r.json()
    except Exception:
        body = {"error": r.text[:500]}
    if r.status_code >= 400:
        msg = body.get("error") if isinstance(body, dict) else None
        raise RuntimeError(f"{r.status_code} {msg or r.text[:300]}")
    return body


# ---- catalog / schema -------------------------------------------------------

@mcp.tool()
async def get_version() -> dict:
    """The backend's Boltjar version, Python version and platform: {"version",
    "python", "platform"}. Quote it when reporting a bug."""
    return await _get("/api/version")


def _condense_catalog(object_info: dict) -> list[dict]:
    """Shrink /api/object_info to the shape an author needs: per node its id,
    title, category, summary, ports (name/type/direction) and knobs
    (name/kind/default/options). This is the schema source of truth so the AI
    wires real ports and sets real knobs instead of guessing."""
    out: list[dict] = []
    for n in object_info.get("nodes", []):
        ports = (
            [{"name": p["name"], "type": p["type"], "direction": "in"}
             for p in n.get("inputs", [])]
            + [{"name": p["name"], "type": p["type"], "direction": "out"}
               for p in n.get("outputs", [])]
        )
        knobs = [
            {"name": w["name"], "kind": w["kind"],
             "default": w.get("default"), "options": w.get("options") or []}
            for w in n.get("widgets", [])
        ]
        out.append({
            "id": n["id"], "title": n.get("name", n["id"]),
            "category": n.get("category", ""), "summary": n.get("summary", ""),
            "kind": n.get("kind", ""), "pulled": n.get("pulled", False),
            "ports": ports, "knobs": knobs,
        })
    return out


@mcp.tool()
async def list_node_types() -> list[dict]:
    """List every node type available to build graphs with, condensed to what an
    author needs: id, title, category, summary, ports (name/type/direction), and
    knobs (name/kind/default/options). This is the SCHEMA SOURCE OF TRUTH: read it
    before authoring a graph so you wire real port names and set real knob names,
    never guessed ones."""
    return _condense_catalog(await _get("/api/object_info"))


@mcp.tool()
async def list_models() -> dict:
    """List every declared model and its capabilities and params (the catalog the
    LLM node reshapes itself to). Use to pick a valid `model` id for an
    `core.ai.llm` node's config."""
    return await _get("/api/models")


# ---- graph CRUD -------------------------------------------------------------

@mcp.tool()
async def list_graphs() -> dict:
    """List the names (slugs) of every saved graph and shipped example on the backend."""
    return await _get("/api/graphs")


@mcp.tool()
async def get_graph(name: str) -> dict:
    """Fetch one graph's full JSON (nodes + edges + positions) by name. A saved
    copy wins over the shipped example of the same name."""
    return await _get(f"/api/graphs/{name}")


@mcp.tool()
async def save_graph(name: str, graph_json: dict) -> dict:
    """Save (PUT) a graph under `name`, then validate it. Returns
    {"saved": true, "problems": [...]}; a non-empty `problems` list means the
    saved graph would NOT power on as-is (fix those, save again)."""
    await _send("PUT", f"/api/graphs/{name}", graph_json)
    problems = (await _send("POST", "/api/validate", graph_json)).get("problems", [])
    return {"saved": True, "problems": problems}


@mcp.tool()
async def delete_graph(name: str) -> dict:
    """Delete a saved graph by name (a missing file is treated as success). A
    shipped example cannot be deleted; deleting a saved copy of one restores it."""
    return await _send("DELETE", f"/api/graphs/{name}")


@mcp.tool()
async def validate_graph(graph_json: dict) -> dict:
    """Validate a graph WITHOUT saving it. Returns {"problems": [...]}; empty means
    it is powerable (every required input wired, at least one trigger, no
    duplicate channels/tool names)."""
    return await _send("POST", "/api/validate", graph_json)


# ---- runtime control --------------------------------------------------------

@mcp.tool()
async def power(slug: str, action: str) -> dict:
    """Power a graph On, Off, or Restart (a graph is a live server, not a one-shot
    run). `action` is "on" | "off" | "restart". For on/restart the SAVED graph for
    `slug` is loaded and started. Returns {"power": "on"|"off", "problems": [...]};
    a non-empty `problems` means validation rejected it and it did NOT start."""
    return await _send("POST", f"/api/runtime/{slug}/power", {"action": action})


@mcp.tool()
async def runtime_state(slug: str) -> dict:
    """Read a live graph's state: {"power": "on"|"off", "latest": [events]}, where
    `latest` is the last value seen on each wire (node, port). A snapshot; use
    `observe` to watch events as they happen."""
    return await _get(f"/api/runtime/{slug}/state")


@mcp.tool()
async def fire(slug: str, node: str) -> dict:
    """Fire a Manual trigger node by id in a running graph (the same as clicking
    its Fire button). The graph must be powered on."""
    return await _send("POST", f"/api/runtime/{slug}/fire", {"node": node})


@mcp.tool()
async def send_chat(slug: str, node: str, text: str) -> dict:
    """Inject a message into a Chat Input node (by id) of a running graph, exactly
    as typing into the editor's send box would. The graph must be powered on. To
    see the reply flow back, use `observe` or `send_chat_and_observe`."""
    return await _send("POST", f"/api/runtime/{slug}/chat", {"node": node, "text": text})


# ---- live observation (the WebSocket event stream) --------------------------

async def _drain(recv, deadline: float) -> list[dict]:
    """Collect JSON events from an async `recv()` callable until `deadline`
    (loop-clock seconds). Factored out so the collection logic is unit-testable
    without a live socket."""
    loop = asyncio.get_event_loop()
    events: list[dict] = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        except (websockets.ConnectionClosed, StopAsyncIteration):
            break
        events.append(json.loads(raw) if isinstance(raw, (str, bytes)) else raw)
    return events


def _clamp_seconds(seconds: float) -> float:
    return min(max(float(seconds), 0.0), 30.0)


@mcp.tool()
async def observe(slug: str, seconds: float = 5.0) -> list[dict]:
    """Watch a running graph's live event stream for a window and return every
    event seen: this is how you SEE DATA FLOW. Connect, then collect for up to
    `seconds` (capped at 30). On connect the backend replays the current status
    plus the last value on every wire, then you receive each new
    value/log/node_error event as it fires. Power the graph on (and fire/chat)
    before or during the window to capture activity."""
    seconds = _clamp_seconds(seconds)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    try:
        async with websockets.connect(_ws_url(slug)) as ws:
            return await _drain(ws.recv, deadline)
    except (OSError, websockets.InvalidHandshake, websockets.WebSocketException) as exc:
        raise RuntimeError(_BACKEND_DOWN.format(url=_base_url())) from exc


@mcp.tool()
async def send_chat_and_observe(slug: str, node: str, text: str,
                                seconds: float = 5.0) -> list[dict]:
    """Inject a chat message and watch what flows back, in one call. Subscribes to
    the live stream FIRST (so nothing is missed), sends `text` into Chat Input
    `node`, then collects events for up to `seconds` (capped at 30) and returns
    them. The initial batch includes the connect replay (status + last wire
    values); the reply appears as later `value` events (e.g. on the chat output's
    `reply` port). The graph must be powered on."""
    seconds = _clamp_seconds(seconds)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    try:
        async with websockets.connect(_ws_url(slug)) as ws:
            # Subscribed. Now inject the chat over REST, then drain the window.
            await _send("POST", f"/api/runtime/{slug}/chat",
                        {"node": node, "text": text})
            return await _drain(ws.recv, deadline)
    except (OSError, websockets.InvalidHandshake, websockets.WebSocketException) as exc:
        raise RuntimeError(_BACKEND_DOWN.format(url=_base_url())) from exc


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
