"""
boltjar.linked: workflow files a person opened or saved anywhere on this computer.

A workflow does not have to live in user/graphs/. Open... and Save as... in the
editor's file menu work on a file the person picks with the system's own
dialog (boltjar.dialogs), and the server links that file: it gets a slug, shows
in the Workflows list after a restart, opens from its path and saves back to
it. Nothing else about the file changes: it is never moved or copied, and
forgetting the link (Delete in the Workflows list) leaves it where it is.

The links live in user/data/linked_workflows.json, rewritten whole through a
temporary file (atomic) on every change:

    {"version": 1, "files": [{"slug": "flow", "path": "/home/ada/flow.json",
                              "added": "2026-09-27T09:00:00Z"}]}

One entry per file: a path is stored absolute and normalised, so the same file
opened twice is one entry with one slug. A link whose file is no longer at its
path is dropped as it is found, from the list and from the record, and the disk
is never touched otherwise. A file that is there but no longer reads as a
workflow stays linked: opening it says why it cannot open.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import re
import tempfile

from boltjar.graph_format import GraphFormatError, migrate

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Module-level so the test suite can point it at a tmp dir.
PATH = ROOT / "user" / "data" / "linked_workflows.json"
VERSION = 1

_log = logging.getLogger(__name__)


class NotAWorkflow(ValueError):
    """A file that does not read as a Boltjar workflow, with the reason."""


# ---------------------------------------------------------------- paths + slugs
def normalise(path: str | os.PathLike) -> str:
    """The absolute, normalised form a path is stored in."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _same(a: str, b: str) -> bool:
    """Whether two stored paths name one file (case-insensitively where the
    file system is, as on Windows)."""
    return os.path.normcase(a) == os.path.normcase(b)


def slug_for(name: str) -> str:
    """The slug a file name reads as: lower case, each run of spaces and dots a
    single dash, only letters, digits, `-` and `_` kept (what the server keeps
    in a graph's file name). "My Flow.v2" -> "my-flow-v2". The editor makes
    the same slug (workflowSlug in editor/src/lib/workflowFile.ts)."""
    s = re.sub(r"[\s.]+", "-", name.strip().lower())
    s = "".join(c for c in s if c.isalnum() or c in "-_")
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "untitled"


def free_slug(base: str, taken: set[str]) -> str:
    """`base`, else `base-2`, `base-3`...: the first slug not in `taken` (the
    way the editor numbers a new workflow)."""
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


# ---------------------------------------------------------------- files
def write_text_atomic(path: pathlib.Path, text: str) -> None:
    """Write `text` to `path` through a temporary file in the same folder and a
    replace, so a crash mid-write leaves the old file whole."""
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def read_workflow(path: pathlib.Path) -> dict:
    """The workflow in the file at `path`, in the current graph format. Raises
    NotAWorkflow with a plain reason when it is not one (not JSON, not a graph,
    from a newer Boltjar) and OSError when it cannot be read at all."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        raise NotAWorkflow("it is not JSON") from None
    return check_workflow(data)


def check_workflow(data: object) -> dict:
    """`data` as a workflow in the current graph format, or NotAWorkflow. A
    workflow is a JSON object with a `nodes` list (each node an object with a
    text `id` and `type`) and an `edges` list (each edge an object naming its
    `src`, `src_port`, `dst` and `dst_port`). The editor checks a file it
    reads in the browser the same way (parseWorkflowFile)."""
    if not isinstance(data, dict):
        raise NotAWorkflow("it is not a workflow (no nodes and edges)")
    nodes, edges = data.get("nodes"), data.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise NotAWorkflow("it is not a workflow (no nodes and edges)")
    for node in nodes:
        if not (isinstance(node, dict) and isinstance(node.get("id"), str)
                and isinstance(node.get("type"), str)):
            raise NotAWorkflow("a node has no id or type")
    for edge in edges:
        if not (isinstance(edge, dict)
                and all(isinstance(edge.get(k), str) for k in ("src", "src_port", "dst", "dst_port"))):
            raise NotAWorkflow("a wire does not name both of its ends")
    try:
        return migrate(data)
    except GraphFormatError as exc:
        raise NotAWorkflow(str(exc)) from None


# ---------------------------------------------------------------- the record
def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def _read(*, for_write: bool = False) -> list[dict]:
    """The links on record; [] when there is none yet. A record that cannot be
    read raises OSError, so nothing is ever written over it. One that is not a
    record reads as empty; before a write it is set aside as .bad."""
    try:
        text = PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        data = json.loads(text)
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, list):
            raise ValueError("it holds no files")
    except ValueError as exc:
        if for_write:
            bad = PATH.with_name(PATH.name + ".bad")
            os.replace(PATH, bad)
            _log.warning("%s could not be read (%s): set aside as %s", PATH.name, exc, bad.name)
        return []
    return [f for f in files
            if isinstance(f, dict) and isinstance(f.get("slug"), str) and f["slug"]
            and isinstance(f.get("path"), str) and f["path"]]


def _write(files: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(PATH, json.dumps({"version": VERSION, "files": files}, indent=2))


def entries() -> list[dict]:
    """Every link whose file is still at its path, oldest first. A link whose
    file is gone is dropped from the record on the way."""
    files = _read()
    present = [f for f in files if os.path.isfile(f["path"])]
    if len(present) != len(files):
        gone = [f["slug"] for f in files if f not in present]
        _write([f for f in _read(for_write=True) if os.path.isfile(f["path"])])
        _log.info("forgot linked workflows whose file is gone: %s", ", ".join(gone))
    return present


def path_of(slug: str) -> pathlib.Path | None:
    """The file `slug` is linked to, None when it is no link (or its file is gone)."""
    for f in entries():
        if f["slug"] == slug:
            return pathlib.Path(f["path"])
    return None


def slug_of(path: str | os.PathLike) -> str | None:
    """The slug the file at `path` is linked as, None when it is not linked."""
    target = normalise(path)
    for f in entries():
        if _same(f["path"], target):
            return f["slug"]
    return None


def plan(path: str | os.PathLike, taken: set[str]) -> str:
    """The slug the file at `path` has or would get, writing nothing: its own
    when it is linked, else one made from its file name and numbered past
    `taken` (the saved graphs and examples) and every other link."""
    existing = slug_of(path)
    if existing is not None:
        return existing
    used = set(taken) | {f["slug"] for f in _read()}
    return free_slug(slug_for(pathlib.Path(normalise(path)).stem), used)


def link(path: str | os.PathLike, slug: str) -> None:
    """Record the file at `path` as `slug` (a slug from plan). A file already
    linked keeps its one entry."""
    target = normalise(path)
    files = _read(for_write=True)
    if any(_same(f["path"], target) for f in files):
        return
    files.append({"slug": slug, "path": target, "added": _now()})
    _write(files)


def forget(slug: str) -> bool:
    """Drop the link `slug`; the file itself stays where it is. False when
    there was no such link."""
    files = _read(for_write=True)
    kept = [f for f in files if f["slug"] != slug]
    if len(kept) == len(files):
        return False
    _write(kept)
    return True
