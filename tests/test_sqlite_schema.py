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
