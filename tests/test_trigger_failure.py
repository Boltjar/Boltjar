"""A trigger whose own loop dies is reported like any node error: the graph
stops firing from it, so the editor and the terminal must hear why."""
from __future__ import annotations

import asyncio

import pytest

from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY, Kind, Port, node


@pytest.fixture
def broken_trigger():
    @node(id="test.trigger.broken", name="Broken", kind=Kind.TRIGGER, category="Test",
          summary="Dies as soon as it starts.")
    class Broken:
        outputs = [Port("trigger", "event")]

        async def start(self, ctx):
            raise ValueError("bad cron")

    yield "test.trigger.broken"
    NODE_REGISTRY.pop("test.trigger.broken", None)


def _run(graph: dict, seconds: float = 0.05) -> list[dict]:
    events: list[dict] = []

    async def scenario():
        rt = Runtime(observer=events.append)
        rt.build(graph)
        await rt.run()
        await asyncio.sleep(seconds)
        await rt.stop()

    asyncio.run(scenario())
    return events


def test_a_trigger_that_dies_is_reported_not_swallowed(broken_trigger):
    events = _run({"nodes": [{"id": "t", "type": broken_trigger}], "edges": []})
    assert [e for e in events if e["kind"] == "node_error"] == [
        {"kind": "node_error", "node": "t", "error": "trigger stopped: ValueError('bad cron')"}]
    assert any(e["kind"] == "log" and "bad cron" in e["message"] for e in events)
