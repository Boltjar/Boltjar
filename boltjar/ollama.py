"""
boltjar.ollama: whether Ollama is on this computer, whether it runs, and
starting it.

Ollama is a separate program. Boltjar finds it (on PATH, then where its
installer puts it: %LOCALAPPDATA%\\Programs\\Ollama\\ollama.exe on Windows,
/Applications/Ollama.app/Contents/Resources/ollama on macOS; Linux installs it
on PATH) and reports one of four states:

  - running:        it answers on its base URL (OLLAMA_BASE_URL, the address
                    model discovery asks), wherever it was started from
  - unreachable:    OLLAMA_BASE_URL names another machine and nothing answers
                    there; what is installed here does not matter then
  - stopped:        it is installed here but does not answer
  - not_installed:  neither

A stopped Ollama can be started from here: `ollama serve`, detached from the
terminal (no console window on Windows), its output appended to
user/logs/ollama.log. Boltjar stops, when it exits, only the Ollama this very
process started; one that was already running, or that something else started,
is left alone.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import pathlib
import shutil
import signal
import subprocess
import sys
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from boltjar import model_discovery as _discovery
from boltjar.settings import shown_path

_log = logging.getLogger("boltjar.ollama")

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Module-level so the test suite can point it at a tmp dir.
LOG_PATH = ROOT / "user" / "logs" / "ollama.log"
DOWNLOAD_URL = "https://ollama.com/download"

RUNNING, STOPPED, NOT_INSTALLED = "running", "stopped", "not_installed"
UNREACHABLE = "unreachable"

# How long a start waits for Ollama to answer, and how often it asks.
START_WAIT = 20.0  # seconds
POLL_EVERY = 0.5  # seconds
# How long an exit waits for the Ollama it started to end before killing it.
STOP_GRACE = 3.0  # seconds

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
# Windows process creation flags (subprocess names them on Windows only).
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

# The `ollama serve` this process started (None: it started none, or it ended).
_started: subprocess.Popen | None = None
# (loop, lock): one start at a time on the server's loop.
_start_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def _lock() -> asyncio.Lock:
    global _start_lock
    loop = asyncio.get_running_loop()
    if _start_lock is None or _start_lock[0] is not loop:
        _start_lock = (loop, asyncio.Lock())
    return _start_lock[1]


# ---------------------------------------------------------------- where it is
def default_paths(platform: str | None = None, env: Mapping[str, str] | None = None) -> list[pathlib.Path]:
    """Where Ollama's own installer puts the program on `platform`."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if platform == "win32":
        base = env.get("LOCALAPPDATA")
        return [pathlib.Path(base) / "Programs" / "Ollama" / "ollama.exe"] if base else []
    if platform == "darwin":
        return [pathlib.Path("/Applications/Ollama.app/Contents/Resources/ollama")]
    return []  # Linux: the install script puts it on PATH


def find(platform: str | None = None, env: Mapping[str, str] | None = None,
         which: Callable[..., str | None] = shutil.which) -> pathlib.Path | None:
    """The Ollama program: the one on PATH, else the installer's default place."""
    env = os.environ if env is None else env
    found = which("ollama", path=env.get("PATH"))
    if found:
        return pathlib.Path(found)
    return next((p for p in default_paths(platform, env) if p.is_file()), None)


def base_url() -> str:
    return _discovery.ollama_base()


def is_local() -> bool:
    """The base URL is this computer, so an Ollama started here answers on it."""
    host = (urlsplit(base_url()).hostname or "").lower()
    return host in _LOOPBACK or host.endswith(".localhost")


# ---------------------------------------------------------------- its state
def started_here() -> bool:
    """This Boltjar started the Ollama that runs now."""
    return _started is not None and _started.poll() is None


def _elsewhere() -> str:
    return f"not answering at {base_url()} (OLLAMA_BASE_URL): start Ollama on that machine"


def status(running: bool) -> dict:
    """The state the Ollama card shows (Settings, AI Providers). `running`: it
    answers on its base URL now (the caller has just asked). Paths are shown
    with ~ for the home folder."""
    exe = find()
    if running:
        state = RUNNING
    elif not is_local():
        state = UNREACHABLE
    else:
        state = STOPPED if exe is not None else NOT_INSTALLED
    return {
        "state": state,
        "path": shown_path(exe) if exe is not None else None,
        "startable": state == STOPPED,
        "reason": _elsewhere() if state == UNREACHABLE else None,
        "started_here": started_here(),
        "log": _repo_path(LOG_PATH),
        "download": DOWNLOAD_URL,
    }


def _repo_path(path: pathlib.Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


async def answers(timeout: float = 1.0) -> bool:
    """Ollama answers on its base URL now."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url()}/api/version")
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------- start + stop
def _serve_host() -> str | None:
    """OLLAMA_HOST for `ollama serve` when the base URL is not Ollama's default
    address (so the started Ollama answers where Boltjar asks); None keeps it."""
    parts = urlsplit(base_url())
    host, port = parts.hostname or "", parts.port or 11434
    if (host, port) in (("localhost", 11434), ("127.0.0.1", 11434)):
        return None
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


# The process launcher, a seam the test suite replaces (no test ever runs Ollama).
_popen = subprocess.Popen


def spawn(exe: pathlib.Path) -> subprocess.Popen:
    """`ollama serve`, detached: no console window on Windows, its own session
    elsewhere (a Ctrl+C in Boltjar's terminal never reaches it), stdin closed,
    stdout and stderr appended to user/logs/ollama.log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    host = _serve_host()
    if host:
        env["OLLAMA_HOST"] = host
    options: dict = {"stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT, "env": env}
    if os.name == "nt":
        options["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(LOG_PATH, "ab") as log:
        log.write(f"\n--- ollama serve, started by Boltjar at {stamp.replace('+00:00', 'Z')} ---\n"
                  .encode("utf-8"))
        log.flush()
        return _popen([str(exe), "serve"], stdout=log, **options)


async def _wait_until_answers(proc: subprocess.Popen | None, wait: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while True:
        if await answers():
            return True
        if proc is not None and proc.poll() is not None:
            return False  # it ended: its reason is in the log
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(POLL_EVERY)


async def start(wait: float = START_WAIT) -> dict:
    """Start Ollama when it is installed here and does not answer, then wait
    (up to `wait` seconds) until it does. Returns {ok, state, started, error}:
    `started` is true when this call started it. Two starts at once start one."""
    global _started
    if await answers():
        return {"ok": True, "state": RUNNING, "started": False, "error": None}
    if not is_local():
        return {"ok": False, "state": UNREACHABLE, "started": False, "error": _elsewhere()}
    exe = find()
    if exe is None:
        return {"ok": False, "state": NOT_INSTALLED, "started": False,
                "error": f"Ollama is not installed: get it at {DOWNLOAD_URL}"}
    async with _lock():
        started = False
        if not started_here():
            try:
                _started = spawn(exe)
            except OSError as exc:
                return {"ok": False, "state": STOPPED, "started": False,
                        "error": f"could not start {exe.name}: {exc}"}
            started = True
        proc = _started
    if await _wait_until_answers(proc, wait):
        return {"ok": True, "state": RUNNING, "started": started, "error": None}
    log = _repo_path(LOG_PATH)
    if proc is not None and proc.poll() is not None:
        _started = None
        error = f"Ollama stopped right after it started (exit code {proc.returncode}): see {log}"
    else:
        error = f"Ollama did not answer within {wait:g} s: see {log}"
    return {"ok": False, "state": STOPPED, "started": started, "error": error}


def _end_process_tree(proc: subprocess.Popen, grace: float) -> None:
    """End `proc` and the model runners it started: its whole process tree on
    Windows (taskkill /T), its session's process group elsewhere (SIGTERM, then
    SIGKILL after `grace` seconds)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=_CREATE_NO_WINDOW, timeout=grace + 5)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=grace)


# The tree ender, a seam the test suite replaces (no test ever ends a real process).
_end_tree = _end_process_tree


def _take_started() -> subprocess.Popen | None:
    """The Ollama this process started, if it still runs, now no longer ours
    to stop again (so two ways out never end it twice)."""
    global _started
    proc, _started = _started, None
    return proc if proc is not None and proc.poll() is None else None


def _end(proc: subprocess.Popen, grace: float) -> bool:
    try:
        _end_tree(proc, grace)
    except Exception:
        _log.warning("could not stop the Ollama Boltjar started (pid %s)", proc.pid, exc_info=True)
        return False
    _log.info("stopped the Ollama Boltjar started", extra={"tone": "ok"})
    return True


async def stop_if_started(grace: float = STOP_GRACE) -> bool:
    """Stop the Ollama this process started, if it still runs. Returns whether
    one was stopped. An Ollama started anywhere else is never touched."""
    proc = _take_started()
    return proc is not None and await asyncio.to_thread(_end, proc, grace)


def stop_started_now(grace: float = STOP_GRACE) -> bool:
    """stop_if_started with no event loop: `python -m boltjar serve` calls it
    once the server has returned, on every way out, so an exit that stopped
    waiting on the graphs (one ran late, or a second Ctrl+C) still ends the
    Ollama it started. A no-op when the exit already did."""
    proc = _take_started()
    return proc is not None and _end(proc, grace)


async def launch_start() -> tuple[str, str]:
    """The launch step for "Start Ollama with Boltjar": start it when it is
    installed and stopped. Returns the console line (tone, text) either way."""
    result = await start()
    if result["ok"] and not result["started"]:
        return "info", f"Ollama is already running at {base_url()}"
    if result["ok"]:
        return "ok", f"started Ollama at {base_url()} (Boltjar stops it when it exits)"
    if result["state"] == NOT_INSTALLED:
        return "warn", f"Start Ollama with Boltjar is on, but Ollama is not installed: {DOWNLOAD_URL}"
    return "warn", f"Ollama did not start: {result['error']}"
