"""Tests for the model registry and the capability-driven LLM + chat output node."""
import asyncio
import json
import pathlib

from fastapi.testclient import TestClient

from boltjar import models
from boltjar.runtime import Runtime
from boltjar.server import app
import boltjar.nodes.core  # noqa: F401  (registers nodes + loads the model manifests)

client = TestClient(app)


# The curated 2026-06 manifest set (boltjar/nodes/core/models/). Kept here so a manifest
# added or removed without a matching test update is caught.
CURATED_MODEL_IDS = {
    # LLM
    "anthropic/claude-opus-4-8",
    "anthropic/claude-sonnet-4-6",
    "xai/grok-4.3",
    "xai/grok-4.20",
    "ollama/gemma4:12b",
    "ollama/gemma4:e4b",
    "ollama/gemma4:e2b",
    "ollama/qwen3.5:9b",
    "ollama/qwen3.5:9b-q8_0",
    "ollama/qwen3.5:4b",
    "ollama/qwen3:14b",
    "ollama/devstral",
    # TTS / STT (same picker as the LLM, filtered by kind)
    "fish/s2",
    "fish/asr",
    "elevenlabs/multilingual_v2",
    "elevenlabs/scribe_v2",
    "xai/tts",
    "xai/stt",
    # Embed / Rerank (semantic memory pack; same picker, filtered by kind)
    "ollama/bge-m3",
    "rerank/bge-v2-m3",
}


def test_model_registry_loads_declared_models() -> None:
    assert "ollama/gemma4:e4b" in models.MODELS, "the demo default model must load"
    multimodal = models.get("ollama/gemma4:e4b")
    assert multimodal and "image" in multimodal.inputs, "the multimodal model declares an image input"
    assert multimodal and "audio" not in multimodal.inputs, "gemma4 does not have audio input over Ollama"
    # the 12B is the same at the API layer: Ollama 0.30.6 silently ignores an
    # `audio` field (verified 2026-06-08), so it must NOT declare an audio input.
    big = models.get("ollama/gemma4:12b")
    assert big and "audio" not in big.inputs, "gemma4:12b has no audio input over Ollama (API ignores audio)"
    assert multimodal and multimodal.tools, "gemma4 supports tool-calls"
    assert multimodal and multimodal.context == 131072, "gemma4:e4b context was corrected to 131072"


def test_registry_is_exactly_the_curated_set() -> None:
    assert set(models.MODELS) == CURATED_MODEL_IDS, "the registry must match the curated set"
    # the dropped models are gone.
    for dead in ("google/gemini-2.0-flash", "xai/grok-2", "anthropic/claude-3.7-sonnet"):
        assert dead not in models.MODELS


def test_capability_flags_and_synthetic_knobs() -> None:
    opus = models.get("anthropic/claude-opus-4-8")
    assert opus and opus.thinking and opus.json
    assert opus.thinking_style == "level"
    # Opus 4.8 drops temperature / top_p / top_k.
    assert not any(p.name in ("temperature", "top_p", "top_k") for p in opus.params)
    # the thinking lever and json toggle are surfaced as promotable knobs.
    names = {p.name for p in opus.params}
    assert "think" in names and "json" in names
    grok = models.get("xai/grok-4.3")
    # grok-4.3 is the unified reasoning variant: thinking=true, thinking_style="effort",
    # so a `think` SELECT knob (none/low/medium/high, default low) is surfaced.
    assert grok and grok.thinking and grok.json
    assert grok.thinking_style == "effort"
    think_param = next((p for p in grok.params if p.name == "think"), None)
    assert think_param is not None, "grok-4.3 must expose a think knob"
    assert think_param.type == "select"
    assert think_param.options == ["none", "low", "medium", "high"]
    assert think_param.default == "low"
    devstral = models.get("ollama/devstral")
    assert devstral and not devstral.thinking and devstral.json
    assert "think" not in {p.name for p in devstral.params}, "no thinking knob on a non-thinking model"


def test_models_endpoint_shape() -> None:
    ms = client.get("/api/models").json()["models"]
    assert ms, "the catalog should not be empty"
    assert all({"id", "provider", "inputs", "outputs", "tools", "params",
                "thinking", "thinking_style", "json"} <= set(m) for m in ms)
    assert not any(m["id"].startswith("mock/") for m in ms), "the mock models are gone from the picker"
    gemma = next(m for m in ms if m["id"] == "ollama/gemma4:e4b")
    assert any(p["name"] == "temperature" for p in gemma["params"])


def test_chat_output_node_registered() -> None:
    ids = {n["id"] for n in client.get("/api/object_info").json()["nodes"]}
    assert "core.output.chat" in ids


def test_llm_uses_the_declared_model() -> None:
    graph = {
        "nodes": [
            {"id": "p", "type": "core.value.text", "config": {"text": "You are a helpful assistant."}},
            {"id": "tpl", "type": "core.data.template",
             "config": {"template": "{persona}\nUser: hi\nAssistant:"}},
            {"id": "fire", "type": "core.trigger.manual"},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "mock/echo", "params": {"temperature": 0.5}}},
            {"id": "out", "type": "core.output.log"},
        ],
        "edges": [
            {"src": "p", "src_port": "out", "dst": "tpl", "dst_port": "persona"},
            {"src": "tpl", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
            {"src": "fire", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"},
        ],
    }
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        await asyncio.sleep(0.4)
        await rt.stop()

    asyncio.run(drive())
    logs = [e for e in events if e["kind"] == "log"]
    assert any("hi" in e["message"] for e in logs), "the mock model should quote the user line"


def test_chat_console_graph_validates() -> None:
    # frozen fixture, not the editable examples/chat.json.
    g = json.loads(pathlib.Path("tests/fixtures/chat-console.json").read_text(encoding="utf-8"))
    assert client.post("/api/validate", json=g).json()["problems"] == []
