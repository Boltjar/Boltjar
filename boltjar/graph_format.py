"""
boltjar.graph_format: the saved graph's format version and its migrations.

A saved graph carries a top-level ``"format"`` number. When the shape of a saved
graph changes, CURRENT_FORMAT goes up by one and a step that turns the previous
format into the new one is added to _STEPS. migrate() runs every step a graph
is behind, in order, so a graph saved by any earlier Boltjar still loads. A
graph without the field predates it and is format 0.

The server migrates every graph it loads (GET, power, validate), stamps the
current format on every save and never saves over a graph whose format it
cannot read; the editor writes the same number (GRAPH_FORMAT in
editor/src/lib/graphAdapter.ts).
"""
from __future__ import annotations

import copy
from typing import Callable

CURRENT_FORMAT = 3


class GraphFormatError(ValueError):
    """A graph this Boltjar cannot read: saved by a newer Boltjar, or with a
    `format` that is not a format number."""


def _from_0(graph: dict) -> dict:
    """Format 0 (saved before the field existed) already has the format 1 shape."""
    return graph


# The model value format 1 had for "run whatever model can run now". It is gone:
# a model can cost money, so only a person picks one.
_AUTO = "auto"


def _from_1(graph: dict) -> dict:
    """Format 2 drops the "auto" model: a model picker that held it holds none,
    which is what it loads as (the LLM answers with the offline mock, any other
    model node waits for a model to be picked). "auto" was only ever the value
    of a model picker, and every model picker Boltjar shipped is `model`."""
    for node in graph.get("nodes") or []:
        config = node.get("config") if isinstance(node, dict) else None
        if isinstance(config, dict) and config.get("model") == _AUTO:
            del config["model"]
    return graph


# Format 3: a data wire never fires a node. These nodes used to fire on a data
# input and now fire on an event input, reading that data when it fires:
# {node type: (data input, its new trigger input)}. The Queue is reshaped by
# its own rule (see migrate_triggers).
FIRED_BY_DATA = {
    "core.output.log": ("in", "trigger"),
    "core.output.preview": ("in", "trigger"),
    "core.logic.condition": ("value", "trigger"),
    "core.ai.tool": ("result", "result_trigger"),
    "core.ai.tool_args": ("call", "trigger"),
}
_QUEUE = "core.flow.queue"
_LOGIC = "core.logic.condition"
_ROUTER = "core.flow.router"
_WIRELESS_IN = "core.flow.wireless_in"
_WIRELESS_OUT = "core.flow.wireless_out"
_WIRE_KEYS = ("src", "src_port", "dst", "dst_port")


# the TriggerWalk's marker for a (node, port) whose trigger is being worked out
_ACTIVE = object()


class TriggerWalk:
    """The wires of a saved graph, and the trigger behind any output of it.

    trigger_for(src, port) is the trigger that fires when (src, port) carries a
    new value: the port itself when it is an event, else the node's `trigger`
    output, else its one event output. Through a Router, a bypassed node or a
    Wireless pair it is found at the real source; for a data node with no event
    output it is the one trigger behind all of its wired inputs. None when it
    cannot be told, a cycle included.

    The walk is iterative and memoized over the whole graph: each (node, port)
    is worked out once, so a graph of any size or shape (an opened file can be
    anything) migrates in time linear in its wires and never recurses."""

    def __init__(self, graph: dict, spec_of: Callable) -> None:
        self.spec_of = spec_of
        self.nodes = {n["id"]: n for n in graph.get("nodes") or []
                      if isinstance(n, dict) and isinstance(n.get("id"), str)}
        self.wires = [(i, e) for i, e in enumerate(graph.get("edges") or [])
                      if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in _WIRE_KEYS)]
        # each wired input -> its source, the last wire winning as in the runtime
        self.into = {(e["dst"], e["dst_port"]): (e["src"], e["src_port"]) for _i, e in self.wires}
        # node -> [(its input port, (source, source port))]
        self.inputs: dict[str, list[tuple[str, tuple[str, str]]]] = {}
        for (dst, dp), src in self.into.items():
            self.inputs.setdefault(dst, []).append((dp, src))
        self.wireless_in: dict[str, str] = {}
        for nid, n in self.nodes.items():
            if n.get("type") == _WIRELESS_IN:
                self.wireless_in.setdefault(_channel(n), nid)
        self._memo: dict[tuple[str, str], object] = {}

    def spec(self, nid: str):
        return self.spec_of(self.nodes[nid].get("type")) if nid in self.nodes else None

    def upstream(self, src: str, port: str):
        """The wire (source, source port) a pass-through output (a Router, a
        bypassed node, a Wireless Out) carries on, None when it carries
        nothing; False when (src, port) is no pass-through."""
        node = self.nodes.get(src)
        if node is None:
            return None
        if node.get("type") == _WIRELESS_OUT:
            wi = self.wireless_in.get(_channel(node))
            return self.into.get((wi, port)) if wi else None
        if node.get("disabled") or node.get("type") == _ROUTER:
            sp = self.spec(src)
            if sp is None:
                return None
            in_port = next((i for i, o in sp.bypass.items() if o == port), None)
            return self.into.get((src, in_port)) if in_port else None
        return False

    def source(self, src: str, port: str):
        """The real (node, port) behind an output, through any pass-throughs, or
        None when the wire carries nothing."""
        seen = set()
        while (src, port) not in seen:
            seen.add((src, port))
            up = self.upstream(src, port)
            if up is False:
                return (src, port)
            if up is None:
                return None
            src, port = up
        return None  # a pass-through cycle carries nothing

    def _plan(self, key: tuple[str, str]):
        """("done", trigger) or ("deps", [keys], combine) for one output."""
        src, port = key
        node = self.nodes.get(src)
        if node is None:
            return ("done", None)
        up = self.upstream(src, port)
        if up is None:
            return ("done", None)
        if up is not False:
            if node.get("type") != _WIRELESS_OUT:
                return ("deps", [up], lambda found: found[0])
            wi = self.wireless_in.get(_channel(node))

            def through_pair(found, src=src, wi=wi):
                # the same trigger through this pair, when the Wireless In carries it
                if found[0] is None:
                    return None
                socket = next((dp for dp, s in self.inputs.get(wi, ()) if s == found[0]), None)
                return (src, socket) if socket else found[0]
            return ("deps", [up], through_pair)
        sp = self.spec(src)
        if sp is None:
            return ("done", None)
        if _out_type(sp, port) == "event":
            return ("done", (src, port))
        if _out_type(sp, "trigger") == "event":
            return ("done", (src, "trigger"))
        events = [p.name for p in sp.outputs if p.type == "event" and not p.optional]
        if events:
            return ("done", (src, events[0]) if len(events) == 1 else None)

        def the_one(found):
            distinct = {f for f in found if f is not None}
            return distinct.pop() if len(distinct) == 1 else None
        return ("deps", [s for _dp, s in self.inputs.get(src, ())], the_one)

    def trigger_for(self, src: str, port: str):
        memo = self._memo
        root = (src, port)
        stack: list[list] = [[root, None]]
        while stack:
            frame = stack[-1]
            key, plan = frame
            if plan is None:
                if key in memo:  # worked out, or under way higher up (a cycle)
                    stack.pop()
                    continue
                plan = self._plan(key)
                if plan[0] == "done":
                    memo[key] = plan[1]
                    stack.pop()
                    continue
                memo[key] = _ACTIVE
                frame[1] = plan
                stack.extend([dep, None] for dep in plan[1] if dep not in memo)
                continue
            found = [None if memo.get(dep, None) is _ACTIVE else memo.get(dep) for dep in plan[1]]
            memo[key] = plan[2](found)
            stack.pop()
        result = memo.get(root)
        return None if result is _ACTIVE else result


def _channel(node: dict) -> str:
    config = node.get("config")
    return str((config if isinstance(config, dict) else {}).get("channel", "1"))


def _from_2(graph: dict) -> dict:
    """Format 3: a data wire never fires a node (see migrate_triggers)."""
    import boltjar.nodes.core  # noqa: F401  the walk reads the core node specs
    from boltjar.sdk import NODE_REGISTRY
    return migrate_triggers(graph, NODE_REGISTRY.get)


def _out_type(spec, port: str):
    return next((p.type for p in spec.outputs if p.name == port), None) if spec else None


def _fires(spec, node: dict, port: str) -> bool:
    """Whether `port` of `node` is a trigger input, in the format 3 shape."""
    if spec is None:
        return False
    declared = next((p for p in spec.inputs if p.name == port), None)
    if declared is not None:
        return declared.trigger
    base = spec.growable_base(port, (node.get("config") or {}).get("promoted") or ())
    return base is not None and base.trigger


def migrate_triggers(graph: dict, spec_of: Callable) -> dict:
    """The format 2 -> 3 step. `spec_of(type id)` gives a node's spec in the
    format 3 shape, or None. Mirrors migrateTriggers in
    editor/src/lib/graphAdapter.ts; the shared cases in
    editor/test/fixtures/migrate-format-3.json keep the two in step.

    - The Log, Preview, Logic, Tool and Tool Args fire on a new event input
      (FIRED_BY_DATA) and read the data input that used to fire them. The new
      trigger is wired from the trigger that came with that data (trigger_for).
    - A Logic `true` / `false` wire into a data input carried the value: it
      comes from the Logic's `value` output now (`true` / `false` are events).
    - A Queue fires on growable `trigger` sockets and reads its items from
      growable `in` sockets: each old socket fed by an event becomes
      `trigger<k>`; one fed by data becomes `in<k>`, with `trigger<k>` wired
      from that data's trigger. A Queue `out` wire into a trigger input comes
      from the Queue's `trigger` output now.

    A trigger that cannot be told stays unwired: the graph loads, and
    validation names the trigger, so the person wires it."""
    edges = list(graph.get("edges") or [])
    walk = TriggerWalk(graph, spec_of)
    nodes, wires, into, spec = walk.nodes, walk.wires, walk.into, walk.spec
    trigger_for = walk.trigger_for

    # every decision reads the graph as saved; the changes are applied after
    renames: dict[int, dict] = {}
    added: list[dict] = []
    wired = set(into)

    def add(trigger, dst: str, port: str) -> None:
        if trigger is not None and (dst, port) not in wired:
            wired.add((dst, port))
            added.append({"src": trigger[0], "src_port": trigger[1], "dst": dst, "dst_port": port})

    for nid, node in nodes.items():
        pair = FIRED_BY_DATA.get(node.get("type"))
        if pair is not None and (nid, pair[0]) in into:
            add(trigger_for(*into[(nid, pair[0])]), nid, pair[1])
    for i, e in wires:
        src_type = nodes.get(e["src"], {}).get("type")
        dst = nodes.get(e["dst"])
        if dst is None or dst.get("type") == _QUEUE:
            continue  # a Queue's own sockets: below
        to_trigger = _fires(spec(e["dst"]), dst, e["dst_port"])
        if src_type == _LOGIC and e["src_port"] in ("true", "false") and not to_trigger:
            renames[i] = {"src_port": "value"}
        elif src_type == _QUEUE and e["src_port"] == "out" and to_trigger:
            renames[i] = {"src_port": "trigger"}
    sockets: dict[str, list[tuple[int, dict]]] = {}
    for i, e in wires:
        if nodes.get(e["dst"], {}).get("type") == _QUEUE:
            sockets.setdefault(e["dst"], []).append((i, e))
    for nid, queue_wires in sockets.items():
        promoted = set((nodes[nid].get("config") or {}).get("promoted") or ())
        taken = {dp for (d, dp) in wired if d == nid}
        k = 0
        for i, e in queue_wires:
            if e["dst_port"] == "ack" or e["dst_port"] in promoted:
                continue
            while f"trigger{k}" in taken or f"in{k}" in taken:
                k += 1
            taken.update((f"trigger{k}", f"in{k}"))
            src = (e["src"], e["src_port"])
            trigger = trigger_for(*src)
            if trigger == src:
                renames[i] = {"dst_port": f"trigger{k}"}
                wired.add((nid, f"trigger{k}"))
            else:
                renames[i] = {"dst_port": f"in{k}"}
                wired.add((nid, f"in{k}"))
                add(trigger, nid, f"trigger{k}")
            k += 1
    graph["edges"] = [{**e, **renames[i]} if i in renames else e
                      for i, e in enumerate(edges)] + added
    return graph


# the step that turns a graph of format N (the key) into format N + 1.
_STEPS: dict[int, Callable[[dict], dict]] = {
    0: _from_0,
    1: _from_1,
    2: _from_2,
}


def format_of(graph: dict) -> int:
    """The graph's format number. Raises GraphFormatError for a graph from a newer
    Boltjar (loading it here could silently drop what this version does not
    know) or with a `format` that is not a whole number."""
    version = graph.get("format", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise GraphFormatError(f"the graph's format {version!r} is not a format number")
    if version > CURRENT_FORMAT:
        raise GraphFormatError(
            f"the graph was saved by a newer Boltjar (format {version}); "
            f"this one reads formats up to {CURRENT_FORMAT}, update Boltjar to open it")
    return version


def migrate(graph: dict) -> dict:
    """The graph in CURRENT_FORMAT, with `format` stamped as its first key. The
    input is never modified. Raises GraphFormatError for a graph this Boltjar
    cannot read (see format_of), or whose shape a step cannot read."""
    version = format_of(graph)
    try:
        out = copy.deepcopy(graph)
        while version < CURRENT_FORMAT:
            out = _STEPS[version](out)
            version += 1
    except (RecursionError, TypeError, AttributeError, KeyError, ValueError) as exc:
        # a graph whose shape no step can read (an opened file can hold anything):
        # refused like any graph this Boltjar cannot read, never a crash
        raise GraphFormatError(f"the graph could not be read: {type(exc).__name__}: {exc}") from exc
    return {"format": CURRENT_FORMAT, **{k: v for k, v in out.items() if k != "format"}}
