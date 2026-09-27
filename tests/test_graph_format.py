"""Saved graphs carry a top-level "format" number, and every load migrates a graph
to the current format one step at a time. A graph without the field predates it
(format 0); a graph from a newer Boltjar is refused instead of loaded with parts
silently dropped.

Isolation: the `dirs` fixture repoints the server's graph folders at a tmp dir,
so nothing here touches the project's real user/ or examples/."""
from __future__ import annotations

import copy
import json
import pathlib
import re
import sys

import pytest
from local_client import local_client

import boltjar.graph_format as graph_format
import boltjar.server as server
from boltjar.graph_format import CURRENT_FORMAT, GraphFormatError, migrate

REPO = pathlib.Path(__file__).resolve().parent.parent
client = local_client()

GRAPH = {
    "name": "fmt",
    "nodes": [
        {"id": "m", "type": "core.trigger.manual", "config": {}},
        {"id": "lg", "type": "core.output.log", "config": {"label": "o"}},
    ],
    "edges": [{"src": "m", "src_port": "trigger", "dst": "lg", "dst_port": "in"}],
}


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    user = tmp_path / "user" / "graphs"
    examples = tmp_path / "examples"
    examples.mkdir()
    monkeypatch.setattr(server, "GRAPHS_DIR", user)
    monkeypatch.setattr(server, "EXAMPLES_DIR", examples)
    monkeypatch.setattr(server, "AUTOSAVE_DIR", tmp_path / "user" / "autosave")
    return user, examples


def _write(folder: pathlib.Path, slug: str, graph: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.json").write_text(json.dumps(graph), encoding="utf-8")


# --------------------------------------------------------------- migrate

def test_a_graph_without_the_field_migrates_to_the_current_format():
    graph = copy.deepcopy(GRAPH)
    out = migrate(graph)
    assert out["format"] == CURRENT_FORMAT
    assert list(out)[0] == "format"  # stamped first, so a saved file opens with it
    assert {k: v for k, v in out.items() if k != "format"} == GRAPH
    assert graph == GRAPH  # the input is never modified


def test_a_current_graph_comes_back_unchanged():
    graph = {"format": CURRENT_FORMAT, **GRAPH}
    assert migrate(graph) == graph


@pytest.mark.parametrize("fmt", ["1", 1.0, True, -1, None])
def test_a_format_that_is_not_a_whole_number_is_refused(fmt):
    with pytest.raises(GraphFormatError, match="not a format number"):
        migrate({**GRAPH, "format": fmt})


def test_a_graph_from_a_newer_boltjar_is_refused():
    with pytest.raises(GraphFormatError, match="newer Boltjar"):
        migrate({**GRAPH, "format": CURRENT_FORMAT + 1})


def test_steps_run_in_order_from_the_graphs_own_format(monkeypatch):
    # a future format change: format 2 renames `pos` to `position`, format 3
    # counts the nodes. A format 1 graph runs both steps; a format 2 one only the last.
    def rename_pos(graph):
        for n in graph["nodes"]:
            if "pos" in n:
                n["position"] = n.pop("pos")
        return graph

    def count_nodes(graph):
        graph["node_count"] = len(graph["nodes"])
        return graph

    monkeypatch.setattr(graph_format, "CURRENT_FORMAT", 3)
    monkeypatch.setitem(graph_format._STEPS, 1, rename_pos)
    monkeypatch.setitem(graph_format._STEPS, 2, count_nodes)
    old = {"format": 1, "nodes": [{"id": "a", "pos": [1, 2]}], "edges": []}
    assert graph_format.migrate(old) == {
        "format": 3, "nodes": [{"id": "a", "position": [1, 2]}], "edges": [], "node_count": 1}
    assert old["nodes"][0] == {"id": "a", "pos": [1, 2]}  # a step never reaches the input
    unstamped = {"nodes": [{"id": "a", "pos": [0, 0]}], "edges": []}
    assert graph_format.migrate(unstamped)["nodes"][0] == {"id": "a", "position": [0, 0]}
    two = {"format": 2, "nodes": [{"id": "a", "pos": [5, 5]}], "edges": []}
    assert graph_format.migrate(two)["nodes"][0] == {"id": "a", "pos": [5, 5]}


def test_every_format_below_the_current_one_has_a_step():
    assert sorted(graph_format._STEPS) == list(range(CURRENT_FORMAT))


# a format 1 graph: an LLM on "auto" (gone in format 2), one with a model picked,
# a TTS with a voice picked, and an HTTP node whose own "auto" is not a model.
FORMAT_1 = {
    "format": 1, "name": "fmt1",
    "nodes": [
        {"id": "auto", "type": "core.ai.llm", "config": {"model": "auto", "promoted": []}},
        {"id": "picked", "type": "core.ai.llm",
         "config": {"model": "xai/grok-4.20", "params": {"temperature": 0.6}}},
        {"id": "voice", "type": "core.ai.tts", "config": {"model": "fish/s2", "params": {"voice": "v"}}},
        {"id": "http", "type": "core.net.http", "config": {"response_type": "auto"}},
    ],
    "edges": [],
}


def test_format_2_loads_an_auto_model_as_none_picked():
    out = migrate(FORMAT_1)
    assert out["format"] == CURRENT_FORMAT == 2
    configs = {n["id"]: n["config"] for n in out["nodes"]}
    assert configs["auto"] == {"promoted": []}, "no model picked, never resolved"
    assert configs["picked"] == FORMAT_1["nodes"][1]["config"], "a picked model stays as saved"
    assert configs["voice"] == FORMAT_1["nodes"][2]["config"]
    assert configs["http"] == {"response_type": "auto"}, "only a model picker's auto is dropped"
    assert FORMAT_1["nodes"][0]["config"]["model"] == "auto"  # the input is never modified


def test_format_2_step_survives_nodes_without_a_config():
    graph = {"format": 1, "nodes": [{"id": "a", "type": "core.trigger.manual"},
                                    {"id": "b", "type": "core.ai.llm", "config": None}], "edges": []}
    assert migrate(graph)["nodes"] == graph["nodes"]


def test_a_saved_auto_model_opens_as_none_picked(dirs):
    user, _ = dirs
    _write(user, "fmt1", FORMAT_1)
    served = client.get("/api/graphs/fmt1").json()
    assert "model" not in next(n for n in served["nodes"] if n["id"] == "auto")["config"]
    assert next(n for n in served["nodes"] if n["id"] == "picked")["config"]["model"] == "xai/grok-4.20"


# --------------------------------------------------------------- the server

def test_loading_an_unstamped_graph_returns_the_current_format(dirs):
    user, examples = dirs
    _write(user, "saved", GRAPH)
    _write(examples, "shipped", GRAPH)
    assert client.get("/api/graphs/saved").json()["format"] == CURRENT_FORMAT
    assert client.get("/api/graphs/shipped").json()["format"] == CURRENT_FORMAT


def test_saving_stamps_the_current_format_on_the_file_and_its_snapshot(dirs, tmp_path):
    user, _ = dirs
    assert client.put("/api/graphs/fmt", json=GRAPH).json() == {"ok": True}
    on_disk = json.loads((user / "fmt.json").read_text(encoding="utf-8"))
    assert list(on_disk)[0] == "format" and on_disk["format"] == CURRENT_FORMAT
    snapshot = next((tmp_path / "user" / "autosave" / "fmt").glob("*.json"))
    assert json.loads(snapshot.read_text(encoding="utf-8"))["format"] == CURRENT_FORMAT
    version = snapshot.stem
    assert client.get(f"/api/graphs/fmt/versions/{version}").json()["format"] == CURRENT_FORMAT


def test_a_newer_graph_is_neither_saved_nor_loaded(dirs):
    user, _ = dirs
    newer = {**GRAPH, "format": CURRENT_FORMAT + 1}
    r = client.put("/api/graphs/fmt", json=newer)
    assert r.status_code == 422 and "newer Boltjar" in r.json()["error"]
    assert not (user / "fmt.json").exists()
    _write(user, "future", newer)
    r = client.get("/api/graphs/future")
    assert r.status_code == 422 and "newer Boltjar" in r.json()["error"]


@pytest.mark.parametrize("fmt, error", [(CURRENT_FORMAT + 1, "newer Boltjar"), ("2", "not a format number")])
def test_a_saved_graph_this_boltjar_cannot_read_is_never_saved_over(dirs, tmp_path, fmt, error):
    # the editor opens nothing from such a file, so a save under its slug (an
    # empty canvas, a stale draft) would replace a graph it never read.
    user, _ = dirs
    _write(user, "future", {**GRAPH, "format": fmt})
    before = (user / "future.json").read_text(encoding="utf-8")
    r = client.put("/api/graphs/future", json={"nodes": [], "edges": []})
    assert r.status_code == 409
    assert "the saved graph is kept" in r.json()["error"] and error in r.json()["error"]
    assert (user / "future.json").read_text(encoding="utf-8") == before
    assert not (tmp_path / "user" / "autosave" / "future").exists()
    # deleting it is a deliberate act, and then the slug saves as usual.
    assert client.delete("/api/graphs/future").json() == {"ok": True}
    assert client.put("/api/graphs/future", json=GRAPH).json() == {"ok": True}


def test_a_saved_file_that_is_not_a_graph_is_saved_over(dirs):
    user, _ = dirs
    user.mkdir(parents=True)
    (user / "broken.json").write_text("{not json", encoding="utf-8")
    assert client.put("/api/graphs/broken", json=GRAPH).json() == {"ok": True}
    assert json.loads((user / "broken.json").read_text(encoding="utf-8"))["nodes"] == GRAPH["nodes"]


def test_validate_reports_a_graph_it_cannot_read():
    problems = client.post("/api/validate", json={**GRAPH, "format": CURRENT_FORMAT + 1}).json()["problems"]
    assert [p["kind"] for p in problems] == ["format"]
    assert client.post("/api/validate", json=GRAPH).json() == {"problems": []}


def test_power_migrates_the_graph_and_refuses_a_newer_one():
    with local_client() as live:
        try:
            r = live.post("/api/runtime/fmt-new/power",
                          json={"action": "on", "graph": {**GRAPH, "format": CURRENT_FORMAT + 1}})
            assert r.json()["power"] == "off"
            assert [p["kind"] for p in r.json()["problems"]] == ["format"]
            r = live.post("/api/runtime/fmt-old/power", json={"action": "on", "graph": GRAPH})
            assert r.json() == {"power": "on", "problems": []}
            assert server.HUBS["fmt-old"].graph["format"] == CURRENT_FORMAT
            live.post("/api/runtime/fmt-old/power", json={"action": "off"})
        finally:
            server.HUBS.pop("fmt-new", None)
            server.HUBS.pop("fmt-old", None)


# --------------------------------------------------------------- the headless runner

def test_the_headless_runner_refuses_a_graph_from_a_newer_boltjar(tmp_path, monkeypatch):
    import boltjar.__main__ as headless

    def refuse(*args, **kwargs):
        raise AssertionError("nothing loads for a graph that is refused")

    path = tmp_path / "future.json"
    path.write_text(json.dumps({**GRAPH, "format": CURRENT_FORMAT + 1}), encoding="utf-8")
    monkeypatch.setattr(headless.packs, "load_all", refuse)
    monkeypatch.setattr(headless.secrets, "ensure_loaded", refuse)
    monkeypatch.setattr(sys, "argv", ["boltjar", str(path), "0"])
    with pytest.raises(SystemExit) as stopped:
        headless.main()
    assert str(stopped.value.code).startswith(f"{path}: the graph was saved by a newer Boltjar")


def test_the_headless_runner_runs_the_migrated_graph(tmp_path, monkeypatch):
    import boltjar.__main__ as headless

    built: list[dict] = []
    real_build = headless.Runtime.build

    def build(self, graph):
        built.append(graph)
        return real_build(self, graph)

    path = tmp_path / "old.json"
    path.write_text(json.dumps(GRAPH), encoding="utf-8")  # no `format`: saved before it existed
    monkeypatch.setattr(headless.packs, "load_all", lambda: None)  # the core is already loaded
    monkeypatch.setattr(headless.secrets, "ensure_loaded", lambda: None)
    monkeypatch.setattr(headless.Runtime, "build", build)
    monkeypatch.setattr(sys, "argv", ["boltjar", str(path), "0"])
    headless.main()
    assert [g["format"] for g in built] == [CURRENT_FORMAT]


# --------------------------------------------------------------- shipped graphs + editor

def test_every_shipped_example_carries_the_current_format():
    for path in sorted((REPO / "examples").glob("*.json")):
        assert json.loads(path.read_text(encoding="utf-8")).get("format") == CURRENT_FORMAT, path.name


def test_the_editor_writes_the_current_format():
    source = (REPO / "editor" / "src" / "lib" / "graphAdapter.ts").read_text(encoding="utf-8")
    match = re.search(r"export const GRAPH_FORMAT = (\d+);", source)
    assert match, "graphAdapter.ts must export GRAPH_FORMAT"
    assert int(match.group(1)) == CURRENT_FORMAT
