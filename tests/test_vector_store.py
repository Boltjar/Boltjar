"""The embedded vector store: cosine search, min-score floor, namespaces, delete."""
from __future__ import annotations

from boltjar.vector_store import VectorStore


def test_index_and_cosine_search(tmp_path):
    vs = VectorStore(root=tmp_path)
    vs.index("s1", "facts", [1, 0, 0], doc="east", ref="a")
    vs.index("s1", "facts", [0, 1, 0], doc="north", ref="b")
    vs.index("s1", "facts", [0.9, 0.1, 0], doc="east-ish", ref="c")
    hits = vs.search("s1", "facts", [1, 0, 0], top_k=2)
    # the two closest to [1,0,0] are a (exact) then c (near), ranked by cosine.
    assert [h["ref"] for h in hits] == ["a", "c"]
    assert hits[0]["score"] > hits[1]["score"]
    assert hits[0]["text"] == "east"
    vs.close()


def test_min_score_floor(tmp_path):
    vs = VectorStore(root=tmp_path)
    vs.index("s", "ns", [1, 0, 0], ref="a")
    vs.index("s", "ns", [0, 1, 0], ref="b")  # orthogonal -> cosine 0
    hits = vs.search("s", "ns", [1, 0, 0], top_k=5, min_score=0.5)
    assert [h["ref"] for h in hits] == ["a"]  # b filtered out (0 < 0.5)
    vs.close()


def test_delete_and_namespaces_are_isolated(tmp_path):
    vs = VectorStore(root=tmp_path)
    vs.index("s", "facts", [1, 0], ref="a")
    vs.index("s", "msgs", [1, 0], ref="a")  # same ref, different namespace
    assert vs.delete("s", "facts", "a") == 1
    assert vs.count("s", "facts") == 0
    assert vs.count("s", "msgs") == 1  # the other namespace is untouched
    vs.close()


def test_dim_mismatch_returns_empty(tmp_path):
    vs = VectorStore(root=tmp_path)
    vs.index("s", "ns", [1, 0, 0], ref="a")
    # a query whose dimension differs from the stored vectors yields nothing,
    # rather than raising (lean: a misconfigured embed model never crashes search).
    assert vs.search("s", "ns", [1, 0], top_k=5) == []
    vs.close()


def test_clear_one_namespace_then_all_invalidates_cache(tmp_path):
    vs = VectorStore(root=tmp_path)
    vs.index("s", "facts", [1, 0], ref="a")
    vs.index("s", "msgs", [1, 0], ref="b")
    assert vs.search("s", "facts", [1, 0], top_k=1)        # warm the matrix cache
    assert vs.clear("s", "facts") == 1
    assert vs.count("s", "facts") == 0
    assert vs.search("s", "facts", [1, 0], top_k=1) == []  # cache invalidated, not stale
    assert vs.count("s", "msgs") == 1                       # other namespace untouched
    vs.index("s", "facts", [0, 1], ref="c")
    assert vs.clear("s", None) >= 2                         # drops every ns_ table
    assert vs.count("s", "facts") == 0 and vs.count("s", "msgs") == 0
    vs.close()


def test_destroy_removes_file_and_allows_reuse(tmp_path):
    import os
    vs = VectorStore(root=tmp_path)
    vs.index("s", "ns", [1, 0], ref="a")
    p = vs.path("s")
    assert os.path.isfile(p)
    vs.destroy("s")
    assert not os.path.isfile(p)                            # the file is gone, no undo
    # reuse after destroy: no stale connection/cache; a fresh index + search works.
    vs.index("s", "ns", [0, 1], ref="b")
    assert [h["ref"] for h in vs.search("s", "ns", [0, 1], top_k=5)] == ["b"]
    vs.close()


def test_mixed_dim_tie_break_prefers_larger_dimension(tmp_path):
    # equal counts of dim-3 and dim-2 rows -> a tie; the larger dimension wins
    # deterministically (not a hash-order artifact), so the same model's rows survive.
    vs = VectorStore(root=tmp_path)
    vs.index("s", "ns", [1, 0, 0], ref="big1")
    vs.index("s", "ns", [0, 1], ref="small1")
    vs.index("s", "ns", [0, 0, 1], ref="big2")
    vs.index("s", "ns", [1, 0], ref="small2")
    refs = {h["ref"] for h in vs.search("s", "ns", [1, 0, 0], top_k=5)}
    assert "big1" in refs and "big2" in refs                 # the dim-3 rows are kept
    assert "small1" not in refs and "small2" not in refs     # dim-2 dropped, deterministic
    vs.close()


def test_mixed_dimensions_in_one_namespace_do_not_crash(tmp_path):
    # a stray row of a different dimension (e.g. after an embed-model switch) must
    # NOT break ALL search on the namespace: it is dropped, search still works.
    vs = VectorStore(root=tmp_path)
    vs.index("s", "ns", [1, 0, 0], doc="a3", ref="a")    # dim 3 (modal)
    vs.index("s", "ns", [0, 1], doc="b2", ref="b")        # dim 2 (stray)
    vs.index("s", "ns", [0.9, 0.1, 0], doc="c3", ref="c")  # dim 3
    hits = vs.search("s", "ns", [1, 0, 0], top_k=5)
    refs = [h["ref"] for h in hits]
    assert "b" not in refs            # the mismatched-dimension row is excluded
    assert refs and refs[0] == "a"    # search still ranks the modal-dim rows
    vs.close()
