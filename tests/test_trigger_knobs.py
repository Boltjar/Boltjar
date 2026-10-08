"""A trigger reads the knobs it converted to inputs.

A fired node gets its converted knobs from their wires each time it fires. A
trigger never fires, so it pulls them itself: once as it starts, then each time
it reads its knobs (Interval before each wait, Schedule at each check, Agenda at
each poll, Webhook on each call). An unwired converted knob keeps its own value.
"""
from __future__ import annotations

import asyncio

from local_client import local_client

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.runtime import Runtime
from boltjar.sdk import Kind, Port, node
from boltjar.server import HUBS, validate_graph


@node(id="test.trigger_knobs.broken", name="Broken", kind=Kind.VALUE, category="Values",
      pulled=True, summary="A value whose read always fails.")
class _Broken:
    outputs = [Port("out", "float")]

    def run(self, **_):
        raise RuntimeError("no value today")


_READS: list[str] = []


@node(id="test.trigger_knobs.counted", name="Counted", kind=Kind.VALUE, category="Values",
      pulled=True, summary="A text value that counts its reads.")
class _Counted:
    outputs = [Port("out", "text")]

    def run(self, **_):
        _READS.append("read")
        return {"out": "s3cret"}


def _run(graph: dict, seconds: float) -> tuple[Runtime, list[dict]]:
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def main():
        await rt.run()
        await asyncio.sleep(seconds)
        await rt.stop()

    asyncio.run(main())
    return rt, events


def _fires(events: list[dict], node_id: str) -> int:
    return sum(1 for e in events
               if e.get("kind") == "value" and e.get("node") == node_id and e.get("port") == "trigger")


def _interval(seconds: float, source: dict | None) -> dict:
    nodes = [{"id": "every", "type": "core.trigger.interval",
              "config": {"seconds": seconds, "promoted": ["seconds"]}}]
    edges = []
    if source is not None:
        nodes.append(source)
        edges.append({"src": source["id"], "src_port": "out", "dst": "every", "dst_port": "seconds"})
    return {"nodes": nodes, "edges": edges}


def test_an_interval_waits_the_seconds_on_its_wire():
    graph = _interval(60, {"id": "fast", "type": "core.value.float", "config": {"number": 0.05}})
    assert validate_graph(graph) == []
    _, events = _run(graph, 0.6)
    assert _fires(events, "every") >= 3  # the knob alone (60 s) would fire none


def test_an_unwired_converted_knob_keeps_its_own_value():
    _, events = _run(_interval(0.05, None), 0.6)
    assert _fires(events, "every") >= 3


def test_a_wire_that_fails_leaves_the_knob_and_says_why():
    rt, events = _run(_interval(0.05, {"id": "broken", "type": "test.trigger_knobs.broken"}), 0.6)
    assert _fires(events, "every") >= 3
    errors = [e for e in events if e.get("kind") == "node_error" and e.get("node") == "every"]
    assert errors and "reading the wire into seconds" in errors[0]["error"]
    assert "no value today" in errors[0]["error"]


def test_a_schedule_reads_its_wired_cron_as_it_starts():
    graph = {
        "nodes": [
            {"id": "sched", "type": "core.trigger.schedule",
             "config": {"cron": "0 * * * *", "promoted": ["cron"]}},
            {"id": "cron", "type": "core.value.text", "config": {"text": "*/5 * * * *"}},
        ],
        "edges": [{"src": "cron", "src_port": "out", "dst": "sched", "dst_port": "cron"}],
    }
    assert validate_graph(graph) == []
    rt, _ = _run(graph, 0.05)
    assert rt.nodes["sched"].obj._node_cfg["cron"] == "*/5 * * * *"


# ------------------------------------------------------------------ Webhook
client = local_client()


def _power_on(slug: str, graph: dict):
    HUBS.pop(slug, None)
    ws = client.websocket_connect(f"/ws?slug={slug}").__enter__()
    ws.receive_json()  # the Off status a new socket gets
    ws.send_json({"action": "on", "graph": graph})
    for _ in range(16):
        event = ws.receive_json()
        if event.get("kind") == "status":
            assert event.get("power") == "on", event
            return ws
    raise AssertionError("the graph never turned On")


def _close(ws) -> None:
    ws.send_json({"action": "off"})
    ws.__exit__(None, None, None)


def _hook(knobs: dict, promoted: list[str], sources: list[dict]) -> dict:
    config = {"path": "foo", "method": "POST", "secret": "", **knobs, "promoted": promoted}
    nodes = [{"id": "hook", "type": "core.trigger.webhook", "config": config},
             {"id": "log", "type": "core.output.log", "config": {}}]
    edges = [{"src": "hook", "src_port": "trigger", "dst": "log", "dst_port": "in"}]
    for source in sources:
        nodes.append(source["node"])
        edges.append({"src": source["node"]["id"], "src_port": source["port"],
                      "dst": "hook", "dst_port": source["knob"]})
    return {"nodes": nodes, "edges": edges}


def test_a_webhook_checks_the_secret_on_its_wire():
    graph = _hook({}, ["secret"], [{"node": {"id": "sec", "type": "core.value.text",
                                             "config": {"text": "s3cret"}},
                                    "port": "out", "knob": "secret"}])
    assert validate_graph(graph) == []
    slug = "wh-wired-secret"
    ws = _power_on(slug, graph)
    try:
        assert client.post(f"/hook/{slug}/foo", json={}).status_code == 401
        assert client.post(f"/hook/{slug}/foo", json={},
                           headers={"X-Webhook-Secret": "wrong"}).status_code == 401
        assert client.post(f"/hook/{slug}/foo", json={},
                           headers={"X-Webhook-Secret": "s3cret"}).status_code == 200
    finally:
        _close(ws)


def test_a_webhook_whose_wired_secret_has_no_value_refuses_every_call():
    # a Chat Input's `text` has no value until a message is sent: the empty
    # secret must not turn the check off.
    graph = _hook({}, ["secret"], [{"node": {"id": "chat", "type": "core.trigger.chat", "config": {}},
                                    "port": "text", "knob": "secret"}])
    assert validate_graph(graph) == []
    slug = "wh-wired-empty-secret"
    ws = _power_on(slug, graph)
    try:
        for headers in ({}, {"X-Webhook-Secret": ""}, {"X-Webhook-Secret": "anything"}):
            resp = client.post(f"/hook/{slug}/foo", json={}, headers=headers)
            assert resp.status_code == 503, headers
            assert resp.json() == {"error": "Webhook secret is wired but has no value"}
    finally:
        _close(ws)


def test_a_webhook_answers_on_the_path_its_wire_gives():
    graph = _hook({}, ["path"], [{"node": {"id": "where", "type": "core.value.text",
                                           "config": {"text": "from-wire"}},
                                  "port": "out", "knob": "path"}])
    slug = "wh-wired-path"
    ws = _power_on(slug, graph)
    try:
        assert client.post(f"/hook/{slug}/from-wire", json={}).status_code == 200
        assert client.post(f"/hook/{slug}/foo", json={}).status_code == 404
    finally:
        _close(ws)


def test_a_call_reads_the_secret_wire_of_the_webhook_it_is_for_only():
    graph = _hook({}, [], [])
    # the other Webhook comes first, so the call for `foo` walks past it.
    graph["nodes"].insert(0, {"id": "other", "type": "core.trigger.webhook",
                           "config": {"path": "bar", "method": "POST", "secret": "",
                                      "promoted": ["secret"]}})
    graph["nodes"].append({"id": "count", "type": "test.trigger_knobs.counted", "config": {}})
    graph["edges"] += [{"src": "count", "src_port": "out", "dst": "other", "dst_port": "secret"},
                       {"src": "other", "src_port": "trigger", "dst": "log", "dst_port": "in"}]
    assert validate_graph(graph) == []
    slug = "wh-secret-read-once"
    ws = _power_on(slug, graph)
    try:
        before = len(_READS)
        assert client.post(f"/hook/{slug}/foo", json={}).status_code == 200
        assert len(_READS) == before
        assert client.post(f"/hook/{slug}/bar", json={},
                           headers={"X-Webhook-Secret": "s3cret"}).status_code == 200
        assert len(_READS) == before + 1
    finally:
        _close(ws)
