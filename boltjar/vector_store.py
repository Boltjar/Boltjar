"""Embedded vector stores, one SQLite file per store-key under a data root.

Each store holds named NAMESPACES (e.g. "facts", "messages"); a namespace is a
table of (id, ref, doc, vec BLOB, meta). Vectors are float32, L2-normalized at
index time and stored as raw bytes. Search loads a namespace's vectors into a numpy
matrix (cached, invalidated on write) and ranks by cosine = matrix @ query (both
unit-norm, so the dot product IS the cosine). Brute force over thousands of items
is the right tool here: no ANN index, no torch. Mirrors sqlite_store.py's lazy,
per-key, lock-guarded connection model.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import threading

import numpy as np


def _safe_key(key: str) -> str:
    """Filesystem-safe, collision-resistant store key (mirrors sqlite_store)."""
    cleaned = "".join(c for c in (key or "") if c.isalnum() or c in "-_")
    if cleaned:
        return cleaned
    return "vec_" + hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:12]


def _safe_ns(ns: str) -> str:
    """A safe table name for a namespace. Always prefixed so it cannot collide with
    sqlite internals, and stripped to identifier-safe chars (never concatenated raw)."""
    cleaned = "".join(c for c in (ns or "") if c.isalnum() or c in "_") or "default"
    return "ns_" + cleaned


class VectorStore:
    def __init__(self, root: pathlib.Path | str = "user/data/vectors") -> None:
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._conns: dict[str, sqlite3.Connection] = {}
        # (safe_key, namespace) -> (ids, meta, matrix) cache, dropped on any write.
        self._mats: dict[tuple[str, str], tuple[list, list, np.ndarray]] = {}
        self._lock = threading.RLock()

    def _conn(self, key: str) -> sqlite3.Connection:
        key = _safe_key(key)
        conn = self._conns.get(key)
        if conn is None:
            conn = sqlite3.connect(str(self.root / f"{key}.db"), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._conns[key] = conn
        return conn

    def _ensure(self, conn: sqlite3.Connection, ns: str) -> str:
        t = _safe_ns(ns)
        conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{t}" '
            "(id INTEGER PRIMARY KEY, ref TEXT, doc TEXT, vec BLOB, meta TEXT)"
        )
        return t

    @staticmethod
    def _unit(vec) -> np.ndarray:
        v = np.asarray(vec, dtype=np.float32).ravel()
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    # ---- writes -----------------------------------------------------------
    def index(self, key: str, ns: str, vector, doc: str = "", ref: str = "",
              meta: dict | None = None) -> int:
        """Store one L2-normalized vector + its doc/ref/meta. Returns the row id."""
        v = self._unit(vector)
        with self._lock:
            conn = self._conn(key)
            t = self._ensure(conn, ns)
            cur = conn.execute(
                f'INSERT INTO "{t}" (ref, doc, vec, meta) VALUES (?, ?, ?, ?)',
                (str(ref), str(doc), v.tobytes(), json.dumps(meta or {})),
            )
            conn.commit()
            self._mats.pop((_safe_key(key), _safe_ns(ns)), None)  # invalidate the matrix cache
            return int(cur.lastrowid)

    def delete(self, key: str, ns: str, ref: str) -> int:
        """Delete every row in the namespace with this external ref. Returns count."""
        with self._lock:
            conn = self._conn(key)
            t = self._ensure(conn, ns)
            cur = conn.execute(f'DELETE FROM "{t}" WHERE ref = ?', (str(ref),))
            conn.commit()
            self._mats.pop((_safe_key(key), _safe_ns(ns)), None)
            return max(cur.rowcount, 0)

    def clear(self, key: str, ns: str | None = None) -> int:
        """Drop a namespace (or every namespace when ns is None). Returns tables hit."""
        with self._lock:
            conn = self._conn(key)
            if ns is not None:
                names = [_safe_ns(ns)]
            else:
                names = [
                    r["name"] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ns_%'"
                    ).fetchall()
                ]
            for t in names:
                conn.execute(f'DROP TABLE IF EXISTS "{t}"')
            conn.commit()
            sk = _safe_key(key)
            for k in [k for k in self._mats if k[0] == sk]:
                self._mats.pop(k, None)
            return len(names)

    # ---- read -------------------------------------------------------------
    def _matrix(self, key: str, ns: str):
        # key the cache by the SAME normalised values used for the table/file, so
        # two raw namespaces that map to one table never get separate, stale caches.
        sk, nk = _safe_key(key), _safe_ns(ns)
        cached = self._mats.get((sk, nk))
        if cached is not None:
            return cached
        conn = self._conn(key)
        t = self._ensure(conn, ns)
        rows = conn.execute(f'SELECT id, ref, doc, vec, meta FROM "{t}"').fetchall()
        if not rows:
            packed = ([], [], np.zeros((0, 0), dtype=np.float32))
            self._mats[(sk, nk)] = packed
            return packed
        # Keep only rows whose vector dimension matches the namespace's modal
        # dimension, so a stray row from a different embed model (e.g. after a model
        # switch) can never make np.stack raise and break ALL search on the
        # namespace. A mismatched row is dropped (lean: never crash search).
        vecs = [(r, np.frombuffer(r["vec"], dtype=np.float32)) for r in rows]
        dims = [v.shape[0] for _, v in vecs]
        # the most common dimension; on a count tie prefer the LARGER dimension
        # (usually the current/intended embed model) so the winner is deterministic,
        # not a hash-table-order artifact of max(set(...)).
        from collections import Counter
        counts = Counter(dims)
        modal = max(counts, key=lambda d: (counts[d], d))
        kept = [(r, v) for r, v in vecs if v.shape[0] == modal]
        ids = [r["id"] for r, _ in kept]
        meta = [{"ref": r["ref"], "doc": r["doc"],
                 "metadata": json.loads(r["meta"] or "{}")} for r, _ in kept]
        mat = np.stack([v for _, v in kept]) if kept else np.zeros((0, 0), dtype=np.float32)
        packed = (ids, meta, mat)
        self._mats[(sk, nk)] = packed
        return packed

    def search(self, key: str, ns: str, vector, top_k: int = 5,
               min_score: float = 0.0) -> list[dict]:
        """Top-k by cosine over the namespace. Each hit: {score, text, ref,
        metadata, id}. Empty namespace or zero query returns []."""
        q = self._unit(vector)
        with self._lock:
            ids, meta, mat = self._matrix(key, ns)
            if mat.shape[0] == 0 or q.shape[0] == 0 or mat.shape[1] != q.shape[0]:
                return []
            scores = mat @ q  # both unit-norm -> dot product is cosine
            order = np.argsort(-scores)[: max(1, int(top_k))]
            out: list[dict] = []
            for i in order:
                s = float(scores[int(i)])
                if s < min_score:
                    continue
                m = meta[int(i)]
                out.append({"score": s, "text": m["doc"], "ref": m["ref"],
                            "metadata": m["metadata"], "id": ids[int(i)]})
            return out

    def count(self, key: str, ns: str) -> int:
        with self._lock:
            conn = self._conn(key)
            t = self._ensure(conn, ns)
            return int(conn.execute(f'SELECT count(*) AS n FROM "{t}"').fetchone()["n"])

    def path(self, key: str) -> str:
        return str((self.root / f"{_safe_key(key)}.db").resolve())

    def destroy(self, key: str) -> None:
        """Erase the store FILE from disk (no undo); closes the cached connection."""
        safe = _safe_key(key)
        with self._lock:
            conn = self._conns.pop(safe, None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            for k in [k for k in self._mats if k[0] == safe]:
                self._mats.pop(k, None)
            (self.root / f"{safe}.db").unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            for conn in self._conns.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._conns.clear()
            self._mats.clear()
