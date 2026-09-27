"""Auto-save versioning: every Save drops a timestamped snapshot under
user/autosave/<slug>/, capped at AUTOSAVE_MAX so backups never grow unbounded.
This is the safety net for a clobbered graph.

Isolation: the `iso` fixture repoints BOTH GRAPHS_DIR and AUTOSAVE_DIR at a tmp
dir, so a Save here never touches the project's real user/graphs/ or user/autosave/."""
import pytest
from local_client import local_client

import boltjar.server as server

client = local_client()


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Repoint the server's graph + autosave dirs at tmp; return the autosave dir."""
    monkeypatch.setattr(server, "GRAPHS_DIR", tmp_path / "graphs")
    monkeypatch.setattr(server, "AUTOSAVE_DIR", tmp_path / "auto")
    return tmp_path / "auto"


def _graph(n):
    return {"name": "vtest", "nodes": [{"id": f"n{i}", "type": "core.value.text",
                                        "config": {"text": str(i)}} for i in range(n)],
            "edges": []}


def test_save_writes_a_snapshot(iso):
    r = client.put("/api/graphs/vtest", json=_graph(3))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(list((iso / "vtest").glob("vtest_*.json"))) == 1, "one Save -> one snapshot"


def test_each_save_adds_a_snapshot(iso):
    for i in range(1, 4):
        client.put("/api/graphs/vtest", json=_graph(i))
    assert len(list((iso / "vtest").glob("vtest_*.json"))) == 3, "three Saves -> three snapshots"


def test_prune_keeps_only_max_newest(iso, monkeypatch):
    monkeypatch.setattr(server, "AUTOSAVE_MAX", 3)
    for i in range(6):
        client.put("/api/graphs/vtest", json=_graph(i + 1))
    snaps = list((iso / "vtest").glob("vtest_*.json"))
    assert len(snaps) == 3, f"capped at AUTOSAVE_MAX=3, got {len(snaps)}"


def test_versions_endpoint_lists_and_fetches(iso):
    client.put("/api/graphs/vtest", json=_graph(2))
    client.put("/api/graphs/vtest", json=_graph(5))
    versions = client.get("/api/graphs/vtest/versions").json()["versions"]
    assert len(versions) == 2
    assert all({"id", "savedAt", "bytes"} <= set(v) for v in versions)
    g = client.get(f"/api/graphs/vtest/versions/{versions[0]['id']}").json()
    assert isinstance(g.get("nodes"), list)


def test_versions_endpoint_empty_for_unknown(iso):
    assert client.get("/api/graphs/nope/versions").json() == {"versions": []}
    assert client.get("/api/graphs/nope/versions/whatever").status_code == 404


def test_snapshot_failure_never_breaks_save(tmp_path, monkeypatch):
    # point Auto-save at a file (not a dir) so the snapshot cannot be written, and
    # confirm the Save still succeeds: backups are best-effort.
    monkeypatch.setattr(server, "GRAPHS_DIR", tmp_path / "graphs")
    blocker = tmp_path / "blocked"
    blocker.write_text("not a dir")
    monkeypatch.setattr(server, "AUTOSAVE_DIR", blocker)
    r = client.put("/api/graphs/vtest", json=_graph(1))
    assert r.status_code == 200 and r.json() == {"ok": True}
