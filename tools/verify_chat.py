"""Verify the chat console: inject a message, confirm it flows to a reply."""
import asyncio
import json
import pathlib

from boltjar.runtime import Runtime
import boltjar.nodes.core  # noqa: F401

graph = json.loads(pathlib.Path("examples/chat.json").read_text(encoding="utf-8"))
events: list[dict] = []
rt = Runtime(observer=events.append)
rt.build(graph)


async def go() -> None:
    await rt.run()
    rt.send_chat("chat", "are you there?")
    await asyncio.sleep(0.5)
    await rt.stop()


asyncio.run(go())
for e in events:
    if e["kind"] in ("value", "log"):
        print(e.get("node"), "|", e.get("port") or "log", "|", e.get("value") or e.get("message"))
