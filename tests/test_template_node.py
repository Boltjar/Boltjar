"""Template: runs when its trigger fires, like every node with a trigger.

`trigger` must be wired (explicit, visible sequencing: Chat History ->
Template -> LLM). A fire pulls the tags, assembles, and only then passes the
trigger on: `out` first, then `trigger`, so a consumer fired by that trigger
reads the finished text. A node that reads `out` without a fire gets the text
of the last fire; it is never assembled again on a pull. `trigger` is a
declared port, never a `{tag}`: a `{trigger}` in the string stays literal text.

Real Runtime graphs; an LLM with no model runs the offline mock (no network).
"""
import asyncio

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


def test_a_pull_reads_the_last_fire_and_never_assembles_again() -> None:
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "{msg}"}},
        ],
        "edges": [{"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "msg"}],
    })
    assert rt._pull_output("tpl", "out", rt.new_turn()) is None, "nothing fired it yet"
    asyncio.run(rt._fire(rt.nodes["tpl"], "trigger", True, rt.new_turn()))
    assert rt._pull_output("tpl", "out", rt.new_turn()) == "hi"
    # its tag changes, but only a fire assembles the text again.
    rt.nodes["txt"].obj.text = "bye"
    assert rt._pull_output("tpl", "out", rt.new_turn()) == "hi"
    asyncio.run(rt._fire(rt.nodes["tpl"], "trigger", True, rt.new_turn()))
    assert rt._pull_output("tpl", "out", rt.new_turn()) == "bye"


def test_every_fire_passes_the_trigger_on() -> None:
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "{msg}"}},
        ],
        "edges": [{"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "msg"}],
    })
    fired = asyncio.run(rt._invoke(rt.nodes["tpl"], "trigger", True,
                                   {"trigger": True, "tag": None, "msg": "hi"}))
    assert list(fired.items()) == [("out", "hi"), ("trigger", True)]


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
