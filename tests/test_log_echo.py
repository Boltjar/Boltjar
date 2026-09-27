"""The Log node writes to the editor console AND the server terminal: the
editor gets the value whole, the terminal one summarized line cut from that
same message, so nothing reaches the terminal that the live event does not carry."""
from __future__ import annotations

import asyncio
import base64
import logging
import struct
from typing import Callable

from boltjar.console import GraphLines
from boltjar.runtime import Runtime
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)

KEY = "not-a-real-key-0123456789abcdefghijklmnop"


def _run(graph: dict, act=None, seconds: float = 0.05, observer=None) -> list[dict]:
    events: list[dict] = []

    async def scenario():
        rt = Runtime(observer=observer or events.append)
        rt.build(graph)
        await rt.run()
        if act:
            act(rt)
        await asyncio.sleep(seconds)
        await rt.stop()

    asyncio.run(scenario())
    return events


def _chat_into_log(label: str) -> dict:
    return {
        "nodes": [{"id": "c", "type": "core.trigger.chat", "config": {}},
                  {"id": "lg", "type": "core.output.log", "config": {"label": label}}],
        "edges": [{"src": "c", "src_port": "text", "dst": "lg", "dst_port": "in"}],
    }


def _terminal(caplog) -> tuple[GraphLines, Callable[[], list[str]]]:
    caplog.set_level(logging.DEBUG, logger="test.log_echo")
    lines = GraphLines(logging.getLogger("test.log_echo"))
    return lines, lambda: [r.detail for r in caplog.records if r.event == "Log"]


def test_the_log_node_echoes_a_summary_to_the_terminal(caplog):
    rate, frames = 8000, 8000  # one second of 8-bit mono
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate, 1, 8)
    body = b"WAVEfmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", frames) + b"\x80" * frames
    clip = "data:audio/wav;base64," + base64.b64encode(b"RIFF" + struct.pack("<I", len(body)) + body).decode()
    events = _run(_chat_into_log("clip"), act=lambda rt: rt.send_chat("c", clip))
    (log,) = [e for e in events if e["kind"] == "log"]
    assert log["message"] == f"clip: {clip}"  # the editor still gets the value whole
    assert log["echo"] is True  # a flag, never a second copy of the value

    lines, printed = _terminal(caplog)
    lines.feed({**log, "slug": "chat"})
    assert printed() == ["clip: <audio/wav · 7.9 KB · 1.0 s>"]


def test_a_key_the_event_hides_never_reaches_the_terminal_in_part(caplog):
    # the key straddles where the terminal cuts a long line, so a summary made
    # before the server hides it would keep its first characters.
    value = "x" * 140 + KEY + "y" * 200
    lines, printed = _terminal(caplog)
    broadcast: list[dict] = []

    def hide_the_key(event: dict) -> None:
        # stands in for the server hiding a known secret in every live event
        event = {k: v.replace(KEY, "{{secret.API_KEY}}") if isinstance(v, str) else v
                 for k, v in event.items()}
        broadcast.append(event)
        lines.feed({**event, "slug": "chat"})

    _run(_chat_into_log("http"), act=lambda rt: rt.send_chat("c", value), observer=hide_the_key)
    (log,) = [e for e in broadcast if e["kind"] == "log"]
    assert log["echo"] is True
    (line,) = printed()
    assert line.startswith("http: xxx") and line.endswith("chars)")
    fields = [str(v) for event in broadcast for v in event.values()] + [line]
    assert not any(KEY[:6] in field for field in fields)


def test_other_log_lines_stay_in_the_editor():
    graph = {
        "nodes": [{"id": "m", "type": "core.trigger.manual", "config": {}},
                  {"id": "p", "type": "core.output.preview", "config": {}}],
        "edges": [{"src": "m", "src_port": "trigger", "dst": "p", "dst_port": "in"}],
    }
    logs = [e for e in _run(graph) if e["kind"] == "log"]
    assert logs and all("echo" not in e for e in logs)
