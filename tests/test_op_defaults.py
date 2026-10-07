"""A node saved untouched (`config: {}`, normal through the API or MCP) runs and
validates its operation knob's declared default, as the editor draws it: a
config-less DB node is a `query`, so a wire from its `rows` output validates and
the output fires."""
from __future__ import annotations

import asyncio

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.runtime import Runtime, node_config
from boltjar.server import validate_graph
from boltjar.sqlite_store import SqliteStore
from boltjar.sdk import NODE_REGISTRY


@pytest.fixture
def store(tmp_path, monkeypatch):
    from boltjar import server
    s = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", s)
    s.execute("mydb", "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)")
    s.execute("mydb", "INSERT INTO people(name) VALUES('Ada')")
    return s


def _graph(db_config: dict) -> dict:
    return {"nodes": [
        {"id": "go", "type": "core.trigger.manual", "config": {}},
        {"id": "mydb", "type": "core.store.database", "config": {}},
        {"id": "node", "type": "core.db", "config": db_config},
        {"id": "show", "type": "core.output.preview", "config": {}},
    ], "edges": [
        {"src": "go", "src_port": "trigger", "dst": "node", "dst_port": "trigger"},
        {"src": "mydb", "src_port": "db", "dst": "node", "dst_port": "db"},
        {"src": "node", "src_port": "trigger", "dst": "show", "dst_port": "trigger"},
        {"src": "node", "src_port": "rows", "dst": "show", "dst_port": "in"},
    ]}


@pytest.mark.parametrize("node_type", ["core.db", "core.kv", "core.vectors"])
def test_a_missing_operation_runs_as_the_default(node_type):
    spec = NODE_REGISTRY[node_type]
    default = next(w.default for w in spec.widgets if w.name == "operation")
    assert node_config(spec, {})["operation"] == default


def test_a_wire_from_rows_on_a_config_less_db_validates():
    assert validate_graph(_graph({})) == []


def test_rows_fires_on_a_db_with_no_operation_saved(store):
    rt = Runtime()
    rt.build(_graph({"sql": "SELECT name FROM people"}))
    asyncio.run(rt._fire(rt.nodes["node"], "trigger", "go", rt.new_turn()))
    rows = rt.nodes["node"].out_latch.get("rows")
    assert rows and [r["name"] for r in rows] == ["Ada"]
