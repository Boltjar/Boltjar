"""Workflow files anywhere on this computer: Open... and Save as... through the
system dialog (stubbed here: no test ever shows a real one), the link record,
listing, loading, saving back in place, and forgetting."""
from __future__ import annotations

import json
import subprocess

import pytest

from boltjar import dialogs, linked, server
from tests.local_client import local_client

REMOTE_PEER = ("192.168.1.20", 50000)  # a browser on the LAN, through the token link
GRAPH = {"format": 2, "name": "flow", "nodes": [
    {"id": "Fire", "type": "core.trigger.manual", "config": {}, "pos": [0, 0]}], "edges": []}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated graph folders and link record; the dialog answers what `pick` holds."""
    monkeypatch.setattr(server, "GRAPHS_DIR", tmp_path / "graphs")
    monkeypatch.setattr(server, "EXAMPLES_DIR", tmp_path / "examples")
    monkeypatch.setattr(server, "AUTOSAVE_DIR", tmp_path / "autosave")
    (tmp_path / "graphs").mkdir()
    (tmp_path / "examples").mkdir()
    monkeypatch.setattr(linked, "PATH", tmp_path / "data" / "linked_workflows.json")
    pick = {"path": None, "asked": []}

    def fake_open():
        pick["asked"].append(("open",))
        return pick["path"]

    def fake_save(name, folder=""):
        pick["asked"].append(("save", name))
        return pick["path"]

    monkeypatch.setattr(dialogs, "ask_open", fake_open)
    monkeypatch.setattr(dialogs, "ask_save", fake_save)
    pick["tmp"] = tmp_path
    return pick


def _write(path, graph=GRAPH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph), encoding="utf-8")
    return path


# ---------------------------------------------------------------- open
def test_open_links_the_picked_file_and_lists_it(home):
    f = _write(home["tmp"] / "elsewhere" / "My Flow.json")
    home["path"] = str(f)
    with local_client() as c:
        r = c.post("/api/files/open")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slug"] == "my-flow" and body["linked"] is True
        assert body["graph"]["nodes"][0]["id"] == "Fire"
        listing = c.get("/api/graphs").json()
        assert "my-flow" in listing["graphs"]
        assert [e["slug"] for e in listing["files"]] == ["my-flow"]
        assert c.get("/api/graphs/my-flow").json()["nodes"][0]["id"] == "Fire"


def test_the_same_file_opened_twice_is_one_link(home):
    f = _write(home["tmp"] / "elsewhere" / "flow.json")
    home["path"] = str(f)
    with local_client() as c:
        a = c.post("/api/files/open").json()["slug"]
        b = c.post("/api/files/open").json()["slug"]
        assert a == b
        assert len(c.get("/api/graphs").json()["files"]) == 1


def test_a_linked_slug_never_takes_a_saved_graphs_name(home):
    _write(home["tmp"] / "graphs" / "flow.json")
    f = _write(home["tmp"] / "elsewhere" / "flow.json")
    home["path"] = str(f)
    with local_client() as c:
        assert c.post("/api/files/open").json()["slug"] == "flow-2"


def test_a_file_inside_user_graphs_opens_as_that_saved_workflow(home):
    f = _write(home["tmp"] / "graphs" / "kept.json")
    home["path"] = str(f)
    with local_client() as c:
        body = c.post("/api/files/open").json()
    assert body["slug"] == "kept" and body["linked"] is False
    assert not linked.PATH.exists()


def test_a_cancelled_dialog_opens_nothing(home):
    with local_client() as c:
        assert c.post("/api/files/open").json() == {"cancelled": True}
    assert not linked.PATH.exists()


def test_a_file_that_is_not_a_workflow_is_refused_and_not_linked(home):
    f = home["tmp"] / "elsewhere" / "package.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"name": "x", "version": "1"}), encoding="utf-8")
    home["path"] = str(f)
    with local_client() as c:
        r = c.post("/api/files/open")
    assert r.status_code == 422 and "not a workflow" in r.json()["error"]
    assert not linked.PATH.exists()


def test_a_remote_browser_cannot_open_or_save_files(home):
    with local_client(client=REMOTE_PEER) as c:
        assert c.post("/api/files/open").status_code == 403
        assert c.post("/api/files/save-as", json={"graph": GRAPH, "name": "x"}).status_code == 403
    assert home["asked"] == []  # no dialog was ever shown for them


def test_no_dialog_on_this_computer_says_why(home, monkeypatch):
    def unavailable():
        raise dialogs.DialogUnavailable("there is no display to show a file dialog on")
    monkeypatch.setattr(dialogs, "ask_open", unavailable)
    with local_client() as c:
        r = c.post("/api/files/open")
    assert r.status_code == 409 and "no display" in r.json()["unavailable"]


# ---------------------------------------------------------------- save, save as
def test_save_writes_a_linked_file_back_in_place(home):
    f = _write(home["tmp"] / "elsewhere" / "flow.json")
    home["path"] = str(f)
    edited = dict(GRAPH, nodes=GRAPH["nodes"] + [
        {"id": "Note", "type": "core.value.text", "config": {"text": "hi"}, "pos": [0, 200]}])
    with local_client() as c:
        slug = c.post("/api/files/open").json()["slug"]
        r = c.put(f"/api/graphs/{slug}", json=edited)
        assert r.status_code == 200, r.text
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert [n["id"] for n in saved["nodes"]] == ["Fire", "Note"]
    assert not (home["tmp"] / "graphs" / f"{slug}.json").exists()  # never copied into user/graphs


def test_save_never_writes_over_a_linked_file_from_a_newer_boltjar(home):
    f = _write(home["tmp"] / "elsewhere" / "flow.json")
    home["path"] = str(f)
    with local_client() as c:
        slug = c.post("/api/files/open").json()["slug"]
        _write(f, dict(GRAPH, format=999))
        r = c.put(f"/api/graphs/{slug}", json=GRAPH)
    assert r.status_code == 409
    assert json.loads(f.read_text(encoding="utf-8"))["format"] == 999


def test_save_as_writes_the_file_links_it_and_suggests_the_name(home):
    target = home["tmp"] / "elsewhere" / "copy.json"
    target.parent.mkdir()
    home["path"] = str(target)
    with local_client() as c:
        r = c.post("/api/files/save-as", json={"graph": GRAPH, "name": "My Flow"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["linked"] is True and body["slug"] == "copy"
        assert json.loads(target.read_text(encoding="utf-8"))["nodes"][0]["id"] == "Fire"
        assert "copy" in c.get("/api/graphs").json()["graphs"]
    assert home["asked"] == [("save", "my-flow.json")]


def test_save_as_refuses_something_that_is_not_a_workflow(home):
    with local_client() as c:
        r = c.post("/api/files/save-as", json={"graph": {"hello": 1}, "name": "x"})
    assert r.status_code == 422
    assert home["asked"] == []


# ---------------------------------------------------------------- gone, forget
def test_a_linked_file_that_is_gone_drops_off_the_list_and_the_record(home):
    f = _write(home["tmp"] / "elsewhere" / "flow.json")
    home["path"] = str(f)
    with local_client() as c:
        slug = c.post("/api/files/open").json()["slug"]
        f.unlink()
        listing = c.get("/api/graphs").json()
        assert slug not in listing["graphs"] and listing["files"] == []
        assert c.get(f"/api/graphs/{slug}").status_code == 404
    assert json.loads(linked.PATH.read_text(encoding="utf-8"))["files"] == []


def test_delete_forgets_the_link_and_leaves_the_file(home):
    f = _write(home["tmp"] / "elsewhere" / "flow.json")
    home["path"] = str(f)
    with local_client() as c:
        slug = c.post("/api/files/open").json()["slug"]
        r = c.delete(f"/api/graphs/{slug}")
        assert r.json() == {"ok": True, "forgotten": True}
        assert slug not in c.get("/api/graphs").json()["graphs"]
    assert f.exists()


def test_the_listed_path_hides_the_home_folder(home, monkeypatch):
    monkeypatch.setattr(server.pathlib.Path, "home", classmethod(lambda cls: home["tmp"]))
    f = _write(home["tmp"] / "Documents" / "flow.json")
    home["path"] = str(f)
    with local_client() as c:
        c.post("/api/files/open")
        path = c.get("/api/graphs").json()["files"][0]["path"]
    assert path.startswith("~") and str(home["tmp"]) not in path


# ---------------------------------------------------------------- the dialog child
def test_the_dialog_child_answer_is_read_back():
    ok = subprocess.CompletedProcess([], 0, stdout='{"path": "C:/x/flow.json"}\n', stderr="")
    assert dialogs.parse(ok) == "C:/x/flow.json"
    cancelled = subprocess.CompletedProcess([], 0, stdout='{"path": null}\n', stderr="")
    assert dialogs.parse(cancelled) is None
    no_tk = subprocess.CompletedProcess([], 0, stdout='{"unavailable": "tkinter is not installed"}\n', stderr="")
    with pytest.raises(dialogs.DialogUnavailable, match="tkinter"):
        dialogs.parse(no_tk)
    crashed = subprocess.CompletedProcess([], 1, stdout="", stderr="boom\n")
    with pytest.raises(dialogs.DialogUnavailable, match="boom"):
        dialogs.parse(crashed)
