"""Embedded key-value stores, one JSON file per store-key, under a data root.

Each KV store is a flat string->JSON map persisted as a single JSON file named
after a sandboxed store key (the same `_safe_key` traversal-proofing used by
`sqlite_store`). Values may be any JSON-serialisable Python object. Every write
persists IMMEDIATELY and ATOMICALLY: the file is rewritten to a temp sibling and
`os.replace`d over the target, so a crash mid-write never leaves a half-written
or truncated store. All access goes through a lock so concurrent event-loop tasks
never interleave a read against a partial in-memory map.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import threading


def _safe_key(key: str) -> str:
    """A filesystem-safe, collision-resistant store key. A key already made only of
    [a-zA-Z0-9-_] (the common case: a uuid store key) is used verbatim. The moment
    any character had to be stripped, two different raw keys could clean to the same
    string (e.g. `user.name` and `username`), so we append a stable hash of the raw
    key to keep them distinct. An all-symbol/empty key falls back to a pure hash."""
    raw = key or ""
    cleaned = "".join(c for c in raw if c.isalnum() or c in "-_")
    if cleaned == raw and cleaned:
        return cleaned
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{cleaned}_{h}" if cleaned else f"kv_{h}"


class KvStore:
    def __init__(self, root: pathlib.Path | str = "user/data/kv") -> None:
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # in-memory map per store key, loaded lazily from disk on first access.
        self._maps: dict[str, dict] = {}
        self._lock = threading.RLock()

    def _path(self, key: str) -> pathlib.Path:
        return self.root / f"{_safe_key(key)}.json"

    def _map(self, key: str) -> dict:
        """The live map for a store key, loaded once from disk and cached. A
        missing or unreadable file reads as an empty store (lean: never raises)."""
        sk = _safe_key(key)
        m = self._maps.get(sk)
        if m is None:
            path = self.root / f"{sk}.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                m = data if isinstance(data, dict) else {}
            except Exception:
                m = {}
            self._maps[sk] = m
        return m

    def _flush(self, key: str) -> None:
        """Persist a store key's map atomically: write a temp sibling, then replace.
        os.replace is atomic on both POSIX and Windows, so a reader never sees a
        partially written file and a crash leaves the old file intact."""
        sk = _safe_key(key)
        target = self.root / f"{sk}.json"
        tmp = self.root / f".{sk}.json.tmp"
        tmp.write_text(json.dumps(self._maps.get(sk, {}), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)

    # ---- operations -------------------------------------------------------
    def get(self, key: str, k: str, default=None):
        with self._lock:
            return self._map(key).get(str(k), default)

    def set(self, key: str, k: str, value) -> None:
        with self._lock:
            self._map(key)[str(k)] = value
            self._flush(key)

    def delete(self, key: str, k: str) -> None:
        """Remove a key. Idempotent: a missing key is a no-op and skips the disk
        write (only an actual removal persists)."""
        with self._lock:
            m = self._map(key)
            if str(k) in m:
                del m[str(k)]
                self._flush(key)

    def has(self, key: str, k: str) -> bool:
        with self._lock:
            return str(k) in self._map(key)

    def keys(self, key: str) -> list:
        with self._lock:
            return list(self._map(key).keys())

    def path(self, key: str) -> str:
        """Absolute path of the JSON file backing `key`. Surfaced in the
        editor body so the user can see where the store lives on disk."""
        return str(self._path(key).resolve())

    def destroy(self, key: str) -> None:
        """Erase the JSON FILE from disk (privacy: the data is gone). Drops the
        cached map first; no-op if the file is absent. There is no undo."""
        sk = _safe_key(key)
        with self._lock:
            self._maps.pop(sk, None)
            (self.root / f"{sk}.json").unlink(missing_ok=True)

    def info(self, key: str, sample: int = 5) -> dict:
        """Compact summary for the editor body: path on disk, total key
        count, and the first `sample` keys (sorted lexicographically so the
        preview is stable across renders)."""
        with self._lock:
            m = self._map(key)
            sorted_keys = sorted(m.keys())
            return {
                "path": self.path(key),
                "count": len(m),
                "sample": sorted_keys[:sample],
            }

    def clear_cache(self) -> None:
        """Drop the in-memory maps (test teardown / shutdown). Disk is untouched;
        the next access reloads from the persisted files."""
        with self._lock:
            self._maps.clear()
