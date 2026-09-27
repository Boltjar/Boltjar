"""Schema-mutation coverage for SqliteStore: create/rename/drop of tables and
columns, the identifier escaping, the column-type allow-list, and that every
mutation returns the fresh schema. SQLite supports ALTER TABLE ADD/RENAME/DROP
COLUMN and RENAME TO natively, so these are thin wrappers we verify end-to-end."""
from __future__ import annotations

import pytest

from boltjar.sqlite_store import SqliteStore, _safe_coltype


@pytest.fixture
def store(tmp_path) -> SqliteStore:
    s = SqliteStore(root=tmp_path)
    yield s
    s.close()


def _table(schema: list[dict], name: str) -> dict | None:
    return next((t for t in schema if t["name"] == name), None)


def _colnames(schema: list[dict], table: str) -> list[str]:
    t = _table(schema, table)
    return [c["name"] for c in t["columns"]] if t else []


def test_create_table_default_id(store):
    schema = store.create_table("k", "facts")
    t = _table(schema, "facts")
    assert t is not None
    # an empty column list yields a single id INTEGER PRIMARY KEY.
    assert [c["name"] for c in t["columns"]] == ["id"]
    assert t["columns"][0]["pk"] is True
    assert t["rows"] == 0


def test_create_table_with_columns(store):
    schema = store.create_table("k", "facts", [
        {"name": "id", "type": "int", "pk": True},
        {"name": "user", "type": "text"},
        {"name": "fact", "type": "text"},
    ])
    assert _colnames(schema, "facts") == ["id", "user", "fact"]


def test_add_column(store):
    store.create_table("k", "facts", [{"name": "id", "type": "int", "pk": True}])
    schema = store.add_column("k", "facts", "note", "text")
    assert "note" in _colnames(schema, "facts")


def test_rename_table(store):
    store.create_table("k", "old_name")
    schema = store.rename_table("k", "old_name", "new_name")
    assert _table(schema, "new_name") is not None
    assert _table(schema, "old_name") is None


def test_rename_column(store):
    store.create_table("k", "facts", [
        {"name": "id", "type": "int", "pk": True},
        {"name": "user", "type": "text"},
    ])
    schema = store.rename_column("k", "facts", "user", "owner")
    assert "owner" in _colnames(schema, "facts")
    assert "user" not in _colnames(schema, "facts")


def test_drop_column(store):
    store.create_table("k", "facts", [
        {"name": "id", "type": "int", "pk": True},
        {"name": "tmp", "type": "text"},
    ])
    schema = store.drop_column("k", "facts", "tmp")
    assert "tmp" not in _colnames(schema, "facts")
    assert "id" in _colnames(schema, "facts")


def test_drop_table(store):
    store.create_table("k", "facts")
    store.create_table("k", "history")
    schema = store.drop_table("k", "facts")
    assert _table(schema, "facts") is None
    assert _table(schema, "history") is not None


def test_mutations_return_fresh_schema(store):
    # each call returns the post-mutation schema (refetch-by-result).
    s1 = store.create_table("k", "a")
    assert {t["name"] for t in s1} == {"a"}
    s2 = store.create_table("k", "b")
    assert {t["name"] for t in s2} == {"a", "b"}


def test_rows_preserved_across_add_column(store):
    store.create_table("k", "facts", [
        {"name": "id", "type": "int", "pk": True},
        {"name": "fact", "type": "text"},
    ])
    store.execute("k", "INSERT INTO facts(fact) VALUES('hello')")
    schema = store.add_column("k", "facts", "extra", "text")
    assert _table(schema, "facts")["rows"] == 1


def test_quoting_resists_odd_names(store):
    # a name with a double-quote must round-trip via doubling, not break out.
    weird = 'we"ird'
    store.create_table("k", weird, [{"name": "co\"l", "type": "text"}])
    schema = store.schema("k")
    assert _table(schema, weird) is not None
    assert "co\"l" in _colnames(schema, weird)


def test_safe_coltype_allow_list():
    assert _safe_coltype("int") == "INTEGER"
    assert _safe_coltype("INTEGER") == "INTEGER"
    assert _safe_coltype("text") == "TEXT"
    assert _safe_coltype("real") == "REAL"
    assert _safe_coltype("bool") == "INTEGER"
    # anything unrecognised (or an injection attempt) falls back to TEXT.
    assert _safe_coltype("TEXT); DROP TABLE x;--") == "TEXT"
    assert _safe_coltype(None) == "TEXT"


def test_add_column_uses_safe_type(store):
    store.create_table("k", "facts", [{"name": "id", "type": "int", "pk": True}])
    # a bogus type does not inject; the column is created with TEXT affinity.
    schema = store.add_column("k", "facts", "c", "nonsense")
    t = _table(schema, "facts")
    col = next(c for c in t["columns"] if c["name"] == "c")
    assert col["type"] == "TEXT"


# ---------------------------------------------------------------- ensure_schema
# A graph declares the tables its Database node needs; ensure_schema makes them
# exist without ever taking anything away.

CHAT = [{"name": "chat_history", "columns": [
    {"name": "id", "type": "INTEGER", "pk": True},
    {"name": "time", "type": "TEXT", "pk": False},
    {"name": "sender", "type": "TEXT", "pk": False},
    {"name": "message", "type": "TEXT", "pk": False},
]}]


def test_ensure_schema_creates_a_missing_table(store):
    out = store.ensure_schema("k", CHAT)
    assert out["created"] == ["chat_history"] and out["conflicts"] == []
    t = _table(out["schema"], "chat_history")
    assert [(c["name"], c["type"], c["pk"]) for c in t["columns"]] == [
        ("id", "INTEGER", True), ("time", "TEXT", False),
        ("sender", "TEXT", False), ("message", "TEXT", False),
    ]
    assert out["schema"] == store.schema("k")


def test_ensure_schema_is_idempotent(store):
    store.ensure_schema("k", CHAT)
    again = store.ensure_schema("k", CHAT)
    assert again["created"] == [] and again["added"] == [] and again["conflicts"] == []


def test_ensure_schema_adds_missing_columns_and_keeps_rows(store):
    store.create_table("k", "chat_history", [{"name": "id", "type": "int", "pk": True},
                                             {"name": "message", "type": "text"}])
    store.execute("k", "INSERT INTO chat_history (message) VALUES ('hi')")
    out = store.ensure_schema("k", CHAT)
    assert out["created"] == []
    assert out["added"] == ["chat_history.time", "chat_history.sender"]
    assert _colnames(out["schema"], "chat_history") == ["id", "message", "time", "sender"]
    assert store.query("k", "SELECT message FROM chat_history") == [{"message": "hi"}]


def test_ensure_schema_never_drops_or_retypes(store):
    store.create_table("k", "chat_history", [{"name": "id", "type": "int", "pk": True},
                                             {"name": "time", "type": "int"},
                                             {"name": "mood", "type": "text"}])
    store.create_table("k", "extra")
    out = store.ensure_schema("k", CHAT)
    # the undeclared table and column stay; the mistyped column keeps its type
    # and is reported, not altered.
    assert _table(out["schema"], "extra") is not None
    assert "mood" in _colnames(out["schema"], "chat_history")
    time = next(c for c in _table(out["schema"], "chat_history")["columns"] if c["name"] == "time")
    assert time["type"] == "INTEGER"
    assert out["conflicts"] == ["chat_history.time is declared text but the database holds it as integer"]


def test_ensure_schema_matches_names_ignoring_case(store):
    store.create_table("k", "Chat_History", [{"name": "ID", "type": "int", "pk": True},
                                             {"name": "Time", "type": "text"},
                                             {"name": "Sender", "type": "text"},
                                             {"name": "Message", "type": "text"}])
    out = store.ensure_schema("k", CHAT)
    assert out == {**out, "created": [], "added": [], "conflicts": []}
    assert [t["name"] for t in out["schema"]] == ["Chat_History"]


def test_ensure_schema_reads_types_by_affinity(store):
    store.execute("k", "CREATE TABLE t (a VARCHAR(20), b DATETIME, c BOOLEAN, d, e, f REAL)")
    declared = [{"name": "t", "columns": [
        {"name": "a", "type": "text"}, {"name": "b", "type": "DATETIME"},
        {"name": "c", "type": "bool"}, {"name": "d", "type": ""},
        {"name": "e", "type": "blob"}, {"name": "f", "type": "int"},
    ]}]
    out = store.ensure_schema("k", declared)
    # VARCHAR is text; DATETIME matches itself; BOOLEAN and bool are the same
    # column; an untyped declaration never conflicts, and an untyped column is a
    # blob, as SQLite reads it. Only a real column declared int is another kind.
    assert out["conflicts"] == ["t.f is declared int but the database holds it as real"]


def test_ensure_schema_reports_a_primary_key_it_cannot_add(store):
    store.create_table("k", "chat_history", [{"name": "message", "type": "text"}])
    out = store.ensure_schema("k", CHAT)
    assert "id" not in _colnames(out["schema"], "chat_history")
    assert out["added"] == ["chat_history.time", "chat_history.sender"]
    assert out["conflicts"] == [
        "chat_history.id is declared as the primary key, and SQLite cannot add "
        "a primary key column to a table that exists"
    ]


def test_ensure_schema_round_trips_a_live_schema(store, tmp_path):
    store.execute("k", "CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "body TEXT, score REAL, seen DATETIME, raw BLOB, done BOOLEAN, due BOOL)")
    snapshot = [{"name": t["name"], "columns": t["columns"]} for t in store.schema("k")]
    # a live schema never conflicts with a declaration taken from it
    assert store.ensure_schema("k", snapshot)["conflicts"] == []
    fresh = SqliteStore(root=tmp_path / "other")
    try:
        out = fresh.ensure_schema("k", snapshot)
        assert out["conflicts"] == []
        assert fresh.ensure_schema("k", snapshot)["conflicts"] == []
        cols = _table(out["schema"], "notes")["columns"]
        assert [(c["name"], c["type"], c["pk"]) for c in cols] == [
            ("id", "INTEGER", True), ("body", "TEXT", False), ("score", "REAL", False),
            ("seen", "NUMERIC", False), ("raw", "BLOB", False),
            ("done", "INTEGER", False), ("due", "INTEGER", False),
        ]
    finally:
        fresh.close()


def test_ensure_schema_skips_malformed_entries_and_reports_refused_names(store):
    out = store.ensure_schema("k", [
        "not a table", {"name": ""}, {"columns": []},
        {"name": "sqlite_reserved", "columns": [{"name": "a"}]},
        {"name": "ok", "columns": [{"name": ""}, {"name": "a", "type": "int"}, 7]},
    ])
    assert out["created"] == ["ok"]
    assert _colnames(out["schema"], "ok") == ["a"]
    assert len(out["conflicts"]) == 1 and out["conflicts"][0].startswith("sqlite_reserved: ")


def test_ensure_schema_reads_a_malformed_declaration_without_raising(store):
    # graphs are shared, so a declaration can hold anything a JSON file can
    out = store.ensure_schema("k", [
        {"name": "five", "columns": 5},
        {"name": "typed", "columns": [{"name": "a", "type": 7}, {"name": "b", "type": None}]},
    ])
    assert out["created"] == ["five", "typed"] and out["conflicts"] == []
    assert _colnames(out["schema"], "five") == ["id"]  # declares no columns: the default id
    typed = {c["name"]: c["type"] for c in _table(out["schema"], "typed")["columns"]}
    assert typed == {"a": "NUMERIC", "b": "TEXT"}  # 7 reads as SQLite reads a type named 7
    again = store.ensure_schema("k", [{"name": "typed", "columns": [{"name": "a", "type": 7}]}])
    assert again == {**again, "created": [], "added": [], "conflicts": []}
