"""DB "col = value" knobs: parse the raw template first, then resolve each value.

`_parse_kv_lines` reads the DB node's `fields` and `where` knobs (one
"col = value" per line). Two bugs lived there:
  (a) the value kept the space after '=', so the knob's own placeholder format
      `role = user` stored " user", with the leading space;
  (b) {tag} and {{secret.X}} were substituted into the whole text BEFORE it was
      split into lines, so a multi-line wired value (an LLM reply) was cut at its
      first line, and a later line of it holding '=' became a column of its own.
The template is now parsed first and each VALUE resolved after, so a wired
value's content can never add lines or fields, and it is stored verbatim.

The KV node never parsed lines (its `value` knob is one whole template); its
test pins that a multi-line value round-trips intact through set and get.
Stores are temp copies swapped into the server module, like
test_consolidated_kv_db does; the real user/data/ is never touched.
"""
import asyncio

import pytest

from boltjar import secrets
from boltjar.runtime import Runtime
from boltjar.kv_store import KvStore
from boltjar.sqlite_store import SqliteStore
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)


# A realistic multi-line LLM reply: a later line names a column the table does
# not have (mode), another names one it does (sender), plus a blank line and a
# line with no '=' at all. Every byte of it must land in ONE field.
REPLY = ("Sure, here is the setting:\n"
         "mode = fast\n"
         "sender = Mallory\n"
         "\n"
         "That is all.")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh SQLite store swapped into the server module."""
    from boltjar import server
    store = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", store)
    return store


@pytest.fixture
def kv(tmp_path, monkeypatch):
    """A fresh KV store swapped into the server module."""
    from boltjar import server
    store = KvStore(root=tmp_path)
    monkeypatch.setattr(server, "KV_STORE", store)
    return store


def _run(graph: dict, *fire: str) -> Runtime:
    """Build and start the graph, fire each named node's `trigger` in order
    (the runtime pulls its wired inputs, then calls run), then stop it, all on
    one event loop, as the server runs a graph."""
    rt = Runtime()
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        try:
            for node_id in fire:
                await rt._fire(rt.nodes[node_id], "trigger", "go", rt.new_turn())
        finally:
            await rt.stop()

    asyncio.run(drive())
    return rt


def _text(node_id: str, text: str) -> dict:
    return {"id": node_id, "type": "core.value.text", "config": {"text": text}}


def _wire(src: str, dst: str, dst_port: str, src_port: str = "out") -> dict:
    return {"src": src, "src_port": src_port, "dst": dst, "dst_port": dst_port}


DB_NODE = {"id": "mydb", "type": "core.store.database", "config": {}}
KV_NODE = {"id": "mykv", "type": "core.store.kv", "config": {}}


# ================================================================ DB (b): order
def test_db_multiline_value_with_equals_round_trips_through_insert_and_find(db):
    """The chat example's insert, verbatim: `message = {tag0}` carries the LLM reply.
    Insert stores the whole reply in `message` (no cut, no extra column, no
    overwritten sender), and a find whose `where` uses the same {tag0} matches it."""
    db.execute("mydb", "CREATE TABLE chat_history("
                       "id INTEGER PRIMARY KEY, time TEXT, sender TEXT, message TEXT)")
    rt = _run({
        "nodes": [
            DB_NODE,
            _text("reply", REPLY),
            _text("clock", "2026-09-27 10:00"),
            {"id": "ins", "type": "core.db",
             "config": {"operation": "insert", "table": "chat_history",
                        "fields": "time = {tag1}\nsender = Assistant\nmessage = {tag0}"}},
            {"id": "find", "type": "core.db",
             "config": {"operation": "find", "table": "chat_history",
                        "where": "message = {tag0}"}},
        ],
        "edges": [
            _wire("mydb", "ins", "db", src_port="db"),
            _wire("reply", "ins", "tag0"),
            _wire("clock", "ins", "tag1"),
            _wire("mydb", "find", "db", src_port="db"),
            _wire("reply", "find", "tag0"),
        ],
    }, "ins", "find")

    row = {"time": "2026-09-27 10:00", "sender": "Assistant", "message": REPLY}
    assert db.query("mydb", "SELECT time, sender, message FROM chat_history") == [row]
    assert rt.nodes["find"].out_latch.get("rows") == [{"id": 1, **row}]


def test_db_multiline_secret_value_stays_one_field(db, monkeypatch):
    """{{secret.X}} resolves inside its own value too: a multi-line secret with
    '=' in it is one field, never split into lines or columns."""
    pem = "-----BEGIN KEY-----\nabc=def\n-----END KEY-----"
    # a stored secret (an arbitrary env var is not one).
    monkeypatch.setitem(secrets._store, "DBKV_PARSE_TEST_PEM", pem)
    db.execute("mydb", "CREATE TABLE creds(id INTEGER PRIMARY KEY, name TEXT, pem TEXT)")
    _run({
        "nodes": [
            DB_NODE,
            {"id": "ins", "type": "core.db",
             "config": {"operation": "insert", "table": "creds",
                        "fields": "name = deploy\npem = {{secret.DBKV_PARSE_TEST_PEM}}"}},
        ],
        "edges": [_wire("mydb", "ins", "db", src_port="db")],
    }, "ins")

    assert db.query("mydb", "SELECT name, pem FROM creds") == [{"name": "deploy", "pem": pem}]


# ================================================================ DB (a): trim
def test_db_spaces_around_equals_are_trimmed(db):
    """`role = user` (the knob's own placeholder format) stores "user", not
    " user"; and a `where` written the same way matches a row stored as plain
    'user' by any other path."""
    db.execute("mydb", "CREATE TABLE chat_history(id INTEGER PRIMARY KEY, role TEXT, content TEXT)")
    db.execute("mydb", "INSERT INTO chat_history(role, content) VALUES('user', 'seeded')")
    rt = _run({
        "nodes": [
            DB_NODE,
            {"id": "ins", "type": "core.db",
             "config": {"operation": "insert", "table": "chat_history",
                        "fields": "role = user\ncontent = hello there"}},
            {"id": "find", "type": "core.db",
             "config": {"operation": "find", "table": "chat_history",
                        "where": "role = user"}},
        ],
        "edges": [
            _wire("mydb", "ins", "db", src_port="db"),
            _wire("mydb", "find", "db", src_port="db"),
        ],
    }, "ins", "find")

    assert db.query("mydb", "SELECT role, content FROM chat_history WHERE id = 2") == [
        {"role": "user", "content": "hello there"}]
    assert rt.nodes["find"].out_latch.get("rows") == [
        {"id": 1, "role": "user", "content": "seeded"},
        {"id": 2, "role": "user", "content": "hello there"},
    ]


def test_db_wired_value_keeps_its_own_whitespace(db):
    """Only the template's own spaces around '=' are trimmed. A wired value is
    data: its leading and trailing whitespace and newlines are stored as-is."""
    body = "  indented opening\nlast line\n"
    db.execute("mydb", "CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT)")
    _run({
        "nodes": [
            DB_NODE,
            _text("reply", body),
            {"id": "ins", "type": "core.db",
             "config": {"operation": "insert", "table": "notes", "fields": "body = {tag0}"}},
        ],
        "edges": [
            _wire("mydb", "ins", "db", src_port="db"),
            _wire("reply", "ins", "tag0"),
        ],
    }, "ins")

    assert db.query("mydb", "SELECT body FROM notes") == [{"body": body}]


# ================================================================ KV
def test_kv_multiline_value_with_equals_round_trips_through_set_and_get(kv):
    """The KV equivalent: `value = {tag0}` carrying the same multi-line reply is
    stored whole and read back whole (KV resolves the value as one template)."""
    rt = _run({
        "nodes": [
            KV_NODE,
            _text("reply", REPLY),
            {"id": "set", "type": "core.kv",
             "config": {"operation": "set", "key": "last_reply", "value": "{tag0}"}},
            {"id": "get", "type": "core.kv",
             "config": {"operation": "get", "key": "last_reply"}},
        ],
        "edges": [
            _wire("mykv", "set", "kv", src_port="kv"),
            _wire("reply", "set", "tag0"),
            _wire("mykv", "get", "kv", src_port="kv"),
        ],
    }, "set", "get")

    assert kv.get("mykv", "last_reply") == REPLY
    assert rt.nodes["get"].out_latch.get("value") == REPLY
