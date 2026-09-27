"""Tests for the TTS node (core.ai.tts). It picks a model the same way the LLM
does: a kind=model widget; per-model knobs (voice, format, ElevenLabs
voice_id/stability/speed, xAI voice/language/codec/...) come from the selected
manifest (registered from boltjar/nodes/core/models/*.toml), and the node dispatches
through a provider registry keyed by `manifest.provider`.

Vendor calls are checked at the HTTP boundary (`vendor_http`, see conftest.py):
the real helper builds the real request and only the transport is fake."""
import asyncio
import base64

import httpx
import pytest

from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY
import boltjar.nodes.core  # noqa: F401  (registers nodes + loads manifests)
from boltjar import models

# synthetic Fish reference ids (32 hex chars, the shape Fish issues)
FISH_TEST_REFERENCE = "0123456789abcdef0123456789abcdef"
FISH_OTHER_REFERENCE = "fedcba9876543210fedcba9876543210"


def _tts_widgets():
    spec = NODE_REGISTRY["core.ai.tts"]
    return {w.name: w for w in spec.widgets}


def _tts(cfg: dict | None = None):
    inst = NODE_REGISTRY["core.ai.tts"].cls()
    if cfg is not None:
        inst._node_cfg = cfg
    return inst


@pytest.fixture
def keys(monkeypatch):
    """Fake vendor keys: a test never sees (or sends) a real one."""
    for name in ("FISH_API_KEY", "ELEVENLABS_API_KEY", "XAI_API_KEY"):
        monkeypatch.setenv(name, f"test-{name.lower()}")


def test_tts_uses_the_unified_model_picker():
    widgets = _tts_widgets()
    # Same machinery as the LLM: one kind=model widget. Per-model params (voice,
    # format, ...) come from the chosen manifest and render below as ordinary knobs.
    assert "model" in widgets
    assert widgets["model"].kind == "model"


def test_tts_manifests_registered_under_kind_tts():
    ids = {m.id for m in models.MODELS.values() if m.kind == "tts"}
    assert {"fish/s2", "elevenlabs/multilingual_v2", "xai/tts"} <= ids


def test_tts_ports():
    spec = NODE_REGISTRY["core.ai.tts"]
    # fires on a dedicated `trigger` (like the LLM); `text` is pulled data; an
    # optional `lang` overrides the language for providers that take one (xAI).
    ins = {p.name: p for p in spec.inputs}
    assert set(ins) == {"trigger", "text", "lang"}
    assert ins["trigger"].trigger is True
    assert ins["text"].trigger is False
    assert ins["lang"].type == "lang" and ins["lang"].optional is True
    assert ins["lang"].trigger is False
    assert {p.name for p in spec.outputs} == {"audio", "trigger"}


# ---------------------------------------------------------------- Fish (repair)
def test_fish_payload_sends_chunk_length_100_by_default(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"RIFFwav")
    inst = _tts({"model": "fish/s2", "params": {"voice": FISH_TEST_REFERENCE}})
    out = asyncio.run(inst.run(text="hi"))
    req = vendor_http.last
    assert str(req.url) == "https://api.fish.audio/v1/tts"
    assert req.headers["authorization"] == "Bearer test-fish_api_key"
    assert req.headers["model"] == "s2-pro"
    assert vendor_http.last_json() == {
        "text": "hi", "reference_id": FISH_TEST_REFERENCE, "format": "wav",
        "normalize": True, "latency": "normal", "chunk_length": 100,
    }
    assert out == {"audio": "data:audio/wav;base64," + base64.b64encode(b"RIFFwav").decode(),
                   "trigger": True}


@pytest.mark.parametrize("saved, sent", [(50, 100), (200, 200), (400, 300)])
def test_fish_chunk_length_is_clamped_to_fish_range(vendor_http, keys, saved, sent):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    inst = _tts({"model": "fish/s2",
                 "params": {"voice": FISH_TEST_REFERENCE, "chunk_length": saved}})
    asyncio.run(inst.run(text="hi"))
    assert vendor_http.last_json()["chunk_length"] == sent


def test_fish_400_raises_with_the_response_body(vendor_http, keys):
    body = '{"message":"Invalid chunk_length 50. chunk_length must be in [100, 300]."}'
    vendor_http.reply = lambda r: httpx.Response(400, text=body)
    inst = _tts({"model": "fish/s2", "params": {"voice": FISH_TEST_REFERENCE}})
    with pytest.raises(RuntimeError, match=r"Fish 400: .*Invalid chunk_length 50"):
        asyncio.run(inst.run(text="hi"))


def test_fish_raw_reference_id_passes_through(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    raw = FISH_OTHER_REFERENCE
    asyncio.run(_tts({"model": "fish/s2", "params": {"voice": raw}}).run(text="hi"))
    assert vendor_http.last_json()["reference_id"] == raw   # a raw reference id passes through


@pytest.mark.parametrize("params", [{}, {"voice": ""}], ids=["manifest-default", "cleared"])
def test_fish_without_a_voice_asks_for_a_reference_id(vendor_http, keys, params):
    # Fish has no built-in voice: an empty voice (the manifest default) raises a
    # clear error instead of picking some default clone, and no call is made.
    inst = _tts({"model": "fish/s2", "params": params})
    with pytest.raises(RuntimeError, match="Fish: set a voice reference_id"):
        asyncio.run(inst.run(text="hi"))
    assert vendor_http.requests == []


@pytest.mark.parametrize("voice", ["default", "eve"])
def test_fish_unknown_voice_raises_and_names_it(vendor_http, keys, voice):
    # an unknown voice never silently becomes some default clone, and no call is made.
    inst = _tts({"model": "fish/s2", "params": {"voice": voice}})
    with pytest.raises(RuntimeError, match=f"unknown voice '{voice}'"):
        asyncio.run(inst.run(text="hi"))
    assert vendor_http.requests == []


# ---------------------------------------------------------------- registry
@pytest.mark.parametrize("model_id, params, url", [
    # Fish ships no default voice, so its case names one.
    ("fish/s2", {"voice": FISH_TEST_REFERENCE}, "https://api.fish.audio/v1/tts"),
    ("elevenlabs/multilingual_v2", {},
     "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM?output_format=mp3_44100_128"),
    ("xai/tts", {}, "https://api.x.ai/v1/tts"),
])
def test_tts_dispatches_by_manifest_provider(vendor_http, keys, model_id, params, url):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"clip")
    out = asyncio.run(_tts({"model": model_id, "params": params}).run(text="hi"))
    assert str(vendor_http.last.url) == url
    assert out["audio"].startswith("data:audio/")


def test_tts_without_a_model_uses_the_widget_default_xai(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"clip")
    asyncio.run(_tts({}).run(text="hi"))
    assert str(vendor_http.last.url) == "https://api.x.ai/v1/tts"


def test_tts_unknown_model_raises_and_names_it(vendor_http, keys):
    with pytest.raises(RuntimeError, match="nope/x"):
        asyncio.run(_tts({"model": "nope/x"}).run(text="hi"))
    assert vendor_http.requests == []


def test_tts_model_of_another_kind_raises(vendor_http, keys):
    # an LLM manifest on a TTS node is not a voice: never dispatched as one.
    with pytest.raises(RuntimeError, match="xai/grok-4.3"):
        asyncio.run(_tts({"model": "xai/grok-4.3"}).run(text="hi"))
    assert vendor_http.requests == []


def test_tts_unregistered_provider_raises_and_names_it(vendor_http, keys, monkeypatch):
    fake = models.ModelManifest(id="acme/voice", provider="acme", model="v1",
                                label="Acme", kind="tts", inputs=["text"], outputs=["audio"])
    monkeypatch.setitem(models.MODELS, "acme/voice", fake)
    with pytest.raises(RuntimeError, match="acme"):
        asyncio.run(_tts({"model": "acme/voice"}).run(text="hi"))
    assert vendor_http.requests == []


def test_elevenlabs_tts_error_raises_with_the_response_body(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(401, text='{"detail":"invalid api key"}')
    with pytest.raises(RuntimeError, match=r"ElevenLabs 401: .*invalid api key"):
        asyncio.run(_tts({"model": "elevenlabs/multilingual_v2"}).run(text="hi"))


def test_elevenlabs_empty_voice_id_raises_instead_of_a_default_voice(vendor_http, keys):
    inst = _tts({"model": "elevenlabs/multilingual_v2", "params": {"voice_id": " "}})
    with pytest.raises(RuntimeError, match="no voice_id"):
        asyncio.run(inst.run(text="hi"))
    assert vendor_http.requests == []


def test_elevenlabs_stability_zero_is_sent_as_zero(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    asyncio.run(_tts({"model": "elevenlabs/multilingual_v2",
                      "params": {"stability": 0.0}}).run(text="hi"))
    assert vendor_http.last_json()["voice_settings"]["stability"] == 0.0


def test_elevenlabs_tts_route_uses_eleven_adapter(monkeypatch):
    import boltjar.nodes.core.builtin as b
    captured = {}

    async def fake_eleven(text, voice_id, model_id, settings):
        captured.update(text=text, voice_id=voice_id, model_id=model_id, settings=settings)
        return "data:audio/mpeg;base64,AAAA"

    monkeypatch.setattr(b, "_eleven_tts", fake_eleven)
    inst = _tts({"model": "elevenlabs/multilingual_v2"})
    out = asyncio.run(inst.run(text="hello"))
    assert out["audio"].startswith("data:audio/")
    assert captured["text"] == "hello"
    assert captured["model_id"] == "eleven_multilingual_v2"
    # default voice + settings come from the manifest.
    assert captured["voice_id"] == "21m00Tcm4TlvDq8ikWAM"
    assert captured["settings"] == {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.0}


# ---------------------------------------------------------------- xAI TTS
def test_xai_tts_request_shape_with_manifest_defaults(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"ID3mp3",
                                                 headers={"content-type": "audio/mpeg"})
    out = asyncio.run(_tts({"model": "xai/tts"}).run(text="Hello, how are you?"))
    req = vendor_http.last
    assert req.method == "POST"
    assert str(req.url) == "https://api.x.ai/v1/tts"
    assert req.headers["authorization"] == "Bearer test-xai_api_key"
    assert vendor_http.last_json() == {
        "text": "Hello, how are you?", "voice_id": "eve", "language": "auto",
        "output_format": {"codec": "mp3", "sample_rate": 44100}, "speed": 1.0,
    }
    assert out == {"audio": "data:audio/mpeg;base64," + base64.b64encode(b"ID3mp3").decode(),
                   "trigger": True}


@pytest.mark.parametrize("codec, mime", [("mp3", "audio/mpeg"), ("wav", "audio/wav")])
def test_xai_tts_data_url_mime_follows_the_codec(vendor_http, keys, codec, mime):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"bytes")
    out = asyncio.run(_tts({"model": "xai/tts", "params": {"codec": codec}}).run(text="hi"))
    assert vendor_http.last_json()["output_format"]["codec"] == codec
    assert out["audio"] == f"data:{mime};base64," + base64.b64encode(b"bytes").decode()


def test_xai_tts_joins_a_streamed_body(vendor_http, keys):
    async def chunks():
        for part in (b"ab", b"cd", b"ef"):
            yield part

    vendor_http.reply = lambda r: httpx.Response(200, content=chunks())
    out = asyncio.run(_tts({"model": "xai/tts"}).run(text="hi"))
    assert out["audio"] == "data:audio/mpeg;base64," + base64.b64encode(b"abcdef").decode()


def test_xai_tts_voice_speed_and_sample_rate_come_from_params(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    cfg = {"model": "xai/tts", "params": {"voice": "rex", "speed": 1.25, "sample_rate": 24000}}
    asyncio.run(_tts(cfg).run(text="hi"))
    body = vendor_http.last_json()
    assert body["voice_id"] == "rex"
    assert body["speed"] == 1.25
    assert body["output_format"]["sample_rate"] == 24000


def test_xai_tts_lang_input_overrides_the_language_param(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    inst = _tts({"model": "xai/tts", "params": {"language": "fr"}})
    asyncio.run(inst.run(text="hi", lang="en"))
    assert vendor_http.last_json()["language"] == "en"
    asyncio.run(inst.run(text="hi", lang=""))   # empty lang: the param stands
    assert vendor_http.last_json()["language"] == "fr"


@pytest.mark.parametrize("lang, sent", [
    ("pt", "pt-BR"), ("PT-br", "pt-BR"), ("xx", "auto"), ("es", "es-ES"),
    ("EN", "en"), ("pt-PT", "pt-PT"), ("ja", "ja"), ("ar", "auto"),
    ("pt-br", "pt-BR"),   # what xAI STT itself reported live (2026-09-23)
])
def test_xai_tts_language_mapping(vendor_http, keys, lang, sent):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    asyncio.run(_tts({"model": "xai/tts"}).run(text="hi", lang=lang))
    assert vendor_http.last_json()["language"] == sent


def test_xai_tts_empty_text_makes_no_call(vendor_http, keys):
    out = asyncio.run(_tts({"model": "xai/tts"}).run(text=""))
    assert out == {"audio": "", "trigger": True}
    assert vendor_http.requests == []


def test_xai_tts_error_raises_with_the_response_body(vendor_http, keys):
    # streamed like the real response (a plain `text=` body would arrive
    # pre-read and hide a missing aread() before the body is used).
    async def body():
        yield b'{"error":"voice nova not found"}'

    vendor_http.reply = lambda r: httpx.Response(404, content=body())
    with pytest.raises(RuntimeError, match=r"xAI 404: .*voice nova not found"):
        asyncio.run(_tts({"model": "xai/tts"}).run(text="hi"))


def test_xai_tts_unsupported_codec_raises_and_names_it(vendor_http, keys):
    import boltjar.nodes.core.builtin as b
    with pytest.raises(RuntimeError, match="'flac'"):
        asyncio.run(b._xai_tts("hi", "eve", "en", codec="flac"))
    assert vendor_http.requests == []


def test_xai_tts_without_a_key_raises(vendor_http, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="XAI_API_KEY"):
        asyncio.run(_tts({"model": "xai/tts"}).run(text="hi"))
    assert vendor_http.requests == []


# ------------------------------------------- switching models never leaks params
def _run_graph(graph: dict, seconds: float = 0.5) -> list[dict]:
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        await asyncio.sleep(seconds)
        await rt.stop()

    asyncio.run(drive())
    return events


def test_switch_from_fish_to_xai_does_not_leak_the_fish_voice(vendor_http, keys):
    # a chat graph's TTS saved Fish params; the model is then switched to xai/tts
    # without the params being reset (a graph edited outside the editor). The
    # Fish voice must not reach xAI as voice_id, `format` must not leak at all,
    # and the rejected value is logged, naming the param.
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    stale = {"voice": FISH_OTHER_REFERENCE, "format": "wav"}
    graph = {
        "nodes": [
            {"id": "go", "type": "core.trigger.manual"},
            {"id": "line", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tts", "type": "core.ai.tts", "config": {"model": "xai/tts", "params": stale}},
        ],
        "edges": [
            {"src": "go", "src_port": "trigger", "dst": "tts", "dst_port": "trigger"},
            {"src": "line", "src_port": "out", "dst": "tts", "dst_port": "text"},
        ],
    }
    events = _run_graph(graph)
    assert not [e for e in events if e["kind"] == "node_error"], events
    body = vendor_http.last_json()
    assert body["voice_id"] == "eve"                       # the xai/tts default
    assert body["output_format"] == {"codec": "mp3", "sample_rate": 44100}
    assert "format" not in body
    logs = [e["message"] for e in events if e["kind"] == "log" and e["node"] == "tts"]
    assert any("voice" in m and FISH_OTHER_REFERENCE in m for m in logs), logs


def test_select_param_saved_as_text_matches_its_typed_option(vendor_http, keys):
    # a select knob or a wired value can carry "24000" for the int option 24000:
    # it is the same option (sent as the int), not a rejected value.
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    asyncio.run(_tts({"model": "xai/tts", "params": {"sample_rate": "24000"}}).run(text="hi"))
    assert vendor_http.last_json()["output_format"]["sample_rate"] == 24000


def test_select_param_outside_its_options_falls_back_to_the_default(vendor_http, keys):
    vendor_http.reply = lambda r: httpx.Response(200, content=b"x")
    logged: list[str] = []

    class Ctx:
        def log(self, *args):
            logged.append(" ".join(str(a) for a in args))

    inst = _tts({"model": "xai/tts", "params": {"sample_rate": 12345, "codec": "flac"}})
    inst._ctx = Ctx()
    asyncio.run(inst.run(text="hi"))
    assert vendor_http.last_json()["output_format"] == {"codec": "mp3", "sample_rate": 44100}
    assert any("sample_rate" in m and "12345" in m for m in logged), logged
    assert any("codec" in m and "flac" in m for m in logged), logged
