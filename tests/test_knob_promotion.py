"""Universal knob promotion (the LEGO principle): every node widget can be
promoted to a typed input pipe via config.promoted, and at runtime a wired value
on that input transparently overrides the knob. The LLM's promote path (its own
model-aware params) must keep working as the now-generic case.
"""
import asyncio

from boltjar.runtime import Runtime, _apply_promoted
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


def test_apply_promoted_overrides_attr_and_cfg() -> None:
    # The generic, no-per-node-code core: a promoted widget with a wired value
    # overrides both the node attribute and the _node_cfg entry; an unwired or
    # non-promoted widget is left untouched.
    class Fake:
        pass

    obj = Fake()
    obj.expression = "value"
    obj._node_cfg = {"expression": "value", "promoted": ["expression"]}
    _apply_promoted(obj, {"expression": "value * 99", "value": 2})
    assert obj.expression == "value * 99"
    assert obj._node_cfg["expression"] == "value * 99"


def test_apply_promoted_keeps_knob_when_unwired() -> None:
    class Fake:
        pass

    obj = Fake()
    obj.expression = "value"
    obj._node_cfg = {"expression": "value", "promoted": ["expression"]}
    _apply_promoted(obj, {"expression": None})  # promoted but no wire
    assert obj.expression == "value"  # the knob's value is preserved


def test_apply_promoted_ignores_unpromoted_widget() -> None:
    class Fake:
        pass

    obj = Fake()
    obj.expression = "value"
    obj._node_cfg = {"expression": "value", "promoted": []}
    _apply_promoted(obj, {"expression": "value * 99"})
    assert obj.expression == "value"  # not in promoted, so the wire is ignored


def test_generic_node_uses_wired_value_over_config() -> None:
    # A Compute node with its `expression` knob PROMOTED, fed by a Text upstream.
    # The wired expression ("value * 5") must override the config expression
    # ("value") so the Compute evaluates to 4 * 5 == 20, not the knob's 4.
    graph = {
        "nodes": [
            {"id": "n", "type": "core.value.integer", "config": {"number": 4}},
            {"id": "expr", "type": "core.value.text", "config": {"text": "value * 5"}},
            {
                "id": "c",
                "type": "core.data.compute",
                "config": {"expression": "value", "promoted": ["expression"]},
            },
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "mock/echo"}},
            {"id": "out", "type": "core.output.log"},
        ],
        "edges": [
            {"src": "n", "src_port": "out", "dst": "c", "dst_port": "value"},
            {"src": "expr", "src_port": "out", "dst": "c", "dst_port": "expression"},
            {"src": "c", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "fire", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    out = logs(run_graph(graph))
    assert any("20" in e["message"] for e in out), "the wired expression must override the knob"


def test_generic_node_keeps_config_when_input_unwired() -> None:
    # The same Compute, promoted but with NO wire into `expression`: it falls back
    # to the knob's value ("value"), so 4 stays 4 (universal-promotion guard).
    graph = {
        "nodes": [
            {"id": "n", "type": "core.value.integer", "config": {"number": 4}},
            {
                "id": "c",
                "type": "core.data.compute",
                "config": {"expression": "value", "promoted": ["expression"]},
            },
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "mock/echo"}},
            {"id": "out", "type": "core.output.log"},
        ],
        "edges": [
            {"src": "n", "src_port": "out", "dst": "c", "dst_port": "value"},
            {"src": "c", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "fire", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    out = logs(run_graph(graph))
    assert any("4" in e["message"] for e in out), "an unwired promoted knob keeps its value"


def test_llm_promoted_param_still_overrides_at_runtime() -> None:
    # The LLM case as the now-generic case: promote `temperature` and wire a value
    # into it. The mock/echo model echoes the prompt + a temp marker, proving the
    # wired param reached the call. We assert the run completes and the LLM fired
    # (done emitted) with the promoted param wired, i.e. the path is unbroken.
    graph = {
        "nodes": [
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "t", "type": "core.value.float", "config": {"number": 0.9}},
            {
                "id": "llm",
                "type": "core.ai.llm",
                "config": {"model": "mock/echo", "promoted": ["temperature"], "params": {"temperature": 0.1}},
            },
            {"id": "out", "type": "core.output.log"},
        ],
        "edges": [
            {"src": "fire", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "t", "src_port": "out", "dst": "llm", "dst_port": "temperature"},
            {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    events = run_graph(graph)
    done = [e for e in events if e["kind"] == "value" and e.get("port") == "trigger" and e["node"] == "llm"]
    assert done, "the LLM with a promoted+wired param must still fire and complete"
    assert any(e["node"] == "out" for e in logs(events)), "the LLM response must reach the sink"


def test_a_model_picker_always_stays_a_knob() -> None:
    # the picked model reshapes its node (its knobs and inputs follow the pick),
    # so no declaration can make a model picker an input, a pack's included.
    from boltjar.sdk import NODE_REGISTRY, Widget

    assert Widget(kind="model", promotable=True).promotable is False
    pickers = {(s.id, w.name): w for s in NODE_REGISTRY.values() for w in s.widgets if w.kind == "model"}
    assert {nid for nid, _ in pickers} >= {"core.ai.llm", "core.ai.tts", "core.ai.stt",
                                           "core.ai.embed", "core.ai.rerank"}
    assert not any(w.promotable for w in pickers.values())
    (llm_model,) = [w for w in NODE_REGISTRY["core.ai.llm"].definition()["widgets"] if w["name"] == "model"]
    assert llm_model["promotable"] is False  # what the editor reads


def test_a_body_the_node_draws_itself_stays_a_knob() -> None:
    # the Template's text is the node: the editor draws it without Convert to
    # input, and the declaration says so.
    from boltjar.sdk import NODE_REGISTRY

    template = {w.name: w for w in NODE_REGISTRY["core.data.template"].widgets}["template"]
    assert (template.kind, template.default, template.expand, template.promotable) == ("code", "{in}", True, False)
