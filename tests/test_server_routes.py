"""The new HTTP routes: POST /audio and GET /stream (404 + the resource caps)."""
from __future__ import annotations

from local_client import local_client

import boltjar.nodes.core  # noqa: F401  registers the nodes
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
