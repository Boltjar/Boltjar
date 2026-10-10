"""The thinking knob reaches Ollama both ways.

Ollama applies a model's own default when a request carries no `think` field,
and a thinking model (gemma4, qwen3) then thinks with the knob off: a one-line
reply took 233 tokens instead of 5. So for a model that declares thinking the
knob's value is sent as `think: true` or `think: false`; a model that cannot
think gets no `think` field at all. Ollama is faked at the HTTP boundary
(`vendor_http`, see conftest.py)."""
import asyncio
import json

import httpx

from boltjar.runtime import Runtime
import boltjar.nodes.core  # noqa: F401  (registers nodes + loads manifests)
from boltjar.nodes.core import builtin as b

GENERATE = {"model": "gemma4:12b", "response": "Hello, Alice!", "done": True}
CHAT = {"model": "gemma4:12b", "done": True,
        "message": {"role": "assistant", "content": "Hello, Alice!"}}


def _graph(llm_config: dict) -> dict:
    return {
        "nodes": [
            {"id": "chat", "type": "core.trigger.chat"},
            {"id": "llm", "type": "core.ai.llm", "config": llm_config},
            {"id": "said", "type": "core.output.log", "config": {"label": "SAID"}},
        ],
        "edges": [
            {"src": "chat", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "chat", "src_port": "text", "dst": "llm", "dst_port": "prompt"},
            {"src": "llm", "src_port": "response", "dst": "said", "dst_port": "in"},
        ],
    }


def _chat(graph: dict) -> list[dict]:
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        rt.send_chat("chat", "Greet Alice")
        await asyncio.sleep(0.5)
        await rt.stop()

    asyncio.run(drive())
    return events


def _sent(vendor_http, llm_config: dict) -> dict:
    vendor_http.reply = lambda r: httpx.Response(200, json=GENERATE)
    events = _chat(_graph(llm_config))
    replies = [e for e in events if e["kind"] == "value" and e["node"] == "llm"
               and e["port"] == "response"]
    assert replies and replies[0]["value"] == "Hello, Alice!", events
    assert str(vendor_http.last.url).endswith("/api/generate")
    return vendor_http.last_json()


def test_knob_off_by_default_sends_think_false(vendor_http):
    body = _sent(vendor_http, {"model": "ollama/gemma4:12b"})
    assert body["think"] is False


def test_knob_off_sends_think_false(vendor_http):
    body = _sent(vendor_http, {"model": "ollama/gemma4:12b", "params": {"think": False}})
    assert body["think"] is False


def test_knob_on_sends_think_true(vendor_http):
    body = _sent(vendor_http, {"model": "ollama/gemma4:12b", "params": {"think": True}})
    assert body["think"] is True


def test_a_model_that_cannot_think_gets_no_think_field(vendor_http):
    # devstral declares no thinking; a `think` saved under another model is dropped.
    body = _sent(vendor_http, {"model": "ollama/devstral", "params": {"think": True}})
    assert "think" not in body


def test_tool_loop_sends_think_false_on_every_hop(vendor_http):
    vendor_http.reply = lambda r: httpx.Response(200, json=CHAT)
    tools = [{"id": "t1", "name": "weather", "description": "the weather",
              "parameters": {"type": "object", "properties": {}}}]
    text, reasoning = asyncio.run(b._ollama("gemma4:12b", "Greet Alice", {"think": False},
                                            {}, ctx=None, tools=tools))
    assert (text, reasoning) == ("Hello, Alice!", "")
    assert str(vendor_http.last.url).endswith("/api/chat")
    for request in vendor_http.requests:
        assert json.loads(request.content)["think"] is False


def test_tool_loop_sends_nothing_for_a_model_that_cannot_think(vendor_http):
    vendor_http.reply = lambda r: httpx.Response(200, json=CHAT)
    tools = [{"id": "t1", "name": "weather", "description": "", "parameters": None}]
    asyncio.run(b._ollama("devstral", "Greet Alice", {"temperature": 0.2}, {},
                          ctx=None, tools=tools))
    assert "think" not in vendor_http.last_json()
