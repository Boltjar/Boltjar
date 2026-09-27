"""End-to-end firing of a chat console graph (a frozen test fixture).

This locks the full chat round-trip, as verified manually in the editor:
a Chat Input message fires the LLM (offline mock here, so no network), whose
`response` reaches the `convo` (core.output.chat) node as the `reply`. It also
guards the `event` -> `trigger` firing-port wiring: if a future spec change leaves
the chat trigger edge stale (a src_port the chat no longer emits), this test fails
where graph validation would not (validation only checks the dst side).
"""
import asyncio
import json
import pathlib

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.runtime import Runtime

# a FROZEN console graph kept for this test, never examples/chat.json, which is
# free to change as the example evolves without breaking this test.
GRAPH = pathlib.Path(__file__).resolve().parent / "fixtures" / "chat-console.json"


def _load_mocked():
    g = json.loads(GRAPH.read_text(encoding="utf-8"))
    # force the LLM to the offline mock so the test never hits a real backend
    for n in g["nodes"]:
        if n["type"] == "core.ai.llm":
            n.setdefault("config", {})["model"] = "mock/echo"
    return g


def test_chat_fires_llm_and_reply_reaches_convo():
    g = _load_mocked()
    chat_id = next(n["id"] for n in g["nodes"] if n["type"] == "core.trigger.chat")
    llm_id = next(n["id"] for n in g["nodes"] if n["type"] == "core.ai.llm")
    convo_id = next(n["id"] for n in g["nodes"] if n["type"] == "core.output.chat")

    async def run():
        rt = Runtime()
        rt.build(g)
        await rt.run()
        rt.send_chat(chat_id, "hello there")
        # let the trigger propagate + the (sync mock) LLM fire + convo receive
        for _ in range(20):
            await asyncio.sleep(0.05)
            if rt.nodes[convo_id].latch.get("reply"):
                break
        llm_resp = rt.nodes[llm_id].out_latch.get("response")
        convo = dict(rt.nodes[convo_id].latch)
        await rt.stop()
        return llm_resp, convo

    llm_resp, convo = asyncio.run(run())
    # the LLM fired and produced a reply (the mock echoes the assembled prompt)
    assert llm_resp and "hello there" in llm_resp
    # the reply reached the conversation node, and the user turn is the typed text
    assert convo.get("reply") == llm_resp
    assert convo.get("user") == "hello there"


def test_chat_input_declares_no_knob():
    # the send box's hint is fixed ("Type a message..."): Chat Input has no
    # placeholder knob, so it draws no knob row under the send box.
    from boltjar.sdk import NODE_REGISTRY

    assert NODE_REGISTRY["core.trigger.chat"].widgets == []


def test_a_saved_placeholder_is_ignored():
    # a graph saved while Chat Input had a placeholder knob still validates and
    # runs: the frozen fixture keeps the value, and the node never reads it.
    from boltjar.server import validate_graph

    g = _load_mocked()
    chat = next(n for n in g["nodes"] if n["type"] == "core.trigger.chat")
    assert chat["config"].get("placeholder") == "Type a message..."
    # (its TTS model is not installed here; that problem is not the chat's)
    assert [p for p in validate_graph(g) if p["node"] == chat["id"]] == []

    async def run():
        rt = Runtime()
        rt.build(g)
        await rt.run()
        obj = rt.nodes[chat["id"]].obj
        await rt.stop()
        return obj

    assert not hasattr(asyncio.run(run()), "placeholder")
