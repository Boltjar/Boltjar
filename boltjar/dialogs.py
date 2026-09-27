"""
boltjar.dialogs: the system's own Open and Save as dialogs, shown by the server.

A page in a browser never learns where a file it reads lives on disk, so the
editor asks the server, which runs on the same computer, to show the dialog.
Each dialog runs in a short-lived child Python with tkinter (the standard
library's toolkit), never inside the server process: Tk wants its own main
thread (on macOS a Tk window off the main thread ends the whole process), and a
child that fails takes nothing with it. The server waits for the child in a
worker thread, so the event loop keeps serving while the dialog is open.

One dialog at a time: a second request while one is open is DialogBusy. When no
dialog can be shown (tkinter is not installed with this Python, no display on a
headless machine) the request raises DialogUnavailable with the reason, and the
editor falls back to what a browser can do on its own.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading

# The child: prints one JSON line, {"path": "..."} (null when cancelled) or
# {"unavailable": "<reason>"}. argv: open|save, the suggested file name, the
# folder to start in ("" for the dialog's own choice).
_CHILD = r"""
import json, sys
def say(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()
try:
    import tkinter
    from tkinter import filedialog
except Exception:
    say(unavailable="tkinter is not installed with this Python")
    raise SystemExit(0)
try:
    root = tkinter.Tk()
except Exception as exc:
    say(unavailable="there is no display to show a file dialog on (%s)" % (str(exc) or type(exc).__name__))
    raise SystemExit(0)
root.withdraw()
root.attributes("-topmost", True)  # in front of the browser that asked for it
root.update()
kind, name, folder = sys.argv[1], sys.argv[2], sys.argv[3]
types = [("Boltjar workflow", "*.json"), ("All files", "*.*")]
opts = {"parent": root, "filetypes": types}
if folder:
    opts["initialdir"] = folder
if kind == "open":
    path = filedialog.askopenfilename(title="Open workflow", **opts)
else:
    path = filedialog.asksaveasfilename(title="Save workflow as", initialfile=name,
                                        defaultextension=".json", confirmoverwrite=True, **opts)
root.destroy()
say(path=path or None)
"""

_lock = threading.Lock()


class DialogUnavailable(RuntimeError):
    """No file dialog can be shown on this computer; the message says why."""


class DialogBusy(RuntimeError):
    """A file dialog is already open."""


def _run_child(args: list[str]) -> subprocess.CompletedProcess:
    """Run the dialog child and wait for it. The test suite replaces this: no
    test ever shows a real dialog."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return subprocess.run([sys.executable, "-c", _CHILD, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", creationflags=flags)


def parse(done: subprocess.CompletedProcess) -> str | None:
    """The path the child reported, None when the person cancelled. Raises
    DialogUnavailable when it could show no dialog or did not finish."""
    lines = [ln for ln in (done.stdout or "").splitlines() if ln.strip()]
    try:
        answer = json.loads(lines[-1]) if lines else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        err = [ln for ln in (done.stderr or "").splitlines() if ln.strip()]
        detail = err[-1].strip() if err else f"it exited with code {done.returncode}"
        raise DialogUnavailable(f"the file dialog did not open: {detail}")
    if isinstance(answer.get("unavailable"), str):
        raise DialogUnavailable(answer["unavailable"])
    path = answer.get("path")
    return path if isinstance(path, str) and path else None


def _ask(kind: str, name: str = "", folder: str = "") -> str | None:
    if not _lock.acquire(blocking=False):
        raise DialogBusy("a file dialog is already open")
    try:
        try:
            done = _run_child([kind, name, folder])
        except OSError as exc:
            raise DialogUnavailable(f"the file dialog did not open: {exc}") from None
    finally:
        _lock.release()
    return parse(done)


def ask_open() -> str | None:
    """Show the Open dialog (workflow .json files); the chosen path, or None."""
    return _ask("open")


def ask_save(name: str, folder: str = "") -> str | None:
    """Show the Save as dialog suggesting `name`, starting in `folder` when
    given; the chosen path, or None. The dialog asks before replacing a file."""
    return _ask("save", name, folder)
