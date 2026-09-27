"""Tests for the STT node (core.ai.stt): the same kind=model picker the LLM uses,
dispatched through a provider registry keyed by `manifest.provider` (fish,
elevenlabs, xai). Fish ASR stays the default when no model is picked. Vendor
calls are checked at the HTTP boundary (`vendor_http`, see conftest.py)."""
import asyncio
import base64

import httpx
import pytest

from boltjar import models
from boltjar.sdk import NODE_REGISTRY
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)

CLIP_BYTES = b"ID3\x04fake-mp3-frames"
CLIP = "data:audio/mpeg;base64," + base64.b64encode(CLIP_BYTES).decode()


def _stt(cfg: dict | None = None):
    inst = NODE_REGISTRY["core.ai.stt"].cls()
    if cfg is not None:
        inst._node_cfg = cfg
    return inst


@pytest.fixture
def keys(monkeypatch):
    """Fake vendor keys: a test never sees (or sends) a real one."""
    for name in ("FISH_API_KEY", "ELEVENLABS_API_KEY", "XAI_API_KEY"):
        monkeypatch.setenv(name, f"test-{name.lower()}")


def test_stt_uses_the_unified_model_picker():
    # The STT node uses the same kind=model picker the LLM does. STT models
    # come from the manifest registry (filtered by kind=stt by the editor).
    spec = NODE_REGISTRY["core.ai.stt"]
    model = {w.name: w for w in spec.widgets}["model"]
    assert model.kind == "model"


def test_stt_routes_clip_through_fish(monkeypatch):
    # with no model picked, run() transcribes through Fish ASR (the widget
    # default); empty audio emits nothing without a network call.
    import boltjar.nodes.core.builtin as b
    captured = {}

    async def fake_stt(audio):
        captured["audio"] = audio
        return "the quick brown fox", "en"  # _fish_stt returns (text, lang)

    monkeypatch.setattr(b, "_fish_stt", fake_stt)
    inst = _stt()
    out = asyncio.run(inst.run(audio="data:audio/wav;base64,AAAA"))
    assert out["text"] == "the quick brown fox"
    assert out["lang"] == "en"             # detected language flows out
    assert captured["audio"] == "data:audio/wav;base64,AAAA"
    assert out["trigger"] is True
    captured.clear()
    empty = asyncio.run(inst.run(audio=""))
    assert empty == {"text": "", "lang": "", "trigger": True}
    assert captured == {}


def test_stt_routes_clip_through_eleven(monkeypatch):
    # an elevenlabs manifest routes to _eleven_stt (model threaded through) and the
    # lang comes from the provider's language_code (mapped to the second tuple slot).
    import boltjar.nodes.core.builtin as b
    captured = {}

    async def fake_eleven(audio, model_id):
        captured["audio"], captured["model"] = audio, model_id
        return "hola", "es"

    async def fake_fish(audio):
        captured["fish"] = True
        return "wrong", ""

    monkeypatch.setattr(b, "_eleven_stt", fake_eleven)
    monkeypatch.setattr(b, "_fish_stt", fake_fish)
    out = asyncio.run(_stt({"model": "elevenlabs/scribe_v2"}).run(audio="data:audio/wav;base64,AAAA"))
    assert out["text"] == "hola"
    assert out["lang"] == "es"          # from language_code, via the eleven branch
    assert captured["model"] == "scribe_v2"
    assert "fish" not in captured       # the fish path was not taken


def test_audio_to_bytes_decodes_data_url():
    from boltjar.nodes.core.builtin import _audio_to_bytes
    raw = b"RIFFxxxxWAVE"
    url = "data:audio/wav;base64," + base64.b64encode(raw).decode()
    assert _audio_to_bytes(url) == raw


# ---------------------------------------------------------------- registry
@pytest.mark.parametrize("model_id, url", [
    ("fish/asr", "https://api.fish.audio/v1/asr"),
    ("elevenlabs/scribe_v2", "https://api.elevenlabs.io/v1/speech-to-text"),
    ("xai/stt", "https://api.x.ai/v1/stt"),
])
def test_stt_dispatches_by_manifest_provider(vendor_http, keys, model_id, url):
    vendor_http.reply = lambda r: httpx.Response(200, json={"text": "ok", "language": "en"})
    out = asyncio.run(_stt({"model": model_id}).run(audio=CLIP))
    assert str(vendor_http.last.url) == url
    assert out["text"] == "ok"


def test_stt_without_a_model_uses_the_widget_default_fish(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, json={"text": "ok", "language": "en"})
    asyncio.run(_stt({}).run(audio=CLIP))
    assert str(vendor_http.last.url) == "https://api.fish.audio/v1/asr"


def test_stt_unknown_model_raises_and_names_it(vendor_http, keys):
    with pytest.raises(RuntimeError, match="nope/x"):
        asyncio.run(_stt({"model": "nope/x"}).run(audio=CLIP))
    assert vendor_http.requests == []


def test_stt_model_of_another_kind_raises(vendor_http, keys):
    with pytest.raises(RuntimeError, match="xai/tts"):
        asyncio.run(_stt({"model": "xai/tts"}).run(audio=CLIP))
    assert vendor_http.requests == []


def test_stt_unregistered_provider_raises_and_names_it(vendor_http, keys, monkeypatch):
    fake = models.ModelManifest(id="acme/ears", provider="acme", model="e1",
                                label="Acme", kind="stt", inputs=["audio"], outputs=["text"])
    monkeypatch.setitem(models.MODELS, "acme/ears", fake)
    with pytest.raises(RuntimeError, match="acme"):
        asyncio.run(_stt({"model": "acme/ears"}).run(audio=CLIP))
    assert vendor_http.requests == []


# ---------------------------------------------------------------- xAI STT
def test_xai_stt_multipart_puts_the_file_last(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, json={
        "text": " Bonjour tout le monde ", "language": "fr", "duration": 1.4, "words": []})
    out = asyncio.run(_stt({"model": "xai/stt"}).run(audio=CLIP))
    req = vendor_http.last
    assert req.method == "POST"
    assert str(req.url) == "https://api.x.ai/v1/stt"
    assert req.headers["authorization"] == "Bearer test-xai_api_key"
    parts = vendor_http.last_parts()
    # xAI ignores any field sent after the file, so `file` must be the LAST part.
    assert [name for name, _ in parts] == ["model", "file"]
    assert dict(parts)["model"] == b"grok-voice-transcribe-2.0"
    assert dict(parts)["file"] == CLIP_BYTES
    assert b'filename="clip.mp3"' in req.content
    assert out == {"text": "Bonjour tout le monde", "lang": "fr", "trigger": True}


def test_xai_stt_error_raises_with_the_response_body(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(415, text='{"error":"unsupported audio"}')
    with pytest.raises(RuntimeError, match=r"xAI 415: .*unsupported audio"):
        asyncio.run(_stt({"model": "xai/stt"}).run(audio=CLIP))


@pytest.mark.parametrize("model_id, vendor", [
    ("fish/asr", "Fish"), ("elevenlabs/scribe_v2", "ElevenLabs")])
def test_stt_vendor_errors_carry_the_response_body(vendor_http, keys, model_id, vendor):
    vendor_http.reply = lambda r: httpx.Response(401, text='{"detail":"bad key"}')
    with pytest.raises(RuntimeError, match=rf"{vendor} 401: .*bad key"):
        asyncio.run(_stt({"model": model_id}).run(audio=CLIP))
