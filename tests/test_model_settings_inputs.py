"""A model setting converted to an input, on every node with a model picker.

A model node draws its picker and the picked model's settings. Convert to input
turns a setting into a typed port on any of them, as on the LLM: the graph with
that wire turns On, and the node calls its model with the wired value (a
Rerank's `endpoint` and `keep`, a TTS `speed`).
"""
from __future__ import annotations

import asyncio
import json

import httpx

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.runtime import Runtime
from boltjar.server import validate_graph

EDGE_KINDS = {"bad-target-port", "bad-source-port", "type-mismatch"}


def _graph(node_type: str, config: dict, wires: dict[str, tuple[str, dict]]) -> dict:
    """A Manual firing a node of `node_type`; `wires` maps an input of that
    node to the (type, config) of a value node wired into it."""
    nodes = [{"id": "go", "type": "core.trigger.manual"},
             {"id": "node", "type": node_type, "config": config}]
    edges = [{"src": "go", "src_port": "trigger", "dst": "node", "dst_port": "trigger"}]
    for port, (src_type, src_config) in wires.items():
        nodes.append({"id": f"in_{port}", "type": src_type, "config": src_config})
        edges.append({"src": f"in_{port}", "src_port": "out", "dst": "node", "dst_port": port})
    return {"nodes": nodes, "edges": edges}


def _edge_problems(graph: dict) -> list[str]:
    return [p["message"] for p in validate_graph(graph) if p["kind"] in EDGE_KINDS]


def test_a_converted_setting_of_the_picked_model_is_an_input():
    # xai/tts declares `speed`.
    tts = _graph("core.ai.tts", {"model": "xai/tts", "promoted": ["speed"]},
                 {"speed": ("core.value.float", {"number": 1.2})})
    assert _edge_problems(tts) == []
    rerank = _graph("core.ai.rerank", {"model": "rerank/bge-v2-m3", "promoted": ["endpoint", "keep"]},
                    {"endpoint": ("core.value.text", {"text": "http://box:9000/rerank"}),
                     "keep": ("core.value.integer", {"number": 2})})
    assert _edge_problems(rerank) == []


def test_a_setting_the_model_lacks_or_that_stays_a_knob_is_no_input():
    # `chunk_length` is a Fish setting: xai/tts has none.
    lacks = _graph("core.ai.tts", {"model": "xai/tts", "promoted": ["chunk_length"]},
                   {"chunk_length": ("core.value.integer", {"number": 100})})
    assert _edge_problems(lacks) == [
        "edge in_chunk_length.out -> node.chunk_length: 'chunk_length' is not an input of TTS (core.ai.tts)"]
    # no model picked: no model's settings are inputs (none is chosen for the author).
    unpicked = _graph("core.ai.tts", {"promoted": ["speed"]},
                      {"speed": ("core.value.float", {"number": 1.2})})
    assert _edge_problems(unpicked) == [
        "edge in_speed.out -> node.speed: 'speed' is not an input of TTS (core.ai.tts)"]
    knob = _graph("core.ai.rerank", {"model": "rerank/bge-v2-m3"},
                  {"keep": ("core.value.integer", {"number": 2})})
    assert _edge_problems(knob) == [
        "edge in_keep.out -> node.keep: 'keep' is not an input of Rerank (core.ai.rerank)"]
    typed = _graph("core.ai.rerank", {"model": "rerank/bge-v2-m3", "promoted": ["keep"]},
                   {"keep": ("core.value.text", {"text": "2"})})
    assert _edge_problems(typed) == ["edge in_keep.out -> node.keep: text output cannot feed int input"]


def _run_until(graph: dict, node_id: str, port: str, prepare=None):
    rt = Runtime()
    rt.build(graph)
    if prepare:
        prepare(rt)

    async def main():
        await rt.run()  # the Manual fires the node at once
        for _ in range(300):
            await asyncio.sleep(0.01)
            if port in rt.nodes[node_id].out_latch:
                break
        value = rt.nodes[node_id].out_latch.get(port)
        await rt.stop()
        return value

    return asyncio.run(main())


def test_a_rerank_calls_the_wired_endpoint_and_keeps_the_wired_count(vendor_http):
    vendor_http.reply = lambda request: httpx.Response(200, json={"scores": [0.1, 0.9, 0.5]})
    graph = _graph("core.ai.rerank", {"model": "rerank/bge-v2-m3", "promoted": ["endpoint", "keep"],
                                      "params": {"endpoint": "http://localhost:8181/rerank", "keep": 5}},
                   {"endpoint": ("core.value.text", {"text": "http://box:9000/rerank"}),
                    "keep": ("core.value.integer", {"number": 2}),
                    "query": ("core.value.text", {"text": "which one"})})

    def candidates(rt):
        rt.nodes["node"].latch["candidates"] = [{"text": "a"}, {"text": "b"}, {"text": "c"}]

    results = _run_until(graph, "node", "results", candidates)
    assert str(vendor_http.last.url) == "http://box:9000/rerank"
    assert vendor_http.last_json()["query"] == "which one"
    assert results == [{"text": "b"}, {"text": "c"}]


def test_a_tts_speaks_at_the_wired_speed(vendor_http, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    vendor_http.reply = lambda request: httpx.Response(200, content=b"ID3 clip")
    graph = _graph("core.ai.tts", {"model": "xai/tts", "promoted": ["speed"]},
                   {"speed": ("core.value.float", {"number": 1.3}),
                    "text": ("core.value.text", {"text": "Hello there"})})
    assert _run_until(graph, "node", "audio")
    sent = json.loads(vendor_http.last.content)
    assert (sent["text"], sent["speed"]) == ("Hello there", 1.3)
