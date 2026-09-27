import pathlib
from boltjar.sqlite_store import SqliteStore


def test_exec_then_query_roundtrip(tmp_path):
    store = SqliteStore(root=tmp_path)
    store.execute("k1", "CREATE TABLE facts(id INTEGER PRIMARY KEY, fact TEXT)")
    r = store.execute("k1", "INSERT INTO facts(fact) VALUES(:f)", {"f": "sky is blue"})
    assert r["changes"] == 1
    assert r["lastId"] == 1
    rows = store.query("k1", "SELECT fact FROM facts WHERE id = :id", {"id": 1})
    assert rows == [{"fact": "sky is blue"}]


def test_persists_to_disk(tmp_path):
    SqliteStore(root=tmp_path).execute("k2", "CREATE TABLE t(x INTEGER)")
    assert (tmp_path / "k2.db").exists()


def test_schema_lists_tables_and_columns(tmp_path):
    store = SqliteStore(root=tmp_path)
    store.execute("k3", "CREATE TABLE facts(id INTEGER PRIMARY KEY, user TEXT, fact TEXT)")
    store.execute("k3", "INSERT INTO facts(user, fact) VALUES('me','hi')")
    schema = store.schema("k3")
    assert schema == [
        {
            "name": "facts",
            "rows": 1,
            "columns": [
                {"name": "id", "type": "INTEGER", "pk": True},
                {"name": "user", "type": "TEXT", "pk": False},
                {"name": "fact", "type": "TEXT", "pk": False},
            ],
        }
    ]


def test_schema_handles_quoted_table_name(tmp_path):
    # a table whose name contains a double-quote must not break schema()
    store = SqliteStore(root=tmp_path)
    store.execute("k4", 'CREATE TABLE "we""ird"(x INTEGER)')
    store.execute("k4", 'INSERT INTO "we""ird"(x) VALUES(1)')
    schema = store.schema("k4")
    assert len(schema) == 1
    assert schema[0]["name"] == 'we"ird'
    assert schema[0]["rows"] == 1
    assert schema[0]["columns"] == [{"name": "x", "type": "INTEGER", "pk": False}]


def test_ddl_reports_zero_changes(tmp_path):
    # cur.rowcount is -1 for DDL; execute() clamps it to 0
    store = SqliteStore(root=tmp_path)
    assert store.execute("k5", "CREATE TABLE t(x INTEGER)")["changes"] == 0


def test_symbol_keys_do_not_collide(tmp_path):
    # different all-symbol keys must NOT silently share one db file
    store = SqliteStore(root=tmp_path)
    store.execute("!!!", "CREATE TABLE a(x INTEGER)")
    store.execute("###", "CREATE TABLE b(x INTEGER)")
    a = store.query("!!!", "SELECT name FROM sqlite_master WHERE type='table'")
    b = store.query("###", "SELECT name FROM sqlite_master WHERE type='table'")
    assert a == [{"name": "a"}] and b == [{"name": "b"}]


def test_close_releases_connections(tmp_path):
    store = SqliteStore(root=tmp_path)
    store.execute("k6", "CREATE TABLE t(x INTEGER)")
    store.close()
    assert store._conns == {}
