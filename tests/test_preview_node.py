"""Preview is a transparent tap: one upstream turn relays as ONE downstream fire.

A common chat graph wires one source into the Preview (`llm.trigger ->
preview.trigger`, `llm.response -> preview.in`) and drives the next node from
it (`preview.trigger -> tts.trigger`, `preview.out -> tts.text`). The Preview
once fired on its value as well as on its trigger and relayed both, so the TTS
spoke (and billed) every reply twice. A data wire never fires a node: only
`trigger` fires the Preview, which reads `in` then (pushed or pulled), shows it
once, re-emits it on `out` and forwards exactly one `trigger`. So `out` and
`trigger` each leave once per turn, whatever is wired to them.

Real Runtime graphs; an LLM with no model runs the offline mock (no network).
"""
import asyncio

from boltjar.runtime import Runtime
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


def _log(node_id: str, label: str) -> dict:
    return {"id": node_id, "type": "core.output.log", "config": {"label": label}}


# the common shape: one LLM feeds the Preview its value and its done event.
_CHAT_LLM = [
    {"id": "chat", "type": "core.trigger.chat"},
    {"id": "llm", "type": "core.ai.llm", "config": {}},
    {"id": "pv", "type": "core.output.preview", "config": {}},
]
_CHAT_LLM_EDGES = [
    {"src": "chat", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
    {"src": "chat", "src_port": "text", "dst": "llm", "dst_port": "prompt"},
]


def test_value_then_done_source_relays_one_downstream_fire_per_turn() -> None:
    # the next node (a mock LLM standing in for the TTS) is fired by the Preview's
    # trigger and reads the Preview's `out`: it must run ONCE per upstream turn,
    # on the relayed reply.
    graph = {
        "nodes": _CHAT_LLM + [
            {"id": "next", "type": "core.ai.llm", "config": {}},
            _log("said", "said"),
        ],
        "edges": _CHAT_LLM_EDGES + [
            {"src": "llm", "src_port": "response", "dst": "pv", "dst_port": "in"},
            {"src": "llm", "src_port": "trigger", "dst": "pv", "dst_port": "trigger"},
            {"src": "pv", "src_port": "trigger", "dst": "next", "dst_port": "trigger"},
            {"src": "pv", "src_port": "out", "dst": "next", "dst_port": "prompt"},
            {"src": "next", "src_port": "trigger", "dst": "said", "dst_port": "trigger"},
            {"src": "next", "src_port": "response", "dst": "said", "dst_port": "in"},
        ],
    }
    events = _run(graph, "one", "two")
    assert len(_values(events, "pv", "trigger")) == 2, "one Preview trigger per upstream turn"
    assert _values(events, "pv", "out") == ["[mock] one", "[mock] two"], "one value per turn"
    assert _logs(events, "said") == ["said: [mock] [mock] one", "said: [mock] [mock] two"], \
        "the consumer fires once per turn and reads that turn's reply"


def test_the_trigger_fires_what_follows_once_per_turn_with_that_turns_value() -> None:
    # the Preview's trigger fires a Log once per turn, and the Log reads the
    # value the Preview passed on at `out` in that same turn.
    graph = {
        "nodes": _CHAT_LLM + [_log("shown", "shown")],
        "edges": _CHAT_LLM_EDGES + [
            {"src": "llm", "src_port": "response", "dst": "pv", "dst_port": "in"},
            {"src": "llm", "src_port": "trigger", "dst": "pv", "dst_port": "trigger"},
            {"src": "pv", "src_port": "trigger", "dst": "shown", "dst_port": "trigger"},
            {"src": "pv", "src_port": "out", "dst": "shown", "dst_port": "in"},
        ],
    }
    events = _run(graph, "hello", "again")
    assert _logs(events, "shown") == ["shown: [mock] hello", "shown: [mock] again"]


def test_pulled_source_arrives_by_pull_when_the_trigger_fires() -> None:
    # a pulled source (Text) never fires; its value arrives by pull when
    # `trigger` fires, so that fire puts it on `out` and fires once.
    graph = {
        "nodes": [
            {"id": "txt", "type": "core.value.text", "config": {"text": "from a text node"}},
            {"id": "m", "type": "core.trigger.manual"},
            {"id": "pv", "type": "core.output.preview", "config": {}},
            _log("shown", "shown"),
            _log("fired", "fired"),
        ],
        "edges": [
            {"src": "txt", "src_port": "out", "dst": "pv", "dst_port": "in"},
            {"src": "m", "src_port": "trigger", "dst": "pv", "dst_port": "trigger"},
            {"src": "pv", "src_port": "trigger", "dst": "shown", "dst_port": "trigger"},
            {"src": "pv", "src_port": "out", "dst": "shown", "dst_port": "in"},
            {"src": "pv", "src_port": "trigger", "dst": "fired", "dst_port": "trigger"},
            {"src": "pv", "src_port": "trigger", "dst": "fired", "dst_port": "in"},
        ],
    }
    events = _run(graph)  # the Manual kicks once at startup
    assert _values(events, "pv", "out") == ["from a text node"]
    assert _logs(events, "shown") == ["shown: from a text node"]
    assert len(_values(events, "pv", "trigger")) == 1
    assert _logs(events, "fired") == ["fired: True"]
    assert _logs(events, "pv") == ["preview: from a text node"]


def test_console_logs_each_value_once() -> None:
    # the value arriving never fires the Preview; its trigger fire logs it once.
    graph = {
        "nodes": list(_CHAT_LLM),
        "edges": _CHAT_LLM_EDGES + [
            {"src": "llm", "src_port": "response", "dst": "pv", "dst_port": "in"},
            {"src": "llm", "src_port": "trigger", "dst": "pv", "dst_port": "trigger"},
        ],
    }
    events = _run(graph, "hello")
    assert _logs(events, "pv") == ["preview: [mock] hello"]


def test_a_value_on_in_never_fires_the_preview() -> None:
    # `in` is data: a value landing there is latched, and only `trigger` fires.
    rt = Runtime()
    rt.build({"nodes": [{"id": "pv", "type": "core.output.preview", "config": {}}], "edges": []})
    inst = rt.nodes["pv"]
    assert not inst.is_trigger("in") and inst.is_trigger("trigger")
    out = asyncio.run(rt._invoke(inst, "trigger", True, {"in": "hello", "trigger": True}))
    assert out == {"out": "hello", "trigger": True}
