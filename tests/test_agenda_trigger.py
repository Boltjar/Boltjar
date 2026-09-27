"""The Agenda trigger (core.trigger.agenda): fire from DATA, not a static clock.

It polls a wired store table of {when, payload} rows and fires each row when its
`when` is due, marking the row done (or deleting it) so it never double-fires. The
store handle arrives on the `db` input the same way the DB node receives it (a
Database node emits it); the trigger reads it each poll via ctx.pull.
"""
import asyncio
import time

from boltjar.runtime import Runtime
from boltjar.sqlite_store import SqliteStore
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
import boltjar.nodes.core.builtin as builtin


DB_KEY = "adb"  # the Database node id == its emitted handle (no db_key configured)


def _db(tmp_path, monkeypatch):
    from boltjar import server
    store = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", store)
    return store


def _make_table(store, columns_sql, rows):
    """Create the agenda table with the given column DDL and insert `rows`
    (each a dict of column->value). `when` is a reserved word, always quoted."""
    store.execute(DB_KEY, f'CREATE TABLE "agenda" ({columns_sql})')
    for r in rows:
        cols = ", ".join(f'"{c}"' for c in r)
        binds = ", ".join(f":{c}" for c in r)
        store.execute(DB_KEY, f'INSERT INTO "agenda" ({cols}) VALUES ({binds})', r)


def _graph():
    return {
        "nodes": [
            {"id": DB_KEY, "type": "core.store.database", "config": {}},
            {"id": "ag", "type": "core.trigger.agenda",
             "config": {"table": "agenda", "poll": 0.05}},
            {"id": "lg", "type": "core.output.log", "config": {"label": "due"}},
        ],
        "edges": [
            {"src": DB_KEY, "src_port": "db", "dst": "ag", "dst_port": "db"},
            {"src": "ag", "src_port": "due", "dst": "lg", "dst_port": "in"},
        ],
    }


# --------------------------------------------------------------- when parsing

def test_parse_when_is_liberal():
    now = time.time()
    assert builtin._parse_when(1000) == 1000.0            # epoch int
    assert builtin._parse_when(1000.5) == 1000.5          # epoch float
    assert builtin._parse_when("1000") == 1000.0          # numeric string
    # ISO 8601 with and without a trailing Z, and a date-only shape all parse.
    assert builtin._parse_when("2020-01-01T00:00:00Z") is not None
    assert builtin._parse_when("2020-01-01 12:30") is not None
    assert builtin._parse_when("2020-01-01") is not None
    # unparseable / empty / bool -> None (the row is skipped, never fired blind).
    assert builtin._parse_when("not a date") is None
    assert builtin._parse_when("") is None
    assert builtin._parse_when(None) is None
    assert builtin._parse_when(True) is None


# --------------------------------------------------------------- due + order + no double-fire

def test_due_rows_fire_in_order_and_never_double_fire(tmp_path, monkeypatch):
    store = _db(tmp_path, monkeypatch)
    now = time.time()
    # insertion order (late, early) deliberately != time order; a future row and an
    # already-done row must never fire.
    _make_table(
        store,
        '"id" INTEGER PRIMARY KEY, "when" REAL, "payload" TEXT, "done" INTEGER DEFAULT 0',
        [
            {"when": now - 50, "payload": '{"m":"late"}'},
            {"when": now - 100, "payload": '{"m":"early"}'},
            {"when": now + 3600, "payload": '{"m":"future"}'},
            {"when": now - 100, "payload": '{"m":"already"}', "done": 1},
        ],
    )

    events = []
    rt = Runtime(observer=events.append)
    rt.build(_graph())

    async def drive():
        await rt.run()
        await asyncio.sleep(0.3)   # ~6 polls at 0.05s; done-marking must dedupe
        await rt.stop()
    asyncio.run(drive())

    msgs = [e["message"] for e in events if e["kind"] == "log" and e["node"] == "lg"]
    # exactly the two due rows fired, once each despite many polls (no double-fire).
    assert len(msgs) == 2, msgs
    # earliest-first: `early` (now-100) before `late` (now-50).
    assert "early" in msgs[0] and "late" in msgs[1], msgs
    # the future and already-done rows never fired.
    assert not any("future" in m or "already" in m for m in msgs)
    # the two fired rows are now marked done in the store (nothing destroyed).
    done = store.query(DB_KEY, 'SELECT COUNT(*) AS c FROM "agenda" WHERE "done"=1')
    assert done[0]["c"] == 3  # the two just-fired + the pre-marked one


def test_due_carries_payload_on_both_ports(tmp_path, monkeypatch):
    store = _db(tmp_path, monkeypatch)
    now = time.time()
    _make_table(
        store,
        '"id" INTEGER PRIMARY KEY, "when" REAL, "payload" TEXT, "done" INTEGER DEFAULT 0',
        [{"when": now - 10, "payload": '{"task":"wake"}'}],
    )
    rt = Runtime()
    rt.build(_graph())
    ag = rt.nodes["ag"]
    asyncio.run(ag.obj._poll_once(ag.ctx))
    # a JSON payload is parsed to structure; `due` (event) carries it too, so a
    # burst of due rows never loses a payload to the shared latch.
    assert ag.out_latch.get("payload") == {"task": "wake"}
    assert ag.out_latch.get("due") == {"task": "wake"}


# --------------------------------------------------------------- done-marking fallbacks

def test_done_marking_updates_the_flag(tmp_path, monkeypatch):
    store = _db(tmp_path, monkeypatch)
    now = time.time()
    _make_table(
        store,
        '"id" INTEGER PRIMARY KEY, "when" REAL, "payload" TEXT, "done" INTEGER DEFAULT 0',
        [{"when": now - 10, "payload": "hi"}],
    )
    rt = Runtime()
    rt.build(_graph())
    ag = rt.nodes["ag"]
    asyncio.run(ag.obj._poll_once(ag.ctx))
    # the row is preserved (audit), just flagged done, so the next poll skips it.
    rows = store.query(DB_KEY, 'SELECT "done" FROM "agenda"')
    assert rows[0]["done"] == 1
    asyncio.run(ag.obj._poll_once(ag.ctx))  # a second poll must not re-fire
    assert ag.obj._fired_rids == {1}


def test_done_marking_deletes_when_no_done_column(tmp_path, monkeypatch):
    store = _db(tmp_path, monkeypatch)
    now = time.time()
    # NO `done` column: the UPDATE fails and the node falls back to DELETE.
    _make_table(
        store,
        '"id" INTEGER PRIMARY KEY, "when" REAL, "payload" TEXT',
        [{"when": now - 10, "payload": "hi"}],
    )
    rt = Runtime()
    rt.build(_graph())
    ag = rt.nodes["ag"]
    asyncio.run(ag.obj._poll_once(ag.ctx))
    # nothing left: the fired row was deleted (the fallback), so it cannot re-fire.
    assert store.query(DB_KEY, 'SELECT COUNT(*) AS c FROM "agenda"')[0]["c"] == 0


def test_no_table_and_no_db_are_safe(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    rt = Runtime()
    rt.build(_graph())
    ag = rt.nodes["ag"]
    # the table does not exist yet: a poll logs and returns, never raises.
    asyncio.run(ag.obj._poll_once(ag.ctx))
    assert ag.out_latch.get("due") is None
