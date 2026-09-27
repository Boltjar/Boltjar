"""
boltjar.resume: the graphs On when Boltjar last stopped, for the next launch.

The server records a graph here the moment it turns On (the exact graph JSON it
runs, a draft that was never saved included) and keeps it through a Restart. It
drops it when a person turns it Off, when a Restart leaves it Off (the new
build failed after the old runtime stopped) and when its graph is deleted.
Stopping the server (Ctrl+C, closing the window, the computer shutting down) is
not an Off: the record stays as it was, so it names the graphs that were On at
that moment.

Every entry is stamped with the launch that wrote it. What an earlier launch
left is the graphs On when it ended, and this launch settles it
(boltjar.server.launch_sequence): with "Resume workflows after launch" on, each
is powered back On from the JSON recorded here, and one that cannot be stays
recorded, stamped anew, to be tried again at the next launch; with --no-resume
they are all kept for the next launch; with the setting off they are dropped,
since they are Off in this run (a graph turned On again records itself).

The record lives in user/data/resume.json, rewritten whole through a temporary
file (atomic) on every change, in the order the graphs turned On:

    {"version": 1, "graphs": {"chat": {"since": "2026-09-27T09:00:00Z",
                                       "launch": "<id>", "saved": true, "graph": {...}}}}

`saved`: the graph had a file (a saved graph or a shipped example) since it was
recorded, so a launch that finds none drops it as deleted. A graph never saved
has no file to lose and comes back from its JSON.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Module-level so the test suite can point it at a tmp dir.
PATH = ROOT / "user" / "data" / "resume.json"
VERSION = 1

_log = logging.getLogger(__name__)

# This run of Boltjar: the stamp on every entry it writes. An entry stamped
# otherwise was left by an earlier launch.
LAUNCH = uuid.uuid4().hex


def new_launch() -> None:
    """Start a new run, as a new server process does (the test suite's restart)."""
    global LAUNCH
    LAUNCH = uuid.uuid4().hex


# ---------------------------------------------------------------- the file
def _read(*, for_write: bool = False) -> dict[str, dict]:
    """The recorded graphs; {} when there is no record yet. A file that cannot
    be read (a sharing violation, no permission) raises OSError, so nothing is
    ever written over what it holds. One that is not a record reads as empty;
    before a write it is set aside as resume.json.bad, never overwritten."""
    try:
        text = PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
        graphs = data.get("graphs") if isinstance(data, dict) else None
        if not isinstance(graphs, dict):
            raise ValueError("it holds no graphs")
    except ValueError as exc:
        if for_write:
            bad = PATH.with_name(PATH.name + ".bad")
            os.replace(PATH, bad)
            _log.warning("%s could not be read (%s): set aside as %s", PATH.name, exc, bad.name)
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


def _earlier(entry: dict) -> bool:
    """The entry was left by an earlier launch (one from before stamps counts)."""
    return entry.get("launch") != LAUNCH


# ---------------------------------------------------------------- changes
def record(slug: str, graph: dict, saved: bool = True) -> None:
    """`slug` is On in this run, running `graph`; `saved`: it has a graph file.
    A graph already recorded keeps its place in the order and its `since`; its
    JSON becomes the new one (a Restart)."""
    graphs = _read(for_write=True)
    since = graphs.get(slug, {}).get("since") or _now()
    graphs[slug] = {"since": since, "launch": LAUNCH, "saved": bool(saved), "graph": graph}
    _write(graphs)


def mark_saved(slug: str) -> None:
    """`slug` was just saved: from now on it has a graph file to lose. Writes
    only when that changes the entry."""
    graphs = _read(for_write=True)
    entry = graphs.get(slug)
    if entry is None or entry.get("saved") is True:
        return
    entry["saved"] = True
    _write(graphs)


def forget(slug: str) -> bool:
    """`slug` is not to come back: drop it. False when it was not recorded
    (nothing is written then)."""
    graphs = _read(for_write=True)
    if slug not in graphs:
        return False
    del graphs[slug]
    _write(graphs)
    return True


def _settle(slugs: list[str] | None, keep: bool) -> list[str]:
    graphs = _read(for_write=True)
    found = [slug for slug, entry in graphs.items()
             if _earlier(entry) and (slugs is None or slug in slugs)]
    for slug in found:
        if keep:
            graphs[slug]["launch"] = LAUNCH
        else:
            del graphs[slug]
    if found:
        _write(graphs)
    return found


def carry(slugs: list[str] | None = None) -> list[str]:
    """Keep for the next launch what an earlier launch left (only `slugs` of
    it, when given): stamped with this launch, its JSON unchanged. Returns the
    slugs carried."""
    return _settle(slugs, keep=True)


def drop_earlier() -> list[str]:
    """Drop what an earlier launch left; what this run recorded stays. Returns
    the slugs dropped."""
    return _settle(None, keep=False)


# ---------------------------------------------------------------- reading
def earlier() -> list[tuple[str, dict]]:
    """(slug, entry) for every graph an earlier launch left On, in the order
    they turned On: what this launch resumes. The entry holds `graph` and
    `saved` (an entry without one, from an older record, counts as saved)."""
    return [(slug, {**entry, "saved": entry.get("saved") is not False})
            for slug, entry in _read().items() if _earlier(entry)]


def recorded() -> list[tuple[str, dict]]:
    """(slug, graph JSON) for every graph recorded, in the order they turned On."""
    return [(slug, entry["graph"]) for slug, entry in _read().items()]


def slugs() -> list[str]:
    return list(_read())
