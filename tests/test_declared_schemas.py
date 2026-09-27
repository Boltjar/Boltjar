"""A Database node declares the tables its graph needs (its hidden `schema`
widget), and they are made to exist: when the editor opens the graph (POST
/api/stores/ensure) and whenever the graph runs, however it runs (the server's
On, `python -m boltjar`, a script that builds a Runtime), before any node
fires. Only what is missing is added; a table or column that exists is never
dropped, retyped or emptied, and a declaration the database cannot match is a
warning, never a refusal."""
from __future__ import annotations

import asyncio
import json
import pathlib
import sqlite3
import sys

import pytest
from local_client import local_client

import boltjar.server as server
from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY
from boltjar.sqlite_store import SqliteStore

client = local_client()

NOTES = [{"name": "notes", "columns": [
    {"name": "id", "type": "INTEGER", "pk": True},
    {"name": "body", "type": "TEXT", "pk": False},
]}]


@pytest.fixture
def store(tmp_path, monkeypatch) -> SqliteStore:
    s = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", s)
    yield s
    s.close()


def _graph(*db_nodes: dict) -> dict:
    return {"name": "g", "nodes": [{"id": "go", "type": "core.trigger.manual"}, *db_nodes],
            "edges": []}


def _db(node_id: str, schema=None, db_key: str | None = None) -> dict:
    cfg: dict = {}
    if db_key is not None:
        cfg["db_key"] = db_key
    if schema is not None:
        cfg["schema"] = schema
    return {"id": node_id, "type": "core.store.database", "config": cfg}


def _tables(store: SqliteStore, key: str) -> list[str]:
    return [t["name"] for t in store.schema(key)]


def test_database_node_declares_a_hidden_schema_widget():
    widget = next(w for w in NODE_REGISTRY["core.store.database"].definition()["widgets"]
                  if w["name"] == "schema")
    assert widget["kind"] == "schema" and widget["surface"] == "hidden"
    assert widget["default"] == [] and widget["promotable"] is False


def test_ensure_creates_every_declared_table(store):
    r = client.post("/api/stores/ensure", json=_graph(
        _db("a", NOTES, db_key="k1"),
        _db("b", [{"name": "facts", "columns": [{"name": "fact", "type": "text"}]}]),
        _db("c"),  # declares nothing: its store is not touched
    ))
    assert r.status_code == 200
    assert r.json() == {"stores": [
        {"node": "a", "key": "k1", "created": ["notes"], "added": []},
        # no db_key: the node id is the store key, as the Database node emits it
        {"node": "b", "key": "b", "created": ["facts"], "added": []},
    ], "warnings": []}
    assert _tables(store, "k1") == ["notes"]
    assert _tables(store, "b") == ["facts"]
    assert not (store.root / "c.db").exists()


def test_ensure_adds_missing_columns_and_keeps_rows(store):
    store.create_table("k1", "notes", [{"name": "id", "type": "int", "pk": True}])
    store.execute("k1", "INSERT INTO notes (id) VALUES (7)")
    r = client.post("/api/stores/ensure", json=_graph(_db("a", NOTES, db_key="k1")))
    assert r.json()["stores"] == [{"node": "a", "key": "k1", "created": [], "added": ["notes.body"]}]
    assert store.query("k1", "SELECT id, body FROM notes") == [{"id": 7, "body": None}]


def test_ensure_reports_a_conflict_as_a_warning(store):
    store.create_table("k1", "notes", [{"name": "id", "type": "int", "pk": True},
                                      {"name": "body", "type": "real"}])
    r = client.post("/api/stores/ensure", json=_graph(_db("a", NOTES, db_key="k1")))
    assert r.status_code == 200
    assert r.json()["warnings"] == [
        {"node": "a", "message": "notes.body is declared text but the database holds it as real"},
    ]
    body = next(c for c in store.schema("k1")[0]["columns"] if c["name"] == "body")
    assert body["type"] == "REAL"  # reported, never altered


def test_a_store_that_cannot_open_is_a_warning(store, monkeypatch):
    def broken(key, tables):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(store, "ensure_schema", broken)
    r = client.post("/api/stores/ensure", json=_graph(_db("a", NOTES, db_key="k1")))
    assert r.status_code == 200
    assert r.json() == {"stores": [], "warnings": [
        {"node": "a", "message": "its database could not be opened: unable to open database file"},
    ]}


def test_ensure_refuses_a_graph_from_a_newer_boltjar(store):
    r = client.post("/api/stores/ensure", json={**_graph(_db("a", NOTES)), "format": 999})
    assert r.status_code == 422
    assert _tables(store, "a") == []


def _power_on(graph: dict) -> tuple[list[dict] | None, list[dict]]:
    hub = server.Hub()
    events: asyncio.Queue = asyncio.Queue()
    hub.subscribers.add(events)

    async def drive():
        problems = await hub.power_on(graph)
        await hub.power_off()
        return problems

    problems = asyncio.run(drive())
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    return problems, seen


def test_power_on_creates_declared_tables_first(store):
    problems, seen = _power_on(_graph(_db("a", NOTES, db_key="k1")))
    assert problems is None
    assert _tables(store, "k1") == ["notes"]
    assert [e for e in seen if e["kind"] == "status"][0]["power"] == "on"


def test_power_on_warns_about_a_conflict_and_still_runs(store):
    store.create_table("k1", "notes", [{"name": "id", "type": "int", "pk": True},
                                      {"name": "body", "type": "blob"}])
    problems, seen = _power_on(_graph(_db("a", NOTES, db_key="k1")))
    assert problems is None
    warnings = [e for e in seen if e["kind"] == "warning"]
    assert [(w["node"], w["message"]) for w in warnings] == [
        ("a", "notes.body is declared text but the database holds it as blob"),
    ]
    assert any(e["kind"] == "status" and e["power"] == "on" for e in seen)


def _run(graph: dict) -> list[dict]:
    """Run a graph on a bare Runtime, without the server, and return its events."""
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive():
        await rt.run()
        await rt.stop()

    asyncio.run(drive())
    return events


def test_a_graph_run_without_the_server_creates_its_tables(store):
    events = _run(_graph(_db("a", NOTES, db_key="k1")))
    assert _tables(store, "k1") == ["notes"]
    assert not [e for e in events if e["kind"] in ("warning", "node_error")], events


def test_a_graph_run_without_the_server_warns_about_a_conflict(store):
    store.create_table("k1", "notes", [{"name": "id", "type": "int", "pk": True},
                                      {"name": "body", "type": "real"}])
    events = _run(_graph(_db("a", NOTES, db_key="k1")))
    assert [(e["node"], e["message"]) for e in events if e["kind"] == "warning"] == [
        ("a", "notes.body is declared text but the database holds it as real"),
    ]


def test_the_headless_runner_creates_the_chat_example_table(store, monkeypatch, capsys):
    import boltjar.__main__ as headless

    chat = pathlib.Path(__file__).resolve().parent.parent / "examples" / "chat.json"
    key = next(n["config"]["db_key"] for n in json.loads(chat.read_text(encoding="utf-8"))["nodes"]
               if n["type"] == "core.store.database")
    monkeypatch.setattr(headless.packs, "load_all", lambda: None)  # the core is already loaded
    monkeypatch.setattr(headless.secrets, "ensure_loaded", lambda: None)
    monkeypatch.setattr(sys, "argv", ["boltjar", str(chat), "0"])
    headless.main()
    assert _tables(store, key) == ["chat_history"]
    assert '"kind": "warning"' not in capsys.readouterr().out
