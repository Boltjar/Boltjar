"""Saved graph versions: every saved graph carries a version (a short hash of
its file's bytes), GET hands it over in the X-Graph-Version header, a save
answers with the new one, and every editor that has the graph open is told on
the websocket. Two browsers compare it to tell a stale local draft from the
saved copy, so a draft never shadows a graph saved elsewhere."""
from __future__ import annotations

import hashlib
import json

import pytest

from boltjar import dialogs, linked, server
from boltjar.server import HUBS
from tests.local_client import local_client

GRAPH = {"format": 2, "name": "gv", "nodes": [
    {"id": "Fire", "type": "core.trigger.manual", "config": {}, "pos": [0, 0]}], "edges": []}


def _more(graph: dict, node_id: str) -> dict:
    return dict(graph, nodes=graph["nodes"] + [
        {"id": node_id, "type": "core.value.text", "config": {"text": node_id}, "pos": [0, 200]}])


def _hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated graph folders, link record and system dialogs."""
    monkeypatch.setattr(server, "GRAPHS_DIR", tmp_path / "graphs")
    monkeypatch.setattr(server, "EXAMPLES_DIR", tmp_path / "examples")
    monkeypatch.setattr(server, "AUTOSAVE_DIR", tmp_path / "autosave")
    (tmp_path / "graphs").mkdir()
    (tmp_path / "examples").mkdir()
    monkeypatch.setattr(linked, "PATH", tmp_path / "data" / "linked_workflows.json")
    pick = {"path": None, "tmp": tmp_path}
    monkeypatch.setattr(dialogs, "ask_open", lambda: pick["path"])
    monkeypatch.setattr(dialogs, "ask_save", lambda name, folder="": pick["path"])
    return pick


def _drain_until(ws, kind: str, max_frames: int = 16) -> dict:
    for _ in range(max_frames):
        evt = ws.receive_json()
        if evt.get("kind") == kind:
            return evt
    raise AssertionError(f"never received a {kind!r} event after {max_frames} frames")


# ---------------------------------------------------------------- the version
def test_a_saved_graph_answers_with_the_hash_of_its_file(home):
    with local_client() as c:
        put = c.put("/api/graphs/gv", json=GRAPH).json()
        got = c.get("/api/graphs/gv")
    path = home["tmp"] / "graphs" / "gv.json"
    assert len(put["version"]) == 16
    assert put["version"] == _hash(path)
    assert got.headers["X-Graph-Version"] == put["version"]
    assert got.json()["nodes"][0]["id"] == "Fire"


def test_the_version_changes_with_the_file_and_only_with_it(home):
    with local_client() as c:
        first = c.put("/api/graphs/gv", json=GRAPH).json()["version"]
        again = c.put("/api/graphs/gv", json=GRAPH).json()["version"]
        changed = c.put("/api/graphs/gv", json=_more(GRAPH, "Note")).json()["version"]
        served = c.get("/api/graphs/gv").headers["X-Graph-Version"]
    assert again == first, "the same graph saved again is the same version"
    assert changed != first
    assert served == changed


def test_an_edit_made_outside_boltjar_is_a_new_version(home):
    with local_client() as c:
        saved = c.put("/api/graphs/gv", json=GRAPH).json()["version"]
        (home["tmp"] / "graphs" / "gv.json").write_text(json.dumps(_more(GRAPH, "ByHand")), encoding="utf-8")
        assert c.get("/api/graphs/gv").headers["X-Graph-Version"] != saved


def test_an_example_carries_a_version(home):
    example = home["tmp"] / "examples" / "starter.json"
    example.write_text(json.dumps(GRAPH), encoding="utf-8")
    with local_client() as c:
        r = c.get("/api/graphs/starter")
    assert r.status_code == 200
    assert r.headers["X-Graph-Version"] == _hash(example)


def test_a_missing_graph_has_no_version(home):
    with local_client() as c:
        r = c.get("/api/graphs/nothing-here")
    assert r.status_code == 404
    assert "X-Graph-Version" not in r.headers


def test_snapshots_are_not_versioned(home):
    with local_client() as c:
        c.put("/api/graphs/gv", json=GRAPH)
        snap = c.get("/api/graphs/gv/versions").json()["versions"][0]["id"]
        r = c.get(f"/api/graphs/gv/versions/{snap}")
    assert r.status_code == 200
    assert "X-Graph-Version" not in r.headers


def test_a_linked_file_is_versioned_the_same_way(home):
    f = home["tmp"] / "elsewhere" / "flow.json"
    f.parent.mkdir()
    f.write_text(json.dumps(GRAPH), encoding="utf-8")
    home["path"] = str(f)
    with local_client() as c:
        slug = c.post("/api/files/open").json()["slug"]
        assert c.get(f"/api/graphs/{slug}").headers["X-Graph-Version"] == _hash(f)
        put = c.put(f"/api/graphs/{slug}", json=_more(GRAPH, "Note")).json()
        assert put["version"] == _hash(f)
        assert c.get(f"/api/graphs/{slug}").headers["X-Graph-Version"] == put["version"]


def test_save_as_a_file_answers_with_its_version(home):
    target = home["tmp"] / "elsewhere" / "copy.json"
    target.parent.mkdir()
    home["path"] = str(target)
    with local_client() as c:
        body = c.post("/api/files/save-as", json={"graph": GRAPH, "name": "copy"}).json()
        assert body["version"] == _hash(target)
        assert c.get(f"/api/graphs/{body['slug']}").headers["X-Graph-Version"] == body["version"]


# ---------------------------------------------------------------- the broadcast
def test_a_save_tells_every_editor_that_has_the_graph_open(home):
    HUBS.pop("gv-live", None)
    with local_client() as c:
        with c.websocket_connect("/ws?slug=gv-live") as a, c.websocket_connect("/ws?slug=gv-live") as b:
            for ws in (a, b):
                _drain_until(ws, "live_graph")
            version = c.put("/api/graphs/gv-live", json=GRAPH).json()["version"]
            for ws in (a, b):
                evt = _drain_until(ws, "graph-saved")
                assert evt == {"kind": "graph-saved", "version": version, "slug": "gv-live"}
    HUBS.pop("gv-live", None)


def test_a_save_tells_only_the_editors_of_that_graph(home):
    HUBS.pop("gv-other", None)
    HUBS.pop("gv-mine", None)
    with local_client() as c:
        with c.websocket_connect("/ws?slug=gv-other") as other, c.websocket_connect("/ws?slug=gv-mine") as mine:
            _drain_until(other, "live_graph")
            _drain_until(mine, "live_graph")
            c.put("/api/graphs/gv-mine", json=GRAPH)
            assert _drain_until(mine, "graph-saved")["slug"] == "gv-mine"
            # the other editor's next frame is the Off it asks for, not a save
            other.send_json({"action": "off"})
            assert other.receive_json()["kind"] != "graph-saved"
    HUBS.pop("gv-other", None)
    HUBS.pop("gv-mine", None)


def test_a_save_with_no_editor_open_creates_no_hub(home):
    HUBS.pop("gv-quiet", None)
    with local_client() as c:
        assert c.put("/api/graphs/gv-quiet", json=GRAPH).json()["ok"] is True
    assert "gv-quiet" not in HUBS


def test_a_refused_save_tells_no_one(home):
    HUBS.pop("gv-refused", None)
    newer = dict(GRAPH, format=999)
    with local_client() as c:
        with c.websocket_connect("/ws?slug=gv-refused") as ws:
            _drain_until(ws, "live_graph")
            assert c.put("/api/graphs/gv-refused", json=newer).status_code == 422
            ws.send_json({"action": "off"})
            assert ws.receive_json()["kind"] != "graph-saved"
    HUBS.pop("gv-refused", None)
