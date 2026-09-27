"""
boltjar.resume: which graphs are On, kept across a restart of Boltjar.

The server records a graph here the moment it turns On (the exact graph JSON it
runs, a draft included), keeps it through a Restart, and removes it only when a
person turns it Off. Stopping the server (Ctrl+C, closing the window) is not an
Off: the record stays as it was, so the next launch knows what was running.
When "Resume workflows after launch" is on, that launch powers each recorded
graph back On from the JSON recorded here (boltjar.server.resume_graphs).

The record lives in user/data/resume.json, rewritten whole through a temporary
file (atomic) on every change, in the order the graphs turned On:

    {"version": 1, "graphs": {"chat": {"since": "2026-09-27T09:00:00Z", "graph": {...}}}}
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Module-level so the test suite can point it at a tmp dir.
PATH = ROOT / "user" / "data" / "resume.json"
VERSION = 1


def _read() -> dict[str, dict]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    graphs = data.get("graphs") if isinstance(data, dict) else None
    if not isinstance(graphs, dict):
        return {}
    return {slug: entry for slug, entry in graphs.items()
            if isinstance(entry, dict) and isinstance(entry.get("graph"), dict)}


def _write(graphs: dict[str, dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=PATH.name + ".", suffix=".tmp", dir=PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"version": VERSION, "graphs": graphs}, indent=2))
        os.replace(tmp, PATH)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def record(slug: str, graph: dict) -> None:
    """`slug` is On, running `graph`. A graph already recorded keeps its place
    in the order and its `since`; its JSON becomes the new one (a Restart)."""
    graphs = _read()
    since = graphs.get(slug, {}).get("since") or _now()
    graphs[slug] = {"since": since, "graph": graph}
    _write(graphs)


def forget(slug: str) -> bool:
    """A person turned `slug` Off, or its graph is gone: drop it. False when it
    was not recorded (nothing is written then)."""
    graphs = _read()
    if slug not in graphs:
        return False
    del graphs[slug]
    _write(graphs)
    return True


def recorded() -> list[tuple[str, dict]]:
    """(slug, graph JSON) for every graph that was On, in the order they turned On."""
    return [(slug, entry["graph"]) for slug, entry in _read().items()]


def slugs() -> list[str]:
    return list(_read())
