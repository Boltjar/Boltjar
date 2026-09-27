"""The semantic-memory pack nodes: Chunk, Embed, Rerank, Vectors (index/search)."""
from __future__ import annotations

import asyncio

import pytest

import boltjar.nodes.core  # noqa: F401  registers the nodes
import boltjar.nodes.core.builtin as builtin
from boltjar.sdk import NODE_REGISTRY
from boltjar.vector_store import VectorStore


def _node(node_id: str, cfg: dict):
    obj = NODE_REGISTRY[node_id].cls()
    obj._node_cfg = cfg
    obj._node_id = "n"
    return obj


def test_chunk_chars_and_words():
    c = _node("core.data.chunk", {"size": 5, "overlap": 1, "by": "chars"})
    # step = size - overlap = 4
    assert c.run(text="abcdefghij")["out"] == ["abcde", "efghi", "ij"]
    w = _node("core.data.chunk", {"size": 2, "overlap": 0, "by": "words"})
    assert w.run(text="one two three four five")["out"] == ["one two", "three four", "five"]
    assert _node("core.data.chunk", {}).run(text="")["out"] == []


def test_sentences_splits_on_punctuation_and_newlines():
    n = _node("core.data.sentences", {})
    assert n.run(text="Hello world. How are you? I am fine!")["out"] == [
        "Hello world.", "How are you?", "I am fine!"]
    assert n.run(text="line one\nline two")["out"] == ["line one", "line two"]
    assert n.run(text="no punctuation here")["out"] == ["no punctuation here"]
    assert n.run(text="")["out"] == []
    assert n.run(text="   ")["out"] == []
    # a closing quote stays with its sentence; the ellipsis char is one terminator
    assert n.run(text='He said "hi." Then left.')["out"] == ['He said "hi."', "Then left."]
    assert n.run(text="Wait… really?")["out"] == ["Wait…", "really?"]


def test_embed_node_uses_the_provider(monkeypatch):
    async def fake_embed(manifest, text):
        return [0.1, 0.2, 0.3]
    monkeypatch.setattr(builtin, "_embed_model", fake_embed)
    e = _node("core.ai.embed", {"model": "ollama/bge-m3"})
    assert asyncio.run(e.run(text="hello")) == {"embedding": [0.1, 0.2, 0.3], "trigger": True}


def test_rerank_reorders_by_score(monkeypatch):
    items = [{"text": "a", "id": 1}, {"text": "b", "id": 2}, {"text": "c", "id": 3}]

    async def scored(manifest, q, docs, endpoint):
        return [0.1, 0.9, 0.5]  # reorder by score desc
    monkeypatch.setattr(builtin, "_rerank_model", scored)
    r2 = _node("core.ai.rerank", {"model": "rerank/bge-v2-m3", "params": {"keep": 3}})
    out2 = asyncio.run(r2.run(query="x", candidates=items))
    assert [i["id"] for i in out2["results"]] == [2, 3, 1]


def test_rerank_fails_loud_when_backend_down(monkeypatch):
    # The regression this guards: a down reranker must FAIL the fire (raise), not
    # silently pass the candidates through in their original order.
    items = [{"text": "a", "id": 1}, {"text": "b", "id": 2}]

    async def down(manifest, q, docs, endpoint):
        raise builtin.RerankUnavailable(f"no rerank backend reachable at {endpoint!r}")
    monkeypatch.setattr(builtin, "_rerank_model", down)
    r = _node("core.ai.rerank", {"model": "rerank/bge-v2-m3"})
    with pytest.raises(builtin.RerankUnavailable):
        asyncio.run(r.run(query="x", candidates=items))


def test_rerank_empty_candidates_is_a_no_op(monkeypatch):
    # no candidates is a legitimate empty result, never an error.
    async def boom(*a, **k):
        raise AssertionError("must not call the backend with zero candidates")
    monkeypatch.setattr(builtin, "_rerank_model", boom)
    r = _node("core.ai.rerank", {"model": "rerank/bge-v2-m3"})
    assert asyncio.run(r.run(query="x", candidates=[])) == {"results": [], "trigger": True}


def _rerank_spy(monkeypatch) -> dict:
    seen: dict = {}

    async def spy(manifest, q, docs, endpoint):
        seen["endpoint"] = endpoint
        return [float(len(docs) - i) for i in range(len(docs))]
    monkeypatch.setattr(builtin, "_rerank_model", spy)
    return seen


def test_rerank_calls_the_endpoint_its_body_shows(monkeypatch):
    # the editor draws a model node's picker and its model's settings only, so
    # the manifest's `endpoint` setting is the address the node calls.
    seen = _rerank_spy(monkeypatch)
    r = _node("core.ai.rerank", {"model": "rerank/bge-v2-m3"})
    asyncio.run(r.run(query="x", candidates=[{"text": "a"}]))
    assert seen["endpoint"] == "http://localhost:8181/rerank"  # the manifest default
    r = _node("core.ai.rerank", {"model": "rerank/bge-v2-m3",
                                 "params": {"endpoint": "http://box:9999/rerank"}})
    asyncio.run(r.run(query="x", candidates=[{"text": "a"}]))
    assert seen["endpoint"] == "http://box:9999/rerank"


def test_rerank_settings_converted_to_inputs_take_their_wires(monkeypatch):
    seen = _rerank_spy(monkeypatch)
    r = _node("core.ai.rerank", {"model": "rerank/bge-v2-m3",
                                 "params": {"endpoint": "http://box:9999/rerank", "keep": 5},
                                 "promoted": ["endpoint", "keep"]})
    out = asyncio.run(r.run(query="x", candidates=[{"text": t} for t in "abcd"],
                            endpoint="http://wired:7000/rerank", keep=2))
    assert seen["endpoint"] == "http://wired:7000/rerank"
    assert [c["text"] for c in out["results"]] == ["a", "b"]


def test_rerank_without_an_endpoint_says_so(monkeypatch):
    _rerank_spy(monkeypatch)
    r = _node("core.ai.rerank", {"model": "rerank/bge-v2-m3", "params": {"endpoint": " "}})
    with pytest.raises(builtin.RerankUnavailable, match="set its `endpoint` setting"):
        asyncio.run(r.run(query="x", candidates=[{"text": "a"}]))


def test_rerank_model_raises_naming_the_url(monkeypatch):
    # drive the REAL _rerank_model against an unreachable endpoint: it must raise a
    # RerankUnavailable whose message names the url (so the fix is obvious).
    import httpx

    async def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        post = boom
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(builtin.RerankUnavailable) as ei:
        asyncio.run(builtin._rerank_model(None, "q", ["a", "b"],
                                          "http://localhost:8181/rerank"))
    assert "8181" in str(ei.value)


def test_vectors_index_then_search(monkeypatch, tmp_path):
    vs = VectorStore(root=tmp_path)
    monkeypatch.setattr(builtin, "_vector_store", lambda: vs)
    idx = _node("core.vectors", {"operation": "index", "namespace": "facts", "metadata": "ref=a"})
    asyncio.run(idx.run(vectors="s", text="east", embedding=[1, 0, 0]))
    idx2 = _node("core.vectors", {"operation": "index", "namespace": "facts", "metadata": "ref=b"})
    asyncio.run(idx2.run(vectors="s", text="north", embedding=[0, 1, 0]))
    srch = _node("core.vectors", {"operation": "search", "namespace": "facts",
                                  "top_k": 1, "min_score": 0.0})
    out = asyncio.run(srch.run(vectors="s", embedding=[1, 0, 0]))
    assert out["results"][0]["text"] == "east"
    assert out["results"][0]["ref"] == "a"
    assert out["ids"] == [out["results"][0]["id"]]


def test_vectors_multi_probe_union(monkeypatch, tmp_path):
    """Two query embeddings union by best-cosine-per-id (multi-probe search)."""
    vs = VectorStore(root=tmp_path)
    monkeypatch.setattr(builtin, "_vector_store", lambda: vs)
    for ref, vec, doc in [("a", [1, 0, 0], "east"), ("b", [0, 1, 0], "north")]:
        n = _node("core.vectors", {"operation": "index", "namespace": "ns", "metadata": f"ref={ref}"})
        asyncio.run(n.run(vectors="s", text=doc, embedding=vec))
    srch = _node("core.vectors", {"operation": "search", "namespace": "ns",
                                  "top_k": 5, "min_score": 0.0})
    # multi-probe: a LIST of two query vectors (east + north) -> both rows surface,
    # deduped by id (best cosine per id). Wire a List node of Embeds for this.
    out = asyncio.run(srch.run(vectors="s", embedding=[[1, 0, 0], [0, 1, 0]]))
    assert sorted(h["ref"] for h in out["results"]) == ["a", "b"]
    # a single vector still works as one probe.
    one = asyncio.run(srch.run(vectors="s", embedding=[1, 0, 0]))
    assert one["results"][0]["ref"] == "a"


def _vectors_graph(operation: str, *, embedding: bool) -> dict:
    """Manual -> Vectors, with a Vector Store wired and an Embed on `embedding`
    only when asked."""
    nodes = [{"id": "go", "type": "core.trigger.manual"},
             {"id": "vs", "type": "core.store.vectors"},
             {"id": "op", "type": "core.vectors",
              "config": {"operation": operation, "namespace": "facts", "ref": "a"}}]
    edges = [{"src": "go", "src_port": "trigger", "dst": "op", "dst_port": "trigger"},
             {"src": "vs", "src_port": "vectors", "dst": "op", "dst_port": "vectors"}]
    if embedding:
        nodes.append({"id": "emb", "type": "core.ai.embed"})
        edges.append({"src": "emb", "src_port": "embedding", "dst": "op", "dst_port": "embedding"})
    return {"nodes": nodes, "edges": edges}


def test_vectors_needs_an_embedding_only_to_search_and_index():
    from boltjar.server import validate_graph

    def missing(graph: dict) -> list[str]:
        """The Vectors node's unwired inputs (the Embed's own are not the point)."""
        return [p["message"] for p in validate_graph(graph)
                if p["kind"] == "missing-input" and p["node"] == "op"]

    for op in ("search", "index"):
        assert missing(_vectors_graph(op, embedding=False)) == [
            "required input 'embedding' is not connected"], op
        assert missing(_vectors_graph(op, embedding=True)) == [], op
    for op in ("delete", "clear"):
        assert missing(_vectors_graph(op, embedding=False)) == [], op
    # a node saved before its operation was picked runs the default, search
    untouched = _vectors_graph("search", embedding=False)
    untouched["nodes"][2]["config"] = {}
    assert missing(untouched) == ["required input 'embedding' is not connected"]


@pytest.mark.parametrize("operation", ["delete", "clear"])
def test_vectors_delete_and_clear_run_without_an_embedding(operation, monkeypatch, tmp_path):
    from boltjar.runtime import Runtime

    vs = VectorStore(root=tmp_path)
    monkeypatch.setattr(builtin, "_vector_store", lambda: vs)
    vs.index("vs", "facts", [1.0, 0.0], "east", "a", {})  # the store the Vector Store node names
    events: list[dict] = []
    runtime = Runtime(observer=events.append)
    runtime.build(_vectors_graph(operation, embedding=False))

    async def drive() -> None:
        await runtime.run()
        await asyncio.sleep(0.3)
        await runtime.stop()

    asyncio.run(drive())
    assert not [e for e in events if e["kind"] == "node_error"], events
    affected = [e["value"] for e in events
                if e["kind"] == "value" and e["node"] == "op" and e["port"] == "affected"]
    assert affected == ["1"]  # a live value event carries its preview text
    assert vs.search("vs", "facts", [1.0, 0.0], 5, 0.0) == []
