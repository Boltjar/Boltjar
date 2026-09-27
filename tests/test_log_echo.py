"""The Log node writes to the editor console AND the server terminal: the
editor gets the value whole, the terminal one summarized line."""
from __future__ import annotations

import asyncio
import base64
import struct

from boltjar.runtime import Runtime
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)


def _run(graph: dict, act=None, seconds: float = 0.05) -> list[dict]:
    events: list[dict] = []

    async def scenario():
        rt = Runtime(observer=events.append)
        rt.build(graph)
        await rt.run()
        if act:
            act(rt)
        await asyncio.sleep(seconds)
        await rt.stop()

    asyncio.run(scenario())
    return events


def test_the_log_node_echoes_a_summary_to_the_terminal():
    rate, frames = 8000, 8000  # one second of 8-bit mono
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate, 1, 8)
    body = b"WAVEfmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", frames) + b"\x80" * frames
    clip = "data:audio/wav;base64," + base64.b64encode(b"RIFF" + struct.pack("<I", len(body)) + body).decode()
    graph = {
        "nodes": [{"id": "c", "type": "core.trigger.chat", "config": {}},
                  {"id": "lg", "type": "core.output.log", "config": {"label": "clip"}}],
        "edges": [{"src": "c", "src_port": "text", "dst": "lg", "dst_port": "in"}],
    }
    events = _run(graph, act=lambda rt: rt.send_chat("c", clip))
    (log,) = [e for e in events if e["kind"] == "log"]
    assert log["message"] == f"clip: {clip}"  # the editor still gets the value whole
    assert log["echo"] == "clip: audio/wav · 7.9 KB · 1.0 s"


def test_other_log_lines_stay_in_the_editor():
    graph = {
        "nodes": [{"id": "m", "type": "core.trigger.manual", "config": {}},
                  {"id": "p", "type": "core.output.preview", "config": {}}],
        "edges": [{"src": "m", "src_port": "trigger", "dst": "p", "dst_port": "in"}],
    }
    logs = [e for e in _run(graph) if e["kind"] == "log"]
    assert logs and all("echo" not in e for e in logs)
