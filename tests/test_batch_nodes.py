"""Tests for the batch primitives: List, For-each, Sync.

These three close the for-each / inference gap. The one that matters most is the
fresh-epoch test: it proves a For-each body re-pulls a volatile value per item
(guards `opens_turn`), so an LLM in the body re-runs instead of returning a
memoized cache for every element.
"""
import asyncio

import pytest

from boltjar.runtime import Runtime
from boltjar.sdk import node, Kind, Port, types, NODE_REGISTRY
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)


def run_graph(graph: dict, seconds: float = 0.5) -> list[dict]:
    events: list[dict] = []
    runtime = Runtime(observer=events.append)
    runtime.build(graph)

    async def drive() -> None:
        await runtime.run()
        await asyncio.sleep(seconds)
        await runtime.stop()

    asyncio.run(drive())
    return events


def logs(events: list[dict]) -> list[dict]:
    return [e for e in events if e["kind"] == "log"]


# --------------------------------------------------------------- List

def test_list_output_is_wireable_into_a_json_input() -> None:
    # Regression: the editor refused List.out (`list`) into a `json` input (e.g.
    # Format List's `list`), though the runtime consumes it fine. Fix mirrors the
    # tool-call fix: `list` is a SUBTYPE of `json`, so a list output satisfies a
    # json input. Directional: a bare `json` is NOT a `list`.
    lst = NODE_REGISTRY["core.data.list"]
    fmt = NODE_REGISTRY["core.data.format_list"]
    out_port = next(p for p in lst.outputs if p.name == "out")
    in_port = next(p for p in fmt.inputs if p.name == "list")
    assert out_port.type == "list"
    assert in_port.type == "json"
    assert types.compatible("list", "json") is True
    # the exact List.out -> Format List.list direction the editor validates on drop.
    assert types.compatible(out_port.type, in_port.type) is True
    # directional: a bare `json` must NOT satisfy a `list` input.
    assert types.compatible("json", "list") is False


def test_list_collects_in_numeric_order_dropping_none() -> None:
    rt = Runtime()
    rt.build({"nodes": [{"id": "ls", "type": "core.data.list", "config": {}}], "edges": []})
    inst = rt.nodes["ls"]
    # item_10 must follow item_9 (numeric, not lexical); a None is dropped.
    out = inst.obj.run(item_0="a", item_1="b", item_2=None, item_9="i", item_10="j")
    assert out == {"out": ["a", "b", "i", "j"]}


def test_list_editor_naming_also_works() -> None:
    # the live editor mints `item0`, `item1` (no underscore); the collector must
    # order those numerically too.
    rt = Runtime()
    rt.build({"nodes": [{"id": "ls", "type": "core.data.list", "config": {}}], "edges": []})
    out = rt.nodes["ls"].obj.run(item0="a", item1="b", item2="c")
    assert out == {"out": ["a", "b", "c"]}


def test_list_is_pulled_end_to_end() -> None:
    # three Texts -> List -> a Preview-style log via Compute(len) to prove the
    # assembled list reaches a puller.
    graph = {
        "nodes": [
            {"id": "a", "type": "core.value.text", "config": {"text": "x"}},
            {"id": "b", "type": "core.value.text", "config": {"text": "y"}},
            {"id": "c", "type": "core.value.text", "config": {"text": "z"}},
            {"id": "ls", "type": "core.data.list", "config": {}},
            {"id": "cmp", "type": "core.data.compute", "config": {"expression": "len(value)"}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "mock/echo"}},
            {"id": "out", "type": "core.output.log"},
        ],
        "edges": [
            {"src": "a", "src_port": "out", "dst": "ls", "dst_port": "item_0"},
            {"src": "b", "src_port": "out", "dst": "ls", "dst_port": "item_1"},
            {"src": "c", "src_port": "out", "dst": "ls", "dst_port": "item_2"},
            {"src": "ls", "src_port": "out", "dst": "cmp", "dst_port": "value"},
            {"src": "cmp", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "fire", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    assert any("3" in e["message"] for e in logs(run_graph(graph))), "List of 3 must reach the puller"


# --------------------------------------------------------------- For-each

def test_for_each_dispenses_in_order_then_after_last() -> None:
    # A 3-item list, a body that loops straight back: the LLM (one clean trigger)
    # fires per item and its `done` event drives For-each `loop`. Assert `each`
    # fired 3x with items in order and `after_last` fired once after the third.
    graph = {
        "nodes": [
            {"id": "lst", "type": "core.data.list", "config": {}},
            {"id": "a", "type": "core.value.text", "config": {"text": "one"}},
            {"id": "b", "type": "core.value.text", "config": {"text": "two"}},
            {"id": "c", "type": "core.value.text", "config": {"text": "three"}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "fe", "type": "core.flow.for_each", "config": {}},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "mock/echo"}},
            {"id": "done", "type": "core.output.log", "config": {"label": "done"}},
        ],
        "edges": [
            {"src": "a", "src_port": "out", "dst": "lst", "dst_port": "item_0"},
            {"src": "b", "src_port": "out", "dst": "lst", "dst_port": "item_1"},
            {"src": "c", "src_port": "out", "dst": "lst", "dst_port": "item_2"},
            {"src": "lst", "src_port": "out", "dst": "fe", "dst_port": "list"},
            {"src": "fire", "src_port": "trigger", "dst": "fe", "dst_port": "trigger"},
            # body: each -> LLM.trigger, item -> LLM.prompt; LLM.done (trigger) -> loop
            {"src": "fe", "src_port": "each", "dst": "llm", "dst_port": "trigger"},
            {"src": "fe", "src_port": "item", "dst": "llm", "dst_port": "prompt"},
            {"src": "llm", "src_port": "trigger", "dst": "fe", "dst_port": "loop"},
            {"src": "fe", "src_port": "after_last", "dst": "done", "dst_port": "in"},
        ],
    }
    events = run_graph(graph)
    # the per-item value emitted on For-each `item`, in dispatch order.
    items = [e["value"] for e in events
             if e["kind"] == "value" and e["node"] == "fe" and e.get("port") == "item"]
    assert items == ["one", "two", "three"], f"each item dispensed in order, got {items}"
    each = [e for e in events
            if e["kind"] == "value" and e["node"] == "fe" and e.get("port") == "each"]
    assert len(each) == 3, f"`each` pulsed once per item, got {len(each)}"
    assert any(e["node"] == "done" for e in logs(events)), "after_last fires when exhausted"


def test_for_each_empty_list_fires_after_last_only() -> None:
    inst_run = _foreach_instance()
    inst_run.obj._fired_port = "trigger"
    out = inst_run.obj.run(list=[])
    assert out == {"after_last": True}, "empty list -> after_last immediately, no each"


def test_for_each_coerces_non_list_inputs() -> None:
    # fresh instances per case: For-each is single-flight, so a re-trigger mid-loop
    # is ignored (a separate test covers that), it does not restart the loop.
    o1 = _foreach_instance().obj
    o1._fired_port = "trigger"
    assert o1.run(list='["a", "b"]') == {"item": "a", "each": True}, "a json string coerces to a list"
    o2 = _foreach_instance().obj
    o2._fired_port = "trigger"
    assert o2.run(list="solo") == {"item": "solo", "each": True}, "a bare string wraps as one item"
    o3 = _foreach_instance().obj
    o3._fired_port = "trigger"
    assert o3.run(list=None) == {"after_last": True}, "None -> empty -> after_last"


def test_for_each_item_emitted_before_each() -> None:
    # ordering contract: the returned dict lists `item` before `each`, so the
    # data latch is set before the body fires on the trigger.
    obj = _foreach_instance().obj
    obj._fired_port = "trigger"
    out = obj.run(list=["x", "y"])
    assert list(out.keys()) == ["item", "each"], "item must precede each in insertion order"


def _foreach_instance():
    rt = Runtime()
    rt.build({"nodes": [{"id": "fe", "type": "core.flow.for_each", "config": {}}], "edges": []})
    return rt.nodes["fe"]


# --------------------------------------------------------------- fresh epoch (the one that matters)

# a volatile pulled source that returns a fresh, monotonically increasing counter
# every time it is pulled (no per-turn memo because volatile=True).
@node(id="test.volatile.counter", name="Counter", kind=Kind.TRANSFORM, category="Test",
      pulled=True, volatile=True, summary="per-pull counter (test only)")
class _Counter:
    outputs = [Port("out", "int")]

    def __init__(self):
        self._n = 0

    def run(self, **_):
        self._n += 1
        return {"out": self._n}


def test_for_each_fresh_epoch_repulls_per_item() -> None:
    # THE regression that matters: a For-each body that pulls a volatile counter
    # must see a DIFFERENT value per item. If `opens_turn` did not open a fresh
    # epoch, the body would fire on the loop's turn and read the same memoized
    # pull for every item. The body is a Compute (pulled, memoized per turn) that
    # pulls the counter and the current item; an LLM (mock/echo) echoes it and
    # loops back.
    graph = {
        "nodes": [
            {"id": "a", "type": "core.value.text", "config": {"text": "A"}},
            {"id": "b", "type": "core.value.text", "config": {"text": "B"}},
            {"id": "c", "type": "core.value.text", "config": {"text": "C"}},
            {"id": "lst", "type": "core.data.list", "config": {}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "fe", "type": "core.flow.for_each", "config": {}},
            {"id": "ctr", "type": "test.volatile.counter", "config": {}},
            {"id": "body", "type": "core.data.compute",
             "config": {"expression": 'str(item) + "#" + str(count)'}},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "mock/echo"}},
            {"id": "log", "type": "core.output.log", "config": {"label": "body"}},
        ],
        "edges": [
            {"src": "a", "src_port": "out", "dst": "lst", "dst_port": "item_0"},
            {"src": "b", "src_port": "out", "dst": "lst", "dst_port": "item_1"},
            {"src": "c", "src_port": "out", "dst": "lst", "dst_port": "item_2"},
            {"src": "lst", "src_port": "out", "dst": "fe", "dst_port": "list"},
            {"src": "fire", "src_port": "trigger", "dst": "fe", "dst_port": "trigger"},
            # body: item + a fresh counter joined by the Compute, fed to the LLM.
            {"src": "fe", "src_port": "item", "dst": "body", "dst_port": "item"},
            {"src": "ctr", "src_port": "out", "dst": "body", "dst_port": "count"},
            {"src": "body", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "fe", "src_port": "each", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "log", "dst_port": "in"},
            # back-edge: the LLM's done releases the next item.
            {"src": "llm", "src_port": "trigger", "dst": "fe", "dst_port": "loop"},
        ],
    }
    msgs = [e["message"] for e in logs(run_graph(graph))]
    body = "\n".join(msgs)
    # each item paired with a DISTINCT counter value (re-pulled per epoch).
    assert "A#1" in body, f"first item, counter=1; got {msgs}"
    assert "B#2" in body, f"second item must re-pull the counter (=2); got {msgs}"
    assert "C#3" in body, f"third item must re-pull again (=3); got {msgs}"


# --------------------------------------------------------------- Sync

def test_sync_barrier_fires_only_when_all_wired_ins_arrive() -> None:
    rt = Runtime()
    rt.build({
        "nodes": [{"id": "sy", "type": "core.flow.sync", "config": {}}],
        "edges": [
            # two wired ins (the live edges the barrier counts).
            {"src": "p", "src_port": "trigger", "dst": "sy", "dst_port": "in_0"},
            {"src": "q", "src_port": "trigger", "dst": "sy", "dst_port": "in_1"},
        ],
    })
    inst = rt.nodes["sy"]
    obj = inst.obj
    obj._ctx = inst.ctx  # the runtime stashes this in _invoke; we call run() directly
    # firing one in: nothing yet (waiting for the other branch).
    obj._fired_port = "in_0"
    assert obj.run() == {}, "one of two ins: keep waiting"
    # a stray re-fire of the SAME port counts once (it is a set): still waiting.
    obj._fired_port = "in_0"
    assert obj.run() == {}, "same port twice still counts once: keep waiting"
    # the second branch arrives: barrier fires once and resets.
    obj._fired_port = "in_1"
    assert obj.run() == {"out": True}, "all wired ins arrived: fire"
    # after reset, a lone fire on one port does not re-emit.
    obj._fired_port = "in_0"
    assert obj.run() == {}, "after reset, one port alone does not re-emit"
    obj._fired_port = "in_1"
    assert obj.run() == {"out": True}, "the other coming again completes the next round"


def test_sync_barrier_real_fire_path() -> None:
    # Drive the barrier through the runtime (mailbox + _fire), not run() directly:
    # two For-each loops over 1-item lists; each loop's after_last feeds a Sync
    # in_*; Sync fires its `out` -> a log exactly once when both finish.
    graph = {
        "nodes": [
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "la", "type": "core.value.text", "config": {"text": "a"}},
            {"id": "lb", "type": "core.value.text", "config": {"text": "b"}},
            {"id": "lsa", "type": "core.data.list", "config": {}},
            {"id": "lsb", "type": "core.data.list", "config": {}},
            {"id": "fea", "type": "core.flow.for_each", "config": {}},
            {"id": "feb", "type": "core.flow.for_each", "config": {}},
            {"id": "sy", "type": "core.flow.sync", "config": {}},
            {"id": "out", "type": "core.output.log", "config": {"label": "both-done"}},
        ],
        "edges": [
            {"src": "la", "src_port": "out", "dst": "lsa", "dst_port": "item_0"},
            {"src": "lb", "src_port": "out", "dst": "lsb", "dst_port": "item_0"},
            {"src": "lsa", "src_port": "out", "dst": "fea", "dst_port": "list"},
            {"src": "lsb", "src_port": "out", "dst": "feb", "dst_port": "list"},
            {"src": "fire", "src_port": "trigger", "dst": "fea", "dst_port": "trigger"},
            {"src": "fire", "src_port": "trigger", "dst": "feb", "dst_port": "trigger"},
            # 1-item loops: each item's `each` loops straight back to release the next
            # (exhausts immediately after one), and after_last syncs.
            {"src": "fea", "src_port": "each", "dst": "fea", "dst_port": "loop"},
            {"src": "feb", "src_port": "each", "dst": "feb", "dst_port": "loop"},
            {"src": "fea", "src_port": "after_last", "dst": "sy", "dst_port": "in_0"},
            {"src": "feb", "src_port": "after_last", "dst": "sy", "dst_port": "in_1"},
            {"src": "sy", "src_port": "out", "dst": "out", "dst_port": "in"},
        ],
    }
    fired = [e for e in logs(run_graph(graph)) if e["node"] == "out"]
    assert len(fired) == 1, f"Sync fires once when both loops finish, got {len(fired)}"


def test_sync_no_wired_ins_never_fires() -> None:
    rt = Runtime()
    rt.build({"nodes": [{"id": "sy", "type": "core.flow.sync", "config": {}}], "edges": []})
    inst = rt.nodes["sy"]
    obj = inst.obj
    obj._ctx = inst.ctx
    obj._fired_port = "in_0"
    assert obj.run() == {}, "no wired ins (total 0): never fire"


# --------------------------------------------------------------- Wait

def test_wait_relays_trigger() -> None:
    # 1:1 relay: a Wait re-emits its trigger. amount 0 -> no sleep, immediate relay.
    rt = Runtime()
    rt.build({"nodes": [{"id": "w", "type": "core.flow.wait",
                         "config": {"amount": 0, "unit": "Seconds"}}], "edges": []})
    obj = rt.nodes["w"].obj
    assert asyncio.run(obj.run(trigger=True)) == {"trigger": True}


def test_wait_unit_conversion() -> None:
    rt = Runtime()
    rt.build({"nodes": [{"id": "w", "type": "core.flow.wait", "config": {}}], "edges": []})
    factors = rt.nodes["w"].obj._UNIT_SECONDS
    assert factors == {"Seconds": 1, "Minutes": 60, "Hours": 3600}


def test_wait_paces_for_each_loop() -> None:
    # Wait sits IN the loop body (each -> Wait -> loop) and threads it: the loop
    # still dispenses every item in order and ends on after_last. amount 0 keeps
    # the test fast (no real sleep), proving the relay wiring, not the timing.
    graph = {
        "nodes": [
            {"id": "a", "type": "core.value.text", "config": {"text": "one"}},
            {"id": "b", "type": "core.value.text", "config": {"text": "two"}},
            {"id": "c", "type": "core.value.text", "config": {"text": "three"}},
            {"id": "lst", "type": "core.data.list", "config": {}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "fe", "type": "core.flow.for_each", "config": {}},
            {"id": "w", "type": "core.flow.wait", "config": {"amount": 0, "unit": "Seconds"}},
            {"id": "done", "type": "core.output.log", "config": {"label": "done"}},
        ],
        "edges": [
            {"src": "a", "src_port": "out", "dst": "lst", "dst_port": "item_0"},
            {"src": "b", "src_port": "out", "dst": "lst", "dst_port": "item_1"},
            {"src": "c", "src_port": "out", "dst": "lst", "dst_port": "item_2"},
            {"src": "lst", "src_port": "out", "dst": "fe", "dst_port": "list"},
            {"src": "fire", "src_port": "trigger", "dst": "fe", "dst_port": "trigger"},
            # the body is just the Wait: each -> Wait -> loop releases the next.
            {"src": "fe", "src_port": "each", "dst": "w", "dst_port": "trigger"},
            {"src": "w", "src_port": "trigger", "dst": "fe", "dst_port": "loop"},
            {"src": "fe", "src_port": "after_last", "dst": "done", "dst_port": "in"},
        ],
    }
    events = run_graph(graph)
    items = [e["value"] for e in events
             if e["kind"] == "value" and e["node"] == "fe" and e.get("port") == "item"]
    assert items == ["one", "two", "three"], f"Wait paces the loop in order, got {items}"
    assert any(e["node"] == "done" for e in logs(events)), "after_last still fires"


def test_for_each_ignores_a_re_entrant_trigger_mid_loop():
    # a second `trigger` while a loop is in flight must not clobber the cursor/items.
    from boltjar.sdk import NODE_REGISTRY
    fe = NODE_REGISTRY["core.flow.for_each"].cls()
    fe._fired_port = "trigger"
    assert fe.run(list=["a", "b", "c"]) == {"item": "a", "each": True}
    fe._fired_port = "trigger"
    assert fe.run(list=["x", "y"]) == {}          # re-entrant start ignored
    fe._fired_port = "loop"
    assert fe.run() == {"item": "b", "each": True}  # original loop continues in order
    fe._fired_port = "loop"
    assert fe.run() == {"item": "c", "each": True}
    fe._fired_port = "loop"
    assert fe.run() == {"after_last": True}
    fe._fired_port = "trigger"                       # after after_last, a new start works
    assert fe.run(list=["z"]) == {"item": "z", "each": True}
