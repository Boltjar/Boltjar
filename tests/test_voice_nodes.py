"""Voice/avatar input + parse primitives: Tag Parse, Audio value/input,
the Avatar sink + the send_audio / stream-publish boundary."""
from __future__ import annotations

import asyncio

import boltjar.nodes.core  # noqa: F401  registers the nodes
from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY


def _node(nid: str, cfg: dict):
    o = NODE_REGISTRY[nid].cls()
    o._node_cfg = cfg
    o._node_id = "n"
    return o


def test_tag_parse_mood_action_and_clean():
    out = _node("core.parse.tags", {}).run(text="[excited] Let's go! [wave]")
    assert out["mood"] == "excited"        # from the default tag->mood map
    assert out["action"] == "wave"         # from the default tag->action map
    assert "🤩" in out["clean"]            # tag -> emoji in the display text
    assert "[" not in out["clean"]         # the raw tags are gone

    # strip mode: tags removed, no emoji
    out2 = _node("core.parse.tags", {"emoji": False}).run(text="[sad] oh no")
    assert out2["mood"] == "sad"
    assert out2["clean"] == "oh no"

    # no tags -> the default mood, text unchanged
    plain = _node("core.parse.tags", {}).run(text="just text")
    assert plain["mood"] == "neutral"
    assert plain["clean"] == "just text"
    assert plain["action"] is None

    # equal-priority tags tiebreak on text order (angry and sad are both priority 7)
    first = _node("core.parse.tags", {}).run(text="[angry] no [sad] wait")
    assert first["mood"] == "angry"


def test_tag_parse_priority_ranked_mood_beats_text_order():
    # sobbing (priority 9 -> sad) outranks curious (priority 6) even though curious
    # appears first: the highest-priority tag wins.
    out = _node("core.parse.tags", {}).run(text="[curious] hmm... [sobbing]")
    assert out["mood"] == "sad"


def test_tag_parse_emoji_cap_keeps_highest_priority():
    # max_emoji=2 keeps the two highest-priority emoji (laughing=9, sobbing=9),
    # dropping curious (6) and calm (4).
    out = _node("core.parse.tags", {"max_emoji": 2}).run(
        text="[laughing] a [curious] b [sobbing] c [calm] d")
    assert "😂" in out["clean"] and "😭" in out["clean"]
    assert "🤔" not in out["clean"] and "😌" not in out["clean"]


def test_audio_value_passthrough_and_default():
    a = NODE_REGISTRY["core.value.audio"].cls()
    a.src = "https://example.com/clip.wav"   # an https URL passes straight through
    assert a.value() == {"out": "https://example.com/clip.wav"}
    a.src = ""
    assert a.value() == {"out": ""}


def test_audio_input_trigger_ports():
    spec = NODE_REGISTRY["core.trigger.audio_in"]
    assert spec.kind.value == "trigger"
    assert [(p.name, p.type) for p in spec.outputs] == [
        ("trigger", "event"), ("audio", "audio"), ("lang", "lang")]


def test_send_audio_fires_audio_input():
    rt = Runtime()
    graph = {
        "nodes": [
            {"id": "ain", "type": "core.trigger.audio_in"},
            {"id": "log", "type": "core.output.log", "config": {}},
        ],
        "edges": [{"src": "ain", "src_port": "trigger", "dst": "log", "dst_port": "in"}],
    }

    async def run():
        rt.build(graph)
        await rt.run()
        rt.send_audio("ain", "data:audio/wav;base64,AAAA", "en")
        for _ in range(100):
            await asyncio.sleep(0.01)
            if rt.nodes["ain"].out_latch.get("audio"):
                break
        await rt.stop()

    asyncio.run(run())
    assert rt.nodes["ain"].out_latch.get("audio") == "data:audio/wav;base64,AAAA"
    assert rt.nodes["ain"].out_latch.get("lang") == "en"


def test_avatar_publishes_per_sentence_chunk_to_the_stream():
    published: list = []
    rt = Runtime(stream_observer=lambda ch, chunk: published.append((ch, chunk)))
    graph = {
        "nodes": [
            {"id": "m", "type": "core.trigger.manual"},
            {"id": "av", "type": "core.output.avatar", "config": {"channel": "avatar"}},
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi there"}},
            {"id": "mood", "type": "core.value.text", "config": {"text": "happy"}},
        ],
        "edges": [
            {"src": "m", "src_port": "trigger", "dst": "av", "dst_port": "trigger"},
            {"src": "txt", "src_port": "out", "dst": "av", "dst_port": "text"},
            {"src": "mood", "src_port": "out", "dst": "av", "dst_port": "mood"},
        ],
    }

    async def run():
        rt.build(graph)
        await rt.run()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if published:
                break
        await rt.stop()

    asyncio.run(run())
    assert published, "the Avatar sink published nothing to the stream"
    channel, chunk = published[0]
    assert channel == "avatar"
    assert chunk["text"] == "hi there"
    assert chunk["mood"] == "happy"
    assert chunk["is_chunk_final"] is True and chunk["done"] is False


def test_avatar_explicit_ports_win_over_utterance():
    spec = NODE_REGISTRY["core.output.avatar"]
    av = spec.cls()
    av._node_cfg = {"channel": "av"}
    published: list = []

    class FakeCtx:
        def publish(self, ch, chunk):
            published.append((ch, chunk))

        def log(self, *a):
            pass

    av.deliver(1, FakeCtx(), inputs={
        "utterance": {"text": "U", "mood": "sad", "action": "wave"}, "text": "P"})
    ch, chunk = published[0]
    assert ch == "av"
    assert chunk["text"] == "P"          # explicit port wins over utterance default
    assert chunk["mood"] == "sad"        # filled from utterance
    assert chunk["action"] == "wave"     # filled from utterance
    assert chunk["is_chunk_final"] is True and chunk["done"] is False

    # a non-dict utterance is ignored (only the explicit ports apply)
    published.clear()
    av.deliver(1, FakeCtx(), inputs={"utterance": "notadict", "text": "X"})
    assert published[0][1]["text"] == "X"
    assert published[0][1]["mood"] is None

    # the `done` input marks turn-end; default stays False
    published.clear()
    av.deliver(1, FakeCtx(), inputs={"text": "bye", "done": True})
    assert published[0][1]["done"] is True
    published.clear()
    av.deliver(1, FakeCtx(), inputs={"text": "hi"})
    assert published[0][1]["done"] is False


def test_avatar_stream_through_the_real_hub():
    """Integration: power_on wires stream_observer=hub.publish_stream, so an Avatar
    frame reaches a subscriber of its channel (what the /stream SSE route reads)."""
    from boltjar.server import get_hub, HUBS

    graph = {
        "nodes": [
            {"id": "m", "type": "core.trigger.manual"},
            {"id": "txt", "type": "core.value.text", "config": {"text": "hello"}},
            {"id": "av", "type": "core.output.avatar", "config": {"channel": "avatar"}},
        ],
        "edges": [
            {"src": "m", "src_port": "trigger", "dst": "av", "dst_port": "trigger"},
            {"src": "txt", "src_port": "out", "dst": "av", "dst_port": "text"},
        ],
    }

    async def run():
        hub = get_hub("av-test")
        q: asyncio.Queue = asyncio.Queue()
        hub.stream_subscribers.setdefault("avatar", set()).add(q)
        await hub.power_on(graph)
        try:
            return await asyncio.wait_for(q.get(), timeout=2.0)
        finally:
            await hub.power_off()
            HUBS.pop("av-test", None)

    chunk = asyncio.run(run())
    assert chunk["text"] == "hello"
