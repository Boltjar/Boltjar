"""
boltjar.settings: the install's own settings, the ones the editor's Settings
panel keeps (its General tab).

They live in user/data/settings.json, a flat {name: value} JSON written whole
through a temporary file (atomic), so a crash mid-write leaves the last good
copy. Only the names in DEFAULTS are settings: a name the file holds that is
not one of them is kept on disk untouched (a newer Boltjar's) and never served.
Every setting is off until a person turns it on, so a fresh install never
starts a graph or a program on its own.

"Launch with system" is not stored here: whether Boltjar starts at login is a
fact about the account (an entry in its startup folder), read from disk each
time by boltjar.autostart.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_log = logging.getLogger(__name__)
# Module-level so the test suite can point it at a tmp dir.
PATH = ROOT / "user" / "data" / "settings.json"

# name -> default. A new setting is one more entry here (its type is its
# default's type) plus its row in the editor's General tab.
DEFAULTS: dict[str, bool] = {
    # spawn `ollama serve` when Boltjar starts and Ollama is installed but stopped
    "start_ollama": False,
    # power back On, after a launch, the graphs that were On when Boltjar stopped
    "resume_workflows": False,
}


def _read(*, for_write: bool = False) -> dict:
    """The saved settings; {} when there is no file yet. A file that cannot be
    read (a sharing violation, no permission) raises OSError, so nothing is
    ever written over what it holds. One that is not a JSON object reads as
    empty; before a write it is set aside as settings.json.bad, never
    overwritten."""
    try:
        text = PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        if for_write:
            bad = PATH.with_name(PATH.name + ".bad")
            os.replace(PATH, bad)
            _log.warning("%s could not be read (%s): set aside as %s", PATH.name, exc, bad.name)
        return {}
    return data


def _write(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=PATH.name + ".", suffix=".tmp", dir=PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2))
        os.replace(tmp, PATH)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def shown_path(path: pathlib.Path | str, home: pathlib.Path | None = None) -> str:
    """A path as the Settings panel shows it: one under the home folder starts
    with ~, so the account name is never on the screen (or in a screenshot)."""
    path = pathlib.Path(path)
    home = pathlib.Path.home() if home is None else pathlib.Path(home)
    try:
        rest = path.relative_to(home)
    except ValueError:
        return str(path)
    return str(pathlib.PurePath("~", rest)) if rest.parts else "~"


def check(changes: dict) -> None:
    """Raise ValueError for a name that is not a setting or a value of the
    wrong type (bool settings take true or false, never 1 or "yes")."""
    for name, value in changes.items():
        if name not in DEFAULTS:
            raise ValueError(f"{name!r} is not a setting")
        if not isinstance(value, type(DEFAULTS[name])):
            raise ValueError(f"{name} takes {type(DEFAULTS[name]).__name__}, "
                             f"not {type(value).__name__}")


def load() -> dict[str, bool]:
    """Every setting: the saved value when the file holds one of the right
    type, else its default. Raises OSError when the file cannot be read."""
    saved = _read()
    out = {}
    for name, default in DEFAULTS.items():
        value = saved.get(name)
        out[name] = value if isinstance(value, type(default)) else default
    return out


def get(name: str) -> bool:
    return load()[name]


def update(changes: dict) -> dict[str, bool]:
    """Save `changes` ({name: value}) and return every setting. Raises
    ValueError, with nothing written, for a name that is not a setting or a
    value of the wrong type (see `check`), and OSError, with nothing written,
    when the file cannot be read or written."""
    check(changes)
    data = _read(for_write=True)
    data.update(changes)
    _write(data)
    return load()
