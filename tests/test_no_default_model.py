"""A model can cost money, so Boltjar never picks one on anyone's behalf.

A model node starts with no model picked. The LLM with none picked answers with
the free offline mock; every other model node (TTS, STT, Embed, Rerank, a
custom node's own) with none picked is a validation problem, so its graph stays
Off until a model is picked. A model somebody picked is saved and loaded exactly as picked.

Isolation: the `dirs` fixture repoints the server's graph folders at a tmp dir,
so nothing here touches the project's real user/ or examples/."""
from __future__ import annotations

import asyncio

import pytest
from local_client import local_client

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
import boltjar.server as server
from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY, Kind, Port, Widget, model, node
from boltjar.server import validate_graph

client = local_client()


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    user = tmp_path / "user" / "graphs"
    examples = tmp_path / "examples"
    examples.mkdir()
    monkeypatch.setattr(server, "GRAPHS_DIR", user)
    monkeypatch.setattr(server, "EXAMPLES_DIR", examples)
    monkeypatch.setattr(server, "AUTOSAVE_DIR", tmp_path / "user" / "autosave")
    return user


def _graph(node_type: str, config: dict | None = None, **extra) -> dict:
    """A Manual trigger firing one model node of `node_type`."""
    return {"name": "pick", "nodes": [
        {"id": "fire", "type": "core.trigger.manual"},
        {"id": "m", "type": node_type, "config": config or {}, **extra},
    ], "edges": [{"src": "fire", "src_port": "trigger", "dst": "m", "dst_port": "trigger"}]}


def _no_model(graph: dict) -> list[dict]:
    return [p for p in validate_graph(graph) if p["kind"] == "no-model"]


# --------------------------------------------------------------- validation

@pytest.mark.parametrize("node_type, name", [
    ("core.ai.tts", "TTS"), ("core.ai.stt", "STT"),
    ("core.ai.embed", "Embed"), ("core.ai.rerank", "Rerank"),
])
@pytest.mark.parametrize("config", [{}, {"model": ""}, {"model": None}],
                         ids=["unsaved", "cleared", "null"])
def test_a_model_node_with_no_model_picked_does_not_turn_on(node_type, name, config):
    assert _no_model(_graph(node_type, config)) == [
        {"node": "m", "kind": "no-model", "message": f"pick a model for {name}"}]


@pytest.mark.parametrize("node_type, picked", [
    ("core.ai.tts", "xai/tts"), ("core.ai.stt", "fish/asr"),
    ("core.ai.embed", "ollama/bge-m3"), ("core.ai.rerank", "rerank/bge-v2-m3"),
])
def test_a_picked_model_clears_the_problem(node_type, picked):
    assert _no_model(_graph(node_type, {"model": picked})) == []


def test_a_bypassed_model_node_with_no_model_is_no_problem():
    assert _no_model(_graph("core.ai.tts", disabled=True)) == []


def test_the_llm_with_no_model_picked_is_no_problem():
    assert _no_model(_graph("core.ai.llm")) == []


def test_a_custom_model_node_with_no_model_picked_does_not_turn_on():
    @node(id="test.custom_voice", name="Acme Voice", kind=Kind.TRANSFORM, category="Test")
    class AcmeVoice:  # noqa: F841  (declaring it is the test)
        voice: Widget = model("tts", label="Voice model")
        inputs = [Port("trigger", "event", trigger=True)]
        outputs = [Port("audio", "audio")]

    try:
        assert _no_model(_graph("test.custom_voice")) == [
            {"node": "m", "kind": "no-model", "message": "pick a voice model for Acme Voice"}]
    finally:
        NODE_REGISTRY.pop("test.custom_voice", None)


def test_power_on_is_refused_while_a_model_is_missing():
    hub = server.Hub()
    graph = _graph("core.ai.tts")
    problems = asyncio.run(hub.power_on(graph))
    assert problems and {"node": "m", "kind": "no-model",
                         "message": "pick a model for TTS"} in problems
    assert hub.runtime is None


# --------------------------------------------------------------- running

def test_the_llm_with_no_model_picked_answers_with_the_offline_mock():
    rt = Runtime()
    rt.build({"nodes": [{"id": "llm", "type": "core.ai.llm", "config": {}}], "edges": []})
    out = asyncio.run(rt.nodes["llm"].obj.run(trigger=True, prompt="hello there"))
    assert out["response"].startswith("[mock]") and "hello there" in out["response"]


# --------------------------------------------------------------- saving

def test_a_picked_model_is_saved_and_loaded_exactly_as_picked(dirs):
    config = {"model": "fish/s2", "params": {"voice": "abc", "format": "mp3"}}
    graph = _graph("core.ai.tts", config)
    assert client.put("/api/graphs/pick", json=graph).json()["ok"]
    loaded = client.get("/api/graphs/pick").json()
    assert next(n for n in loaded["nodes"] if n["id"] == "m")["config"] == config


# --------------------------------------------------------------- the old "auto"

def test_a_saved_auto_model_turns_on_as_none_picked(vendor_http):
    # format 1 had an "auto" model that ran whatever could run. It loads as no
    # model picked: the LLM answers with the mock and no provider is called.
    graph = {"format": 1, **_graph("core.ai.llm", {"model": "auto"})}
    graph["nodes"] += [{"id": "text", "type": "core.value.text", "config": {"text": "hello"}},
                       {"id": "log", "type": "core.output.log"}]
    graph["edges"] += [{"src": "text", "src_port": "out", "dst": "m", "dst_port": "prompt"},
                       {"src": "m", "src_port": "response", "dst": "log", "dst_port": "in"}]
    hub = server.Hub()
    queue: asyncio.Queue = asyncio.Queue()
    hub.subscribers.add(queue)

    async def drive() -> list[dict]:
        assert await hub.power_on(graph) is None
        assert "model" not in next(n for n in hub.graph["nodes"] if n["id"] == "m")["config"]
        seen: list[dict] = []
        for _ in range(60):
            await asyncio.sleep(0.05)
            while not queue.empty():
                seen.append(queue.get_nowait())
            if any(e["kind"] == "log" for e in seen):
                break
        await hub.power_off()
        return seen

    seen = asyncio.run(drive())
    assert "[mock] hello" in [e["message"] for e in seen if e["kind"] == "log"][0]
    assert vendor_http.requests == []
