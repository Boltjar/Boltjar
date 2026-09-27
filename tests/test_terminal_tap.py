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


def test_a_failed_power_on_shows_no_key_to_the_editor_or_the_terminal(monkeypatch):
    # the error event is built by the Hub, not the runtime, so it must get the
    # runtime's redaction on its own
    key = "sk-test-0123456789abcdef"
    monkeypatch.setitem(server._secrets._store, "TAP_TEST_KEY", key)
    seen: list[dict] = []
    monkeypatch.setattr(server.GRAPH_LINES, "feed", seen.append)

    def build(self, graph):
        raise RuntimeError(f"401 for https://api.example.com/v1/open?key={key}")

    monkeypatch.setattr(server.Runtime, "build", build)
    hub = server.Hub()
    hub.slug = "sd-key"
    editor: asyncio.Queue = asyncio.Queue()
    hub.subscribers.add(editor)
    graph = {"nodes": [{"id": "m", "type": "core.trigger.manual", "config": {}}], "edges": []}
    assert asyncio.run(hub.power_on(graph)) is None
    event = editor.get_nowait()
    assert event["kind"] == "error" and seen == [event]
    assert key not in event["error"] and "key={{secret.TAP_TEST_KEY}}" in event["error"]


def test_a_console_fault_never_reaches_the_runtime(monkeypatch):
    def broken(event):
        raise RuntimeError("the console broke")

    monkeypatch.setattr(server.GRAPH_LINES, "feed", broken)
    hub = server.Hub()
    queue: asyncio.Queue = asyncio.Queue()
    hub.subscribers.add(queue)
    hub.broadcast({"kind": "status", "power": "on"})
    assert queue.get_nowait()["power"] == "on"
