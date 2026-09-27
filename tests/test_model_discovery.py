"""The live model list (boltjar.model_discovery).

Each provider is faked at the HTTP boundary (`vendor_http`, see conftest.py)
with the response shapes the real endpoints return (checked live 2026-09-27):
Ollama GET /api/tags + POST /api/show, xAI GET /v1/language-models, Anthropic
GET /v1/models, and an OpenAI-compatible GET <base>/models. Covers the mapping
onto the manifest shape, the merge with the TOML manifests, and the cache and
its offline fallback.
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

OFFLINE = httpx.ConnectError("connection refused")


def vendors(*, ollama=True, xai=None, anthropic=None, openai=None, endpoints_=None):
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
        if url.startswith(md.XAI_MODELS_URL) and xai is not None:
            return xai(request) if callable(xai) else httpx.Response(200, json=xai)
        if url.startswith(md.ANTHROPIC_MODELS_URL) and anthropic is not None:
            return anthropic(request) if callable(anthropic) else httpx.Response(200, json=anthropic)
        if url.startswith("https://api.openai.com/v1/models") and openai is not None:
            return httpx.Response(200, json=openai)
        for base, body in (endpoints_ or {}).items():
            if url == f"{base}/models":
                return httpx.Response(200, json=body)
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
    assert set(m for m in got if m.startswith("openai/")) == {"openai/gpt-5"}
    by_url = {str(r.url): r for r in vendor_http.requests}
    assert by_url["https://openrouter.ai/api/v1/models"].headers["authorization"] == \
        "Bearer test-openrouter-key"
    assert "authorization" not in by_url["http://localhost:1234/v1/models"].headers
    assert by_url["https://api.openai.com/v1/models"].headers["authorization"] == \
        "Bearer test-openai-key"


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


def test_base_urls_take_the_sdk_shape():
    assert endpoints.normalise_base_url("http://localhost:1234") == "http://localhost:1234/v1"
    assert endpoints.normalise_base_url("https://api.groq.com/openai/v1/") == \
        "https://api.groq.com/openai/v1"
    assert endpoints.secret_name_for("my-vllm") == "MY_VLLM_API_KEY"
