"""Tests for the push/pull runtime: triggered work nodes pulling their data."""
import asyncio

import pytest

from boltjar.runtime import Runtime
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


def test_llm_pulls_assembled_prompt() -> None:
    # Manual fires the LLM; the LLM pulls the Template, which pulls the Texts.
    graph = {
        "nodes": [
            {"id": "sys", "type": "core.value.text", "config": {"text": "You are a helpful assistant."}},
            {"id": "usr", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "{system} :: {user}"}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "llm", "type": "core.ai.llm", "config": {"backend": "mock"}},
            {"id": "out", "type": "core.output.log", "config": {"label": "assistant"}},
        ],
        "edges": [
            {"src": "sys", "src_port": "out", "dst": "tpl", "dst_port": "system"},
            {"src": "usr", "src_port": "out", "dst": "tpl", "dst_port": "user"},
            {"src": "tpl", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "fire", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    out = logs(run_graph(graph))
    assert out, "the output should fire"
    assert "You are a helpful assistant. :: hi" in out[0]["message"], \
        "the assembled prompt must reach the LLM"


def test_template_none_input_is_blank_unknown_tag_is_literal() -> None:
    """A wired-or-declared input that resolves to None substitutes an EMPTY string
    (missing = blank, as n8n/ComfyUI do), NEVER the literal {tag}: a None must not
    leak `{bad}` into an assembled LLM prompt (`bad=[{bad}]` once reached a real
    prompt). An ENTIRELY UNKNOWN tag (no wired/declared source, so absent from
    the pulled inputs) keeps its literal `{tag}` untouched, documenting the two
    distinct cases the fix draws a line between.
    """
    from boltjar.sdk import NODE_REGISTRY
    tpl = NODE_REGISTRY["core.data.template"].cls()
    # the runtime sets the resolved template string on the instance (build() does
    # setattr(obj, "template", cfg value)); do the same for this unit-level call.
    tpl.template = "a=[{a}] bad=[{bad}] unknown=[{nope}]"
    tpl._node_cfg = {}
    # `a` -> a value; `bad` -> a wired source that resolved to None; `nope` -> not
    # a key at all (no source), so it must be left as the literal `{nope}`.
    out = tpl.run(a="hi", bad=None)["out"]
    assert out == "a=[hi] bad=[] unknown=[{nope}]"


def test_compute_is_pulled() -> None:
    graph = {
        "nodes": [
            {"id": "n", "type": "core.value.integer", "config": {"number": 4}},
            {"id": "c", "type": "core.data.compute", "config": {"expression": "value * 10"}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "llm", "type": "core.ai.llm", "config": {"backend": "mock"}},
            {"id": "out", "type": "core.output.log"},
        ],
        "edges": [
            {"src": "n", "src_port": "out", "dst": "c", "dst_port": "value"},
            {"src": "c", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "fire", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    assert any("40" in e["message"] for e in logs(run_graph(graph)))


def test_compute_sandbox_blocks_escape() -> None:
    graph = {
        "nodes": [
            {"id": "n", "type": "core.value.integer", "config": {"number": 1}},
            {"id": "c", "type": "core.data.compute", "config": {"expression": "().__class__.__bases__"}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "llm", "type": "core.ai.llm", "config": {"backend": "mock"}},
            {"id": "out", "type": "core.output.log"},
        ],
        "edges": [
            {"src": "n", "src_port": "out", "dst": "c", "dst_port": "value"},
            {"src": "c", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "fire", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    assert any("error" in e["message"] for e in logs(run_graph(graph)))


def test_logic_routes_on_a_pushed_value() -> None:
    graph = {
        "nodes": [
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "g", "type": "core.logic.condition", "config": {"expression": "value > 0"}},
            {"id": "yes", "type": "core.output.log", "config": {"label": "yes"}},
            {"id": "no", "type": "core.output.log", "config": {"label": "no"}},
        ],
        "edges": [
            {"src": "fire", "src_port": "trigger", "dst": "g", "dst_port": "value"},
            {"src": "g", "src_port": "true", "dst": "yes", "dst_port": "in"},
            {"src": "g", "src_port": "false", "dst": "no", "dst_port": "in"},
        ],
    }
    out = logs(run_graph(graph))
    assert any(e["node"] == "yes" for e in out)
    assert not any(e["node"] == "no" for e in out)


def test_fired_nodes_emit_done() -> None:
    # A fired node emits a `done` event on completion so it can be sequenced:
    # one LLM's `done` triggers the next LLM's `trigger`.
    graph = {
        "nodes": [
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "a", "type": "core.ai.llm", "config": {"model": "mock/echo"}},
            {"id": "b", "type": "core.ai.llm", "config": {"model": "mock/echo"}},
            {"id": "out", "type": "core.output.log", "config": {"label": "second"}},
        ],
        "edges": [
            {"src": "fire", "src_port": "trigger", "dst": "a", "dst_port": "trigger"},
            {"src": "a", "src_port": "trigger", "dst": "b", "dst_port": "trigger"},
            {"src": "b", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    events = run_graph(graph)
    done = [e for e in events if e["kind"] == "value" and e.get("port") == "trigger"]
    assert any(e["node"] == "a" for e in done), "the first LLM must emit done"
    # the second LLM only fires if `a.done` reached it, so its output log proves
    # the done -> trigger sequencing worked.
    assert any(e["node"] == "out" for e in logs(events)), "done must trigger the next node"


def test_output_sink_emits_done() -> None:
    # An OUTPUT sink (Log) emits done so a graph can chain after delivery.
    graph = {
        "nodes": [
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "log", "type": "core.output.log", "config": {"label": "first"}},
            {"id": "after", "type": "core.output.log", "config": {"label": "after"}},
        ],
        "edges": [
            {"src": "fire", "src_port": "trigger", "dst": "log", "dst_port": "in"},
            {"src": "log", "src_port": "trigger", "dst": "after", "dst_port": "in"},
        ],
    }
    out = logs(run_graph(graph))
    assert any(e["node"] == "after" for e in out), "the sink's done must chain to the next node"


def test_output_delivers_the_triggering_port_value() -> None:
    # _invoke must hand deliver() the value on the ACTUAL triggering port, not the
    # first declared input's latched value. ChatOutput declares `trigger` first but
    # fires on `reply`; delivering the latched trigger event would be the bug.
    rt = Runtime()
    rt.build({
        "nodes": [{"id": "chat", "type": "core.output.chat", "config": {}}],
        "edges": [],
    })
    inst = rt.nodes["chat"]
    seen: list = []
    inst.obj.deliver = lambda value, ctx, inputs=None: seen.append(value) or {"trigger": True}
    # simulate firing on `reply` with the reply text, while `trigger` is latched.
    inputs = {"trigger": "<event>", "user": "hi", "reply": "the reply text"}
    asyncio.run(rt._invoke(inst, "reply", "the reply text", inputs))
    assert seen == ["the reply text"], "deliver must receive the reply, not the trigger event"


def test_preview_passthrough_reemits_in_on_out() -> None:
    # Preview sits mid-flow: it re-emits its `in` value on `out` even when fired
    # by a separate `trigger`, so in -> preview -> out passes the value through.
    rt = Runtime()
    rt.build({"nodes": [{"id": "pv", "type": "core.output.preview", "config": {}}], "edges": []})
    inst = rt.nodes["pv"]
    # fired by the `trigger` event, with `in` latched to a value
    out = asyncio.run(rt._invoke(inst, "trigger", True, {"in": "hello", "trigger": True}))
    assert out == {"out": "hello", "trigger": True}


def test_disabled_passthrough_bypass_rewires() -> None:
    # A disabled node with a `bypass` shape (Preview: in->out) is wired through:
    # its source feeds its consumers directly, so the flow survives the bypass.
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "iv", "type": "core.trigger.interval", "config": {"seconds": 0.05}},
            {"id": "pv", "type": "core.output.preview", "config": {}, "disabled": True},
            {"id": "log", "type": "core.output.log", "config": {}},
        ],
        "edges": [
            {"src": "iv", "src_port": "trigger", "dst": "pv", "dst_port": "in"},
            {"src": "pv", "src_port": "out", "dst": "log", "dst_port": "in"},
        ],
    })
    assert "pv" not in rt.nodes, "disabled node is not instantiated"
    assert rt.edges_into.get(("log", "in")) == ("iv", "trigger"), "bypass rewired source -> consumer"


def test_disabled_non_passthrough_drops_edges() -> None:
    # A disabled plain sink (Log has no bypass shape) just loses its edges.
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "iv", "type": "core.trigger.interval", "config": {"seconds": 0.05}},
            {"id": "log", "type": "core.output.log", "config": {}, "disabled": True},
        ],
        "edges": [{"src": "iv", "src_port": "trigger", "dst": "log", "dst_port": "in"}],
    })
    assert rt.edges_into == {}, "disabled non-passthrough node drops its edges"


def test_manual_fire_pushes_another_event() -> None:
    # Manual kicks once at startup; pressing Fire (fire_manual) pushes another event,
    # so the downstream fires more than once.
    events: list = []
    rt = Runtime(observer=events.append)
    rt.build({
        "nodes": [
            {"id": "m", "type": "core.trigger.manual", "config": {}},
            {"id": "log", "type": "core.output.log", "config": {}},
        ],
        "edges": [{"src": "m", "src_port": "trigger", "dst": "log", "dst_port": "in"}],
    })

    async def drive() -> None:
        await rt.run()
        await asyncio.sleep(0.05)
        first = sum(1 for e in events if e.get("kind") == "value" and e["node"] == "log")
        rt.fire_manual("m")
        await asyncio.sleep(0.1)
        await rt.stop()
        after = sum(1 for e in events if e.get("kind") == "value" and e["node"] == "log")
        assert first >= 1, "Manual kicks once at startup"
        assert after > first, "Fire pushes another event"

    asyncio.run(drive())


def test_wireless_flattens_to_a_direct_edge() -> None:
    # Wireless In/Out are a virtual wire: the runtime never instantiates them; each
    # channel resolves to a direct source -> consumer edge at build.
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "iv", "type": "core.trigger.interval", "config": {"seconds": 0.05}},
            {"id": "win", "type": "core.flow.wireless_in", "config": {"channel": "3"}},
            {"id": "wout", "type": "core.flow.wireless_out", "config": {"channel": "3"}},
            {"id": "log", "type": "core.output.log", "config": {}},
        ],
        "edges": [
            {"src": "iv", "src_port": "trigger", "dst": "win", "dst_port": "iv"},
            {"src": "wout", "src_port": "iv", "dst": "log", "dst_port": "in"},
        ],
    })
    assert "win" not in rt.nodes and "wout" not in rt.nodes, "wireless nodes are not instantiated"
    assert rt.edges_into.get(("log", "in")) == ("iv", "trigger"), "channel 3 flattened to a direct edge"


def test_wireless_unmatched_channel_drops_edge() -> None:
    # a Wireless Out with no matching In on its channel just produces nothing.
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "wout", "type": "core.flow.wireless_out", "config": {"channel": "7"}},
            {"id": "log", "type": "core.output.log", "config": {}},
        ],
        "edges": [{"src": "wout", "src_port": "x", "dst": "log", "dst_port": "in"}],
    })
    assert rt.edges_into == {}, "unmatched wireless channel drops the edge"


def test_unknown_node_type_raises() -> None:
    rt = Runtime()
    with pytest.raises(ValueError):
        rt.build({"nodes": [{"id": "x", "type": "core.nope"}], "edges": []})
