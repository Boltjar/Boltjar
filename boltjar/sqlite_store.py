"""Embedded SQLite stores, one file per db-key, under a data root.

The store is the single source of truth for the row DATA and the live schema.
Connections are opened lazily and cached per key. Rows come back as plain dicts
(json-friendly). Parameters are bound (`:name`), never string-concatenated. All
access goes through a lock: the cached connections are shared across the event
loop's concurrent tasks (check_same_thread is off), so callers must not interleave
raw access to a connection.
"""
from __future__ import annotations

import hashlib
import pathlib
import sqlite3
import threading


def _safe_key(key: str) -> str:
    """A filesystem-safe, collision-resistant store key. Strips to [a-zA-Z0-9-_];
    if that leaves nothing (empty or all-symbol key), derives a stable hash so two
    different odd keys never silently share one file."""
    cleaned = "".join(c for c in (key or "") if c.isalnum() or c in "-_")
    if cleaned:
        return cleaned
    return "db_" + hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:12]


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier (table / column) by doubling embedded quotes, so a
    name fetched from the schema can be interpolated without injection."""
    return '"' + name.replace('"', '""') + '"'


# The column types the schema editor offers. A SQL type is a keyword, not an
# identifier, so it cannot be quoted: instead we map a caller's type to one of
# these allow-listed affinities, defaulting to TEXT for anything unrecognised.
_COLTYPES = {
    "text": "TEXT",
    "int": "INTEGER",
    "integer": "INTEGER",
    "real": "REAL",
    "float": "REAL",
    "blob": "BLOB",
    "numeric": "NUMERIC",
    "bool": "INTEGER",
    "boolean": "INTEGER",
}


def _safe_coltype(coltype: str | None) -> str:
    """Map a requested column type to an allow-listed SQLite affinity. Defaults to
    TEXT, so a bogus or missing type can never inject SQL through the type slot."""
    return _COLTYPES.get((coltype or "").strip().lower(), "TEXT")


def _affinity(coltype: str | None) -> str:
    """The affinity SQLite gives a declared column type (the rules in section 3.1
    of its datatype page), so VARCHAR(20) and TEXT read as the same kind of
    column when a declared schema is compared with a live one."""
    t = (coltype or "").upper()
    if "INT" in t:
        return "INTEGER"
    if "CHAR" in t or "CLOB" in t or "TEXT" in t:
        return "TEXT"
    if "BLOB" in t or not t:
        return "BLOB"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "REAL"
    return "NUMERIC"


def _declared_type(coltype: str | None) -> str:
    """The allow-listed type a declared column is created with: the schema
    editor's names (int, bool, real...) map as `_safe_coltype` maps them, and a
    type read back from a live table (DATETIME, VARCHAR(20)) keeps the affinity
    SQLite gave it. No type at all is TEXT, as in `create_table`."""
    raw = (coltype or "").strip()
    if not raw:
        return "TEXT"
    return _affinity(_COLTYPES.get(raw.lower(), raw))


def _authorize(action: int, arg1, arg2, db_name, trigger) -> int:
    """The authorizer on every store connection: no statement may reach a file
    outside the store. ATTACH (and VACUUM INTO, which runs one with its target
    file) could create or read an SQLite file anywhere the user can write, such
    as a startup folder. Plain VACUUM attaches a private temporary database
    whose name is '', so that one stays allowed."""
    if action == sqlite3.SQLITE_ATTACH and arg1 != "":
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_DETACH:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class SqliteStore:
    def __init__(self, root: pathlib.Path | str = "user/data/dbs") -> None:
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._conns: dict[str, sqlite3.Connection] = {}
        self._lock = threading.RLock()

    def _conn(self, key: str) -> sqlite3.Connection:
        key = _safe_key(key)
        conn = self._conns.get(key)
        if conn is None:
            conn = sqlite3.connect(str(self.root / f"{key}.db"), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.set_authorizer(_authorize)
            if hasattr(conn, "setlimit"):  # Python 3.11+
                # one slot, for the temporary database plain VACUUM attaches (0
                # would refuse VACUUM itself); the authorizer refuses every file.
                conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 1)
            self._conns[key] = conn
        return conn

    def query(self, key: str, sql: str, params: dict | None = None) -> list[dict]:
        with self._lock:
            cur = self._conn(key).execute(sql, params or {})
            return [dict(row) for row in cur.fetchall()]

    def execute(self, key: str, sql: str, params: dict | None = None) -> dict:
        with self._lock:
            conn = self._conn(key)
            cur = conn.execute(sql, params or {})
            conn.commit()
            # rowcount is -1 for DDL (CREATE/DROP/...); surface 0 changes there.
            return {"changes": max(cur.rowcount, 0), "lastId": cur.lastrowid}

    # ---- schema mutations -------------------------------------------------
    # All DDL is built with `_quote_ident` on every table/column name, so a name
    # is escaped (never string-concatenated raw) before it reaches SQLite. The
    # column TYPE is validated against a small allow-list rather than quoted,
    # since a SQL type is a keyword, not an identifier. Each runs under the lock
    # on the same cached connection as query/execute.

    def create_table(self, key: str, table: str, columns: list[dict] | None = None) -> dict:
        """Create a table. `columns` is a list of {name, type, pk?} dicts; when
        empty, the table is created with a single `id` integer primary key so it
        is always valid SQLite (a table needs at least one column)."""
        cols = list(columns or [])
        defs: list[str] = []
        has_pk = False
        for c in cols:
            name = c.get("name")
            if not name:
                continue
            coltype = _safe_coltype(c.get("type"))
            piece = f"{_quote_ident(name)} {coltype}"
            if c.get("pk"):
                piece += " PRIMARY KEY"
                has_pk = True
            defs.append(piece)
        if not defs:
            defs.append(f"{_quote_ident('id')} INTEGER PRIMARY KEY")
            has_pk = True
        body = ", ".join(defs)
        with self._lock:
            conn = self._conn(key)
            conn.execute(f"CREATE TABLE {_quote_ident(table)} ({body})")
            conn.commit()
        return self.schema(key)

    def restore_schema(self, key: str, tables: list[dict]) -> dict:
        """Recreate tables (EMPTY) from a snapshot of {name, columns:[{name,type,
        pk}]}, used when an erased store node is brought back by undo: the column
        structure returns, the rows do not. Skips a table that already exists."""
        with self._lock:
            conn = self._conn(key)
            existing = {
                r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        for t in tables or []:
            name = t.get("name")
            if not name or name in existing:
                continue
            self.create_table(key, name, t.get("columns") or [])
        return self.schema(key)

    def ensure_schema(self, key: str, tables: list[dict]) -> dict:
        """Make a declared schema exist, in the {name, columns:[{name, type, pk}]}
        shape `schema()` returns: create each missing table and add each missing
        column. It only ever adds. A table or column that exists stays as it is
        (never dropped, retyped or emptied), and names match the way SQLite
        matches them, ignoring case. What cannot be made to match is reported in
        `conflicts`: a column whose live type has another affinity than the
        declared one, a primary key column missing from a table that exists
        (SQLite cannot add one), or a name SQLite refuses. Returns
        {created, added, conflicts, schema}."""
        created: list[str] = []
        added: list[str] = []
        conflicts: list[str] = []
        with self._lock:
            conn = self._conn(key)
            live = {
                r["name"].lower(): r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for t in tables or []:
                if not isinstance(t, dict) or not str(t.get("name") or "").strip():
                    continue
                name = str(t["name"]).strip()
                cols = [
                    {"name": str(c["name"]).strip(), "type": c.get("type"), "pk": bool(c.get("pk"))}
                    for c in (t.get("columns") or [])
                    if isinstance(c, dict) and str(c.get("name") or "").strip()
                ]
                try:
                    if name.lower() not in live:
                        self.create_table(key, name, [
                            {**c, "type": _declared_type(c["type"])} for c in cols
                        ])
                        live[name.lower()] = name
                        created.append(name)
                        continue
                    table = live[name.lower()]
                    existing = {
                        c["name"].lower(): c["type"] for c in conn.execute(
                            f"PRAGMA table_info({_quote_ident(table)})"
                        ).fetchall()
                    }
                    for c in cols:
                        col, declared = c["name"], str(c["type"] or "").strip()
                        if col.lower() in existing:
                            have = existing[col.lower()]
                            if declared and _affinity(have) != _declared_type(declared):
                                conflicts.append(
                                    f"{table}.{col} is declared {declared.lower()} but the "
                                    f"database holds it as {(have or 'untyped').lower()}"
                                )
                            continue
                        if c["pk"]:
                            conflicts.append(
                                f"{table}.{col} is declared as the primary key, and SQLite "
                                f"cannot add a primary key column to a table that exists"
                            )
                            continue
                        self.add_column(key, table, col, _declared_type(declared))
                        existing[col.lower()] = _declared_type(declared)
                        added.append(f"{table}.{col}")
                except sqlite3.DatabaseError as exc:
                    conflicts.append(f"{name}: {exc}")
        return {"created": created, "added": added, "conflicts": conflicts,
                "schema": self.schema(key)}

    def add_column(self, key: str, table: str, name: str, coltype: str | None = None) -> dict:
        coltype = _safe_coltype(coltype)
        with self._lock:
            conn = self._conn(key)
            conn.execute(
                f"ALTER TABLE {_quote_ident(table)} ADD COLUMN {_quote_ident(name)} {coltype}"
            )
            conn.commit()
        return self.schema(key)

    def rename_table(self, key: str, old: str, new: str) -> dict:
        with self._lock:
            conn = self._conn(key)
            conn.execute(f"ALTER TABLE {_quote_ident(old)} RENAME TO {_quote_ident(new)}")
            conn.commit()
        return self.schema(key)

    def rename_column(self, key: str, table: str, old: str, new: str) -> dict:
        with self._lock:
            conn = self._conn(key)
            conn.execute(
                f"ALTER TABLE {_quote_ident(table)} "
                f"RENAME COLUMN {_quote_ident(old)} TO {_quote_ident(new)}"
            )
            conn.commit()
        return self.schema(key)

    def drop_table(self, key: str, table: str) -> dict:
        with self._lock:
            conn = self._conn(key)
            conn.execute(f"DROP TABLE {_quote_ident(table)}")
            conn.commit()
        return self.schema(key)

    def drop_column(self, key: str, table: str, name: str) -> dict:
        with self._lock:
            conn = self._conn(key)
            conn.execute(
                f"ALTER TABLE {_quote_ident(table)} DROP COLUMN {_quote_ident(name)}"
            )
            conn.commit()
        return self.schema(key)

    def clear(self, key: str) -> dict:
        """Delete every ROW from every table: wipe the data but keep the schema
        (tables + columns stay). Returns the schema (now all rowCount 0). Each
        table name comes from sqlite_master and is quoted."""
        with self._lock:
            conn = self._conn(key)
            names = [
                r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for t in names:
                conn.execute(f"DELETE FROM {_quote_ident(t)}")
            conn.commit()
        return self.schema(key)

    def path(self, key: str) -> str:
        """Absolute path of the SQLite file backing `key` (created lazily on
        first access). Used by the editor to surface where the data lives."""
        return str((self.root / f"{_safe_key(key)}.db").resolve())

    def destroy(self, key: str) -> None:
        """Erase the database FILE from disk (privacy: the data is gone, not just
        the tables). Closes the cached connection first; no-op if the file is
        absent. There is no undo."""
        safe = _safe_key(key)
        with self._lock:
            conn = self._conns.pop(safe, None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            p = self.root / f"{safe}.db"
            p.unlink(missing_ok=True)

    def info(self, key: str) -> dict:
        """Compact summary of the database state for the editor body: path on
        disk, plus each table's name, rowCount and columns (name/type/pk) so the
        node can show the fields up front, sorted by row count desc."""
        tables = [
            {"name": t["name"], "rows": t["rows"], "columns": t["columns"]}
            for t in self.schema(key)
        ]
        tables.sort(key=lambda t: t["rows"], reverse=True)
        return {"path": self.path(key), "tables": tables}

    def schema(self, key: str) -> list[dict]:
        with self._lock:
            conn = self._conn(key)
            tables = [
                r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            out: list[dict] = []
            for t in tables:
                q = _quote_ident(t)
                cols = [
                    {"name": c["name"], "type": c["type"], "pk": bool(c["pk"])}
                    for c in conn.execute(f"PRAGMA table_info({q})").fetchall()
                ]
                rows = conn.execute(f"SELECT count(*) AS n FROM {q}").fetchone()["n"]
                out.append({"name": t, "rows": rows, "columns": cols})
            return out

    def close(self) -> None:
        """Close all cached connections (shutdown / test teardown)."""
        with self._lock:
            for conn in self._conns.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._conns.clear()
