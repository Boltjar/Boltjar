"""Template: its trigger fires it, and a read always gets the current turn's text.

`trigger` must be wired (explicit, visible sequencing: Chat History ->
Template -> LLM). A fire pulls the tags, assembles, and only then passes the
trigger on: `out` first, then `trigger`, so a consumer fired by that trigger
reads the finished text. The Template is also pulled: a node that reads `out`
gets the fire's result when the fire ran first in the turn, else the text
assembled at the read, so a node fired alongside the Template by the same
source never answers the previous turn, whichever wire comes first. Only a
fire passes the trigger on. `trigger` is a declared port, never a `{tag}`: a
`{trigger}` in the string stays literal text.

Real Runtime graphs; an LLM with no model runs the offline mock (no network).
"""
import asyncio

import pytest

from boltjar.runtime import Runtime
from boltjar.server import validate_graph
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)


def _run(graph: dict, *messages: str, seconds: float = 0.3) -> list[dict]:
    """Build + power the graph, send each chat message as its own turn (waiting
    for the turn to settle), then stop. Returns every observer event."""
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        for text in messages:
            rt.send_chat("chat", text)
            await asyncio.sleep(seconds)
        await asyncio.sleep(seconds)
        await rt.stop()

    asyncio.run(drive())
    return events


def _logs(events: list[dict], node: str) -> list[str]:
    return [e["message"] for e in events if e["kind"] == "log" and e["node"] == node]


def _values(events: list[dict], node: str, port: str) -> list:
    return [e["value"] for e in events
            if e["kind"] == "value" and e["node"] == node and e["port"] == port]


# Chat Input drives the Template; the Template assembles "{persona} :: {msg}".
_NODES = [
    {"id": "chat", "type": "core.trigger.chat"},
    {"id": "persona", "type": "core.value.text", "config": {"text": "Bot"}},
    {"id": "tpl", "type": "core.data.template", "config": {"template": "{persona} :: {msg}"}},
]
_EDGES = [
    {"src": "persona", "src_port": "out", "dst": "tpl", "dst_port": "persona"},
    {"src": "chat", "src_port": "text", "dst": "tpl", "dst_port": "msg"},
    {"src": "chat", "src_port": "trigger", "dst": "tpl", "dst_port": "trigger"},
]


def test_trigger_fire_emits_out_then_trigger() -> None:
    graph = {
        "nodes": _NODES + [{"id": "done", "type": "core.output.log", "config": {"label": "done"}}],
        "edges": _EDGES + [{"src": "tpl", "src_port": "trigger", "dst": "done", "dst_port": "in"}],
    }
    events = _run(graph, "hi")
    emitted = [(e["port"], e["value"]) for e in events if e["kind"] == "value" and e["node"] == "tpl"]
    assert emitted == [("out", "Bot :: hi"), ("trigger", "True")], "assembled text first, then the trigger"
    assert _logs(events, "done") == ["done: True"]


def test_llm_fired_by_the_template_reads_the_assembled_prompt_once_per_turn() -> None:
    graph = {
        "nodes": _NODES + [
            {"id": "llm", "type": "core.ai.llm", "config": {}},
            {"id": "said", "type": "core.output.log", "config": {"label": "said"}},
        ],
        "edges": _EDGES + [
            {"src": "tpl", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "tpl", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "said", "dst_port": "in"},
        ],
    }
    events = _run(graph, "hi", "again")
    assert _logs(events, "said") == ["said: [mock] Bot :: hi", "said: [mock] Bot :: again"], \
        "the LLM fires once per turn, on the prompt the Template assembled"
    # the LLM's pull reads the Template's fired result for that turn; it is never
    # assembled a second time (a volatile tag such as a clock could differ).
    assert _values(events, "tpl", "out") == ["Bot :: hi", "Bot :: again"]


def _txt_tpl() -> Runtime:
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "{msg}"}},
        ],
        "edges": [{"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "msg"}],
    })
    return rt


def test_a_read_gets_the_current_text_and_the_fire_of_its_turn() -> None:
    rt = _txt_tpl()
    assert rt._pull_output("tpl", "out", rt.new_turn()) == "hi", "read before any fire"
    rt.nodes["txt"].obj.text = "bye"
    assert rt._pull_output("tpl", "out", rt.new_turn()) == "bye", "a read never gets an old turn"
    # a fire memoizes its result for its turn: a read in that turn gets exactly
    # what the fire emitted, never a second assembly.
    turn = rt.new_turn()
    asyncio.run(rt._fire(rt.nodes["tpl"], "trigger", True, turn))
    rt.nodes["txt"].obj.text = "later"
    assert rt._pull_output("tpl", "out", turn) == "bye"
    assert rt._pull_output("tpl", "out", rt.new_turn()) == "later"


def test_only_a_fire_passes_the_trigger_on() -> None:
    rt = _txt_tpl()
    ins = {"trigger": True, "tag": None, "msg": "hi"}
    fired = asyncio.run(rt._invoke(rt.nodes["tpl"], "trigger", True, dict(ins)))
    assert list(fired.items()) == [("out", "hi"), ("trigger", True)]
    events: list[dict] = []
    rt._observer = events.append
    assert rt._pull_output("tpl", "out", rt.new_turn()) == "hi"
    assert [(e["port"], e["value"]) for e in events
            if e["kind"] == "value" and e["node"] == "tpl"] == [("out", "hi")], \
        "a read shows the text and passes no trigger on"


def _fan_out(llm_first: bool) -> dict:
    """Chat fires the LLM AND the Template, and the LLM reads the Template's
    text: the shape an older graph gets when its Chat already fired the LLM and
    the Template's trigger is wired from the same Chat. `llm_first` puts the
    LLM's wire first in the edge list, so the LLM runs before the Template."""
    to_llm = {"src": "chat", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"}
    to_tpl = {"src": "chat", "src_port": "trigger", "dst": "tpl", "dst_port": "trigger"}
    return {
        "nodes": [
            {"id": "chat", "type": "core.trigger.chat"},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "Q: {msg}"}},
            {"id": "llm", "type": "core.ai.llm", "config": {}},
            {"id": "said", "type": "core.output.log", "config": {"label": "said"}},
        ],
        "edges": ([to_llm, to_tpl] if llm_first else [to_tpl, to_llm]) + [
            {"src": "chat", "src_port": "text", "dst": "tpl", "dst_port": "msg"},
            {"src": "tpl", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "llm", "src_port": "response", "dst": "said", "dst_port": "in"},
        ],
    }


@pytest.mark.parametrize("llm_first", [True, False], ids=["llm-wire-first", "template-wire-first"])
def test_a_node_fired_alongside_the_template_answers_the_current_message(llm_first) -> None:
    graph = _fan_out(llm_first)
    assert validate_graph(graph) == []
    events = _run(graph, "one", "two", "three")
    assert _logs(events, "said") == ["said: [mock] Q: one", "said: [mock] Q: two",
                                     "said: [mock] Q: three"], "each reply answers its own message"
    fires = [e["node"] for e in events if e["kind"] == "node_status" and e["status"] == "running"]
    assert {n: fires.count(n) for n in ("tpl", "llm", "said")} == {"tpl": 3, "llm": 3, "said": 3}
    assert _values(events, "tpl", "trigger") == ["True"] * 3, "one trigger per send, from the fire"


def test_a_template_read_by_a_template_fired_alongside_it_is_current() -> None:
    # Chat fires both Templates; the outer one reads the inner one's text.
    graph = {
        "nodes": [
            {"id": "chat", "type": "core.trigger.chat"},
            {"id": "inner", "type": "core.data.template", "config": {"template": "<{msg}>"}},
            {"id": "outer", "type": "core.data.template", "config": {"template": "Q: {inner}"}},
            {"id": "llm", "type": "core.ai.llm", "config": {}},
            {"id": "said", "type": "core.output.log", "config": {"label": "said"}},
        ],
        "edges": [
            {"src": "chat", "src_port": "trigger", "dst": "outer", "dst_port": "trigger"},
            {"src": "chat", "src_port": "trigger", "dst": "inner", "dst_port": "trigger"},
            {"src": "chat", "src_port": "text", "dst": "inner", "dst_port": "msg"},
            {"src": "inner", "src_port": "out", "dst": "outer", "dst_port": "inner"},
            {"src": "outer", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "outer", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "llm", "src_port": "response", "dst": "said", "dst_port": "in"},
        ],
    }
    assert validate_graph(graph) == []
    events = _run(graph, "one", "two")
    assert _logs(events, "said") == ["said: [mock] Q: <one>", "said: [mock] Q: <two>"]
    assert _values(events, "outer", "out") == ["Q: <one>", "Q: <two>"]


def test_a_for_each_body_fanned_out_to_template_and_llm_answers_every_item() -> None:
    # each item fires the LLM and the Template together (the LLM's wire first):
    # the LLM reads the Template's text for the current item, never the last one.
    graph = {
        "nodes": [
            {"id": "go", "type": "core.trigger.manual"},
            {"id": "lst", "type": "core.value.text", "config": {"text": '["a","b","c"]'}},
            {"id": "fe", "type": "core.flow.for_each"},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "item={item}"}},
            {"id": "llm", "type": "core.ai.llm", "config": {}},
            {"id": "said", "type": "core.output.log", "config": {"label": "said"}},
        ],
        "edges": [
            {"src": "go", "src_port": "trigger", "dst": "fe", "dst_port": "trigger"},
            {"src": "lst", "src_port": "out", "dst": "fe", "dst_port": "list"},
            {"src": "fe", "src_port": "each", "dst": "llm", "dst_port": "trigger"},
            {"src": "fe", "src_port": "each", "dst": "tpl", "dst_port": "trigger"},
            {"src": "fe", "src_port": "item", "dst": "tpl", "dst_port": "item"},
            {"src": "tpl", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "llm", "src_port": "trigger", "dst": "fe", "dst_port": "loop"},
            {"src": "llm", "src_port": "response", "dst": "said", "dst_port": "in"},
        ],
    }
    assert validate_graph(graph) == []
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        rt.fire_manual("go")
        await asyncio.sleep(0.5)
        await rt.stop()

    asyncio.run(drive())
    assert _logs(events, "said") == ["said: [mock] item=a", "said: [mock] item=b",
                                     "said: [mock] item=c"]


def test_trigger_is_a_port_not_a_tag() -> None:
    # the chat's trigger carries the typed text as its payload; if `trigger` were
    # treated as a tag, `{trigger}` would become that text. It stays literal.
    graph = {
        "nodes": [
            {"id": "chat", "type": "core.trigger.chat"},
            {"id": "tpl", "type": "core.data.template",
             "config": {"template": "{trigger} stays :: {msg}"}},
        ],
        "edges": [
            {"src": "chat", "src_port": "text", "dst": "tpl", "dst_port": "msg"},
            {"src": "chat", "src_port": "trigger", "dst": "tpl", "dst_port": "trigger"},
        ],
    }
    events = _run(graph, "hi")
    assert _values(events, "tpl", "out") == ["{trigger} stays :: hi"]


def test_validate_accepts_the_template_trigger_wires() -> None:
    graph = {
        "nodes": [
            {"id": "m", "type": "core.trigger.manual"},
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "{msg}"}},
            {"id": "llm", "type": "core.ai.llm", "config": {}},
        ],
        "edges": [
            {"src": "m", "src_port": "trigger", "dst": "tpl", "dst_port": "trigger"},
            {"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "msg"},
            {"src": "tpl", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "tpl", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
        ],
    }
    assert validate_graph(graph) == []
    # `trigger` is an event port, not a `{trigger}` tag: a text wire cannot land on it.
    graph["edges"][0] = {"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "trigger"}
    assert [p["kind"] for p in validate_graph(graph)] == ["type-mismatch"]
    # and a Template whose trigger nothing fires can never run.
    del graph["edges"][0]
    assert [(p["node"], p["kind"]) for p in validate_graph(graph)] == [("tpl", "missing-input")]
