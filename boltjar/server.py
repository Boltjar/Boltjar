"""
boltjar.server: the FastAPI bridge. Node defs, validation, the On/Off/
Restart power model, chat injection, and a live event stream.

WS protocol (the editor is the client):
    -> { action: "on",      graph }          start the graph live (gated on validity)
    -> { action: "off" }                      stop
    -> { action: "restart", graph }           stop + start
    -> { action: "chat", node, text }         inject a message into a Chat Input
    <- { kind: "status",  power: "on"|"off" }
    <- { kind: "invalid", problems: [...] }   On rejected (broken graph)
    <- { kind: "value",   node, port, value } live value on a wire
    <- { kind: "log",     node, message }
    <- { kind: "node_error", node, error }

Power persistence: the live runtime lives in a module-level Hub singleton, not in
any one websocket. A browser refresh / tab close only drops a SUBSCRIBER; the graph
keeps running. On reconnect the editor receives the current status plus a replay of
every cached live value so its wires light up again. Only the server process
exiting stops the graph, and on the way out it stops every graph cleanly.

Run:  python -m boltjar serve   (see boltjar/__main__.py)
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import hmac
import json
import logging
import pathlib
import platform
import re
import sqlite3

import os

import httpx

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import Body

from boltjar.sdk import registry_definitions, types, NODE_REGISTRY, Kind
from boltjar.runtime import Runtime, node_config
from boltjar.sqlite_store import SqliteStore
from boltjar.file_store import FileStore
from boltjar.kv_store import KvStore
from boltjar.vector_store import VectorStore
from boltjar.graph_format import GraphFormatError, format_of, migrate
from boltjar import __version__, models
from boltjar import packs as _packs
import boltjar.secrets as _secrets
import boltjar.security as _security

# the core pack, every pack under packs/ and the user's model manifests. A broken
# pack is skipped and reported by /api/packs; it never stops the server.
_packs.load_all()

_log = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Everything created at runtime lives under user/ (gitignored): saved graphs,
# their Auto-save snapshots, and the store data. Each folder is created on demand.
USER_DIR = ROOT / "user"
GRAPHS_DIR = USER_DIR / "graphs"
# Shipped example graphs are read-only sources: a slug with no saved copy under
# GRAPHS_DIR loads from here, and saving it writes a user copy that overrides
# the example from then on.
EXAMPLES_DIR = ROOT / "examples"
# Auto-save: every explicit Save drops a timestamped snapshot under
# user/autosave/<slug>/, pruned to AUTOSAVE_MAX newest, so a graph can always be
# rolled back (the safety net that a clobbered save needs). Module-level so a
# test can repoint it at a tmp dir.
AUTOSAVE_DIR = USER_DIR / "autosave"
AUTOSAVE_MAX = 50
DATA_DIR = USER_DIR / "data"
# Resource bounds for the avatar media-out stream + the audio POST (localhost
# robustness, so a looping client cannot grow memory or buffer an absurd blob).
STREAM_MAX_CHANNELS = 256
STREAM_MAX_SUBS_PER_CHANNEL = 64
AUDIO_POST_MAX_BYTES = 25 * 1024 * 1024  # 25 MB; a mic clip is well under this
HOOK_POST_MAX_BYTES = 10 * 1024 * 1024  # 10 MB; a webhook payload is well under this
EDITOR_DIST = ROOT / "editor" / "dist"

# One shared embedded-db store the Database/Query/Exec nodes resolve handles
# against. Files live under user/data/dbs/ (gitignored runtime data).
STORE = SqliteStore(DATA_DIR / "dbs")

# One shared sandboxed file store the core.file.* nodes read/write through.
# Every node path is resolved RELATIVE to user/data/files/ and may never escape
# it (user/ is gitignored runtime data).
FILE_STORE = FileStore(DATA_DIR / "files")

# One shared persistent key-value store the core.kv node resolves handles
# against. One JSON file per store key under user/data/kv/ (gitignored runtime
# data), the genuine persistent replacement for the in-RAM State/Memory stores.
KV_STORE = KvStore(DATA_DIR / "kv")

# One shared embedded vector store the Vector Store / Vectors nodes resolve
# handles against. One SQLite file per store key under user/data/vectors/
# (gitignored), namespaced, brute-force cosine over a numpy matrix (semantic
# memory pack).
VECTOR_STORE = VectorStore(DATA_DIR / "vectors")


class Hub:
    """A backend-resident live runtime for ONE graph slug, shared across every
    websocket subscribed to that slug.

    The runtime is created with `observer=self.broadcast`, so every live event
    fans out to all subscriber queues and (for `value` events) is cached in
    `latest` so a reconnecting editor can be replayed the current wire state.
    Power ops operate this one runtime; a websocket disconnect never stops it.

    The owning registry (HUBS) keys Hubs by slug; each Hub stamps its slug on
    every outgoing event so a multiplexed client can route by graph.
    """

    def __init__(self) -> None:
        self.runtime: Runtime | None = None
        # the graph the live runtime was built from: the source of truth for which
        # nodes/edges are actually LIVE. Replayed to a reconnecting editor as a
        # `live_graph` event so a draft-only node/wire (a structural edit made while
        # power is on, not yet Saved & Restarted) is never mistaken for live.
        self.graph: dict | None = None
        self.subscribers: set[asyncio.Queue] = set()
        # media-out stream subscribers, keyed by channel (the avatar SSE). Distinct
        # from `subscribers` (the editor value/log/status websocket fan-out).
        self.stream_subscribers: dict[str, set[asyncio.Queue]] = {}
        # most recent `value` event per (node, port), for replay on reconnect.
        self.latest: dict[tuple[str, str], dict] = {}
        # serialises power ops so two concurrent "on" actions can't orphan a runtime.
        self._lock = asyncio.Lock()
        # set by get_hub() when this Hub is registered; broadcast() stamps it.
        self.slug: str = "_default"

    def broadcast(self, event: dict) -> None:
        event["slug"] = self.slug
        if event.get("kind") == "value":
            self.latest[(event["node"], event["port"])] = event
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                # a full or closed subscriber queue must never stall the runtime.
                pass

    def close_connections(self) -> None:
        """End every live connection on this Hub: each editor websocket and each
        media stream gets a `None` sentinel, and its pump closes it. A stream
        never ends by itself, and the server waits for open connections before
        it can exit, so shutdown calls this after stopping the graph."""
        queues = list(self.subscribers)
        for channel in list(self.stream_subscribers.values()):
            queues.extend(channel)
        for q in queues:
            try:
                if q.full():
                    q.get_nowait()  # a stalled client: drop its oldest event for the sentinel
                q.put_nowait(None)
            except Exception:
                # one dead subscriber must not keep the others open.
                _log.debug("could not close a subscriber of %s", self.slug, exc_info=True)

    async def _stop(self) -> None:
        if self.runtime:
            await self.runtime.stop()
            self.runtime = None
        self.graph = None
        self.latest.clear()

    def live_graph_event(self) -> dict:
        """A compact descriptor of the graph currently running: node ids + edge
        tuples. Empty when nothing is live. The editor classifies each node/wire
        against this set so live values only paint/pulse through the LIVE graph,
        never a draft-only structural edit made since the last Save & Restart."""
        g = self.graph if self.runtime is not None else None
        nodes = [n["id"] for n in g.get("nodes", [])] if g else []
        edges = (
            [[e["src"], e["src_port"], e["dst"], e["dst_port"]] for e in g.get("edges", [])]
            if g
            else []
        )
        return {"kind": "live_graph", "nodes": nodes, "edges": edges}

    def publish_stream(self, channel: str, chunk: dict) -> None:
        """Fan one media chunk to every subscriber of `channel` (the avatar SSE).
        Drop-on-full like broadcast(): a slow client never stalls the runtime."""
        for q in list(self.stream_subscribers.get(channel, ())):
            try:
                q.put_nowait(chunk)
            except Exception:
                pass

    async def power_off(self) -> None:
        async with self._lock:
            await self._stop()
        self.broadcast({"kind": "status", "power": "off"})
        self.broadcast(self.live_graph_event())  # empty set: nothing is live now

    async def power_on(self, graph: dict) -> list[dict] | None:
        """Start the graph live. Returns validation problems if the graph is broken."""
        try:
            graph = migrate(graph)
        except GraphFormatError as exc:
            return [_format_problem(exc)]
        problems = validate_graph(graph)
        if problems:
            return problems
        async with self._lock:
            await self._stop()
            runtime = Runtime(observer=self.broadcast, stream_observer=self.publish_stream)
            try:
                runtime.build(graph)
                # reachable before run() so an "off" arriving mid-start can still stop it.
                self.runtime = runtime
                await runtime.run()
            except Exception as exc:
                self.runtime = None
                self.graph = None
                self.broadcast({"kind": "error", "error": repr(exc)})
                return None
            self.graph = graph  # the now-running graph (the live set source of truth)
        self.broadcast({"kind": "status", "power": "on"})
        self.broadcast(self.live_graph_event())
        return None

    async def restart(self, graph: dict) -> list[dict] | None:
        # power_on stops any current runtime under the lock before starting.
        return await self.power_on(graph)

    def send_chat(self, node: str, text: str) -> None:
        if self.runtime is not None:
            self.runtime.send_chat(node, text)

    def send_audio(self, node: str, audio: str, lang: str = "") -> None:
        if self.runtime is not None:
            self.runtime.send_audio(node, audio, lang)

    def fire_manual(self, node: str) -> None:
        if self.runtime is not None:
            self.runtime.fire_manual(node)


# Registry of live Hubs, keyed by graph slug. A graph turns On in its OWN Hub,
# so multiple graphs can run live concurrently (one editor tab per slug).
HUBS: dict[str, Hub] = {}


def get_hub(slug: str) -> Hub:
    """Return the Hub for this slug, lazily creating one on first use.

    Each Hub stashes its slug so its broadcast() can stamp outgoing events.
    """
    hub = HUBS.get(slug)
    if hub is None:
        hub = Hub()
        hub.slug = slug
        HUBS[slug] = hub
    return hub


async def shutdown_all() -> int:
    """Stop every running graph (services and stores close cleanly) and end
    every live connection, the editors' /ws and the /stream media clients, so
    the server exits on one Ctrl+C. Safe to call twice. Returns how many graphs
    were running."""
    stopped = 0
    for hub in list(HUBS.values()):
        if hub.runtime is not None:
            try:
                await hub.power_off()
                stopped += 1
            except Exception:
                _log.exception("could not stop graph %s cleanly", hub.slug)
        hub.close_connections()
    return stopped


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    # the user's secrets load when the server starts, not when the registry is
    # imported: nodes read provider keys from os.environ once a graph runs.
    _secrets.ensure_loaded()
    yield
    # `python -m boltjar serve` calls shutdown_all() earlier, before the server
    # waits on open connections; this covers a server started any other way.
    await shutdown_all()


app = FastAPI(title="Boltjar", version=__version__, lifespan=_lifespan)

# Create the per-install token now, so a local client (the MCP server) can read
# it from user/data/token before any browser has opened the editor.
_security.get_token()


# --------------------------------------------------------------- edge validation
# Every edge is checked structurally (does the port exist?) and by type
# (`types.compatible`). Nodes carry DYNAMIC ports the backend cannot always know
# statically (growable sockets minted per wire, params promoted to inputs, LLM
# ports reshaped by the selected model's manifest). We reproduce the surfaces we
# CAN know cheaply and stay PERMISSIVE for the ones we cannot, so a legal dynamic
# wire is never rejected. Every permissive case is documented at its branch.

_TAG_RE = re.compile(r"\{([a-zA-Z_]\w*)\}")


def _template_tags(template: str) -> set[str]:
    """The `{tag}` names in a Template string: each is a real dynamic input
    socket (mirrors the editor's parseTemplateTags)."""
    return set(_TAG_RE.findall(template or ""))


def _keys_of(raw: str) -> list[str]:
    """Split a comma/space/newline key list, deduped (mirrors builtin `_split_keys`
    and the editor's parseSplitKeys): Split JSON keys, Tool Args fields."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in str(raw or "").replace(",", " ").split():
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _widget_port_type(kind: str) -> str:
    """A promoted widget's input-port type (mirrors dynamicPorts.widgetPortType)."""
    if kind == "number":
        return "float"
    if kind == "bool":
        return "bool"
    return "text"


def _param_port_type(t: str) -> str:
    """A promoted model-param's input-port type (mirrors dynamicPorts.paramPortType)."""
    return {"float": "float", "int": "int", "bool": "bool"}.get(t, "text")


# LLM output ports the model manifest can reshape onto the node (the editor's
# concreteOutputs): the reply rides `response`, plus audio/image/tool_call/
# reasoning per capability, plus the completion `trigger`. Accepted as a set
# regardless of the concrete model so a text-only model that once had an `audio`
# wire is not falsely flagged; a genuinely stale rename (e.g. an old `text` port)
# still falls outside the set and is caught.
_LLM_OUTPUT_TYPES = {
    "response": "text", "reasoning": "text", "trigger": "event",
    "audio": "audio", "image": "image", "tool_call": "tool-call",
}


def _output_port_check(spec, config: dict, port: str):
    """Return (ok, type) for an output `port` on a node of `spec`. `type` may be
    None meaning 'permissive: accept, but don't type-check this end'."""
    if spec.id == "core.flow.wireless_out":
        # PERMISSIVE: a Wireless Out mirrors the sockets of the Wireless In on its
        # channel, a graph-wide lookup we don't do here. Any src_port is accepted.
        return True, None
    if spec.id == "core.data.split":
        # Split JSON reshapes its outputs from the `keys` config (one per key).
        return port in _keys_of(config.get("keys", "")), "any"
    if spec.id == "core.ai.tool_args":
        # Tool Args emits `trigger` + one output per declared argument (`fields`).
        if port == "trigger":
            return True, "event"
        return port in _keys_of(config.get("fields", "")), "any"
    if spec.id == "core.ai.llm":
        declared = {p.name: p.type for p in spec.outputs}
        if port in declared:
            return True, declared[port]
        if port in _LLM_OUTPUT_TYPES:
            return True, _LLM_OUTPUT_TYPES[port]
        return False, None
    for p in spec.outputs:
        if p.name == port:
            # HTTP reshapes its `body` output TYPE from response_type; the name is
            # stable, so accept the name and leave the type permissive.
            if spec.id == "core.net.http" and port == "body":
                return True, None
            return True, p.type
    # op-shaped outputs (KV/DB) are declared ports; the loop above accepts them for
    # ANY current operation (permissive on op) so an edge left from another op is
    # not flagged: the port genuinely exists on the node type.
    return False, None


def _input_port_check(spec, config: dict, port: str):
    """Return (ok, type) for an input `port` on a node of `spec`. `type` may be
    None meaning 'permissive: accept, but don't type-check this end'."""
    for p in spec.inputs:
        if p.name == port and not p.growable:
            return True, p.type
    # a widget promoted to a typed input port (config.promoted names a real widget).
    if port in (config.get("promoted") or []):
        w = next((w for w in spec.widgets if w.name == port), None)
        if w is not None:
            return True, _widget_port_type(w.kind)
    if spec.id == "core.data.template":
        # Template's dynamic inputs are its `{tag}` sockets. A wire into a tag the
        # text no longer uses is harmless (its value is simply not substituted),
        # and the editor keeps drawing that socket, so it must never block power-on:
        # accept any non-declared socket, exactly like every other growable base.
        # (Its declared `trigger` is matched by the declared-port loop above.)
        return True, "any"
    if spec.id == "core.ai.llm":
        return _llm_input_port_check(spec, config, port)
    grow = next((p for p in spec.inputs if p.growable), None)
    if grow is not None:
        # PERMISSIVE: a growable base (List `item`, Compute `value`, Sync `in`,
        # Build `field`, HTTP/KV/DB `tag`, Wireless In `in`) mints one socket per
        # wire, named after the source or author, not statically enumerable. Any
        # non-declared dst_port belongs to that base; accept as the base's type.
        return True, grow.type
    # no dynamic input surface: only declared + promoted ports are real. A dst_port
    # outside them (e.g. a stale wire after a port rename) is flagged.
    return False, None


def _llm_input_port_check(spec, config: dict, port: str):
    """The LLM node reshapes its inputs from the selected model's manifest: base
    trigger/prompt, one port per non-text input modality, a growable `tools` base,
    and one port per promoted param. Mirrors dynamicPorts.llmInputType."""
    for p in spec.inputs:
        if p.name == port and not p.growable:
            return True, p.type  # trigger, prompt
    manifest = models.get(config.get("model") or "")
    if manifest is not None:
        if port != "text" and port in manifest.inputs:
            return True, port  # a modality port (image/audio/video)
        param = next((pp for pp in manifest.params if pp.name == port), None)
        if param is not None and port in (config.get("promoted") or []):
            return True, _param_port_type(param.type)
    # PERMISSIVE: anything else is a growable `tools` socket (named per wired Tool,
    # not statically enumerable). Accept it; leave the type permissive so a valid
    # tool wire is never rejected on an unresolved / unloaded manifest.
    return True, None


def _edge_problems(graph: dict) -> list[dict]:
    """Structural + type validation for every edge. A node whose TYPE is unknown or
    that is disabled is skipped here (its node-level problem, or its bypass, already
    governs it), matching the disabled-nodes-never-block-power-on rule."""
    problems: list[dict] = []
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    for e in graph.get("edges", []):
        src, sp = e.get("src"), e.get("src_port")
        dst, dp = e.get("dst"), e.get("dst_port")
        wire = f"{src}.{sp} -> {dst}.{dp}"
        s_node, d_node = nodes.get(src), nodes.get(dst)
        # an edge to/from a missing node (deleted node, dangling edge).
        if s_node is None:
            problems.append({"node": dst, "kind": "dangling-edge",
                             "message": f"edge {wire}: source node '{src}' does not exist"})
            continue
        if d_node is None:
            problems.append({"node": src, "kind": "dangling-edge",
                             "message": f"edge {wire}: target node '{dst}' does not exist"})
            continue
        # a disabled endpoint may be bypassed/flattened at runtime; skip its edges
        # so a disabled node never blocks power-on (as elsewhere in validation).
        if s_node.get("disabled") or d_node.get("disabled"):
            continue
        s_spec = NODE_REGISTRY.get(s_node["type"])
        d_spec = NODE_REGISTRY.get(d_node["type"])
        # an unknown node type is already flagged at the node level; don't pile on.
        if s_spec is None or d_spec is None:
            continue
        # the config each node RUNS with (declared defaults under the saved
        # config), so a fresh Template's `{in}` is a real socket here too.
        s_cfg = node_config(s_spec, s_node.get("config"))
        d_cfg = node_config(d_spec, d_node.get("config"))
        out_ok, out_type = _output_port_check(s_spec, s_cfg, sp)
        in_ok, in_type = _input_port_check(d_spec, d_cfg, dp)
        if not out_ok:
            problems.append({"node": src, "kind": "bad-source-port",
                             "message": f"edge {wire}: '{sp}' is not an output of "
                                        f"{s_spec.name} ({s_node['type']})"})
        if not in_ok:
            problems.append({"node": dst, "kind": "bad-target-port",
                             "message": f"edge {wire}: '{dp}' is not an input of "
                                        f"{d_spec.name} ({d_node['type']})"})
        # type-check only when BOTH ends resolved to a concrete (non-permissive)
        # type; `any` on either side is the universal wildcard, as in the editor.
        if out_ok and in_ok and out_type and in_type:
            if not types.compatible(out_type, in_type):
                problems.append({"node": dst, "kind": "type-mismatch",
                                 "message": f"edge {wire}: {out_type} output cannot "
                                            f"feed {in_type} input"})
    return problems


def _format_problem(exc: GraphFormatError) -> dict:
    """A graph whose format this Boltjar cannot read, as a graph-level problem."""
    return {"node": None, "kind": "format", "message": str(exc)}


def _widget_in_use(node_id: str, cfg: dict, widget, edges_in: set) -> bool:
    """Whether a widget's own value reaches the node: not hidden under another
    operation (op_field), and not promoted to an input that a wire now feeds."""
    if widget.op_field and cfg.get(widget.op_field) not in widget.op_values:
        return False
    return not (widget.name in (cfg.get("promoted") or []) and (node_id, widget.name) in edges_in)


def validate_graph(graph: dict) -> list[dict]:
    """Pre-run validation: a graph cannot turn On if any node is broken."""
    problems: list[dict] = []
    edges_in = {(e["dst"], e["dst_port"]) for e in graph.get("edges", [])}
    has_trigger = False
    for n in graph.get("nodes", []):
        # a bypassed node never runs, so it cannot break the graph: skip every
        # check for it (missing inputs, trigger counting, unknown type).
        if n.get("disabled"):
            continue
        spec = NODE_REGISTRY.get(n["type"])
        if spec is None:
            problems.append({"node": n["id"], "kind": "unknown",
                             "message": f"unknown node type {n['type']}"})
            continue
        if spec.kind == Kind.TRIGGER:
            has_trigger = True
        for p in spec.inputs:
            if p.optional or p.growable:
                continue
            if (n["id"], p.name) not in edges_in and p.default is None:
                problems.append({"node": n["id"], "kind": "missing-input",
                                 "message": f"required input '{p.name}' is not connected"})
    if graph.get("nodes") and not has_trigger:
        problems.append({"node": None, "kind": "no-trigger",
                         "message": "no trigger node: nothing can fire"})
    # a wireless channel may have only ONE Wireless In (the broadcast source); two
    # Ins on the same channel is ambiguous. (Wireless Out may repeat freely.)
    seen_channels: dict[str, str] = {}
    for n in graph.get("nodes", []):
        if n.get("disabled") or n["type"] != "core.flow.wireless_in":
            continue
        ch = str((n.get("config") or {}).get("channel", "1"))
        if ch in seen_channels:
            problems.append({"node": n["id"], "kind": "duplicate-channel",
                             "message": f"channel {ch} already has a Wireless In ({seen_channels[ch]})"})
        else:
            seen_channels[ch] = n["id"]
    # two Tool nodes that share a `name` collide: the model addresses a tool BY
    # name, so a duplicate silently shadows. A tool with no name can't be called.
    tool_names: dict[str, str] = {}
    for n in graph.get("nodes", []):
        if n.get("disabled") or n["type"] != "core.ai.tool":
            continue
        # the name the runtime offers the model: a fresh Tool is its declared default.
        nm = str(node_config(NODE_REGISTRY[n["type"]], n.get("config"))["name"]).strip()
        if not nm:
            problems.append({"node": n["id"], "kind": "tool-no-name",
                             "message": "tool has no name; the model cannot call it"})
        elif nm in tool_names:
            problems.append({"node": n["id"], "kind": "duplicate-tool-name",
                             "message": f"tool name '{nm}' already used by {tool_names[nm]}"})
        else:
            tool_names[nm] = n["id"]
    # a {{secret.NAME}} nobody defined stays literal text where it is used (sent
    # as a key, or refused as a Webhook's secret), so name it before On. Only
    # stored secrets and provider keys resolve, never another .env name.
    for n in graph.get("nodes", []):
        spec = NODE_REGISTRY.get(n["type"])
        if n.get("disabled") or spec is None:
            continue
        cfg = node_config(spec, n.get("config"))
        text = "\n".join(str(cfg.get(w.name) or "") for w in spec.widgets
                         if w.accepts_secrets and _widget_in_use(n["id"], cfg, w, edges_in))
        for name in _secrets.unresolved(text):
            problems.append({"node": n["id"], "kind": "missing-secret",
                             "message": f"secret {name} is not defined: add it in Connections"})
    # every edge: src/dst ports exist (statically or as a legal dynamic port) and
    # the wire's types are compatible. Runs after the node checks so an unknown /
    # disabled node is already handled and its edges are skipped.
    problems.extend(_edge_problems(graph))
    return problems


@app.get("/api/version")
def api_version() -> dict:
    """What is running: the Boltjar version (boltjar.__version__, the one source
    of it), the Python version and the platform, as a bug report needs them."""
    return {"version": __version__, "python": platform.python_version(),
            "platform": platform.platform()}


@app.get("/api/session")
def session() -> dict:
    """The editor calls this once at boot. To a browser on this machine,
    LocalGuard answers it with the session cookie that every later /api, /ws,
    /stream and /audio call carries (GET / sets it too, but under the Vite dev
    server the page comes from Vite). Any other browser got the cookie from the
    /?token=<token> link, and a request without it is refused."""
    return {"ok": True}


@app.get("/api/object_info")
def object_info() -> dict:
    return {"nodes": registry_definitions(), "types": types.catalog()}


@app.get("/api/models")
def list_models() -> dict:
    """The model registry: every declared model and its capabilities/params."""
    return {"models": models.catalog()}


@app.get("/api/packs")
def list_packs() -> dict:
    """The node packs: {loaded: [{id, name, version, nodes}], failed: [{id,
    folder, error}]}. The core pack is always first in `loaded`; a pack folder
    that could not load is in `failed` with the reason."""
    return _packs.report()


def _graph_path(name: str) -> pathlib.Path | None:
    """The file a slug loads from: the saved user copy when there is one, else the
    shipped example of that name. None when neither exists."""
    filename = f"{_safe(name)}.json"
    for folder in (GRAPHS_DIR, EXAMPLES_DIR):
        path = folder / filename
        if path.exists():
            return path
    return None


@app.get("/api/graphs")
def list_graphs() -> dict:
    """Every loadable slug: the saved user graphs plus the shipped examples (a
    user copy and an example with the same slug are one entry)."""
    slugs = {p.stem for folder in (GRAPHS_DIR, EXAMPLES_DIR) for p in folder.glob("*.json")}
    return {"graphs": sorted(slugs)}


def _load_graph_file(path: pathlib.Path):
    """A saved graph file in the current format (migrated on the way out; the
    file itself is rewritten only by the next save), or a 422 when this Boltjar
    cannot read its format."""
    try:
        return migrate(json.loads(path.read_text(encoding="utf-8")))
    except GraphFormatError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@app.get("/api/graphs/{name}")
def get_graph(name: str):
    path = _graph_path(name)
    if path is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _load_graph_file(path)


def _snapshot_graph(name: str, graph: dict) -> None:
    """Drop a timestamped backup under user/autosave/<slug>/ and prune to the newest
    AUTOSAVE_MAX. Best-effort: a snapshot failure never blocks the save itself."""
    try:
        slug = _safe(name)
        folder = AUTOSAVE_DIR / slug
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = folder / f"{slug}_{ts}.json"
        bump = 1
        while path.exists():  # more than one save in the same second
            path = folder / f"{slug}_{ts}_{bump}.json"
            bump += 1
        path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
        snaps = sorted(folder.glob(f"{slug}_*.json"), key=lambda p: p.stat().st_mtime)
        for old in snaps[:-AUTOSAVE_MAX]:
            old.unlink(missing_ok=True)
    except Exception:
        pass  # backups are best-effort; never break a save


def _unreadable_saved_graph(name: str) -> GraphFormatError | None:
    """Why this Boltjar cannot read the user's saved graph `name` (one saved by a
    newer Boltjar, or with a broken `format`), or None when it can. No file, or a
    file that is not a graph at all, is None: there is nothing a Boltjar reads."""
    try:
        saved = json.loads((GRAPHS_DIR / f"{_safe(name)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    try:
        format_of(saved)
    except GraphFormatError as exc:
        return exc
    return None


@app.put("/api/graphs/{name}")
async def put_graph(name: str, graph: dict):
    # every save is stamped with the current format (a graph from an older
    # editor is migrated first; one from a newer Boltjar is refused, not truncated).
    try:
        graph = migrate(graph)
    except GraphFormatError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    # nor is a saved graph this Boltjar cannot read ever saved over: nothing here
    # could open it, so whatever is sent in its place is not an edit of it.
    unreadable = _unreadable_saved_graph(name)
    if unreadable is not None:
        return JSONResponse(
            {"error": f"the saved graph is kept, this Boltjar cannot read it: {unreadable}"},
            status_code=409,
        )
    # always a user copy: an example is never overwritten, only overridden.
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    (GRAPHS_DIR / f"{_safe(name)}.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")
    _snapshot_graph(name, graph)  # versioned backup on every explicit Save
    return {"ok": True}


@app.get("/api/graphs/{name}/versions")
def list_versions(name: str) -> dict:
    """Timestamped Auto-save snapshots for a graph, newest first: the id (to
    fetch), a readable timestamp, and the byte size."""
    folder = AUTOSAVE_DIR / _safe(name)
    if not folder.exists():
        return {"versions": []}
    out = []
    for p in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({"id": p.stem,
                    "savedAt": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    "bytes": st.st_size})
    return {"versions": out}


@app.get("/api/graphs/{name}/versions/{version}")
def get_version(name: str, version: str):
    """The graph JSON of one snapshot, so the editor can preview/restore it
    (restoring is just loading it and Saving, which snapshots again)."""
    path = AUTOSAVE_DIR / _safe(name) / f"{_safe(version)}.json"
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return _load_graph_file(path)


@app.delete("/api/graphs/{name}")
async def delete_graph(name: str):
    """Permanently remove a saved graph file. The editor's rename + delete tab
    actions call this; missing file is a no-op (already gone is success). Only
    user copies are deleted: removing a user copy of an example brings the
    example back, and a slug that exists only as an example is a 409."""
    path = GRAPHS_DIR / f"{_safe(name)}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        if (EXAMPLES_DIR / path.name).exists():
            return JSONResponse(
                {"error": f"{path.stem!r} is a built-in example and cannot be deleted"},
                status_code=409,
            )
    return {"ok": True}


def _repo_path(path: str) -> str:
    """A store file's path as the editor shows it: relative to the repo root
    (user/data/dbs/x.db), never absolute, which would put the account name on
    every screen and screenshot. A file outside the repo shows its name."""
    p = pathlib.Path(path)
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.name


@app.get("/api/store/db/{key}/info")
async def db_info(key: str) -> dict:
    """Snapshot of a SQLite store: disk path + tables (name, rowCount) sorted
    by row count desc. The Database node body polls this so the user sees
    whether the store is empty or what tables live in it."""
    info = STORE.info(key)
    return {**info, "path": _repo_path(info["path"])}


@app.get("/api/store/kv/{key}/info")
async def kv_info(key: str) -> dict:
    """Snapshot of a KV store: disk path + total key count + first few keys
    as a preview. The KV Store node body polls this for the same reason."""
    info = KV_STORE.info(key)
    return {**info, "path": _repo_path(info["path"])}


@app.post("/api/store/db/{key}/restore")
async def db_restore(key: str, body: dict = Body(...)) -> dict:
    """Recreate empty tables from a schema snapshot (undo of a store-node delete:
    columns come back, rows do not)."""
    return _db(lambda: STORE.restore_schema(_need(key, "db key"), body.get("tables") or []))


@app.delete("/api/store/db/{key}")
async def db_destroy(key: str) -> dict:
    """Erase a SQLite store's FILE from disk. Called when a Database node is
    deleted, so sensitive data is not left behind (privacy). No undo."""
    STORE.destroy(key)
    return {"ok": True}


@app.delete("/api/store/kv/{key}")
async def kv_destroy(key: str) -> dict:
    """Erase a KV store's FILE from disk. Called when a KV Store node is deleted,
    so sensitive data is not left behind (privacy). No undo."""
    KV_STORE.destroy(key)
    return {"ok": True}


@app.post("/api/validate")
async def validate(graph: dict) -> dict:
    try:
        graph = migrate(graph)
    except GraphFormatError as exc:
        return {"problems": [_format_problem(exc)]}
    return {"problems": validate_graph(graph)}


# ---- Runtime control (REST) -------------------------------------------------
# Thin HTTP wrappers over the SAME Hub methods the /ws handler drives, so an
# agent (or any HTTP client) can power a graph, fire triggers, inject chat, and
# read the live wire state without a websocket. Behaviour is identical to the WS
# path: the same power_on/power_off/restart/send_chat/fire_manual on the per-slug
# Hub, the same validation gate on power-on.

def _saved_graph(slug: str) -> dict | None:
    """Load the graph for `slug` (the saved user copy, else the shipped example),
    or None if there is neither. Used by the power endpoint when the caller omits
    an inline graph."""
    path = _graph_path(slug)
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_events(hub: Hub) -> list[dict]:
    """The hub's cached last event per (node, port) as a plain list, mirroring
    the replay a reconnecting websocket receives."""
    return list(hub.latest.values())


@app.post("/api/runtime/{slug}/power")
async def runtime_power(slug: str, body: dict = Body(...)):
    """Turn a graph On / Off / Restart, the REST twin of the /ws power actions.

    Body: {"action": "on"|"off"|"restart", "graph": <graph JSON, optional>}.
    For "on"/"restart" the graph may be given inline; when absent the graph for
    `slug` is loaded (user/graphs/<slug>.json, else examples/<slug>.json), and a
    slug with neither is a 404.
    Returns {"power": "on"|"off", "problems": [...]}; a non-empty `problems` means
    the graph was rejected by validation and did NOT power on.
    """
    action = (body.get("action") or "").strip().lower()
    hub = get_hub(slug)
    if action == "off":
        await hub.power_off()
        return {"power": "off", "problems": []}
    if action in ("on", "restart"):
        graph = body.get("graph")
        if graph is None:
            graph = _saved_graph(slug)
            if graph is None:
                return JSONResponse(
                    {"error": f"no saved graph for slug {slug!r}; pass a graph in the body"},
                    status_code=404,
                )
        problems = await (hub.restart(graph) if action == "restart" else hub.power_on(graph))
        power = "on" if hub.runtime is not None else "off"
        return {"power": power, "problems": problems or []}
    return JSONResponse(
        {"error": f"unknown action {action!r}; use on, off or restart"},
        status_code=400,
    )


@app.get("/api/runtime/{slug}/state")
async def runtime_state(slug: str) -> dict:
    """The live state of a slug: {"power": "on"|"off", "latest": [events]}.
    `latest` is the cached last value event per wire (node, port), the same set a
    reconnecting editor is replayed. An unknown or never-powered slug is simply
    "off" with an empty `latest` (asking the state of an idle graph is valid)."""
    hub = HUBS.get(slug)
    if hub is None:
        return {"power": "off", "latest": []}
    return {"power": "on" if hub.runtime is not None else "off",
            "latest": _latest_events(hub)}


@app.post("/api/runtime/{slug}/fire")
async def runtime_fire(slug: str, body: dict = Body(...)):
    """Fire a Manual trigger node by id (REST twin of the /ws `fire` action).
    Body: {"node": <id>}. 404 when the graph is not running."""
    hub = HUBS.get(slug)
    if hub is None or hub.runtime is None:
        return JSONResponse({"error": "graph not running"}, status_code=404)
    node = body.get("node")
    if not node:
        return JSONResponse({"error": "node id is required"}, status_code=400)
    hub.fire_manual(node)
    return {"ok": True}


@app.post("/api/runtime/{slug}/chat")
async def runtime_chat(slug: str, body: dict = Body(...)):
    """Inject a message into a Chat Input node (REST twin of the /ws `chat`
    action). Body: {"node": <id>, "text": ...}. 404 when the graph is not
    running."""
    hub = HUBS.get(slug)
    if hub is None or hub.runtime is None:
        return JSONResponse({"error": "graph not running"}, status_code=404)
    node = body.get("node")
    if not node:
        return JSONResponse({"error": "node id is required"}, status_code=400)
    hub.send_chat(node, body.get("text", ""))
    return {"ok": True}


# ---- Connections (provider API keys + connectivity status) ------------------
# Values are NEVER returned from any route.

@app.get("/api/connections")
def api_connections() -> dict:
    """Return the connectivity status of every known provider."""
    return {"providers": _secrets.provider_status()}


@app.post("/api/connections/providers/{provider}/key")
async def api_set_provider_key(provider: str, body: dict = Body(...)) -> dict:
    """Store a provider API key (cloud providers only; not Ollama)."""
    env_var = _secrets.PROVIDERS.get(provider)
    if env_var is None:
        if provider not in _secrets.PROVIDERS:
            return JSONResponse({"error": f"Unknown provider {provider!r}"}, status_code=400)
        # Provider is in PROVIDERS but has no env var (e.g. "ollama").
        return JSONResponse(
            {"error": f"Provider {provider!r} does not use an API key"},
            status_code=400,
        )
    value = body.get("value", "")
    try:
        _secrets.set_secret(env_var, value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True}


@app.delete("/api/connections/providers/{provider}/key")
async def api_delete_provider_key(provider: str) -> dict:
    """Remove a user-set provider API key."""
    env_var = _secrets.PROVIDERS.get(provider)
    if env_var is None:
        return JSONResponse({"error": f"Provider {provider!r} has no API key"}, status_code=404)
    deleted = _secrets.delete_secret(env_var)
    if not deleted:
        # The key exists in os.environ but not in user secrets, so it came from .env.
        if _secrets.get_secret(env_var) is not None:
            return JSONResponse(
                {"error": "managed in server .env"}, status_code=409
            )
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True}


# ---- Inbound webhooks -------------------------------------------------------
# An external HTTP call is routed to the matching `core.trigger.webhook` node
# inside the live runtime for {slug}. The route is intentionally tiny: look up
# the hub, find the matching webhook instance, emit the body/json/headers/query
# onto its data ports, then fire `trigger` LAST so a downstream graph sees data
# already latched before the fan-out. Returns 200 immediately (fire-and-forget).

# Headers we never echo back into the graph: a webhook payload must not carry
# session cookies, auth tokens or the webhook's own shared secret to a downstream
# LLM / Log / HTTP node (the headers port is also cached and served by /state).
_WEBHOOK_HEADER_DENYLIST = frozenset({
    "cookie", "authorization", "proxy-authorization", "x-api-key", "x-webhook-secret",
})


def _find_webhook(runtime, path: str, method: str):
    """Walk a runtime's nodes and return the first WebhookTrigger instance whose
    live config matches the request. Match keys:
      - `spec.id == "core.trigger.webhook"`
      - `obj._node_cfg["path"]` (or the knob default) == request path
      - `obj._node_cfg["method"]` is "ANY" or == request method (uppercase).
    Returns the NodeInstance or None.
    """
    for inst in runtime.nodes.values():
        if inst.spec.id != "core.trigger.webhook":
            continue
        cfg = getattr(inst.obj, "_node_cfg", {}) or {}
        node_path = (cfg.get("path") if cfg.get("path") is not None
                     else getattr(inst.obj, "path", ""))
        node_method = (cfg.get("method") if cfg.get("method") is not None
                       else getattr(inst.obj, "method", "POST"))
        if str(node_path or "") != path:
            continue
        nm = str(node_method or "POST").upper()
        if nm != "ANY" and nm != method.upper():
            continue
        return inst
    return None


@app.api_route("/hook/{slug}/{path:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def webhook_handler(slug: str, path: str, request: Request):
    hub = HUBS.get(slug)
    if hub is None or hub.runtime is None:
        return JSONResponse({"error": "workflow not running"}, status_code=404)

    runtime = hub.runtime
    method = request.method.upper()
    node = _find_webhook(runtime, path, method)
    if node is None:
        return JSONResponse({"error": "workflow not running"}, status_code=404)

    # Optional shared-secret header check. The configured value may carry a
    # {{secret.NAME}} token, so resolve before comparing.
    cfg = getattr(node.obj, "_node_cfg", {}) or {}
    secret_cfg = str((cfg.get("secret") if cfg.get("secret") is not None
                      else getattr(node.obj, "secret", "")) or "")
    missing = _secrets.unresolved(secret_cfg)
    if missing:
        # an undefined secret stays literal text, which anyone who has seen the
        # graph knows: never compare against it, refuse every call instead.
        message = f"Webhook secret is not defined: {', '.join(missing)}"
        runtime.log(node.id, f"ERROR {message}")
        return JSONResponse({"error": message}, status_code=503)
    secret_expected = _secrets.resolve_secrets(secret_cfg).strip()
    if secret_expected:
        provided = request.headers.get("x-webhook-secret", "")
        # constant time, so the response timing never leaks how much matched.
        if not hmac.compare_digest(provided.encode("utf-8"), secret_expected.encode("utf-8")):
            return JSONResponse({"error": "forbidden"}, status_code=401)

    body_bytes = await _read_capped(request, HOOK_POST_MAX_BYTES)
    if body_bytes is None:
        return JSONResponse({"error": "body too large"}, status_code=413)
    try:
        body_text = body_bytes.decode("utf-8", errors="replace")
    except Exception:
        body_text = ""

    json_value = None
    content_type = (request.headers.get("content-type") or "").lower()
    if body_bytes and content_type.startswith("application/json"):
        try:
            json_value = json.loads(body_text)
        except Exception:
            json_value = None

    headers_dict: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _WEBHOOK_HEADER_DENYLIST:
            continue
        headers_dict[lk] = v

    query_dict = dict(request.query_params)

    # Fresh turn so the propagation/pull cache resets per webhook hit.
    turn = runtime.new_turn()
    runtime.emit(node.id, "body", body_text, turn)
    runtime.emit(node.id, "json", json_value, turn)
    runtime.emit(node.id, "headers", headers_dict, turn)
    runtime.emit(node.id, "query", query_dict, turn)
    # Fire LAST so downstream consumers see data latched before they run.
    runtime.emit(node.id, "trigger", 1, turn)

    return {"ok": True}


# ---- Ollama local model management -----------------------------------------

def _ollama_base() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


@app.get("/api/connections/ollama/models")
async def ollama_models() -> dict:
    """List locally-pulled Ollama models. Returns {models:[{name,size,modified}]}."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{_ollama_base()}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("models", [])
            models_out = [
                {"name": m["name"], "size": m.get("size", 0), "modified": m.get("modified_at", "")}
                for m in raw
            ]
            return {"models": models_out}
    except Exception as exc:
        return JSONResponse({"models": [], "offline": True, "error": str(exc)}, status_code=200)


@app.post("/api/connections/ollama/pull")
async def ollama_pull(payload: dict = Body(...)):
    """Stream NDJSON pull progress. Body: {\"model\": \"gemma4:e4b\"}."""
    model = (payload.get("model") or "").strip()
    if not model:
        return JSONResponse({"error": "model is required"}, status_code=400)

    async def _stream():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{_ollama_base()}/api/pull",
                    json={"model": model, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n"
        except Exception as exc:
            import json as _json
            yield _json.dumps({"status": "error", "error": str(exc)}) + "\n"

    return StreamingResponse(content=_stream(), media_type="application/x-ndjson")


@app.delete("/api/connections/ollama/models/{name:path}")
async def ollama_delete(name: str) -> dict:
    """Delete a locally-pulled Ollama model."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.request(
                "DELETE",
                f"{_ollama_base()}/api/delete",
                json={"model": name},
            )
            if resp.status_code not in (200, 204):
                text = resp.text[:200]
                return JSONResponse({"error": text or f"{resp.status_code}"}, status_code=502)
            return {"ok": True}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# ---- Secrets store ----------------------------------------------------------
# CRUD for server-managed secrets. Values are NEVER returned from any route.

@app.get("/api/secrets")
def api_list_secrets() -> dict:
    return {"secrets": _secrets.list_secrets()}


@app.post("/api/secrets")
async def api_set_secret(body: dict = Body(...)) -> dict:
    name = body.get("name", "")
    value = body.get("value", "")
    try:
        _secrets.set_secret(name, value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True}


@app.delete("/api/secrets/{name}")
async def api_delete_secret(name: str) -> dict:
    deleted = _secrets.delete_secret(name)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True}


# ---- Database schema editor -------------------------------------------------
# REST over the shared STORE. Reads return the live schema; every mutation
# returns the FRESH schema so the editor can refetch-by-result. The store builds
# all DDL with identifier escaping, so the {key}/table/column names are safe.

def _need(value: str, what: str) -> str:
    """Require a non-empty identifier; raises (caught -> clean 400) rather than
    letting an empty name reach SQL and produce a malformed statement."""
    v = (value or "").strip()
    if not v:
        raise ValueError(f"{what} must not be empty")
    return v


def _db(op):
    """Run a schema op, returning {schema: ...} or a clean 400. Never lets a raw
    sqlite error (with file paths / SQL fragments) escape as a 500 traceback."""
    try:
        return {"schema": op()}
    except (sqlite3.OperationalError, sqlite3.IntegrityError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/db/{key}/schema")
def db_schema(key: str):
    return _db(lambda: STORE.schema(_need(key, "db key")))


@app.post("/api/db/{key}/table")
def db_create_table(key: str, body: dict):
    table = body.get("table") or body.get("name") or ""
    columns = body.get("columns") or []
    return _db(lambda: STORE.create_table(_need(key, "db key"), _need(table, "table"), columns))


@app.patch("/api/db/{key}/table")
def db_rename_table(key: str, body: dict):
    old = body.get("old") or body.get("table") or ""
    new = body.get("new") or body.get("name") or ""
    return _db(lambda: STORE.rename_table(_need(key, "db key"), _need(old, "table"), _need(new, "new name")))


@app.delete("/api/db/{key}/table")
def db_drop_table(key: str, body: dict):
    table = body.get("table") or body.get("name") or ""
    return _db(lambda: STORE.drop_table(_need(key, "db key"), _need(table, "table")))


@app.post("/api/db/{key}/column")
def db_add_column(key: str, body: dict):
    table = body.get("table") or ""
    name = body.get("name") or ""
    coltype = body.get("type") or "TEXT"
    return _db(lambda: STORE.add_column(_need(key, "db key"), _need(table, "table"), _need(name, "column"), coltype))


@app.patch("/api/db/{key}/column")
def db_rename_column(key: str, body: dict):
    table = body.get("table") or ""
    old = body.get("old") or ""
    new = body.get("new") or ""
    return _db(lambda: STORE.rename_column(_need(key, "db key"), _need(table, "table"), _need(old, "column"), _need(new, "new name")))


@app.delete("/api/db/{key}/column")
def db_drop_column(key: str, body: dict):
    table = body.get("table") or ""
    name = body.get("name") or ""
    return _db(lambda: STORE.drop_column(_need(key, "db key"), _need(table, "table"), _need(name, "column")))


@app.delete("/api/db/{key}/all")
def db_clear(key: str):
    """Drop every table: reset the database to zero. Returns the empty schema."""
    return _db(lambda: STORE.clear(_need(key, "db key")))


@app.websocket("/ws")
async def runtime_ws(websocket: WebSocket, slug: str = "_default") -> None:
    await websocket.accept()
    hub = get_hub(slug)
    # bounded so a slow/stalled client can't grow the queue without limit; the
    # broadcast() guard drops events on a full queue rather than stalling the runtime.
    outgoing: asyncio.Queue = asyncio.Queue(maxsize=512)
    hub.subscribers.add(outgoing)

    async def pump() -> None:
        while True:
            event = await outgoing.get()
            if event is None:  # the server is shutting down (Hub.close_connections)
                await websocket.close(code=1001)
                return
            await websocket.send_json(event)

    pump_task = asyncio.create_task(pump())

    # On connect: tell this editor the live status of THIS slug, then replay
    # every cached value so its wires light up to the runtime's current state.
    outgoing.put_nowait({"kind": "status", "power": "on" if hub.runtime else "off", "slug": hub.slug})
    # the live set, so this editor can tell live nodes/wires from draft-only edits.
    outgoing.put_nowait({**hub.live_graph_event(), "slug": hub.slug})
    for event in list(hub.latest.values()):
        outgoing.put_nowait(event)

    try:
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")
            if action == "on":
                problems = await hub.power_on(msg["graph"])
                if problems:
                    outgoing.put_nowait({"kind": "invalid", "problems": problems, "slug": hub.slug})
            elif action == "off":
                await hub.power_off()
            elif action == "restart":
                problems = await hub.restart(msg["graph"])
                if problems:
                    outgoing.put_nowait({"kind": "invalid", "problems": problems, "slug": hub.slug})
            elif action == "chat":
                hub.send_chat(msg["node"], msg.get("text", ""))
            elif action == "audio":
                hub.send_audio(msg["node"], msg.get("audio", ""), msg.get("lang", ""))
            elif action == "fire":
                hub.fire_manual(msg["node"])
    except WebSocketDisconnect:
        pass
    finally:
        hub.subscribers.discard(outgoing)
        pump_task.cancel()
        # Drop an idle, unsubscribed hub from the registry so we don't leak.
        if not hub.subscribers and hub.runtime is None:
            HUBS.pop(hub.slug, None)


@app.get("/stream/{slug}/{channel}")
async def avatar_stream(slug: str, channel: str):
    """Server-sent events of avatar chunks on a channel. The Avatar node publishes
    one chunk per fire ({text, audio, mood, action, lang, ...}); an avatar client
    (any engine) subscribes here and renders it, doing its own lip-sync. The ONE
    media-out stream, distinct from the editor's /ws value broadcast. 404 if not
    running."""
    hub = HUBS.get(slug)
    if hub is None:
        return JSONResponse({"error": "graph not running"}, status_code=404)
    # bound resource use (localhost robustness, mirrors the /ws subscriber model): a
    # looping/misbehaving client cannot grow memory without limit via many channels
    # or many subscribers per channel.
    if channel not in hub.stream_subscribers and len(hub.stream_subscribers) >= STREAM_MAX_CHANNELS:
        return JSONResponse({"error": "too many stream channels"}, status_code=429)
    chan = hub.stream_subscribers.setdefault(channel, set())
    if len(chan) >= STREAM_MAX_SUBS_PER_CHANNEL:
        return JSONResponse({"error": "too many subscribers on this channel"}, status_code=429)
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    chan.add(q)

    async def gen():
        try:
            while True:
                chunk = await q.get()
                if chunk is None:  # the server is shutting down (Hub.close_connections)
                    return
                yield f"data: {json.dumps(chunk)}\n\n"
        finally:
            subs = hub.stream_subscribers.get(channel)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    hub.stream_subscribers.pop(channel, None)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/audio/{slug}/{node}")
async def post_audio(slug: str, node: str, request: Request):
    """An external mic client posts a clip to an Audio Input node: {audio, lang}.
    Mirrors the chat-send path (the editor's mic uses the WS `audio` action);
    fire-and-forget. 404 when the graph is not live."""
    hub = HUBS.get(slug)
    if hub is None or hub.runtime is None:
        return JSONResponse({"error": "graph not running"}, status_code=404)
    # JSON only: a browser can send text/plain cross-site without a preflight.
    if not _is_json(request.headers.get("content-type")):
        return JSONResponse({"error": "send the clip as application/json"}, status_code=415)
    # cap the body (audio clips are MB-scale; reject an absurd payload up front).
    raw = await _read_capped(request, AUDIO_POST_MAX_BYTES)
    if raw is None:
        return JSONResponse({"error": "audio too large"}, status_code=413)
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    hub.send_audio(node, body.get("audio", ""), body.get("lang", ""))
    return {"ok": True}


def _is_json(content_type: str | None) -> bool:
    media = (content_type or "").split(";", 1)[0].strip().lower()
    return media == "application/json" or (media.startswith("application/") and media.endswith("+json"))


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """The request body, or None once it passes `limit` bytes. Counts the bytes
    that actually arrive, so a chunked upload (no content-length) is capped too;
    a declared content-length over the limit is refused before reading."""
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_") or "untitled"


@app.middleware("http")
async def _cache_headers(request, call_next):
    """Make a reload reliably pick up a fresh build: the hashed /assets are
    immutable (cache hard), but the HTML shell must always be revalidated, else a
    browser's heuristic cache can keep serving an old bundle after a rebuild."""
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        resp.headers["cache-control"] = "public, max-age=31536000, immutable"
    elif path == "/" or path.endswith(".html") or path.endswith(".json"):
        resp.headers["cache-control"] = "no-cache"
    return resp


# Added last so it wraps everything above: no route, and no other middleware,
# runs for a request that fails the Host, Origin or token check.
app.add_middleware(_security.LocalGuard)


if EDITOR_DIST.exists():
    app.mount("/", StaticFiles(directory=str(EDITOR_DIST), html=True), name="editor")
