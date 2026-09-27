"""A run started in a TurnScope keeps it: every turn opened while one of its
turns fires joins it, a node reads the scope's origin, and a value a source
emitted inside the scope wins over one a concurrent run emitted later. A Queue
hands each item on in the scope it arrived in."""
from __future__ import annotations

import asyncio

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY, Kind, Port, node

SEEN: list[tuple] = []


@pytest.fixture(autouse=True)
def probe():
    @node(id="test.scope.probe", name="Probe", kind=Kind.OUTPUT, category="Inspect")
    class Probe:
        inputs = [Port("trigger", "event", trigger=True), Port("v", "any", optional=True)]
        outputs = [Port("trigger", "event")]

        def deliver(self, value, ctx, inputs=None):
            SEEN.append((ctx.origin, (inputs or {}).get("v")))
            return {"trigger": True}

    SEEN.clear()
    yield
    NODE_REGISTRY.pop("test.scope.probe", None)


def _edge(src, sp, dst, dp):
    return {"src": src, "src_port": sp, "dst": dst, "dst_port": dp}


def _send(rt: Runtime, text: str, origin) -> None:
    """A chat message fired the way the webhook route fires a held call: its
    first turn opened in a scope of its own (or in none)."""
    scope = rt.open_scope(origin) if origin is not None else None
    turn = rt.new_turn(scope=scope)
    rt.emit("chat", "text", text, turn)
    rt.emit("chat", "trigger", 1, turn)


def _run(graph: dict, sends: list[tuple[str, object]], settle: float = 0.3) -> None:
    async def drive():
        rt = Runtime()
        rt.build(graph)
        await rt.run()
        for text, origin in sends:
            _send(rt, text, origin)
        await asyncio.sleep(settle)
        await rt.stop()
    asyncio.run(drive())


_DELAYED = {"nodes": [{"id": "chat", "type": "core.trigger.chat", "config": {}},
                      {"id": "wait", "type": "core.flow.wait", "config": {"amount": 0.02}},
                      {"id": "probe", "type": "test.scope.probe", "config": {}}],
            "edges": [_edge("chat", "trigger", "wait", "trigger"),
                      _edge("wait", "trigger", "probe", "trigger"),
                      _edge("chat", "text", "probe", "v")]}


def test_each_run_reads_its_own_value_and_origin():
    _run(_DELAYED, [("a", "A"), ("b", "B")])
    assert SEEN == [("A", "a"), ("B", "b")]


def test_without_a_scope_a_reader_gets_the_latest_value():
    _run(_DELAYED, [("a", None), ("b", None)])
    assert SEEN == [(None, "b"), (None, "b")]


def test_a_queue_releases_each_item_in_the_scope_it_arrived_in():
    graph = {"nodes": [{"id": "chat", "type": "core.trigger.chat", "config": {}},
                       {"id": "queue", "type": "core.flow.queue", "config": {}},
                       {"id": "wait", "type": "core.flow.wait", "config": {"amount": 0.02}},
                       {"id": "probe", "type": "test.scope.probe", "config": {}}],
             "edges": [_edge("chat", "trigger", "queue", "chat"),
                       _edge("queue", "out", "wait", "trigger"),
                       _edge("wait", "trigger", "probe", "trigger"),
                       _edge("chat", "text", "probe", "v"),
                       _edge("probe", "trigger", "queue", "ack")]}
    _run(graph, [("a", "A"), ("b", "B"), ("c", "C")])
    assert SEEN == [("A", "a"), ("B", "b"), ("C", "c")]


def test_turns_opened_inside_a_fire_join_its_scope():
    graph = {"nodes": [{"id": "chat", "type": "core.trigger.chat", "config": {}},
                       {"id": "each", "type": "core.flow.for_each", "config": {}},
                       {"id": "probe", "type": "test.scope.probe", "config": {}}],
             "edges": [_edge("chat", "trigger", "each", "trigger"),
                       _edge("chat", "text", "each", "list"),
                       _edge("each", "each", "probe", "trigger"),
                       _edge("each", "item", "probe", "v"),
                       _edge("probe", "trigger", "each", "loop")]}
    _run(graph, [('["x", "y"]', "A")])
    assert SEEN == [("A", "x"), ("A", "y")]


def test_a_closed_scope_drops_its_values():
    async def drive():
        rt = Runtime()
        rt.build(_DELAYED)
        await rt.run()
        scope = rt.open_scope("A")
        turn = rt.new_turn(scope=scope)
        rt.emit("chat", "text", "a", turn)
        assert rt._pull_output("chat", "text", turn) == "a"
        rt.emit("chat", "text", "later", rt.new_turn(scope=None))
        assert rt._pull_output("chat", "text", turn) == "a"
        scope.close()
        assert scope.values == {}
        assert rt._pull_output("chat", "text", turn) == "later"
        await rt.stop()
    asyncio.run(drive())
