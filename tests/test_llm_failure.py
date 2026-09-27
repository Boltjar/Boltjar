"""The LLM failure branch and the declared-params rule for the LLM.

A provider failure must never become a reply: the node fires its `error`
event with the failure message, emits no `response` / `reasoning` / `trigger`
(so nothing downstream speaks or stores it), and the failure is reported as the
node's error (log + node_error) so the editor marks the node. The vendor is
faked at the HTTP boundary (`vendor_http`, see conftest.py)."""
import asyncio

import httpx
import pytest

from boltjar import models
from boltjar.runtime import Runtime
import boltjar.nodes.core  # noqa: F401  (registers nodes + loads manifests)

COMPLETION = {
    "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
    "model": "grok-4.20-0309-non-reasoning",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "Hello!"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


@pytest.fixture
def xai_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")  # never the real key


def _graph(llm_config: dict) -> dict:
    return {
        "nodes": [
            {"id": "chat", "type": "core.trigger.chat"},
            {"id": "llm", "type": "core.ai.llm", "config": llm_config},
            {"id": "said", "type": "core.output.log", "config": {"label": "SAID"}},
            {"id": "done", "type": "core.output.log", "config": {"label": "DONE"}},
            {"id": "oops", "type": "core.output.log", "config": {"label": "OOPS"}},
        ],
        "edges": [
            {"src": "chat", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "chat", "src_port": "text", "dst": "llm", "dst_port": "prompt"},
            {"src": "llm", "src_port": "response", "dst": "said", "dst_port": "in"},
            {"src": "llm", "src_port": "trigger", "dst": "done", "dst_port": "in"},
            {"src": "llm", "src_port": "error", "dst": "oops", "dst_port": "in"},
        ],
    }


def _chat(graph: dict, text: str = "hi") -> list[dict]:
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        rt.send_chat("chat", text)
        await asyncio.sleep(0.5)
        await rt.stop()

    asyncio.run(drive())
    return events


def _values(events: list[dict], node: str) -> dict:
    return {e["port"]: e["value"] for e in events if e["kind"] == "value" and e["node"] == node}


def _logs(events: list[dict], node: str) -> list[str]:
    return [e["message"] for e in events if e["kind"] == "log" and e["node"] == node]


def test_llm_error_declares_an_event_output():
    from boltjar.sdk import NODE_REGISTRY
    outs = {p.name: p for p in NODE_REGISTRY["core.ai.llm"].outputs}
    assert outs["error"].type == "event" and outs["error"].optional is True


def test_provider_failure_fires_error_and_never_a_reply(vendor_http, xai_key):
    vendor_http.reply = lambda r: httpx.Response(400, text='{"error":"model overloaded"}')
    events = _chat(_graph({"model": "xai/grok-4.20"}))

    llm = _values(events, "llm")
    assert "error" in llm, events
    assert "400" in llm["error"]                 # the failure message is the payload
    for silent in ("response", "reasoning", "trigger"):
        assert silent not in llm, f"a failed call must not emit `{silent}`"

    errors = [e for e in events if e["kind"] == "node_error" and e["node"] == "llm"]
    assert len(errors) == 1 and "400" in errors[0]["error"]
    # the node's error names the real failure, not the branch wrapper.
    assert errors[0]["error"].startswith("HTTPStatusError(")
    assert any(m.startswith("ERROR HTTPStatusError(") and "400" in m for m in _logs(events, "llm"))

    # the branch drives what is wired to it; the reply path stays silent.
    assert any(m.startswith("OOPS: ") and "400" in m for m in _logs(events, "oops"))
    assert _logs(events, "said") == [] and _logs(events, "done") == []


def test_success_replies_and_leaves_the_error_branch_silent(vendor_http, xai_key):
    vendor_http.reply = lambda r: httpx.Response(200, json=COMPLETION)
    events = _chat(_graph({"model": "xai/grok-4.20"}))
    llm = _values(events, "llm")
    assert llm.get("response") == "Hello!"
    assert "trigger" in llm and "error" not in llm
    assert not [e for e in events if e["kind"] == "node_error"]
    assert _logs(events, "said") == ["SAID: Hello!"] and _logs(events, "oops") == []


def test_grok_4_20_never_sends_reasoning_effort(vendor_http, xai_key):
    # a `think` saved while the node was on grok-4.3 must not survive the switch:
    # grok-4.20 declares no think param, so reasoning_effort is never sent.
    vendor_http.reply = lambda r: httpx.Response(200, json=COMPLETION)
    stale = {"temperature": 0.5, "top_p": 1, "max_completion_tokens": 2048,
             "think": "low", "json": False, "keep_alive": "5m"}
    _chat(_graph({"model": "xai/grok-4.20", "params": stale}))
    body = vendor_http.last_json()
    assert str(vendor_http.last.url) == "https://api.x.ai/v1/chat/completions"
    assert body["model"] == "grok-4.20-0309-non-reasoning"
    assert "reasoning_effort" not in body
    assert "keep_alive" not in body
    assert body["temperature"] == 0.5
    assert body["max_completion_tokens"] == 2048


def test_grok_4_20_manifest_matches_the_docs():
    # docs.x.ai model card (2026-09-23): "text, image -> text", function calling
    # yes, structured outputs yes, reasoning no, 1,000,000 context.
    m = models.get("xai/grok-4.20")
    assert m is not None
    assert (m.provider, m.model) == ("xai", "grok-4.20-0309-non-reasoning")
    assert m.inputs == ["text", "image"] and m.outputs == ["text"]
    assert m.tools and m.json and not m.thinking
    assert m.context == 1_000_000
    params = {p.name: p for p in m.params}
    assert "think" not in params
    assert params["temperature"].default == 0.9
    assert params["max_completion_tokens"].default == 2048
    assert "top_p" in params
