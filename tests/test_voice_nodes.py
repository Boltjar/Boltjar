"""Voice input + parse primitives: Tag Parse, Audio value/input and the
send_audio boundary."""
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
