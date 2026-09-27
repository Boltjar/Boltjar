"""The live model list (boltjar.model_discovery).

Each provider is faked at the HTTP boundary (`vendor_http`, see conftest.py)
with the response shapes the real endpoints return (checked live 2026-09-27):
Ollama GET /api/tags + POST /api/show, xAI GET /v1/language-models, Anthropic
GET /v1/models, and an OpenAI-compatible GET <base>/models. Covers the mapping
onto the manifest shape, the merge with the TOML manifests, the cache and its
offline fallback, the "auto" model, and the validation of a vanished model.
"""
from __future__ import annotations

import asyncio
import datetime
import json

import httpx
import pytest

import boltjar.nodes.core  # noqa: F401  (registers nodes + loads manifests)
import boltjar.secrets as secrets
from boltjar import endpoints, model_discovery as md, models
from boltjar.runtime import Runtime
from local_client import local_client

client = local_client()

# ---------------------------------------------------------------- recorded shapes

OLLAMA_TAGS = {"models": [
    {"name": "gemma4:e4b", "model": "gemma4:e4b", "modified_at": "2026-09-20T09:00:00Z",
     "size": 9600000000, "digest": "a1",
     "details": {"format": "gguf", "family": "gemma4", "parameter_size": "8.0B",
                 "quantization_level": "Q4_K_M"}},
    {"name": "llama3.2:latest", "model": "llama3.2:latest", "modified_at": "2026-09-19T09:00:00Z",
     "size": 2019393189, "digest": "b2",
     "details": {"format": "gguf", "family": "llama", "parameter_size": "3.2B",
                 "quantization_level": "Q4_K_M"}},
    {"name": "nomic-embed-text:latest", "model": "nomic-embed-text:latest",
     "modified_at": "2026-09-18T09:00:00Z", "size": 274302450, "digest": "c3",
     "details": {"format": "gguf", "family": "nomic-bert", "parameter_size": "137M",
                 "quantization_level": "F16"}},
    {"name": "x/flux2-klein:latest", "model": "x/flux2-klein:latest",
     "modified_at": "2026-09-17T09:00:00Z", "size": 5000000000, "digest": "d4", "details": {}},
]}
OLLAMA_SHOW = {
    "gemma4:e4b": {"capabilities": ["completion", "vision", "tools", "thinking"],
                   "model_info": {"general.architecture": "gemma4", "gemma4.context_length": 131072}},
    "llama3.2:latest": {"capabilities": ["completion", "tools"],
                        "model_info": {"general.architecture": "llama", "llama.context_length": 131072}},
    "nomic-embed-text:latest": {"capabilities": ["embedding"],
                                "model_info": {"nomic-bert.context_length": 2048,
                                               "nomic-bert.embedding_length": 768}},
    "x/flux2-klein:latest": {"capabilities": ["image"], "model_info": {}},
}

XAI_MODELS = {"models": [
    {"id": "grok-4.20-0309-non-reasoning", "fingerprint": "fp_1", "created": 1773014400,
     "object": "model", "owned_by": "xai", "version": "1.0",
     "input_modalities": ["text", "image"], "output_modalities": ["text"],
     "prompt_text_token_price": 12500, "completion_text_token_price": 25000,
     "aliases": ["grok-4.20-non-reasoning"],
     "capabilities": {"reasoning_effort": [], "default_reasoning_effort": ""}},
    {"id": "grok-4.7", "fingerprint": "fp_2", "created": 1788000000, "object": "model",
     "owned_by": "xai", "version": "1.0", "input_modalities": ["text", "image"],
     "output_modalities": ["text"], "aliases": ["grok-4.7-latest"],
     "capabilities": {"reasoning_effort": ["low", "medium", "high"],
                      "default_reasoning_effort": "medium"}},
    {"id": "grok-imagine-video", "fingerprint": "fp_3", "created": 1788000000, "object": "model",
     "owned_by": "xai", "version": "1.0", "input_modalities": ["text", "image"],
     "output_modalities": ["video"], "aliases": []},
]}


def _anthropic_model(mid: str, name: str, *, image=True, adaptive=True, structured=True,
                     max_in=1000000, max_out=64000) -> dict:
    yes = {"supported": True}
    return {
        "type": "model", "id": mid, "display_name": name, "created_at": "2026-07-24T00:00:00Z",
        "max_input_tokens": max_in, "max_tokens": max_out,
        "capabilities": {
            "batch": yes, "citations": yes, "code_execution": yes,
            "context_management": {"supported": True},
            "effort": {"supported": True, "low": yes, "medium": yes, "high": yes, "max": yes},
            "image_input": {"supported": image}, "pdf_input": yes,
            "structured_outputs": {"supported": structured},
            "thinking": {"supported": True, "types": {"adaptive": {"supported": adaptive},
                                                      "enabled": yes}},
        },
    }


ANTHROPIC_MODELS = {
    "data": [_anthropic_model("claude-opus-5", "Claude Opus 5"),
             _anthropic_model("claude-sonnet-4-6", "Claude Sonnet 4.6")],
    "first_id": "claude-opus-5", "has_more": False, "last_id": "claude-sonnet-4-6",
}

OPENROUTER_MODELS = {"data": [
    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Meta: Llama 3.3 70B Instruct",
     "created": 1733506137, "context_length": 131072,
     "architecture": {"modality": "text->text", "input_modalities": ["text"],
                      "output_modalities": ["text"], "tokenizer": "Llama3"},
     "pricing": {"prompt": "0.0000001", "completion": "0.00000025"},
     "supported_parameters": ["max_tokens", "temperature", "tools", "tool_choice",
                              "response_format"]},
    {"id": "google/gemini-2.5-flash-image", "name": "Google: Nano Banana", "context_length": 32768,
     "architecture": {"input_modalities": ["image", "text"], "output_modalities": ["image", "text"]},
     "supported_parameters": ["temperature"]},
    {"id": "openai/gpt-image-1", "name": "OpenAI: GPT Image 1", "context_length": 0,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
     "supported_parameters": []},
]}

LMSTUDIO_MODELS = {"object": "list", "data": [
    {"id": "qwen2.5-7b-instruct", "object": "model", "owned_by": "organization_owner"},
    {"id": "text-embedding-nomic-embed-text-v1.5", "object": "model", "owned_by": "organization_owner"},
]}

OPENAI_MODELS = {"object": "list", "data": [
    {"id": "gpt-5", "object": "model", "created": 1754425777, "owned_by": "system"},
    {"id": "text-embedding-3-small", "object": "model", "created": 1705948997, "owned_by": "system"},
    {"id": "whisper-1", "object": "model", "created": 1677532384, "owned_by": "openai-internal"},
    {"id": "gpt-4o-mini-tts", "object": "model", "created": 1742403959, "owned_by": "system"},
]}

COMPLETION = {"id": "c1", "object": "chat.completion",
              "choices": [{"index": 0, "finish_reason": "stop",
                           "message": {"role": "assistant", "content": "Hello!"}}]}

OFFLINE = httpx.ConnectError("connection refused")


def vendors(*, ollama=True, xai=None, anthropic=None, openai=None, endpoints_=None,
            chat=None):
    """A reply router in the recorded shapes. A provider left None answers 404;
    ollama=False refuses the connection like a stopped Ollama."""
    def reply(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        if request.url.port == 11434:
            if not ollama:
                raise OFFLINE
            if path == "/api/tags":
                return httpx.Response(200, json=OLLAMA_TAGS)
            if path == "/api/show":
                return httpx.Response(200, json=OLLAMA_SHOW[json.loads(request.content)["model"]])
            if path == "/api/generate":
                return httpx.Response(200, json={"response": "from ollama", "done": True})
        if url.startswith(md.XAI_MODELS_URL) and xai is not None:
            return xai(request) if callable(xai) else httpx.Response(200, json=xai)
        if url.startswith(md.ANTHROPIC_MODELS_URL) and anthropic is not None:
            return anthropic(request) if callable(anthropic) else httpx.Response(200, json=anthropic)
        if url.startswith("https://api.openai.com/v1/models") and openai is not None:
            return httpx.Response(200, json=openai)
        for base, body in (endpoints_ or {}).items():
            if url == f"{base}/models":
                return httpx.Response(200, json=body)
        if path.endswith("/chat/completions") and chat is not None:
            return httpx.Response(200, json=chat)
        return httpx.Response(404, text="not faked")
    return reply


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """An empty discovery state, cache and endpoint list, with no provider keys."""
    monkeypatch.setattr(md, "CACHE_PATH", tmp_path / "models-cache.json")
    monkeypatch.setattr(endpoints, "PATH", tmp_path / "endpoints.json")
    monkeypatch.setattr(secrets, "_PATH", tmp_path / "secrets.json")
    monkeypatch.setattr(secrets, "_store", {})
    monkeypatch.setattr(secrets, "_loaded", True)
    for name in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
                 "FISH_API_KEY", "ELEVENLABS_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    md.reset()
    yield tmp_path
    md.reset()


def refresh() -> None:
    asyncio.run(md.refresh())


def rows() -> dict[str, dict]:
    return {m["id"]: m for m in md.catalog()}


# ------------------------------------------------------------------ mapping

def test_ollama_capabilities_map_onto_the_manifest_shape(fresh, vendor_http):
    vendor_http.reply = vendors()
    refresh()
    got = rows()
    # the curated manifest enriches the installed model: its own label and params.
    gemma = got["ollama/gemma4:e4b"]
    assert gemma["source"] == "both" and gemma["available"] is True
    assert gemma["label"] == models.MODELS["ollama/gemma4:e4b"].label
    # an installed model with no manifest gets defaults from its capabilities.
    llama = got["ollama/llama3.2:latest"]
    assert llama["source"] == "discovered" and llama["available"] is True
    assert (llama["kind"], llama["inputs"], llama["outputs"]) == ("llm", ["text"], ["text"])
    assert llama["tools"] is True and llama["thinking"] is False and llama["json"] is True
    assert llama["context"] == 131072
    assert {p["name"] for p in llama["params"]} == {"temperature", "num_ctx", "keep_alive", "json"}
    assert got["ollama/nomic-embed-text:latest"]["kind"] == "embed"
    assert "ollama/x/flux2-klein:latest" not in got, "an image generator is no chat model"
    # a manifest for a model Ollama does not have is kept, marked unavailable.
    qwen = got["ollama/qwen3:14b"]
    assert qwen["source"] == "manifest" and qwen["available"] is False
    assert "not installed" in qwen["reason"]
    # one /api/tags, then one /api/show per installed model, by name.
    shown = sorted(json.loads(r.content)["model"] for r in vendor_http.requests
                   if r.url.path == "/api/show")
    assert shown == sorted(t["name"] for t in OLLAMA_TAGS["models"])


def test_an_ollama_without_capabilities_reads_as_a_chat_model():
    tag = {"name": "old:latest", "details": {}}
    found = md.ollama_manifest(tag, {"model_info": {}})
    assert found.kind == "llm" and found.inputs == ["text"] and found.tools is False
    assert found.aliases == ["old"]


def test_xai_lists_models_with_its_key_and_reported_efforts(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    vendor_http.reply = vendors(ollama=False, xai=XAI_MODELS)
    refresh()
    asked = [r for r in vendor_http.requests if str(r.url) == md.XAI_MODELS_URL]
    assert len(asked) == 1 and asked[0].headers["authorization"] == "Bearer test-xai-key"
    got = rows()
    assert got["xai/grok-4.20"]["source"] == "both"
    new = got["xai/grok-4.7"]
    assert new["source"] == "discovered" and new["inputs"] == ["text", "image"]
    think = next(p for p in new["params"] if p["name"] == "think")
    assert (think["type"], think["options"], think["default"]) == (
        "select", ["low", "medium", "high"], "medium")
    assert "xai/grok-imagine-video" not in got, "a video model is no chat model"
    # xAI no longer lists grok-4.3: the manifest stays, unavailable.
    assert got["xai/grok-4.3"]["available"] is False
    assert got["xai/grok-4.3"]["reason"] == "no longer offered by xAI"
    # the chat list says nothing about TTS: the xAI TTS manifest is untouched.
    assert got["xai/tts"]["available"] is True
    # a graph saved with the provider's own id runs the manifest it names.
    assert models.get("xai/grok-4.20-0309-non-reasoning") is models.MODELS["xai/grok-4.20"]


def test_anthropic_capabilities_and_context(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    vendor_http.reply = vendors(ollama=False, anthropic=ANTHROPIC_MODELS)
    refresh()
    asked = [r for r in vendor_http.requests if str(r.url).startswith(md.ANTHROPIC_MODELS_URL)][0]
    assert asked.headers["x-api-key"] == "test-anthropic-key"
    assert asked.headers["anthropic-version"] == "2023-06-01"
    assert asked.url.params["limit"] == "1000"
    got = rows()
    opus = got["anthropic/claude-opus-5"]
    assert opus["label"] == "Claude Opus 5" and opus["context"] == 1000000
    assert opus["inputs"] == ["text", "image"] and opus["tools"] and opus["json"]
    assert opus["thinking"] and opus["thinking_style"] == "level"
    max_tokens = next(p for p in opus["params"] if p["name"] == "max_tokens")
    assert max_tokens["max"] == 64000
    assert got["anthropic/claude-sonnet-4-6"]["source"] == "both"
    assert got["anthropic/claude-opus-4-8"]["available"] is False


def test_anthropic_follows_the_page_cursor(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    pages = {
        None: {"data": [_anthropic_model("claude-opus-5", "Claude Opus 5")],
               "has_more": True, "first_id": "claude-opus-5", "last_id": "claude-opus-5"},
        "claude-opus-5": {"data": [_anthropic_model("claude-haiku-5", "Claude Haiku 5", image=False)],
                          "has_more": False, "first_id": "claude-haiku-5", "last_id": "claude-haiku-5"},
    }
    vendor_http.reply = vendors(ollama=False, anthropic=lambda r: httpx.Response(
        200, json=pages[r.url.params.get("after_id")]))
    refresh()
    got = rows()
    assert "anthropic/claude-opus-5" in got and got["anthropic/claude-haiku-5"]["inputs"] == ["text"]


def test_openai_compatible_endpoints_read_what_they_report(fresh, vendor_http, monkeypatch):
    endpoints.save("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
    endpoints.save("lmstudio", "http://localhost:1234")  # a bare host gets /v1
    secrets._store["OPENROUTER_API_KEY"] = "test-openrouter-key"
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    vendor_http.reply = vendors(ollama=False, openai=OPENAI_MODELS, endpoints_={
        "https://openrouter.ai/api/v1": OPENROUTER_MODELS,
        "http://localhost:1234/v1": LMSTUDIO_MODELS,
    })
    refresh()
    got = rows()
    llama = got["openrouter/meta-llama/llama-3.3-70b-instruct"]
    assert llama["label"] == "Meta: Llama 3.3 70B Instruct" and llama["context"] == 131072
    assert llama["tools"] is True and llama["json"] is True
    assert got["openrouter/google/gemini-2.5-flash-image"]["inputs"] == ["text", "image"]
    assert "openrouter/openai/gpt-image-1" not in got, "an image-only model is no chat model"
    qwen = got["lmstudio/qwen2.5-7b-instruct"]
    assert qwen["tools"] is False and qwen["inputs"] == ["text"], "nothing reported, nothing claimed"
    assert "lmstudio/text-embedding-nomic-embed-text-v1.5" not in got
    # api.openai.com lists gpt-5 (a manifest enriches it) and three non-chat models.
    listed = {m for m in got if m.startswith("openai/") and got[m]["source"] != "manifest"}
    assert listed == {"openai/gpt-5"}
    assert got["openai/gpt-5"]["source"] == "both"
    by_url = {str(r.url): r for r in vendor_http.requests}
    assert by_url["https://openrouter.ai/api/v1/models"].headers["authorization"] == \
        "Bearer test-openrouter-key"
    assert "authorization" not in by_url["http://localhost:1234/v1/models"].headers
    assert by_url["https://api.openai.com/v1/models"].headers["authorization"] == \
        "Bearer test-openai-key"


def test_the_openai_list_keeps_only_chat_completions_models(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    ids = ["gpt-3.5-turbo-instruct", "o1-pro", "o3-pro-2025-06-10", "gpt-5-pro",
           "codex-mini-latest", "gpt-5-codex", "o3-deep-research", "gpt-4o-search-preview",
           "gpt-realtime", "gpt-live-1", "gpt-4o-audio-preview", "gpt-4o-mini-tts",
           "gpt-4o-transcribe", "gpt-image-1", "dall-e-3", "tts-1", "whisper-1", "davinci-002",
           "text-embedding-3-small", "omni-moderation-latest", "computer-use-preview", "sora-2",
           # the chat completions models
           "gpt-4o", "gpt-4o-2024-08-06", "gpt-4.1-mini", "gpt-5", "gpt-5-chat-latest",
           "o3", "o4-mini", "chatgpt-4o-latest"]
    listed = {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}
    vendor_http.reply = vendors(ollama=False, openai=listed)
    refresh()
    got = sorted(m.split("/", 1)[1] for m in rows() if m.startswith("openai/")
                 and rows()[m]["source"] != "manifest")
    assert got == sorted(["gpt-4o", "gpt-4o-2024-08-06", "gpt-4.1-mini", "gpt-5",
                          "gpt-5-chat-latest", "o3", "o4-mini", "chatgpt-4o-latest"])
    # the same names from another OpenAI-compatible server are its own chat models.
    assert md.openai_manifest("lmstudio", {"id": "qwen2.5-7b-instruct"}) is not None


def test_listed_openai_models_get_images_and_tools_from_their_manifests(fresh, vendor_http,
                                                                      monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    listed = {"object": "list", "data": [{"id": i, "object": "model"} for i in
                                         ("gpt-4o", "gpt-4.1-mini", "gpt-5", "gpt-4o-2024-08-06")]}
    vendor_http.reply = vendors(ollama=False, openai=listed, chat=COMPLETION)
    refresh()
    got = rows()
    for mid in ("openai/gpt-4o", "openai/gpt-4.1-mini", "openai/gpt-5"):
        m = got[mid]
        assert m["source"] == "both" and m["available"] is True, mid
        assert m["inputs"] == ["text", "image"] and m["tools"] is True and m["json"] is True, mid
    assert got["openai/gpt-4o"]["context"] == 128000
    # a snapshot no manifest names stays as listed: nothing reported, nothing claimed.
    assert got["openai/gpt-4o-2024-08-06"]["tools"] is False
    # GPT-5 reasons: an effort knob with its own levels, no empty reasoning output
    # and no sampling knobs it would refuse.
    gpt5 = got["openai/gpt-5"]
    assert gpt5["thinking"] is False
    think = [p for p in gpt5["params"] if p["name"] == "think"]
    assert [(p["options"], p["default"]) for p in think] == [
        (["minimal", "low", "medium", "high"], "medium")]
    assert not {"temperature", "top_p"} & {p["name"] for p in gpt5["params"]}
    assert _run_llm("openai/gpt-5")["response"] == "Hello!"
    sent = vendor_http.last_json()
    assert sent["model"] == "gpt-5" and sent["reasoning_effort"] == "medium"
    assert "temperature" not in sent


# ---------------------------------------------------------- cache + offline

def test_the_cache_keeps_the_list_when_a_provider_goes_offline(fresh, vendor_http):
    vendor_http.reply = vendors()
    refresh()
    cache = json.loads(md.CACHE_PATH.read_text(encoding="utf-8"))
    ollama = cache["providers"]["ollama"]
    assert ollama["ok"] is True and ollama["updated"].endswith("Z"), "times are UTC"
    first_update = ollama["updated"]

    vendor_http.reply = vendors(ollama=False)
    refresh()
    got = rows()
    assert got["ollama/llama3.2:latest"]["available"] is True, "the last list stays in use"
    state = md.payload()["providers"]["ollama"]
    assert state["ok"] is False and "refused" in state["error"]
    assert state["updated"] == first_update

    # a restart with Ollama still down: the list comes back from the cache.
    md.reset()
    assert "ollama/llama3.2:latest" in rows()
    assert models.get("ollama/llama3.2:latest") is not None


def test_no_provider_answering_never_breaks_the_list(fresh, vendor_http):
    vendor_http.reply = vendors(ollama=False)
    refresh()
    body = client.get("/api/models").json()
    by_id = {m["id"]: m for m in body["models"]}
    assert by_id["ollama/gemma4:e4b"]["available"] is False
    assert by_id["ollama/gemma4:e4b"]["reason"] == "Ollama is not running"
    assert body["providers"]["ollama"]["ok"] is False and body["updated"] is None
    assert body["auto"] is None


def test_a_removed_key_takes_its_models_out(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    vendor_http.reply = vendors(ollama=False, xai=XAI_MODELS)
    refresh()
    assert "xai/grok-4.7" in rows()
    monkeypatch.delenv("XAI_API_KEY")
    got = rows()
    assert "xai/grok-4.7" not in got
    assert got["xai/grok-4.20"]["available"] is False
    assert "no xAI key" in got["xai/grok-4.20"]["reason"]


def test_refresh_runs_when_stale_and_not_when_fresh(fresh, vendor_http, monkeypatch):
    scheduled: list[bool] = []
    monkeypatch.setattr(md, "schedule_refresh", lambda: scheduled.append(True))
    md.refresh_if_stale()
    assert scheduled == [True], "a list never fetched is stale"

    vendor_http.reply = vendors()
    refresh()
    scheduled.clear()
    md.refresh_if_stale()
    assert scheduled == []
    old = md._iso(md._now() - datetime.timedelta(seconds=md.STALE_AFTER + 60))
    md._state["ollama"].checked = old
    md.refresh_if_stale()
    assert scheduled == [True]


def test_the_server_start_refreshes_in_the_background(fresh, monkeypatch):
    import boltjar.server as server
    from fastapi.testclient import TestClient

    calls: list[str] = []
    monkeypatch.setattr(md, "startup", lambda: calls.append("startup"))
    with TestClient(server.app):
        assert calls == ["startup"]


def test_startup_schedules_without_waiting(fresh, monkeypatch):
    monkeypatch.setattr(md, "AUTO_REFRESH", True)
    started: list[bool] = []

    async def never(*_):
        started.append(True)
        await asyncio.sleep(3600)

    monkeypatch.setattr(md, "_refresh", never)

    async def boot():
        md.startup()          # returns at once, the refresh runs on its own
        assert md.refreshing()
        await asyncio.sleep(0)
        await md.shutdown()   # and a shutdown cancels it
        assert not md.refreshing()

    asyncio.run(boot())
    assert started == [True]


def test_refresh_endpoint_returns_the_fresh_list(fresh, vendor_http):
    vendor_http.reply = vendors()
    body = client.post("/api/models/refresh").json()
    ids = {m["id"]: m for m in body["models"]}
    assert ids["ollama/llama3.2:latest"]["source"] == "discovered"
    assert body["providers"]["ollama"]["ok"] is True
    assert body["updated"].endswith("Z") and body["refreshing"] is False


def _held_ollama(base):
    """A reply router whose first /api/tags waits for `gate` (a slow Ollama)."""
    gate = asyncio.Event()

    async def reply(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags" and not gate.is_set():
            await gate.wait()
        return base(request)
    return gate, reply


def _asked(vendor_http, url: str) -> list[httpx.Request]:
    return [r for r in vendor_http.requests if str(r.url).startswith(url)]


def test_a_key_added_during_a_refresh_is_asked_after_it(fresh, vendor_http, monkeypatch):
    async def scenario():
        gate, vendor_http.reply = _held_ollama(vendors(xai=XAI_MODELS))
        first = asyncio.create_task(md.refresh())    # only Ollama is usable yet
        await asyncio.sleep(0.05)
        assert md.refreshing()
        monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
        again = asyncio.create_task(md.refresh())    # Refresh right after the key
        await asyncio.sleep(0.05)
        assert not again.done(), "the running refresh never asks xAI, so it is not joined"
        gate.set()
        await again
        assert first.done()

    asyncio.run(scenario())
    assert "xai/grok-4.7" in rows()
    assert len(_asked(vendor_http, md.XAI_MODELS_URL)) == 1


def test_a_refresh_that_covers_a_request_is_joined(fresh, vendor_http):
    async def scenario():
        gate, vendor_http.reply = _held_ollama(vendors())
        first = asyncio.create_task(md.refresh())
        await asyncio.sleep(0.05)
        again = asyncio.create_task(md.refresh())
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(first, again)

    asyncio.run(scenario())
    assert len([r for r in vendor_http.requests if r.url.path == "/api/tags"]) == 1


def test_a_key_replaced_during_a_refresh_is_asked_again(fresh, vendor_http, monkeypatch):
    monkeypatch.setattr(md, "AUTO_REFRESH", True)
    monkeypatch.setenv("XAI_API_KEY", "old-xai-key")

    async def scenario():
        gate, vendor_http.reply = _held_ollama(vendors(xai=XAI_MODELS))
        md.schedule_refresh()
        await asyncio.sleep(0.05)
        monkeypatch.setenv("XAI_API_KEY", "new-xai-key")
        md.schedule_refresh(changed=True)     # the same providers, but a key changed
        md.schedule_refresh({"ollama"}, changed=True)   # merges into the queued one
        await asyncio.sleep(0.05)
        gate.set()
        while md.refreshing():
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    keys = [r.headers["authorization"] for r in _asked(vendor_http, md.XAI_MODELS_URL)]
    assert keys == ["Bearer old-xai-key", "Bearer new-xai-key"], "two refreshes, not three"


def test_shutdown_cancels_a_queued_refresh_too(fresh, monkeypatch):
    monkeypatch.setattr(md, "AUTO_REFRESH", True)
    runs: list[bool] = []

    async def never(*_):
        runs.append(True)
        await asyncio.sleep(3600)

    monkeypatch.setattr(md, "_refresh", never)

    async def scenario():
        md.schedule_refresh()
        await asyncio.sleep(0)
        md.schedule_refresh(changed=True)   # queued behind the first
        await asyncio.sleep(0)
        await md.shutdown()
        assert not md.refreshing() and all(t.cancelled() for t in md._tasks)

    asyncio.run(scenario())
    assert runs == [True], "the queued refresh never began"


# ------------------------------------------------------- the LLM node

def _run_llm(model: str, prompt: str = "hi") -> dict:
    graph = {
        "nodes": [{"id": "chat", "type": "core.trigger.chat"},
                  {"id": "llm", "type": "core.ai.llm", "config": {"model": model}},
                  {"id": "out", "type": "core.output.log"}],
        "edges": [{"src": "chat", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
                  {"src": "chat", "src_port": "text", "dst": "llm", "dst_port": "prompt"},
                  {"src": "llm", "src_port": "response", "dst": "out", "dst_port": "in"}],
    }
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive():
        await rt.run()
        rt.send_chat("chat", prompt)
        await asyncio.sleep(0.4)
        await rt.stop()

    asyncio.run(drive())
    return {e["port"]: e["value"] for e in events if e["kind"] == "value" and e["node"] == "llm"}


# ------------------------------------------------ OpenAI-compatible calls

class _Ctx:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tid, args):
        self.calls.append((tid, args))
        return "sunny"


def test_openai_compatible_request_shape_with_image_and_tools(vendor_http):
    from boltjar.nodes.core import builtin as b

    tool_call = {"id": "call_1", "type": "function",
                 "function": {"name": "weather", "arguments": '{"city": "Paris"}'}}
    replies = iter([
        {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "It is sunny.",
                                  "reasoning": "checked the tool"}}]},
    ])
    vendor_http.reply = lambda r: httpx.Response(200, json=next(replies))
    ctx = _Ctx()
    tools = [{"id": "t1", "name": "weather", "description": "the weather",
              "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}]
    text, reasoning = asyncio.run(b._openai_chat(
        "https://openrouter.ai/api/v1/chat/completions", "test-key", "meta-llama/llama-3.3-70b-instruct",
        "weather?", {"temperature": 0.2, "json": True, "think": "off"},
        {"image": "data:image/png;base64,AAAA"}, ctx=ctx, tools=tools))
    assert (text, reasoning) == ("It is sunny.", "checked the tool")
    assert ctx.calls == [("t1", {"city": "Paris"})]
    first, second = (json.loads(r.content) for r in vendor_http.requests)
    assert str(vendor_http.requests[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert vendor_http.requests[0].headers["authorization"] == "Bearer test-key"
    assert first["model"] == "meta-llama/llama-3.3-70b-instruct" and first["temperature"] == 0.2
    assert first["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in first, "think off sends no effort"
    assert first["messages"][0]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "weather?"}]
    assert first["tools"][0]["function"]["name"] == "weather"
    assert second["messages"][1]["tool_calls"] == [tool_call]
    assert second["messages"][2] == {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}


def test_a_keyless_endpoint_sends_no_authorization(vendor_http):
    from boltjar.nodes.core import builtin as b

    vendor_http.reply = lambda r: httpx.Response(200, json=COMPLETION)
    asyncio.run(b._openai_chat("http://localhost:1234/v1/chat/completions", "", "qwen", "hi", {}))
    assert "authorization" not in vendor_http.last.headers
    assert vendor_http.last_json() == {"model": "qwen",
                                       "messages": [{"role": "user", "content": "hi"}]}


def test_the_llm_node_calls_openai_and_custom_endpoints(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    endpoints.save("lmstudio", "http://localhost:1234/v1")
    vendor_http.reply = vendors(ollama=False, openai=OPENAI_MODELS,
                                endpoints_={"http://localhost:1234/v1": LMSTUDIO_MODELS},
                                chat=COMPLETION)
    refresh()
    assert _run_llm("openai/gpt-5")["response"] == "Hello!"
    call = vendor_http.last
    assert str(call.url) == "https://api.openai.com/v1/chat/completions"
    assert call.headers["authorization"] == "Bearer test-openai-key"
    assert json.loads(call.content)["model"] == "gpt-5"
    assert _run_llm("lmstudio/qwen2.5-7b-instruct")["response"] == "Hello!"
    assert str(vendor_http.last.url) == "http://localhost:1234/v1/chat/completions"


# ----------------------------------------------------------------- auto

def test_auto_runs_the_first_installed_ollama_chat_model(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    vendor_http.reply = vendors(xai=XAI_MODELS)
    refresh()
    # curated first: the installed model a manifest describes.
    assert md.resolve_auto().id == "ollama/gemma4:e4b"
    assert md.payload()["auto"] == "ollama/gemma4:e4b"
    out = _run_llm("auto")
    assert out["response"] == "from ollama"
    generate = [r for r in vendor_http.requests if r.url.path == "/api/generate"][-1]
    assert json.loads(generate.content)["model"] == "gemma4:e4b"


def test_auto_without_ollama_uses_a_provider_with_a_key(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    vendor_http.reply = vendors(ollama=False, xai=XAI_MODELS, chat=COMPLETION)
    refresh()
    assert md.resolve_auto().id == "xai/grok-4.20"
    assert _run_llm("auto")["response"] == "Hello!"
    call = [r for r in vendor_http.requests if r.url.path.endswith("/chat/completions")][-1]
    assert str(call.url) == "https://api.x.ai/v1/chat/completions"
    assert json.loads(call.content)["model"] == "grok-4.20-0309-non-reasoning"


def test_auto_skips_an_ollama_that_stopped_answering(fresh, vendor_http, monkeypatch):
    vendor_http.reply = vendors()
    refresh()
    vendor_http.reply = vendors(ollama=False)
    refresh()
    assert md.resolve_auto() is None, "the cached Ollama list is shown, not run"


def test_auto_with_nothing_connected_says_how_to_connect(fresh, vendor_http):
    vendor_http.reply = vendors(ollama=False)
    refresh()
    out = _run_llm("auto")
    assert out["response"] == md.AUTO_MOCK_REPLY
    assert "Connections" in md.AUTO_MOCK_REPLY and md.AUTO_MOCK_REPLY.count(".") == 1
    assert "trigger" in out, "the mock reply still completes the node"


def _openai_list(*ids: str) -> dict:
    return {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}


def test_auto_never_picks_a_model_chat_completions_refuses(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    # api.openai.com answers in no useful order; the first row is completions only.
    vendor_http.reply = vendors(ollama=False, openai=_openai_list(
        "gpt-3.5-turbo-instruct", "o1-pro", "gpt-6-luna", "gpt-4o"))
    refresh()
    assert md.payload()["auto"] == "openai/gpt-4o", "a curated model before a bare listed one"
    # with no curated model listed, a listed chat model still serves.
    vendor_http.reply = vendors(ollama=False, openai=_openai_list("gpt-3.5-turbo-instruct", "o3"))
    refresh()
    assert md.payload()["auto"] == "openai/o3"


def test_auto_prefers_a_curated_model_over_an_earlier_listed_one(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    only_new = {"models": [m for m in XAI_MODELS["models"] if m["id"] == "grok-4.7"]}
    vendor_http.reply = vendors(ollama=False, xai=only_new, openai=_openai_list("gpt-4o"))
    refresh()
    # xAI ranks first, but grok-4.7 has no manifest: a bare listing waits.
    assert md.resolve_auto().id == "openai/gpt-4o"


def test_auto_skips_a_provider_that_did_not_answer(fresh, vendor_http, monkeypatch):
    endpoints.save("lmstudio", "http://localhost:1234")
    vendor_http.reply = vendors(ollama=False, endpoints_={"http://localhost:1234/v1": LMSTUDIO_MODELS})
    refresh()
    assert md.resolve_auto().id == "lmstudio/qwen2.5-7b-instruct"
    # LM Studio stopped: its cached list stays in the picker, auto no longer runs it.
    vendor_http.reply = vendors(ollama=False)
    refresh()
    assert md.payload()["providers"]["lmstudio"]["ok"] is False
    assert md.resolve_auto() is None
    # a key the provider refused is skipped the same way.
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    vendor_http.reply = vendors(ollama=False, xai=lambda r: httpx.Response(401, text="bad key"))
    refresh()
    assert md.resolve_auto() is None


# -------------------------------------------------------- vanished models

def _llm_graph(model: str, node_type: str = "core.ai.llm") -> dict:
    return {"nodes": [{"id": "go", "type": "core.trigger.manual"},
                      {"id": "m", "type": node_type, "config": {"model": model}}],
            "edges": [{"src": "go", "src_port": "trigger", "dst": "m", "dst_port": "trigger"}]}


def _model_problems(graph: dict) -> list[dict]:
    return [p for p in client.post("/api/validate", json=graph).json()["problems"]
            if p["kind"] == "model-missing"]


def test_a_vanished_ollama_model_names_the_closest_installed_one(fresh, vendor_http):
    vendor_http.reply = vendors()
    refresh()
    [problem] = _model_problems(_llm_graph("ollama/llama3.1:8b"))
    assert problem["node"] == "m"
    assert problem["message"] == ("model ollama/llama3.1:8b is not installed in Ollama: pull it "
                                  "in Connections; closest available: ollama/llama3.2:latest")


def test_a_manifest_its_provider_dropped_suggests_the_same_family(fresh, vendor_http, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    vendor_http.reply = vendors(ollama=False, xai=XAI_MODELS)
    refresh()
    [problem] = _model_problems(_llm_graph("xai/grok-4.3"))
    assert problem["message"] == ("model xai/grok-4.3 is no longer offered by xAI; "
                                  "closest available: xai/grok-4.7")


def test_an_unknown_model_with_nothing_available_says_what_to_do(fresh):
    [problem] = _model_problems(_llm_graph("acme/gone"))
    assert problem["message"] == ("model acme/gone is not in the model list; pick another "
                                  "model, or connect a llm model in Connections")


def test_a_model_of_another_family_is_named(fresh):
    [problem] = _model_problems(_llm_graph("ollama/gemma4:e4b", "core.ai.tts"))
    assert problem["message"] == "model ollama/gemma4:e4b is a llm model; this node takes tts models"


def test_models_that_can_run_raise_no_problem(fresh, vendor_http):
    vendor_http.reply = vendors()
    refresh()
    for model in ("", "auto", "mock/echo", "ollama/gemma4:e4b", "ollama/llama3.2:latest",
                  "ollama/llama3.2"):
        assert _model_problems(_llm_graph(model)) == [], model
    # a manifest whose provider has never answered is not called vanished.
    md.CACHE_PATH.unlink()
    md.reset()
    assert _model_problems(_llm_graph("ollama/qwen3:14b")) == []
    # a disabled node is never checked.
    graph = _llm_graph("acme/gone")
    graph["nodes"][1]["disabled"] = True
    assert _model_problems(graph) == []


# ------------------------------------------------------------ endpoints API

def test_endpoint_routes_store_the_key_as_a_secret(fresh):
    res = client.put("/api/connections/endpoints/openrouter",
                     json={"base_url": "https://openrouter.ai/api/v1/", "key": "test-or-key"})
    assert res.status_code == 200
    assert res.json()["endpoint"] == {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1",
                                      "key_secret": "OPENROUTER_API_KEY", "has_key": True}
    assert secrets.get_secret("OPENROUTER_API_KEY") == "test-or-key"
    listed = client.get("/api/connections/endpoints").json()["endpoints"]
    assert listed == [res.json()["endpoint"]] and "test-or-key" not in json.dumps(listed)
    assert client.delete("/api/connections/endpoints/openrouter").status_code == 200
    assert client.delete("/api/connections/endpoints/openrouter").status_code == 404


@pytest.mark.parametrize("name, body", [
    ("ollama", {"base_url": "http://localhost:1234/v1"}),       # a built-in provider's name
    ("Bad Name", {"base_url": "http://localhost:1234/v1"}),
    ("lmstudio", {"base_url": "localhost:1234"}),               # no scheme
    ("lmstudio", {"base_url": "http://localhost:1234/v1?x=1"}),
])
def test_endpoint_routes_refuse_bad_input(fresh, name, body):
    res = client.put(f"/api/connections/endpoints/{name}", json=body)
    assert res.status_code == 400 and res.json()["error"]


@pytest.mark.parametrize("name, body", [
    # a built-in provider's name: its secret would be XAI_API_KEY and so on.
    ("xai", {"base_url": "https://api.x.ai/v1", "key": "replaced-key"}),
    ("anthropic", {"base_url": "https://api.anthropic.com/v1", "key": "replaced-key"}),
    ("openai", {"base_url": "https://api.openai.com/v1", "key": "replaced-key"}),
    ("Bad Name", {"base_url": "http://localhost:1234/v1", "key": "replaced-key"}),
    ("lmstudio", {"base_url": "localhost:1234", "key": "replaced-key"}),
    ("lmstudio", {"base_url": "http://localhost:1234/v1?x=1", "key": "replaced-key"}),
    # a valid endpoint that would write its key over a built-in provider's.
    ("proxy", {"base_url": "https://proxy.example/v1", "key": "replaced-key",
               "key_secret": "OPENAI_API_KEY"}),
    ("proxy", {"base_url": "https://proxy.example/v1", "key": "replaced-key",
               "key_secret": "not a name"}),
])
def test_a_refused_endpoint_stores_no_key(fresh, monkeypatch, name, body):
    for env in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(env, "original-key")
    before = dict(secrets._store)
    res = client.put(f"/api/connections/endpoints/{name}", json=body)
    assert res.status_code == 400 and res.json()["error"]
    assert secrets._store == before, "a refused request wrote a secret"
    for env in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        assert secrets.get_secret(env) == "original-key"
    assert secrets.get_secret("LMSTUDIO_API_KEY") is None
    assert client.get("/api/connections/endpoints").json()["endpoints"] == []


def test_an_endpoint_without_a_key_may_reuse_a_provider_key(fresh, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "original-key")
    res = client.put("/api/connections/endpoints/proxy",
                     json={"base_url": "https://proxy.example/v1", "key_secret": "OPENAI_API_KEY"})
    assert res.status_code == 200
    assert res.json()["endpoint"]["key_secret"] == "OPENAI_API_KEY"
    assert res.json()["endpoint"]["has_key"] is True
    assert secrets.get_secret("OPENAI_API_KEY") == "original-key"


def test_base_urls_take_the_sdk_shape():
    assert endpoints.normalise_base_url("http://localhost:1234") == "http://localhost:1234/v1"
    assert endpoints.normalise_base_url("https://api.groq.com/openai/v1/") == \
        "https://api.groq.com/openai/v1"
    assert endpoints.secret_name_for("my-vllm") == "MY_VLLM_API_KEY"


# ------------------------------------------------------------- lookup cost

def _big_endpoint_list(count: int = 200) -> None:
    """One keyed custom endpoint that lists `count` chat models."""
    endpoints.save("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
    secrets._store["OPENROUTER_API_KEY"] = "test-openrouter-key"
    found = [md.openai_manifest("openrouter", {
        "id": f"vendor/model-{i}", "architecture": {"output_modalities": ["text"]}})
        for i in range(count)]
    md.ensure_loaded()
    now = md._iso(md._now())
    md._state["openrouter"] = md.Listing(ok=True, checked=now, updated=now, models=found)
    md._publish()


def test_a_lookup_reads_the_endpoint_list_once(fresh, monkeypatch):
    _big_endpoint_list()
    calls: list[int] = []
    real = endpoints.list_endpoints
    monkeypatch.setattr(endpoints, "list_endpoints", lambda: calls.append(1) or real())
    body = md.payload()
    assert len(body["models"]) > 200
    assert len(calls) == 1, "one read for the whole list, not one per row"
    calls.clear()
    graph = {"nodes": [{"id": "go", "type": "core.trigger.manual"}] + [
        {"id": f"m{i}", "type": "core.ai.llm", "config": {"model": f"openrouter/vendor/gone-{i}"}}
        for i in range(5)], "edges": []}
    assert len(_model_problems(graph)) == 5
    assert len(calls) == 1, "one snapshot serves every model widget of a graph"


def test_the_endpoint_file_is_parsed_again_only_when_it_changes(fresh, monkeypatch):
    endpoints.save("lmstudio", "http://localhost:1234")
    reads: list[int] = []
    real = endpoints._read
    monkeypatch.setattr(endpoints, "_read", lambda: reads.append(1) or real())
    assert [e.name for e in endpoints.list_endpoints()] == ["lmstudio"]
    assert [e.name for e in endpoints.list_endpoints()] == ["lmstudio"]
    assert len(reads) == 1
    # an edit from outside (another size) is read on the next lookup.
    endpoints.PATH.write_text(json.dumps({
        "lmstudio": {"base_url": "http://localhost:1234/v1", "key_secret": ""},
        "vllm": {"base_url": "http://localhost:8000/v1", "key_secret": ""}}), encoding="utf-8")
    assert [e.name for e in endpoints.list_endpoints()] == ["lmstudio", "vllm"]
