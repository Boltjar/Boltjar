"""The terminal follows the live graphs through the Hub: every broadcast and
every rejected power-on passes the console tap, which can never break the runtime."""
from __future__ import annotations

import asyncio

from boltjar import server


def test_a_rejected_power_on_reaches_the_terminal(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(server.GRAPH_LINES, "feed", seen.append)
    hub = server.Hub()
    hub.slug = "sd-bad"
    problems = asyncio.run(hub.power_on({"nodes": [{"id": "x", "type": "nope"}], "edges": []}))
    assert problems
    assert seen[-1] == {"kind": "invalid", "problems": problems, "slug": "sd-bad"}


def test_every_broadcast_passes_the_terminal_tap(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(server.GRAPH_LINES, "feed", seen.append)
    hub = server.Hub()
    hub.slug = "sd-tap"
    hub.broadcast({"kind": "status", "power": "off"})
    assert seen == [{"kind": "status", "power": "off", "slug": "sd-tap"}]


def test_a_console_fault_never_reaches_the_runtime(monkeypatch):
    def broken(event):
        raise RuntimeError("the console broke")

    monkeypatch.setattr(server.GRAPH_LINES, "feed", broken)
    hub = server.Hub()
    queue: asyncio.Queue = asyncio.Queue()
    hub.subscribers.add(queue)
    hub.broadcast({"kind": "status", "power": "on"})
    assert queue.get_nowait()["power"] == "on"
