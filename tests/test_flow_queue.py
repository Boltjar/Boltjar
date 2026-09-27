"""The Queue flow node (single serial lane, ack-gated backpressure)."""
from __future__ import annotations

import asyncio

import boltjar.nodes.core  # noqa: F401  registers the nodes
from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY


def test_queue_serializes_with_ack_backpressure():
    q = NODE_REGISTRY["core.flow.queue"].cls()
    # first arrival while idle -> releases immediately, lane goes busy.
    q._fired_port = "in_0"
    assert q.run(in_0="A") == {"out": "A", "count": 0}
    # second arrival while busy -> buffered, nothing released.
    q._fired_port = "in_1"
    assert q.run(in_1="B") == {"count": 1}
    # a third while still busy -> buffered behind B.
    q._fired_port = "in_2"
    assert q.run(in_2="C") == {"count": 2}
    # ack frees the lane -> release the FIFO head (B).
    q._fired_port = "ack"
    assert q.run() == {"out": "B", "count": 1}
    # ack again -> release C.
    q._fired_port = "ack"
    assert q.run() == {"out": "C", "count": 0}
    # ack with an empty buffer -> nothing to release.
    q._fired_port = "ack"
    assert q.run() == {"count": 0}


def test_queue_watchdog_frees_a_stuck_lane():
    # a job whose ack never fires must not wedge the lane forever: once busy past the
    # `timeout`, the next arrival self-heals the lane and releases the FIFO head.
    import time
    q = NODE_REGISTRY["core.flow.queue"].cls()
    q._node_cfg = {"timeout": 0.01}  # 10ms watchdog
    q._fired_port = "in_0"
    assert q.run(in_0="A") == {"out": "A", "count": 0}   # A released, lane busy
    q._fired_port = "in_1"
    assert q.run(in_1="B") == {"count": 1}               # B buffered (no ack)
    time.sleep(0.02)                                      # let the watchdog elapse
    q._fired_port = "in_2"
    out = q.run(in_2="C")
    assert out.get("out") == "B"                         # stuck lane self-healed, head released


def test_queue_unknown_fired_port_is_a_noop():
    q = NODE_REGISTRY["core.flow.queue"].cls()
    q._fired_port = "in_0"
    q.run(in_0="A")                                       # busy with A
    q._fired_port = "in_1"
    q.run(in_1="B")                                       # buffer B
    q._fired_port = ""                                    # no socket matched
    assert q.run() == {"count": 1}                        # nothing buffered, nothing released


def test_queue_growable_trigger_fires_through_runtime():
    """Two producers wired into one lane: only one releases until `ack` frees it.
    (Producers feed q.in_0 / q.in_1 via real edges, so the growable sockets exist.)"""
    rt = Runtime()
    released: list = []
    rt._observer = lambda e: (
        released.append(e["value"])
        if e.get("kind") == "value" and e.get("node") == "q" and e.get("port") == "out"
        else None
    )
    graph = {
        "nodes": [
            {"id": "c0", "type": "core.trigger.chat"},
            {"id": "c1", "type": "core.trigger.chat"},
            {"id": "q", "type": "core.flow.queue"},
        ],
        "edges": [
            {"src": "c0", "src_port": "trigger", "dst": "q", "dst_port": "in_0"},
            {"src": "c1", "src_port": "trigger", "dst": "q", "dst_port": "in_1"},
        ],
    }

    async def run():
        rt.build(graph)
        await rt.run()
        rt.send_chat("c0", "A")           # fires q.in_0 with payload "A" -> released
        await asyncio.sleep(0.05)
        rt.send_chat("c1", "B")           # fires q.in_1 with "B" -> buffered (busy)
        await asyncio.sleep(0.1)
        first = list(released)
        rt.nodes["q"].mailbox.put_nowait(("ack", 1, rt.new_turn()))  # free the lane
        await asyncio.sleep(0.1)
        await rt.stop()
        return first, list(released)

    first, after_ack = asyncio.run(run())
    assert first == ["A"]                 # backpressure held B
    assert after_ack == ["A", "B"]        # ack released the next


def test_queue_sockets_named_after_their_sources_fire_it():
    """The editor names each Queue socket after the node wired into it (`ask`,
    `nudge`), not in0: each still fires the Queue, and a promoted knob wired
    beside them (`timeout`) only sets the knob."""
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build({
        "nodes": [
            {"id": "ask", "type": "core.trigger.chat"},
            {"id": "nudge", "type": "core.trigger.chat"},
            {"id": "secs", "type": "core.value.integer", "config": {"number": 60}},
            {"id": "q", "type": "core.flow.queue", "config": {"promoted": ["timeout"]}},
        ],
        "edges": [
            {"src": "ask", "src_port": "text", "dst": "q", "dst_port": "ask"},
            {"src": "nudge", "src_port": "text", "dst": "q", "dst_port": "nudge"},
            {"src": "secs", "src_port": "out", "dst": "q", "dst_port": "timeout"},
        ],
    })
    q = rt.nodes["q"]
    assert q.is_trigger("ask") and q.is_trigger("nudge") and q.is_trigger("ack")
    assert not q.is_trigger("timeout")

    async def run():
        await rt.run()
        rt.send_chat("ask", "A")
        await asyncio.sleep(0.05)
        rt.send_chat("nudge", "B")
        await asyncio.sleep(0.05)
        q.mailbox.put_nowait(("ack", 1, rt.new_turn()))
        await asyncio.sleep(0.05)
        await rt.stop()

    asyncio.run(run())
    fires = [e for e in events if e["kind"] == "node_status" and e["node"] == "q"
             and e["status"] == "running"]
    assert len(fires) == 3, "two arrivals and one ack, each fires the Queue once"
    assert [e["value"] for e in events if e["kind"] == "value" and e["node"] == "q"
            and e["port"] == "out"] == ["A", "B"]
