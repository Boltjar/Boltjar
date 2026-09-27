"""The new HTTP routes: POST /audio, GET /stream and POST /hook (404 + the resource caps)."""
from __future__ import annotations

from local_client import local_client

import boltjar.nodes.core  # noqa: F401  registers the nodes
import boltjar.server as server
from boltjar.server import (
    get_hub, HUBS, STREAM_MAX_CHANNELS, STREAM_MAX_SUBS_PER_CHANNEL,
    AUDIO_POST_MAX_BYTES,
)

client = local_client()


def test_audio_post_404_then_happy_path():
    assert client.post("/audio/nope-slug/n", json={"audio": "x"}).status_code == 404
    hub = get_hub("audio-test")
    hub.runtime = object()  # a non-None sentinel so the running-check passes
    sent = {}
    hub.send_audio = lambda node, audio, lang="": sent.update(node=node, audio=audio, lang=lang)
    try:
        r = client.post("/audio/audio-test/ain",
                        json={"audio": "data:audio/wav;base64,AAAA", "lang": "en"})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert sent == {"node": "ain", "audio": "data:audio/wav;base64,AAAA", "lang": "en"}
    finally:
        HUBS.pop("audio-test", None)


def test_audio_post_413_on_oversized_body():
    hub = get_hub("audio-big")
    hub.runtime = object()
    try:
        r = client.post("/audio/audio-big/ain", content=b"{}",
                        headers={"content-length": str(AUDIO_POST_MAX_BYTES + 1),
                                 "content-type": "application/json"})
        assert r.status_code == 413
    finally:
        HUBS.pop("audio-big", None)


def _chunked(total: int, size: int = 1024):
    """A body with no content-length: httpx sends it chunked."""
    for _ in range(0, total, size):
        yield b"x" * size


def test_audio_post_413_on_oversized_chunked_body(monkeypatch):
    monkeypatch.setattr(server, "AUDIO_POST_MAX_BYTES", 4096)
    hub = get_hub("audio-chunked")
    hub.runtime = object()
    try:
        r = client.post("/audio/audio-chunked/ain", content=_chunked(8192),
                        headers={"content-type": "application/json"})
        assert r.status_code == 413
    finally:
        HUBS.pop("audio-chunked", None)


def test_audio_post_needs_a_json_object():
    hub = get_hub("audio-json")
    hub.runtime = object()
    hub.send_audio = lambda *a, **k: None
    try:
        # text/plain is what a cross-site form or fetch can send without a preflight.
        r = client.post("/audio/audio-json/ain", content=b'{"audio": "x"}',
                        headers={"content-type": "text/plain"})
        assert r.status_code == 415
        r = client.post("/audio/audio-json/ain", content=b'["x"]',
                        headers={"content-type": "application/json"})
        assert r.status_code == 400
        r = client.post("/audio/audio-json/ain", content=b'{"audio": "x"}',
                        headers={"content-type": "application/json; charset=utf-8"})
        assert r.status_code == 200
    finally:
        HUBS.pop("audio-json", None)


def test_hook_413_on_oversized_body(monkeypatch):
    monkeypatch.setattr(server, "HOOK_POST_MAX_BYTES", 4096)
    graph = {"nodes": [{"id": "hook", "type": "core.trigger.webhook", "config": {"path": "in"}}],
             "edges": []}
    with local_client() as live:
        live.post("/api/runtime/hook-big/power", json={"action": "on", "graph": graph})
        try:
            assert live.post("/hook/hook-big/in", content=_chunked(8192)).status_code == 413
            r = live.post("/hook/hook-big/in", content=b"{}",
                          headers={"content-length": "999999"})
            assert r.status_code == 413
            assert live.post("/hook/hook-big/in", content=b"small").status_code == 200
        finally:
            live.post("/api/runtime/hook-big/power", json={"action": "off"})
            HUBS.pop("hook-big", None)


def test_stream_404_and_caps():
    assert client.get("/stream/nope-slug/avatar").status_code == 404
    hub = get_hub("stream-test")
    try:
        # fill the channel cap; a NEW channel beyond it is refused.
        for i in range(STREAM_MAX_CHANNELS):
            hub.stream_subscribers[f"ch{i}"] = set()
        assert client.get("/stream/stream-test/brand-new").status_code == 429
        # fill one channel's subscriber cap; another subscriber is refused.
        hub.stream_subscribers["ch0"] = {object() for _ in range(STREAM_MAX_SUBS_PER_CHANNEL)}
        assert client.get("/stream/stream-test/ch0").status_code == 429
    finally:
        HUBS.pop("stream-test", None)
