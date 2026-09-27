"""Tests for the per-slug Hub registry.

Each graph slug owns its own Hub; multiple graphs can be On concurrently and
their event streams must NOT cross. A WS with no slug defaults to "_default"
(back-compat with the original single-graph client). An idle hub is dropped
from the registry when its last subscriber leaves AND nothing is running.
"""
from __future__ import annotations

from local_client import local_client

from boltjar.server import HUBS, get_hub

client = local_client()


def _drain_until(ws, kind: str, max_frames: int = 12) -> dict:
    """Read frames until one matches `kind`; raise if we exhaust the budget."""
    for _ in range(max_frames):
        evt = ws.receive_json()
        if evt.get("kind") == kind:
            return evt
    raise AssertionError(f"never received a {kind!r} event after {max_frames} frames")


def test_default_slug_when_missing() -> None:
    """Back-compat: a connect with no `slug` query lands on '_default'."""
    HUBS.pop("_default", None)
    with client.websocket_connect("/ws") as ws:
        status = ws.receive_json()
        assert status["kind"] == "status"
        assert status["slug"] == "_default"
        assert status["power"] == "off"
    # last subscriber gone, runtime never started: hub dropped from the registry.
    assert "_default" not in HUBS


def test_two_slugs_do_not_cross_streams() -> None:
    """Each slug gets its OWN Hub. An action on slug A must not produce events
    on slug B's subscriber. We run a tiny Manual+Log graph on slug A and verify
    its events all carry slug=A while slug B sees only its own initial status."""
    HUBS.pop("graph-a", None)
    HUBS.pop("graph-b", None)

    graph = {
        "nodes": [
            {"id": "m", "type": "core.trigger.manual"},
            {"id": "log", "type": "core.output.log", "config": {"label": "t"}},
        ],
        "edges": [{"src": "m", "src_port": "trigger", "dst": "log", "dst_port": "in"}],
    }

    with client.websocket_connect("/ws?slug=graph-a") as ws_a, \
         client.websocket_connect("/ws?slug=graph-b") as ws_b:

        status_a = ws_a.receive_json()
        status_b = ws_b.receive_json()
        assert status_a["slug"] == "graph-a"
        assert status_b["slug"] == "graph-b"

        # Turn graph-a on; graph-b should be untouched.
        ws_a.send_json({"action": "on", "graph": graph})

        # Read enough events from A to see the manual trigger fire.
        a_events = [ws_a.receive_json() for _ in range(5)]
        assert all(e.get("slug") == "graph-a" for e in a_events), \
            f"every event on slug-a's stream must be stamped 'graph-a': {a_events}"
        assert any(e.get("kind") == "value" for e in a_events)

        ws_a.send_json({"action": "off"})

        # Hub B must still exist (subscriber attached), runtime still None.
        assert "graph-b" in HUBS
        assert HUBS["graph-b"].runtime is None


def test_idle_hub_dropped_when_last_subscriber_leaves() -> None:
    """When a slug's runtime is off AND the last subscriber disconnects, the
    Hub is evicted from HUBS so we don't leak per-slug state forever."""
    HUBS.pop("ephemeral", None)
    with client.websocket_connect("/ws?slug=ephemeral") as ws:
        status = ws.receive_json()
        assert status["slug"] == "ephemeral"
        # Hub exists while we're attached.
        assert "ephemeral" in HUBS
        assert HUBS["ephemeral"].runtime is None
    # After disconnect: no subscribers, no runtime -> removed.
    assert "ephemeral" not in HUBS


def test_get_hub_is_lazy_and_idempotent() -> None:
    HUBS.pop("brandnew", None)
    h1 = get_hub("brandnew")
    h2 = get_hub("brandnew")
    assert h1 is h2
    assert h1.slug == "brandnew"
    # Cleanup so we don't leak state to other tests.
    HUBS.pop("brandnew", None)
