"""
boltjar.runtime: push events, pull data.

Two flows:

  - **control (push):** a Trigger fires an `event` (with an optional payload) to
    the nodes it drives. A *fired* node runs when an event/value lands on one of
    its trigger inputs.
  - **data (pull):** when a node fires it PULLS its data inputs, evaluating the
    upstream pure-data subgraph on demand (memoized per turn; `volatile` sensors
    re-read every pull). Pure data nodes (Text, Compute, Format List, ...) are
    evaluated when something needs them. A fired node that is not pulled (an
    LLM, a TTS) gives a reader the value of its last fire.

So `Text -> Compute -> LLM.prompt` is pulled by the LLM when the LLM is
triggered; the Compute never sits on a clock. A pulled node may declare a
trigger too (the Template): a fire runs it and passes the trigger on, so
`Chat -> Template -> LLM` assembles the prompt, then fires the LLM, which reads
that fire's result from the turn memo. A read is not a fire: it never passes
the trigger on, and a read that comes before the fire in the same turn
assembles this turn's text, so no reader gets the previous turn's.

A trigger input fires its node, so every one must be wired:
server.validate_graph keeps a graph with an unwired one from turning On.

Node execution surfaces (selected by Kind):
    VALUE / pulled data   def run(**inputs) -> dict        (or def value() -> dict)
    TRIGGER (source)      async def start(self, ctx)
    LOGIC                 def route(**inputs) -> port name
    SERVICE              async def open/close; call(payload, ctx) -> dict
    STORE                async def open/close; handle(port, value, ctx) -> dict
    OUTPUT                def deliver(value, ctx, inputs=None)
    work / actor          def run(**inputs) -> dict        (sync or async)
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import secrets as _secrets
from .sdk import Kind, NodeFailure, NodeSpec, NODE_REGISTRY, types

_TURN_CACHE_KEEP = 64  # bound the per-turn memo cache


class Ctx:
    """The handle a node receives."""

    def __init__(self, rt: "Runtime", node_id: str) -> None:
        self._rt = rt
        self.node_id = node_id
        self.state: dict[str, Any] = {}

    @property
    def alive(self) -> bool:
        return self._rt.alive

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def log(self, *args: Any, echo: bool = False) -> None:
        """A log line for the editor console. With `echo` the server's terminal
        prints it too (the Log node does), summarized from this same message, so
        whatever the server keeps out of a live event stays out of the terminal."""
        self._rt.log(self.node_id, " ".join(str(a) for a in args), echo=echo)

    def warn(self, *args: Any) -> None:
        """A warning on the console naming this node: something the node could
        not do that does not stop the graph (a declared table its database
        cannot match)."""
        self._rt.warn(self.node_id, " ".join(str(a) for a in args))

    def emit(self, port: str, value: Any) -> None:
        # a source emitting opens a new turn (a fresh propagation + pull cache)
        self._rt.emit(self.node_id, port, value, self._rt.new_turn())

    def emit_many(self, values: dict) -> None:
        """Emit several ports on ONE shared turn (all part of the same propagation),
        mirroring how send_chat/send_audio latch data before a trigger. Insertion
        order matters: list a data port before the trigger that consumes it, so a
        downstream node pulls the fresh data the moment the trigger fires (the
        Agenda trigger emits its `payload` before its `due` event this way)."""
        turn = self._rt.new_turn()
        for port, value in values.items():
            self._rt.emit(self.node_id, port, value, turn)

    def pull(self, port: str) -> Any:
        """Pull the CURRENT value of a wired data input, evaluating the upstream
        data subgraph in a fresh turn (volatiles re-read). This lets a self-driving
        trigger read a wired handle it was given rather than a static knob, e.g. the
        Agenda trigger polling the `db` handle emitted by a wired Database node.
        Returns the input's latch (or None) when nothing is wired."""
        return self._rt._pull_input(self.node_id, port, self._rt.new_turn())

    def wired_input_ports(self) -> set[str]:
        """The set of input port names that actually have a wire into this node,
        read from the live edge set. Sync uses this to count its wired `in_*`
        trigger ports (the barrier fires when each has arrived once)."""
        return {port for (nid, port) in self._rt.edges_into if nid == self.node_id}

    async def call_tool(self, tool_node_id: str, args: dict, timeout: float = 120.0) -> str:
        """Invoke a Tool node and await its `result`.

        Emits the tool's `call` event with `args` (opening a new turn so any
        downstream pulls are fresh), registers a Future keyed by the Tool node
        id, and waits for a value to land on the tool's `result` trigger port.
        The runtime's `_consume` loop intercepts the `result` value, sets this
        future's result, and skips the Tool node's own fire, so the Tool node
        never tries to "run" by itself; its lifecycle is driven by this primitive.
        One in-flight call per tool at a time is fine for v1; a second call while
        the first is pending raises so the caller sees the contention immediately.
        """
        if tool_node_id not in self._rt.nodes:
            raise RuntimeError(f"call_tool: unknown tool node id {tool_node_id!r}")
        if tool_node_id in self._rt._tool_waiters:
            raise RuntimeError(f"call_tool: a call to {tool_node_id!r} is already in flight")
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._rt._tool_waiters[tool_node_id] = fut
        # surface the hop so the editor can show each tool round-trip (which tool,
        # with what args, and the result) on the Tool node. Truncated via _preview.
        self._rt._notify({"kind": "tool_call", "node": tool_node_id, "args": _preview(args)})
        try:
            self._rt.emit(tool_node_id, "call", args, self._rt.new_turn())
            result = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._rt._tool_waiters.pop(tool_node_id, None)
        self._rt._notify({"kind": "tool_result", "node": tool_node_id, "result": _preview(result)})
        return "" if result is None else str(result)


@dataclass
class NodeInstance:
    id: str
    spec: NodeSpec
    obj: Any
    ctx: Ctx
    mailbox: "asyncio.Queue[tuple[str, Any, int]]" = field(default_factory=asyncio.Queue)
    out_latch: dict[str, Any] = field(default_factory=dict)   # last value emitted per output port
    latch: dict[str, Any] = field(default_factory=dict)        # last value seen per latched input

    def trigger_ports(self) -> set[str]:
        # fired nodes mark their firing input(s) trigger=True; data inputs latch.
        return {p.name for p in self.spec.inputs if p.trigger}

    def is_trigger(self, port: str) -> bool:
        """Whether a value landing on `port` should FIRE this node. A declared
        trigger port fires; so does every socket of a growable trigger input
        (Sync's in0, a Queue's socket named after its source), found the way
        validation finds it (NodeSpec.growable_base). Data ports, promoted
        knobs and the sockets of a data input (Template tags) only latch."""
        if port in self.trigger_ports():
            return True
        promoted = (getattr(self.obj, "_node_cfg", None) or {}).get("promoted") or ()
        base = self.spec.growable_base(port, promoted)
        return base is not None and base.trigger


class Runtime:
    """Runs one graph. `observer` receives live events (values, logs, errors)."""

    def __init__(self, observer: Optional[Callable[[dict], None]] = None) -> None:
        self.nodes: dict[str, NodeInstance] = {}
        self.edges_from: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self.edges_into: dict[tuple[str, str], tuple[str, str]] = {}
        self.alive = False
        self._tasks: list[asyncio.Task] = []
        self._observer = observer
        self._turn_seq = itertools.count(1)
        self._turn_cache: "dict[int, dict[str, dict]]" = {}
        # Tool call request/response bridge: when an LLM awaits a tool's result
        # via Ctx.call_tool, a Future is parked here keyed by the Tool node id.
        # The consumer loop sets it when a value arrives on the tool's `result`
        # trigger port (and SKIPS the Tool's own fire for that turn).
        self._tool_waiters: dict[str, asyncio.Future] = {}

    # --------------------------------------------------------------- build
    def build(self, graph: dict) -> None:
        # flattened nodes (a disabled node, the Wireless In/Out pair, a Router) are
        # not instantiated: their wires are resolved to the real source, or
        # dropped at a dead end (see flatten_graph).
        flat = flatten_graph(graph)
        for n in graph.get("nodes", []):
            if n["id"] in flat.flattened:
                continue
            spec = NODE_REGISTRY.get(n["type"])
            if spec is None:
                raise ValueError(f"unknown node type: {n['type']}")
            obj = spec.cls()
            setattr(obj, "_node_id", n["id"])
            # the config the node RUNS with (node_config): its declared widget
            # defaults under the saved config, so it reads what the editor shows.
            # The widget attributes and the full dict (per-model dynamic fields
            # too) both come from that one merge.
            cfg = node_config(spec, n.get("config"))
            for w in spec.widgets:
                setattr(obj, w.name, cfg[w.name])
            setattr(obj, "_node_cfg", cfg)
            # pristine knob values: a promoted widget that goes unwired restores its
            # knob value instead of freezing a stale wired one (universal promotion).
            setattr(obj, "_knob_cfg", {w.name: getattr(obj, w.name, None) for w in spec.widgets})
            inst = NodeInstance(id=n["id"], spec=spec, obj=obj, ctx=Ctx(self, n["id"]))
            for p in spec.inputs:
                if p.default is not None:
                    inst.latch[p.name] = p.default
            self.nodes[n["id"]] = inst
        for src, sp, dst, dp in flat.live:
            self.edges_from.setdefault((src, sp), []).append((dst, dp))
            self.edges_into[(dst, dp)] = (src, sp)

    # --------------------------------------------------------------- turns
    def new_turn(self) -> int:
        t = next(self._turn_seq)
        self._turn_cache[t] = {}
        if len(self._turn_cache) > _TURN_CACHE_KEEP:
            oldest = min(self._turn_cache)
            self._turn_cache.pop(oldest, None)
        return t

    # --------------------------------------------------------------- run
    async def run(self) -> None:
        self.alive = True
        for inst in self.nodes.values():
            if inst.spec.kind in (Kind.SERVICE, Kind.STORE) and hasattr(inst.obj, "open"):
                await _aw(inst.obj.open(inst.ctx))
        for inst in self.nodes.values():
            # every node that can FIRE gets a consumer: a fired node, and a pulled
            # node that also declares a trigger input (the Template), which fires
            # through it as well as being evaluated on a read.
            if inst.spec.kind != Kind.TRIGGER and (not inst.spec.pulled or inst.trigger_ports()):
                self._tasks.append(asyncio.create_task(self._consume(inst)))
        for inst in self.nodes.values():
            if inst.spec.kind == Kind.TRIGGER and hasattr(inst.obj, "start"):
                self._tasks.append(asyncio.create_task(self._run_start(inst)))

    async def _run_start(self, inst: NodeInstance) -> None:
        """A trigger's own loop. When it dies the trigger stops firing, so the
        failure is reported like any node error (log + node_error), never left
        for stop() to collect in silence."""
        try:
            await inst.obj.start(inst.ctx)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log(inst.id, f"ERROR trigger stopped: {exc!r}")
            self._notify({"kind": "node_error", "node": inst.id, "error": f"trigger stopped: {exc!r}"})

    async def _consume(self, inst: NodeInstance) -> None:
        while self.alive:
            try:
                port, value, turn = await inst.mailbox.get()
            except asyncio.CancelledError:
                return
            inst.latch[port] = value
            if not inst.is_trigger(port):
                continue
            # Tool request/response bridge: if a Tool node receives its `result`
            # while an LLM is awaiting a call_tool() future, hand the value to
            # the waiter and SKIP the Tool's own fire (it has no work to do; its
            # whole purpose is to be the routing point between LLM and graph).
            if port == "result" and inst.id in self._tool_waiters:
                fut = self._tool_waiters.get(inst.id)
                if fut is not None and not fut.done():
                    fut.set_result(value)
                continue
            try:
                await self._fire(inst, port, value, turn)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # one node's failure never stops the graph
                # a NodeFailure carries its branch outputs (already emitted); report
                # the error that caused it, not the wrapper.
                err = exc.__cause__ if isinstance(exc, NodeFailure) and exc.__cause__ else exc
                self.log(inst.id, f"ERROR {err!r}")
                self._notify({"kind": "node_error", "node": inst.id, "error": repr(err)})

    async def _fire(self, inst: NodeInstance, trig_port: str, payload: Any, turn: int) -> None:
        # mark this node as actively working for the whole fire (input pull +
        # invoke), so the editor shows exactly what is running right now (e.g.
        # the LLM stays lit for the full duration of its model call). A source
        # (trigger) never fires, so it never sticks "running".
        self._notify({"kind": "node_status", "node": inst.id, "status": "running"})
        inputs: dict[str, Any] = {}
        for name in self._all_inputs(inst.id):
            if name == trig_port:
                inputs[name] = payload
            else:
                # every non-firing input (data OR a trigger port that didn't fire,
                # e.g. Preview.in while `trigger` fired) is pulled: _pull_input
                # evaluates a pulled source or reads a pushed source's last value,
                # so a mid-flow node sees its upstream's current value, not a stale
                # latch. Unwired inputs fall back to the latch inside _pull_input.
                inputs[name] = self._pull_input(inst.id, name, turn)
        try:
            out = await self._invoke(inst, trig_port, payload, inputs)
        except NodeFailure as failure:
            # a declared failure branch (the LLM's `error` event): emit its outputs
            # so what is wired to the branch reacts, then re-raise so _consume
            # reports it as the node's error and the node is never marked ok.
            fail_turn = self.new_turn() if inst.spec.opens_turn else turn
            for port, val in failure.outputs.items():
                self.emit(inst.id, port, val, fail_turn)
            raise
        if isinstance(out, dict):
            # a node that `opens_turn` (For-each, Sync) emits each output on a
            # FRESH epoch, so the body it drives re-pulls instead of reading this
            # incoming turn's memoized cache (e.g. the LLM re-runs per item). A
            # data output (item) is latched immediately by emit, so emitting it
            # BEFORE the `each` trigger (insertion order) means the body sees the
            # current item the moment it fires.
            out_turn = self.new_turn() if inst.spec.opens_turn else turn
            if inst.spec.pulled and not inst.spec.volatile:
                # a fired DATA node's result is this turn's memoized value: a
                # consumer it triggers pulls it in the same turn and reads exactly
                # what was emitted, never a second evaluation.
                self._turn_cache.setdefault(out_turn, {})[inst.id] = out
            for port, val in out.items():
                self.emit(inst.id, port, val, out_turn)
        # done: settle back to ok (an exception instead routes to node_error).
        self._notify({"kind": "node_status", "node": inst.id, "status": "ok"})

    async def _invoke(self, inst: NodeInstance, trig_port: str, payload: Any, inputs: dict) -> Any:
        obj, kind = inst.obj, inst.spec.kind
        _apply_promoted(obj, inputs)
        # generic, cheap: tell the node which trigger port fired (For-each reads
        # this to tell `trigger` from the `loop` back-edge; Sync reads it to know
        # which `in_*` arrived) and hand it the live Ctx (Sync asks the runtime
        # how many of its `in_*` ports are actually wired). Both are read off
        # `self`; no per-node-id branching in the engine.
        setattr(obj, "_fired_port", trig_port)
        setattr(obj, "_ctx", inst.ctx)
        # Tool wiring for the LLM node: stash the live Ctx + the list of Tool
        # nodes wired into the LLM's growable `tools` input, so LLM.run can
        # collect each tool's manifest (name/description/schema) and await its
        # result via ctx.call_tool(). Cheap to compute; only the LLM reads it.
        if inst.spec.id == "core.ai.llm":
            setattr(obj, "_ctx", inst.ctx)
            tool_ids = [src for src, _port in self.growable_sources(inst.id, "tools")
                        if self.nodes[src].spec.id == "core.ai.tool"]
            setattr(obj, "_tool_node_ids", tool_ids)
        if kind == Kind.OUTPUT:
            # deliver the value on the actual triggering port. `inputs` carries every
            # input (the firing payload + the latched/pulled others), so a sink like
            # Preview can re-emit its `in` even when fired by a separate `trigger`.
            return await _aw(obj.deliver(payload, inst.ctx, inputs)) or {}
        if kind == Kind.LOGIC:
            port = await _aw(obj.route(**inputs))
            return {port: payload} if port else {}
        if kind == Kind.STORE and hasattr(obj, "handle"):
            return await _aw(obj.handle(trig_port, payload, inst.ctx)) or {}
        if kind == Kind.SERVICE and hasattr(obj, "call"):
            return await _aw(obj.call(payload, inst.ctx)) or {}
        if hasattr(obj, "run"):
            return await _aw(obj.run(**inputs)) or {}
        return {}

    # --------------------------------------------------------------- pull
    def _all_inputs(self, node_id: str) -> list[str]:
        """Declared input ports plus any dynamic (growable {tag}) ports from edges."""
        inst = self.nodes.get(node_id)
        if inst is None:
            return []
        names = [p.name for p in inst.spec.inputs]
        for (nid, port) in self.edges_into:
            if nid == node_id and port not in names:
                names.append(port)
        return names

    def growable_sources(self, node_id: str, base: str) -> list[tuple[str, str]]:
        """The (source node, source port) of every wire into a socket of the
        growable input `base` of `node_id`, in the graph's edge order.

        A socket is never known by its name alone: the editor names the sockets
        it mints (`tool0`, `tool1` for the LLM's `tools`), a hand-written graph
        may use the base's own name, and a pack may pick any other. So a socket
        of `base` is a wired input that NodeSpec.growable_base gives to `base`
        (the one rule validation and firing use), fed by an output whose type
        fits the base's type: an LLM's model-shaped inputs (an image, audio)
        are wired sockets too, and only the type tells them from a tool."""
        inst = self.nodes.get(node_id)
        if inst is None:
            return []
        grow = next((p for p in inst.spec.inputs if p.name == base and p.growable), None)
        if grow is None:
            return []
        cfg = getattr(inst.obj, "_node_cfg", None) or {}
        promoted = cfg.get("promoted") if isinstance(cfg.get("promoted"), (list, tuple)) else []
        found: list[tuple[str, str]] = []
        for (dst, port), (src, src_port) in self.edges_into.items():
            if dst != node_id or inst.spec.growable_base(port, promoted) is not grow:
                continue
            src_inst = self.nodes.get(src)
            if src_inst is None:
                continue
            out_type = next((p.type for p in src_inst.spec.outputs if p.name == src_port), "any")
            if types.compatible(out_type, grow.type):
                found.append((src, src_port))
        return found

    def _pull_input(self, node_id: str, port: str, turn: int) -> Any:
        src = self.edges_into.get((node_id, port))
        if src is None:
            inst = self.nodes.get(node_id)
            return inst.latch.get(port) if inst is not None else None
        return self._pull_output(src[0], src[1], turn)

    def _pull_output(self, node_id: str, port: str, turn: int) -> Any:
        inst = self.nodes.get(node_id)
        if inst is None:
            # a dangling source (a flattened/removed node): pull as empty, never a
            # KeyError that would masquerade as a crash in the pulling node.
            return None
        if not inst.spec.pulled:
            # source or fired node: use its latched last output
            return inst.out_latch.get(port)
        cache = self._turn_cache.setdefault(turn, {})
        if not inst.spec.volatile and node_id in cache:
            return cache[node_id].get(port)
        inputs = {name: self._pull_input(node_id, name, turn) for name in self._all_inputs(node_id)}
        _apply_promoted(inst.obj, inputs)
        # a pull is not a fire: no trigger port fired, so a pulled node that
        # also fires (the Template) never mistakes this evaluation for one and
        # never passes its trigger on from a read.
        setattr(inst.obj, "_fired_port", None)
        if hasattr(inst.obj, "run"):
            out = inst.obj.run(**inputs)
        elif hasattr(inst.obj, "value"):
            out = inst.obj.value()
        else:
            out = {}
        out = out if isinstance(out, dict) else {}
        if not inst.spec.volatile:
            cache[node_id] = out
        self._notify({"kind": "value", "node": node_id, "port": port, "value": _preview(out.get(port))})
        return out.get(port)

    # --------------------------------------------------------------- emit
    def emit(self, node_id: str, port: str, value: Any, turn: int) -> None:
        self.nodes[node_id].out_latch[port] = value
        self._notify({"kind": "value", "node": node_id, "port": port, "value": _preview(value)})
        for dst, dst_port in self.edges_from.get((node_id, port), []):
            dst_inst = self.nodes[dst]
            if dst_inst.is_trigger(dst_port):
                # a trigger input fires its node, a pulled node that declares
                # one (the Template) included.
                dst_inst.mailbox.put_nowait((dst_port, value, turn))
            elif not dst_inst.spec.pulled:
                dst_inst.latch[dst_port] = value
            # a pulled node's data inputs read our out_latch when it evaluates.

    def send_chat(self, node_id: str, text: str) -> None:
        """Inject a chat message into a Chat Input node (latch text, then fire)."""
        if node_id in self.nodes:
            turn = self.new_turn()
            self.emit(node_id, "text", text, turn)
            self.emit(node_id, "trigger", text, turn)

    def send_audio(self, node_id: str, audio: str, lang: str = "") -> None:
        """Inject an audio clip into an Audio Input node (latch audio/lang, then
        fire). Mirrors send_chat: data ports latch before the trigger fires."""
        if node_id in self.nodes:
            turn = self.new_turn()
            self.emit(node_id, "audio", audio, turn)
            if lang:
                self.emit(node_id, "lang", lang, turn)
            self.emit(node_id, "trigger", audio, turn)

    def fire_manual(self, node_id: str) -> None:
        """Fire a Manual trigger by hand (mirrors send_chat): emit a trigger event
        on the node's `trigger` output, opening a fresh turn."""
        if node_id in self.nodes:
            self.emit(node_id, "trigger", 1, self.new_turn())

    # --------------------------------------------------------------- lifecycle
    async def stop(self) -> None:
        self.alive = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for inst in self.nodes.values():
            if inst.spec.kind in (Kind.SERVICE, Kind.STORE) and hasattr(inst.obj, "close"):
                await _aw(inst.obj.close())

    def log(self, node_id: str, msg: str, echo: bool = False) -> None:
        event = {"kind": "log", "node": node_id, "message": msg}
        if echo:
            event["echo"] = True
        self._notify(event)

    def warn(self, node_id: str, msg: str) -> None:
        self._notify({"kind": "warning", "node": node_id, "message": msg})

    def _notify(self, event: dict) -> None:
        if self._observer:
            # every live event leaves through here, so this is where a resolved
            # secret is swapped back for its {{secret.NAME}} token: the editor, the
            # /state cache and every websocket see the token, never the key. The
            # values flowing between nodes are untouched.
            self._observer(_redact(event))


WIRELESS_IN, WIRELESS_OUT, ROUTER = "core.flow.wireless_in", "core.flow.wireless_out", "core.flow.router"


@dataclass
class FlatGraph:
    """The wires a graph runs on (see flatten_graph)."""
    flattened: set[str]                      # node ids never instantiated
    live: list[tuple[str, str, str, str]]    # (src, src_port, dst, dst_port), src resolved
    dead: list[tuple[str, str, str]]         # (dst, dst_port, stop): resolves to nothing


def flatten_graph(graph: dict) -> FlatGraph:
    """Resolve a graph's FLATTENED nodes, which never run: a disabled node (wired
    through when it declares a `bypass` shape, else dropped), and the Wireless
    In/Out pair and the Router (virtual wires, always resolved to a direct
    source -> consumer edge). `live` is every wire into a node that runs, its
    source walked back to the real one. `dead` is every wire into a node that
    runs whose source resolves to nothing, with `stop`, the flattened node where
    the walk back ends (a bypassed node that passes nothing on, a Wireless Out on
    a channel with no Wireless In), however many hops up the wire it is: the
    runtime drops the wire. One walk, shared by the runtime (Runtime.build) and
    validation (server.validate_graph), so a trigger validation counts as wired
    is one the runtime fires."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    disabled = {n["id"] for n in nodes if n.get("disabled")}
    types_by_id = {n["id"]: n["type"] for n in nodes}
    configs_by_id = {n["id"]: (n.get("config") or {}) for n in nodes}
    # wireless + routers are ALWAYS flattened (pure bypass), like a disabled
    # passthrough; resolve_source walks their `bypass` to the real upstream.
    wireless = {nid for nid, t in types_by_id.items() if t in (WIRELESS_IN, WIRELESS_OUT, ROUTER)}
    # first Wireless In per channel (a channel is one broadcast source).
    wireless_in_by_channel: dict[str, str] = {}
    for n in nodes:
        if n["type"] == WIRELESS_IN:
            ch = str((n.get("config") or {}).get("channel", "1"))
            wireless_in_by_channel.setdefault(ch, n["id"])
    flattened = disabled | wireless
    # raw edges into a flattened node's input, used to resolve a bypass.
    raw_into: dict[tuple[str, str], tuple[str, str]] = {
        (e["dst"], e["dst_port"]): (e["src"], e["src_port"]) for e in edges
    }

    def bypass_map(node_id: str) -> dict:
        spec = NODE_REGISTRY.get(types_by_id.get(node_id, ""))
        return spec.bypass if spec else {}

    def resolve_source(node_id: str, out_port: str,
                       seen: frozenset) -> tuple[Optional[tuple[str, str]], str]:
        """Walk back through flattened nodes to the real source that should feed
        `(node_id, out_port)`: a Wireless Out routes through its channel's
        Wireless In socket of the same name; a disabled passthrough walks its
        `bypass` shape. Returns (source, "") or, at a dead-end, (None, the
        flattened node the walk stops at). Cycle-safe via `seen`."""
        if node_id in seen:
            return None, node_id
        if types_by_id.get(node_id) == WIRELESS_OUT:
            ch = str(configs_by_id.get(node_id, {}).get("channel", "1"))
            in_id = wireless_in_by_channel.get(ch)
            # the Out's output mirrors the In's socket of the same name.
            up = raw_into.get((in_id, out_port)) if in_id else None
        else:
            in_port = next((i for i, o in bypass_map(node_id).items() if o == out_port), None)
            up = raw_into.get((node_id, in_port)) if in_port is not None else None
        if up is None:
            return None, node_id
        return resolve_source(up[0], up[1], seen | {node_id}) if up[0] in flattened else (up, "")

    live: list[tuple[str, str, str, str]] = []
    dead: list[tuple[str, str, str]] = []
    for e in edges:
        src, sp, dst, dp = e["src"], e["src_port"], e["dst"], e["dst_port"]
        if dst in flattened:
            # the input side of a flattened node (a disabled passthrough's input,
            # or a Wireless In socket): consumed by resolution, so drop it here.
            continue
        if src in flattened:
            resolved, stop = resolve_source(src, sp, frozenset())
            if resolved is None:
                # dead-end (unmatched channel, disabled sink): the edge is dropped.
                dead.append((dst, dp, stop))
                continue
            src, sp = resolved
        live.append((src, sp, dst, dp))
    return FlatGraph(flattened=flattened, live=live, dead=dead)


def node_config(spec: NodeSpec, config: Optional[dict]) -> dict:
    """The config a node runs with: every declared widget default, overlaid by
    the saved config. The editor shows a widget's saved value, or its declared
    default when none is saved (the key is absent or null), and a graph can store
    an untouched node as `config: {}`. So a node must see that same default, never
    a fallback literal of its own. One merge, shared by the runtime (build) and
    validation (server.validate_graph), so both read what the editor shows. Keys
    that are not widgets (`promoted`, `params`, a store's `db_key`) pass through.
    A saved value is read as its widget's kind (`Widget.coerce`), so a graph
    saved while a toggle was a text box runs "false" as off."""
    merged = dict(config or {})
    for w in spec.widgets:
        if merged.get(w.name) is None:
            merged[w.name] = w.default
        else:
            merged[w.name] = w.coerce(merged[w.name])
    return merged


def _apply_promoted(obj: Any, inputs: dict) -> None:
    """Universal knob promotion (the LEGO principle): for every widget the author
    promoted to an input (``config.promoted``), prefer the wired value over the
    knob. We override both the node attribute (what ``run()``/``value()`` reads via
    ``self.<name>``) and the ``_node_cfg`` entry (what nodes reading the raw config
    see), so a promoted knob becomes transparent, no per-node code required. Only
    a wired, non-None value overrides; an unwired promoted port keeps the knob's
    value so the node never silently loses its default.

    The LLM node also reshapes promoted params inside its own ``run()`` (from its
    model manifest); that richer path still works because it reads ``kw[name]`` from
    the same wired inputs, and overriding ``self.<name>`` here is harmless for it.
    """
    cfg = getattr(obj, "_node_cfg", None)
    if not isinstance(cfg, dict):
        return
    promoted = cfg.get("promoted")
    if not isinstance(promoted, (list, tuple)):
        return
    knob = getattr(obj, "_knob_cfg", {})
    for name in promoted:
        if not isinstance(name, str):
            continue
        wired = inputs.get(name)
        if wired is not None:
            setattr(obj, name, wired)
            cfg[name] = wired
        elif name in knob:
            # unwired this turn: restore the original knob value, never freeze a
            # stale wired value from a previous turn.
            setattr(obj, name, knob[name])
            cfg[name] = knob[name]


def _redact(value: Any) -> Any:
    """`value` with every known secret in its strings redacted (secrets.redact),
    walking dicts and lists; anything else passes through."""
    if isinstance(value, str):
        return _secrets.redact(value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


async def _aw(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _preview(value: Any) -> Any:
    s = str(value)
    # a clip reference (a data:/blob:/http audio url) must reach the editor whole,
    # so its player can decode it; truncating it yields a broken clip.
    head = s[:5].lower()
    if head.startswith(("data:", "blob:", "http:", "https")):
        return s
    # the Preview node is the dedicated inspection surface, so it must show the
    # whole value (an assembled prompt with chat history is a few KB). Keep only a
    # generous safety cap so a pathological value can't flood the socket.
    return s if len(s) <= 8000 else s[:8000] + "…"
