"""The consolidated `core.kv` and `core.db` nodes, end to end through the
Runtime. Each replaces a family of single-op nodes that used to exist:

  core.kv          replaces core.kv.{get,set,delete,has,keys}
  core.db          replaces core.data.{query,exec,insert,find,update,delete}

The `operation` knob reshapes the node's visible knobs + output ports; the
runtime dispatches by op. Every knob is a TEMPLATE (mirroring HTTP): {tag}
substitutes a wired source value, {{secret.X}} resolves a secret. Tests cover
the per-op dispatch for both nodes plus the {tag} template substitution that
makes the two consolidated nodes a real replacement for the wired-port families
they retire.
"""
import asyncio

import pytest

from boltjar.sdk import types, NODE_REGISTRY
from boltjar.runtime import Runtime
from boltjar.kv_store import KvStore
from boltjar.sqlite_store import SqliteStore
import boltjar.nodes.core  # noqa: F401  (registers the core nodes + the kv / db types)


# ----------------------------------------------------------------- shared helpers
def _kv(tmp_path, monkeypatch):
    """A fresh per-test KV store, swapped into the server module."""
    from boltjar import server
    store = KvStore(root=tmp_path)
    monkeypatch.setattr(server, "KV_STORE", store)
    return store


def _db(tmp_path, monkeypatch):
    """A fresh per-test SQLite store, swapped into the server module."""
    from boltjar import server
    store = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", store)
    return store


def _fire(rt, node_id, port="trigger", payload="go"):
    """Fire a triggering port (the runtime pulls inputs + invokes run())."""
    asyncio.run(rt._fire(rt.nodes[node_id], port, payload, rt.new_turn()))


# ----------------------------------------------------------------- type registry
def test_kv_type_registered():
    """The `kv` wire type ships with the core pack."""
    assert "kv" in types.catalog()


def test_db_type_registered():
    """The `db` wire type ships with the core pack."""
    assert "db" in types.catalog()


# ----------------------------------------------------------------- old ids retired
def test_dead_node_ids_are_gone():
    """The single-op nodes the consolidated KV and DB replace must NOT register;
    keeping them as aliases would defeat the consolidation."""
    for dead in ("core.kv.get", "core.kv.set", "core.kv.delete", "core.kv.has", "core.kv.keys",
                 "core.data.query", "core.data.exec", "core.data.insert", "core.data.find",
                 "core.data.update", "core.data.delete"):
        assert dead not in NODE_REGISTRY, f"{dead} should be retired"


def test_consolidated_node_ids_exist():
    assert "core.kv" in NODE_REGISTRY
    assert "core.db" in NODE_REGISTRY


# ----------------------------------------------------------------- store handles
def test_kv_store_pulls_its_handle():
    """The KV Store source node still emits its kv key (unchanged surface)."""
    rt = Runtime()
    rt.build({
        "nodes": [{"id": "mykv", "type": "core.store.kv", "config": {}}],
        "edges": [],
    })
    assert rt._pull_output("mykv", "kv", rt.new_turn()) == "mykv"


def test_kv_store_uses_configured_key():
    rt = Runtime()
    rt.build({
        "nodes": [{"id": "mykv", "type": "core.store.kv", "config": {"kv_key": "stable-1"}}],
        "edges": [],
    })
    assert rt._pull_output("mykv", "kv", rt.new_turn()) == "stable-1"


def test_database_pulls_its_handle():
    """The Database source node still emits its db key (unchanged surface)."""
    rt = Runtime()
    rt.build({
        "nodes": [{"id": "mydb", "type": "core.store.database", "config": {}}],
        "edges": [],
    })
    assert rt._pull_output("mydb", "db", rt.new_turn()) == "mydb"


# ============================================================== KV per-op tests
def test_kv_set_then_get_roundtrip(tmp_path, monkeypatch):
    """The headline lifecycle: set a value with op=set, then read it back with
    op=get. Both invocations go through the same `core.kv` node id."""
    store = _kv(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "set", "type": "core.kv",
             "config": {"operation": "set", "key": "name", "value": "Ada"}},
        ],
        "edges": [{"src": "mykv", "src_port": "kv", "dst": "set", "dst_port": "kv"}],
    })
    asyncio.run(rt.run())
    _fire(rt, "set")
    assert store.get("mykv", "name") == "Ada"
    asyncio.run(rt.stop())

    # round-trip: a get op against the same store reads it back through `value`.
    rt2 = Runtime()
    rt2.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "get", "type": "core.kv",
             "config": {"operation": "get", "key": "name"}},
        ],
        "edges": [{"src": "mykv", "src_port": "kv", "dst": "get", "dst_port": "kv"}],
    })
    asyncio.run(rt2.run())
    _fire(rt2, "get")
    assert rt2.nodes["get"].out_latch.get("value") == "Ada"
    assert rt2.nodes["get"].out_latch.get("trigger") is True
    asyncio.run(rt2.stop())


def test_kv_get_missing_returns_none(tmp_path, monkeypatch):
    """A get against an absent key reads as None (lean: never raises)."""
    _kv(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "get", "type": "core.kv",
             "config": {"operation": "get", "key": "absent"}},
        ],
        "edges": [{"src": "mykv", "src_port": "kv", "dst": "get", "dst_port": "kv"}],
    })
    asyncio.run(rt.run())
    _fire(rt, "get")
    assert rt.nodes["get"].out_latch.get("value") is None
    asyncio.run(rt.stop())


def test_kv_delete_then_has_is_false(tmp_path, monkeypatch):
    """Delete -> has=false: the delete op removes the key (idempotent under
    the hood), and a follow-up has op confirms it on the `ok` output."""
    store = _kv(tmp_path, monkeypatch)
    store.set("mykv", "doomed", "x")

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "del", "type": "core.kv",
             "config": {"operation": "delete", "key": "doomed"}},
            {"id": "has", "type": "core.kv",
             "config": {"operation": "has", "key": "doomed"}},
        ],
        "edges": [
            {"src": "mykv", "src_port": "kv", "dst": "del", "dst_port": "kv"},
            {"src": "mykv", "src_port": "kv", "dst": "has", "dst_port": "kv"},
        ],
    })
    asyncio.run(rt.run())
    _fire(rt, "del")
    assert store.has("mykv", "doomed") is False
    _fire(rt, "has")
    assert rt.nodes["has"].out_latch.get("ok") is False
    asyncio.run(rt.stop())


def test_kv_delete_is_idempotent(tmp_path, monkeypatch):
    """Deleting an absent key never raises (the underlying KvStore.delete
    is a no-op on a miss)."""
    _kv(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "del", "type": "core.kv",
             "config": {"operation": "delete", "key": "never-existed"}},
        ],
        "edges": [{"src": "mykv", "src_port": "kv", "dst": "del", "dst_port": "kv"}],
    })
    asyncio.run(rt.run())
    _fire(rt, "del")  # must not raise
    asyncio.run(rt.stop())


def test_kv_has_reflects_presence(tmp_path, monkeypatch):
    """has=true when the key is there, false when it isn't."""
    store = _kv(tmp_path, monkeypatch)
    store.set("mykv", "here", 1)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "has", "type": "core.kv",
             "config": {"operation": "has", "key": "here"}},
            {"id": "nope", "type": "core.kv",
             "config": {"operation": "has", "key": "gone"}},
        ],
        "edges": [
            {"src": "mykv", "src_port": "kv", "dst": "has", "dst_port": "kv"},
            {"src": "mykv", "src_port": "kv", "dst": "nope", "dst_port": "kv"},
        ],
    })
    asyncio.run(rt.run())
    _fire(rt, "has")
    _fire(rt, "nope")
    assert rt.nodes["has"].out_latch.get("ok") is True
    assert rt.nodes["nope"].out_latch.get("ok") is False
    asyncio.run(rt.stop())


def test_kv_keys_with_prefix(tmp_path, monkeypatch):
    """`keys` lists every stored key; the `prefix` knob filters that list to
    the keys starting with the prefix (composes with downstream JSON Get)."""
    store = _kv(tmp_path, monkeypatch)
    store.set("mykv", "user.name", "Ada")
    store.set("mykv", "user.age", 7)
    store.set("mykv", "session.id", "abc")

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "all", "type": "core.kv",
             "config": {"operation": "keys"}},
            {"id": "users", "type": "core.kv",
             "config": {"operation": "keys", "prefix": "user."}},
        ],
        "edges": [
            {"src": "mykv", "src_port": "kv", "dst": "all", "dst_port": "kv"},
            {"src": "mykv", "src_port": "kv", "dst": "users", "dst_port": "kv"},
        ],
    })
    asyncio.run(rt.run())
    _fire(rt, "all")
    _fire(rt, "users")
    assert sorted(rt.nodes["all"].out_latch.get("keys")) == ["session.id", "user.age", "user.name"]
    assert sorted(rt.nodes["users"].out_latch.get("keys")) == ["user.age", "user.name"]
    asyncio.run(rt.stop())


def test_kv_persistence_across_fresh_store(tmp_path, monkeypatch):
    """A KV set survives a cold restart: a brand-new KvStore reads the
    persisted value off disk."""
    _kv(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "set", "type": "core.kv",
             "config": {"operation": "set", "key": "count", "value": "7"}},
        ],
        "edges": [{"src": "mykv", "src_port": "kv", "dst": "set", "dst_port": "kv"}],
    })
    asyncio.run(rt.run())
    _fire(rt, "set")
    asyncio.run(rt.stop())

    # a fresh in-memory KvStore (cold cache) reloads from disk.
    assert KvStore(root=tmp_path).get("mykv", "count") == "7"


def test_kv_no_store_wired_raises(tmp_path, monkeypatch):
    """A KV op with no kv handle wired must NOT silently no-op: surface a
    clear error like the rest of the trigger-fired family."""
    _kv(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "set", "type": "core.kv",
             "config": {"operation": "set", "key": "x", "value": "y"}},
        ],
        "edges": [],
    })
    inst = rt.nodes["set"]
    with pytest.raises(ValueError, match="no kv store wired"):
        asyncio.run(rt._fire(inst, "trigger", "go", rt.new_turn()))


# ============================================================== DB per-op tests
def test_db_exec_writes_then_query_reads(tmp_path, monkeypatch):
    """The lean SQL-shaped path: exec a DDL + INSERT, then query reads back
    the rows. Both run through the same `core.db` node id."""
    store = _db(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "create", "type": "core.db",
             "config": {"operation": "exec",
                        "sql": "CREATE TABLE facts(id INTEGER PRIMARY KEY, fact TEXT)"}},
            {"id": "ins", "type": "core.db",
             "config": {"operation": "exec",
                        "sql": "INSERT INTO facts(fact) VALUES('hello')"}},
            {"id": "q", "type": "core.db",
             "config": {"operation": "query", "sql": "SELECT fact FROM facts"}},
        ],
        "edges": [
            {"src": "mydb", "src_port": "db", "dst": "create", "dst_port": "db"},
            {"src": "mydb", "src_port": "db", "dst": "ins", "dst_port": "db"},
            {"src": "mydb", "src_port": "db", "dst": "q", "dst_port": "db"},
        ],
    })
    asyncio.run(rt.run())
    _fire(rt, "create")
    _fire(rt, "ins")
    _fire(rt, "q")
    assert rt.nodes["q"].out_latch.get("rows") == [{"fact": "hello"}]
    # exec emits `affected`; INSERT counts as one change.
    assert rt.nodes["ins"].out_latch.get("affected") == 1
    asyncio.run(rt.stop())
    # the row really landed in the underlying store.
    assert store.query("mydb", "SELECT fact FROM facts") == [{"fact": "hello"}]


def test_db_insert_emits_row_id(tmp_path, monkeypatch):
    """The structured insert path: parse "col=value" lines into a bound
    INSERT; the lastrowid comes back on the `row_id` output."""
    store = _db(tmp_path, monkeypatch)
    store.execute("mydb", "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "ins", "type": "core.db",
             "config": {"operation": "insert", "table": "people",
                        "fields": "name=Ada\nage=36"}},
        ],
        "edges": [{"src": "mydb", "src_port": "db", "dst": "ins", "dst_port": "db"}],
    })
    asyncio.run(rt.run())
    _fire(rt, "ins")
    assert rt.nodes["ins"].out_latch.get("row_id") == 1
    assert rt.nodes["ins"].out_latch.get("trigger") is True
    asyncio.run(rt.stop())
    # SQLite column affinity coerces the bound TEXT "36" into the INTEGER column,
    # so the stored value comes back as an int. V1 is fine with this.
    assert store.query("mydb", "SELECT name, age FROM people") == [{"name": "Ada", "age": 36}]


def test_db_find_filters_by_where(tmp_path, monkeypatch):
    """find: optional where filters by equality on each "col=value" line."""
    store = _db(tmp_path, monkeypatch)
    store.execute("mydb", "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)")
    store.execute("mydb", "INSERT INTO people(name) VALUES('Ada')")
    store.execute("mydb", "INSERT INTO people(name) VALUES('Bob')")

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "all", "type": "core.db",
             "config": {"operation": "find", "table": "people"}},
            {"id": "ada", "type": "core.db",
             "config": {"operation": "find", "table": "people", "where": "name=Ada"}},
        ],
        "edges": [
            {"src": "mydb", "src_port": "db", "dst": "all", "dst_port": "db"},
            {"src": "mydb", "src_port": "db", "dst": "ada", "dst_port": "db"},
        ],
    })
    asyncio.run(rt.run())
    _fire(rt, "all")
    _fire(rt, "ada")
    assert rt.nodes["all"].out_latch.get("rows") == [
        {"id": 1, "name": "Ada"},
        {"id": 2, "name": "Bob"},
    ]
    assert rt.nodes["ada"].out_latch.get("rows") == [{"id": 1, "name": "Ada"}]
    asyncio.run(rt.stop())


def test_db_update_returns_affected_count(tmp_path, monkeypatch):
    """update sets each field; the affected-row count comes back on `affected`."""
    store = _db(tmp_path, monkeypatch)
    store.execute("mydb", "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
    store.execute("mydb", "INSERT INTO people(name, age) VALUES('Ada', 36)")

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "upd", "type": "core.db",
             "config": {"operation": "update", "table": "people",
                        "fields": "age=37", "where": "name=Ada"}},
        ],
        "edges": [{"src": "mydb", "src_port": "db", "dst": "upd", "dst_port": "db"}],
    })
    asyncio.run(rt.run())
    _fire(rt, "upd")
    assert rt.nodes["upd"].out_latch.get("affected") == 1
    assert rt.nodes["upd"].out_latch.get("trigger") is True
    asyncio.run(rt.stop())
    # the stored row really updated (the bound TEXT "37" coerces into the
    # INTEGER column via SQLite affinity rules).
    assert store.query("mydb", "SELECT name, age FROM people") == [{"name": "Ada", "age": 37}]


def test_db_delete_returns_affected_count(tmp_path, monkeypatch):
    """delete drops rows matching the where; affected reflects the count."""
    store = _db(tmp_path, monkeypatch)
    store.execute("mydb", "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)")
    store.execute("mydb", "INSERT INTO people(name) VALUES('Ada')")
    store.execute("mydb", "INSERT INTO people(name) VALUES('Bob')")

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "del", "type": "core.db",
             "config": {"operation": "delete", "table": "people", "where": "name=Ada"}},
        ],
        "edges": [{"src": "mydb", "src_port": "db", "dst": "del", "dst_port": "db"}],
    })
    asyncio.run(rt.run())
    _fire(rt, "del")
    assert rt.nodes["del"].out_latch.get("affected") == 1
    assert rt.nodes["del"].out_latch.get("trigger") is True
    asyncio.run(rt.stop())
    assert store.query("mydb", "SELECT name FROM people") == [{"name": "Bob"}]


def test_db_no_db_wired_raises(tmp_path, monkeypatch):
    """A DB op with no db handle wired must NOT silently no-op."""
    _db(tmp_path, monkeypatch)
    rt = Runtime()
    rt.build({
        "nodes": [{"id": "q", "type": "core.db",
                   "config": {"operation": "query", "sql": "SELECT 1"}}],
        "edges": [],
    })
    inst = rt.nodes["q"]
    with pytest.raises(ValueError, match="no db connection"):
        asyncio.run(rt._fire(inst, "trigger", "go", rt.new_turn()))


def test_db_insert_without_fields_raises(tmp_path, monkeypatch):
    """An insert with no field=value lines refuses up front (a DEFAULT VALUES
    insert would fail on NOT NULL columns; surface the error early)."""
    store = _db(tmp_path, monkeypatch)
    store.execute("mydb", "CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT NOT NULL)")

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "ins", "type": "core.db",
             "config": {"operation": "insert", "table": "t", "fields": ""}},
        ],
        "edges": [{"src": "mydb", "src_port": "db", "dst": "ins", "dst_port": "db"}],
    })
    inst = rt.nodes["ins"]
    with pytest.raises(ValueError, match="needs at least one field=value"):
        asyncio.run(rt._fire(inst, "trigger", "go", rt.new_turn()))
    assert store.query("mydb", "SELECT count(*) AS n FROM t") == [{"n": 0}]


def test_db_insert_into_missing_table_raises_friendly_error(tmp_path, monkeypatch):
    """Writing to a table that doesn't exist yet surfaces the node's actionable
    error (table name + how to create it), NOT a raw sqlite OperationalError, and
    does NOT auto-create the table (a pending design decision)."""
    store = _db(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "ins", "type": "core.db",
             "config": {"operation": "insert", "table": "chat_history",
                        "fields": "role=user"}},
        ],
        "edges": [{"src": "mydb", "src_port": "db", "dst": "ins", "dst_port": "db"}],
    })
    inst = rt.nodes["ins"]
    with pytest.raises(ValueError, match="no such table 'chat_history'"):
        asyncio.run(rt._fire(inst, "trigger", "go", rt.new_turn()))
    # the actionable hint (create it first) is part of the message.
    with pytest.raises(ValueError, match="Create it first"):
        asyncio.run(rt._fire(inst, "trigger", "go", rt.new_turn()))
    # NOT auto-created: the table still does not exist.
    assert store.query(
        "mydb",
        "SELECT count(*) AS n FROM sqlite_master "
        "WHERE type='table' AND name='chat_history'",
    ) == [{"n": 0}]


def test_db_query_missing_table_raises_friendly_error(tmp_path, monkeypatch):
    """A raw-SQL query against a missing table gets the same friendly translation
    (the table name is parsed out of sqlite's 'no such table: NAME')."""
    _db(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "q", "type": "core.db",
             "config": {"operation": "query", "sql": "SELECT * FROM ghosts"}},
        ],
        "edges": [{"src": "mydb", "src_port": "db", "dst": "q", "dst_port": "db"}],
    })
    inst = rt.nodes["q"]
    with pytest.raises(ValueError, match="no such table 'ghosts'"):
        asyncio.run(rt._fire(inst, "trigger", "go", rt.new_turn()))


# ============================================================== {tag} templating
# The headline of the consolidation: every knob is a template. Wiring a source
# into the growable `tag` port mints a named socket (the dst_port name IS the
# tag, like HTTP), and {tag} in a knob substitutes that source's live value.
def test_kv_tag_substitutes_into_key(tmp_path, monkeypatch):
    """A {tag} in the `key` knob is substituted with the wired source's value
    before the KV op runs. The wired port is named after the source, mirroring
    HTTP's `tag` growable port."""
    store = _kv(tmp_path, monkeypatch)

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mykv", "type": "core.store.kv", "config": {}},
            {"id": "uid", "type": "core.value.text", "config": {"text": "user-42"}},
            {"id": "set", "type": "core.kv",
             # the `key` knob carries a {uid} template; the wired source named
             # `uid` lands on the `uid` dst_port and substitutes in.
             "config": {"operation": "set", "key": "session:{uid}", "value": "hello"}},
        ],
        "edges": [
            {"src": "mykv", "src_port": "kv", "dst": "set", "dst_port": "kv"},
            {"src": "uid", "src_port": "out", "dst": "set", "dst_port": "uid"},
        ],
    })
    asyncio.run(rt.run())
    _fire(rt, "set")
    assert store.get("mykv", "session:user-42") == "hello"
    asyncio.run(rt.stop())


def test_db_tag_substitutes_into_fields_and_where(tmp_path, monkeypatch):
    """A {tag} in the DB node's `fields` and `where` knobs is substituted with
    the wired source's value. Same template grammar as KV and HTTP."""
    store = _db(tmp_path, monkeypatch)
    store.execute("mydb", "CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT, owner TEXT)")
    store.execute("mydb", "INSERT INTO notes(body, owner) VALUES('seed', 'ada')")

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "who", "type": "core.value.text", "config": {"text": "ada"}},
            {"id": "msg", "type": "core.value.text", "config": {"text": "second"}},
            # insert uses {msg} in `fields`, {who} in `fields` (multiple tags ok).
            {"id": "ins", "type": "core.db",
             "config": {"operation": "insert", "table": "notes",
                        "fields": "body={msg}\nowner={who}"}},
            # find uses {who} in `where`.
            {"id": "find", "type": "core.db",
             "config": {"operation": "find", "table": "notes",
                        "where": "owner={who}"}},
        ],
        "edges": [
            {"src": "mydb", "src_port": "db", "dst": "ins", "dst_port": "db"},
            {"src": "msg", "src_port": "out", "dst": "ins", "dst_port": "msg"},
            {"src": "who", "src_port": "out", "dst": "ins", "dst_port": "who"},
            {"src": "mydb", "src_port": "db", "dst": "find", "dst_port": "db"},
            {"src": "who", "src_port": "out", "dst": "find", "dst_port": "who"},
        ],
    })
    asyncio.run(rt.run())
    _fire(rt, "ins")
    _fire(rt, "find")
    rows = rt.nodes["find"].out_latch.get("rows")
    assert [r["body"] for r in rows] == ["seed", "second"]
    assert all(r["owner"] == "ada" for r in rows)
    asyncio.run(rt.stop())


def test_db_exec_attach_is_refused_with_a_reason(tmp_path, monkeypatch):
    """ATTACH through the DB node's exec gets a named reason, and no file."""
    _db(tmp_path, monkeypatch)
    target = tmp_path / "startup" / "planted.db"
    target.parent.mkdir()

    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "mydb", "type": "core.store.database", "config": {}},
            {"id": "x", "type": "core.db",
             "config": {"operation": "exec", "sql": f"ATTACH '{target}' AS planted"}},
        ],
        "edges": [{"src": "mydb", "src_port": "db", "dst": "x", "dst_port": "db"}],
    })
    with pytest.raises(ValueError, match="ATTACH, DETACH and VACUUM INTO are refused"):
        asyncio.run(rt._fire(rt.nodes["x"], "trigger", "go", rt.new_turn()))
    assert not target.exists()
