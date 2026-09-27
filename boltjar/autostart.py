"""
boltjar.autostart: "Launch with system", Boltjar starting when the person logs in.

One file in the current account's own startup place, never a system service,
never the registry, never administrator rights:

  - Windows: Boltjar.cmd in the Startup folder (%APPDATA%\\Microsoft\\Windows\\
    Start Menu\\Programs\\Startup). It runs start.bat --no-browser in a
    minimized console window.
  - macOS: ~/Library/LaunchAgents/link.boltjar.plist (RunAtLoad), running
    start.sh --no-browser, its output in user/logs/boltjar.log.
  - Linux and other XDG desktops: ~/.config/autostart/boltjar.desktop
    ($XDG_CONFIG_HOME/autostart when that is set), running start.sh
    --no-browser, its output in user/logs/boltjar.log.

Whether it is on is read from disk each time: the entry exists and starts THIS
install. Turning it off deletes exactly that one file, and only when it starts
this install; an entry another Boltjar install wrote is left alone.
"""
from __future__ import annotations

import ntpath
import os
import pathlib
import plistlib
import posixpath
import re
import sys
import tempfile
from typing import Mapping

from boltjar.settings import shown_path

ROOT = pathlib.Path(__file__).resolve().parent.parent

WINDOWS, MACOS, XDG = "windows", "macos", "xdg"
FILE_NAMES = {WINDOWS: "Boltjar.cmd", MACOS: "link.boltjar.plist", XDG: "boltjar.desktop"}
LABEL = "link.boltjar"
# launchd starts a job with a bare PATH; Homebrew's folders are added so the
# start script finds Node.js (and a Python) where most Macs have them.
MACOS_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _account() -> tuple[str, Mapping[str, str], pathlib.Path]:
    """(platform, environment, home folder) of the account Boltjar runs as.
    The test suite replaces this, so no test ever writes into a real account."""
    return sys.platform, os.environ, pathlib.Path.home()


def family(platform: str) -> str:
    if platform == "win32":
        return WINDOWS
    if platform == "darwin":
        return MACOS
    return XDG


def entry_path(platform: str, env: Mapping[str, str], home: pathlib.Path) -> pathlib.Path:
    """Where the entry lives for this account."""
    kind = family(platform)
    if kind == WINDOWS:
        appdata = env.get("APPDATA")
        base = pathlib.Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / FILE_NAMES[kind]
    if kind == MACOS:
        return home / "Library" / "LaunchAgents" / FILE_NAMES[kind]
    config = env.get("XDG_CONFIG_HOME") or ""
    base = pathlib.Path(config) if os.path.isabs(config) else home / ".config"
    return base / "autostart" / FILE_NAMES[kind]


# ---------------------------------------------------------------- Windows
def _cmd_quote(path: str) -> str:
    """A path in double quotes on a .cmd line. Inside the quotes a space, &, (,
    ), ^ and < > are plain text; only % still expands, so it is doubled. No
    Windows path can hold a double quote."""
    return '"' + path.replace("%", "%%") + '"'


def windows_lines(root: pathlib.PurePath) -> list[str]:
    """The .cmd, a line each. `cd` first, then `start` runs .\\start.bat by its
    plain relative name: a quoted path handed to `start` for a batch file goes
    through cmd's own quote stripping, which breaks on & and parentheses."""
    return [
        "@echo off",
        "rem Starts Boltjar when you log in: Settings, General, Launch with system.",
        "rem Turning that off deletes this file.",
        "setlocal DisableDelayedExpansion",
        "rem UTF-8, so the folder name below reads right in any language.",
        "chcp 65001 >nul",
        f"cd /d {_cmd_quote(str(root))} || exit /b 1",
        'start "Boltjar" /min cmd /c .\\start.bat --no-browser',
    ]


_CMD_CD = re.compile(r'^cd /d "([^"]*)"', re.M)


def _windows_target(text: str) -> str | None:
    m = _CMD_CD.search(text)
    return m.group(1).replace("%%", "%") if m else None


# ---------------------------------------------------------------- macOS
def macos_plist(root: pathlib.PurePath) -> bytes:
    log = str(root / "user" / "logs" / "boltjar.log")
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(root / "start.sh"), "--no-browser"],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PATH": MACOS_PATH},
    })


def _macos_target(data: bytes) -> str | None:
    try:
        entry = plistlib.loads(data)
    except Exception:
        return None
    target = entry.get("WorkingDirectory") if isinstance(entry, dict) else None
    return target if isinstance(target, str) else None


# ---------------------------------------------------------------- XDG desktops
_VALUE_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r"}
_VALUE_UNESCAPES = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}


def _desktop_value(text: str) -> str:
    """A string value in a .desktop file (the spec's \\\\ \\n \\t \\r escapes)."""
    return "".join(_VALUE_ESCAPES.get(c, c) for c in text)


def _desktop_unvalue(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(_VALUE_UNESCAPES.get(text[i + 1], text[i:i + 2]))
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _exec_quote(arg: str) -> str:
    """One Exec argument: in double quotes, with " ` $ and \\ escaped by a
    backslash, and % doubled (a lone % starts a field code)."""
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg).replace("%", "%%") + '"'


# What the desktop runs at login: start.sh, its output appended to the log (a
# desktop session has no terminal to show it). Both paths reach sh as $0 and
# $1, never inside the script, so no folder name can change what runs.
_XDG_SCRIPT = 'exec /bin/sh "$0" --no-browser >>"$1" 2>&1'


def desktop_entry(root: pathlib.PurePath) -> str:
    start, log = str(root / "start.sh"), str(root / "user" / "logs" / "boltjar.log")
    command = f"/bin/sh -c {_exec_quote(_XDG_SCRIPT)} {_exec_quote(start)} {_exec_quote(log)}"
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Boltjar",
        "Comment=Starts Boltjar when you log in (Settings, General, Launch with system)",
        # the string escapes apply on top of the quoting (the spec reads them first)
        f"Exec={_desktop_value(command)}",
        f"Path={_desktop_value(str(root))}",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
    ]) + "\n"


def _xdg_target(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("Path="):
            return _desktop_unvalue(line[len("Path="):])
    return None


# ---------------------------------------------------------------- one file
def entry_bytes(platform: str, root: pathlib.PurePath) -> bytes:
    """The entry that starts the install at `root`, as written to disk. The
    .cmd is UTF-8 with no byte order mark (one would break its first line)."""
    kind = family(platform)
    if kind == WINDOWS:
        return ("\r\n".join(windows_lines(root)) + "\r\n").encode("utf-8")
    if kind == MACOS:
        return macos_plist(root)
    return desktop_entry(root).encode("utf-8")


def target_of(platform: str, data: bytes) -> str | None:
    """The install folder an entry starts, or None when it names none."""
    kind = family(platform)
    if kind == MACOS:
        return _macos_target(data)
    text = data.decode("utf-8", errors="replace")
    return _windows_target(text) if kind == WINDOWS else _xdg_target(text)


def _same(a: str, b: str, platform: str) -> bool:
    """The same folder, by the rules of `platform` (Windows ignores case)."""
    if family(platform) == WINDOWS:
        return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))
    return posixpath.normpath(a) == posixpath.normpath(b)


def _read(path: pathlib.Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def status(root: pathlib.Path = ROOT) -> dict:
    """{enabled, where, other, system}: `enabled` when the entry exists and
    starts this install; `where` is the entry's path (~ for the home folder);
    `other` the install another entry at that path starts, else None."""
    platform, env, home = _account()
    path = entry_path(platform, env, home)
    data = _read(path)
    target = target_of(platform, data) if data is not None else None
    enabled = target is not None and _same(target, str(root), platform)
    other = shown_path(target, home) if target is not None and not enabled else None
    return {"enabled": enabled, "where": shown_path(path, home), "other": other,
            "system": family(platform)}


def enable(root: pathlib.Path = ROOT) -> dict:
    """Write the entry for this install (replacing one another install wrote
    at the same path: an account starts one Boltjar at login). Returns status()."""
    platform, env, home = _account()
    path = entry_path(platform, env, home)
    if family(platform) in (MACOS, XDG):
        # neither launchd nor a shell redirect creates the log's folder
        (root / "user" / "logs").mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(entry_bytes(platform, root))
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise
    return status(root)


def disable(root: pathlib.Path = ROOT) -> dict:
    """Delete the entry, only when it starts this install. Returns status()."""
    platform, env, home = _account()
    path = entry_path(platform, env, home)
    data = _read(path)
    target = target_of(platform, data) if data is not None else None
    if target is not None and _same(target, str(root), platform):
        path.unlink(missing_ok=True)
    return status(root)
