"""
boltjar.console: the terminal a person watches while Boltjar runs.

Stdlib only: the start scripts import it before any requirement is known to be
installed. It detects what the terminal can do (a TTY, NO_COLOR, TERM=dumb,
COLORTERM, Windows VT processing, the output encoding, the width) and draws to
match: truecolor, 256, 16 or no colours, unicode glyphs or an ASCII set. It owns
the banner, the boot checklist, the ready line, one log line format for
Boltjar's loggers and uvicorn's, and the live graph lines read off the runtime's
event stream.

Colours are semantic roles taken from the editor tokens (editor/src/styles/
tokens.css), so the terminal and the editor console agree on what green means.
Emphasis is bold in the terminal's own foreground, never a hard-coded white
that vanishes on a light theme.
"""
from __future__ import annotations

import contextlib
import datetime
import logging
import os
import pathlib
import re
import shutil
import sys
import textwrap
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, TextIO

from boltjar.media import summarize

# ---------------------------------------------------------------- palette
# RGB per role, from the editor tokens (--accent, --good, --warn, --bad, --info).
# `brand` (the logo cyan) is for the banner mark only.
ROLES: dict[str, tuple[int, int, int]] = {
    "accent": (0x6E, 0xC6, 0xC0),
    "good": (0x4A, 0xDE, 0x80),
    "warn": (0xEA, 0xB3, 0x08),
    "bad": (0xEF, 0x44, 0x44),
    "info": (0x60, 0xA5, 0xFA),
    "brand": (0x00, 0xE5, 0xFF),
}
# A 16-colour terminal gets the ANSI colour that MEANS the same thing, not the
# nearest RGB (the nearest to the calm teal accent would be a grey).
ROLES_16: dict[str, int] = {
    "accent": 36, "good": 32, "warn": 33, "bad": 31, "info": 94, "brand": 96,
}

# A line's tone (the editor console's levels) and the role that colours it.
TONES: dict[str, str | None] = {"ok": "good", "info": "info", "warn": "warn", "bad": "bad", "debug": None}

GLYPHS: dict[bool, dict[str, str]] = {
    True: {"ok": "✓", "info": "•", "warn": "▲", "bad": "✗", "debug": "·", "bar": "▌", "sep": "·"},
    False: {"ok": "+", "info": "-", "warn": "!", "bad": "x", "debug": ".", "bar": "|", "sep": "-"},
}
SPINNER: dict[bool, str] = {True: "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", False: "|/-\\"}

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\]8;;[^\x1b]*\x1b\\")

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Serialises every write to the screen: the spinner thread, the checklist and
# the log handler. A log record that lands mid-spinner clears the spinner line
# first, so the two never print over each other.
_SCREEN = threading.RLock()
_live_spinner: "_Spinner | None" = None


# ---------------------------------------------------------------- capabilities
@dataclass(frozen=True)
class Caps:
    """What one output stream can show."""
    tty: bool = False
    vt: bool = False          # cursor control and SGR sequences are understood
    color: str = "none"       # "truecolor" | "256" | "16" | "none"
    unicode: bool = False     # the glyph set encodes AND the terminal font draws it
    links: bool = False       # OSC 8 hyperlinks


def detect(stream: TextIO | None = None, env: Mapping[str, str] | None = None,
           platform: str | None = None,
           vt_probe: Callable[[TextIO], bool] | None = None) -> Caps:
    """Read `stream`'s capabilities from the environment. On Windows this also
    turns VT processing on for the console (`vt_probe`, injectable for tests)."""
    stream = sys.stdout if stream is None else stream
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    tty = _isatty(stream)
    if not tty or env.get("TERM", "").lower() == "dumb":
        vt = False
    elif platform == "win32":
        vt = (vt_probe or enable_windows_vt)(stream)
    else:
        vt = True
    return Caps(
        tty=tty,
        vt=vt,
        color=color_tier(vt, env, platform),
        unicode=unicode_ok(stream, env, platform),
        links=vt and hyperlinks_ok(env),
    )


def color_tier(vt: bool, env: Mapping[str, str], platform: str) -> str:
    """truecolor, 256, 16 or none. NO_COLOR (any non-empty value, no-color.org)
    wins over everything."""
    if not vt or env.get("NO_COLOR"):
        return "none"
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return "truecolor"
    if platform == "win32":
        # a console with VT processing on (Windows 10 1703+) and Windows Terminal
        # both draw 24-bit colour, and neither sets COLORTERM.
        return "truecolor"
    return "256" if "256" in env.get("TERM", "") else "16"


def unicode_ok(stream: TextIO, env: Mapping[str, str], platform: str) -> bool:
    """Whether the unicode glyph set can be printed AND drawn. The encoding must
    hold it; on Windows the classic console host draws with fonts that lack the
    check mark and braille, so unicode waits for a terminal that identifies
    itself (Windows Terminal, VS Code, ConEmu, mintty)."""
    probe = "".join(GLYPHS[True].values()) + SPINNER[True]
    try:
        probe.encode(getattr(stream, "encoding", None) or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    if platform == "win32":
        return bool(env.get("WT_SESSION") or env.get("TERM_PROGRAM") or env.get("TERM")
                    or env.get("ConEmuANSI") == "ON")
    return True


def hyperlinks_ok(env: Mapping[str, str]) -> bool:
    """OSC 8 links only where the terminal is known to render them; elsewhere
    the plain URL prints (and most terminals link it on their own)."""
    if env.get("WT_SESSION") or env.get("TERM") == "xterm-kitty":
        return True
    if env.get("TERM_PROGRAM") in ("vscode", "iTerm.app", "WezTerm"):
        return True
    vte = env.get("VTE_VERSION", "")
    return vte.isdigit() and int(vte) >= 5000


def enable_windows_vt(stream: TextIO) -> bool:
    """Turn on ENABLE_VIRTUAL_TERMINAL_PROCESSING for a Windows console stream.
    False when the stream is not a console or the console is too old for it."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        vt_flag = 0x0004
        if mode.value & vt_flag:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | vt_flag))
    except Exception:
        return False


def prepare_streams() -> None:
    """Make stdout and stderr (where the logs go) safe before anything prints:
    a character the encoding lacks is replaced, never a crash; a Windows console
    is written as UTF-8 with VT processing on, so colour sequences render."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        windows_tty = sys.platform == "win32" and _isatty(stream)
        try:
            if windows_tty:
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
        if windows_tty:
            enable_windows_vt(stream)


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def terminal_width(stream: TextIO | None = None) -> int:
    """Columns of the terminal behind `stream` (80 when it has none), at least 40."""
    stream = sys.stdout if stream is None else stream
    try:
        columns = os.get_terminal_size(stream.fileno()).columns
    except (AttributeError, ValueError, OSError):
        columns = shutil.get_terminal_size((80, 24)).columns
    return max(40, columns)


def visible_len(text: str) -> int:
    """Printed width of `text` (escape sequences take no columns)."""
    return len(_ANSI_RE.sub("", text))


def rgb_to_256(r: int, g: int, b: int) -> int:
    """The nearest xterm-256 index: the 6x6x6 cube or the 24-step grey ramp."""
    def level(v: int) -> int:
        return 0 if v < 48 else 1 if v < 115 else (v - 35) // 40

    steps = (0, 95, 135, 175, 215, 255)
    cube = (steps[level(r)], steps[level(g)], steps[level(b)])
    grey = min(23, max(0, round(((r + g + b) / 3 - 8) / 10)))
    grey_rgb = (8 + 10 * grey,) * 3

    def distance(c: tuple[int, ...]) -> int:
        return sum((x - y) ** 2 for x, y in zip(c, (r, g, b)))

    if distance(cube) <= distance(grey_rgb):
        return 16 + 36 * level(r) + 6 * level(g) + level(b)
    return 232 + grey


def local_zone() -> str:
    """The machine's current UTC offset as `UTC+10:00`, the zone the log times use."""
    offset = datetime.datetime.now().astimezone().strftime("%z") or "+0000"
    return f"UTC{offset[:3]}:{offset[3:]}"


# ---------------------------------------------------------------- painting
class Style:
    """Paints text for one stream's capabilities. With no colour it returns the
    text untouched, so callers write the same code for every terminal."""

    def __init__(self, caps: Caps) -> None:
        self.caps = caps
        self.glyphs = GLYPHS[caps.unicode]

    def __call__(self, text: str, role: str | None = None, *, bold: bool = False,
                 dim: bool = False, underline: bool = False) -> str:
        if self.caps.color == "none" or not text:
            return text
        codes = [c for c, on in (("1", bold), ("2", dim), ("4", underline)) if on]
        if role:
            codes.append(self._fg(role))
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if codes else text

    def _fg(self, role: str) -> str:
        if self.caps.color == "truecolor":
            return "38;2;{};{};{}".format(*ROLES[role])
        if self.caps.color == "256":
            return f"38;5;{rgb_to_256(*ROLES[role])}"
        return str(ROLES_16[role])

    def glyph(self, tone: str) -> str:
        """The coloured level glyph for a tone (ok, info, warn, bad, debug)."""
        role = TONES.get(tone)
        return self(self.glyphs.get(tone, self.glyphs["info"]), role, dim=role is None)

    def link(self, url: str, text: str | None = None) -> str:
        """`text` as an OSC 8 hyperlink to `url` where the terminal supports it."""
        text = url if text is None else text
        if not self.caps.links:
            return text
        return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


# ---------------------------------------------------------------- boot screen
class Console:
    """The boot sequence's writer: banner, checklist, ready line, goodbye."""

    LABEL_WIDTH = 12  # "requirements"

    def __init__(self, stream: TextIO | None = None, caps: Caps | None = None) -> None:
        self.stream = sys.stdout if stream is None else stream
        self.caps = detect(self.stream) if caps is None else caps
        self.style = Style(self.caps)

    def write(self, line: str = "") -> None:
        with _SCREEN:
            self.stream.write(line + "\n")
            self.stream.flush()

    def banner(self, version: str, tagline: str) -> None:
        s = self.style
        bar = s(s.glyphs["bar"], "brand")
        self.write()
        self.write(f"  {bar} {s('Boltjar', bold=True)} {s(version, dim=True)}")
        self.write(f"  {bar} {s(tagline, dim=True)}")
        self.write()

    def check(self, label: str, detail: str = "", tone: str = "ok", fix: str | None = None) -> None:
        """One checklist line: glyph, label, detail. A problem can carry a dim
        `fix:` line under it saying what to do. Text wraps at the window's
        width and continues under the detail column."""
        for line in self._check_lines(label, detail, tone, fix):
            self.write(line)

    def _check_lines(self, label: str, detail: str, tone: str, fix: str | None = None,
                     glyph: str | None = None) -> list[str]:
        s = self.style
        indent = " " * (6 + self.LABEL_WIDTH + 1)
        room = max(20, terminal_width(self.stream) - len(indent))
        quiet = tone in ("ok", "info")
        parts = textwrap.wrap(detail, room) or [""]
        mark = glyph if glyph is not None else s.glyph(tone)
        lines = [f"    {mark} {label.ljust(self.LABEL_WIDTH)} {s(parts[0], dim=quiet)}".rstrip()]
        lines += [indent + s(part, dim=quiet) for part in parts[1:]]
        if fix:
            lines += [indent + s(part, dim=True) for part in textwrap.wrap(f"fix: {fix}", room)]
        return lines

    @contextlib.contextmanager
    def step(self, label: str) -> Iterator["Step"]:
        """A checklist line for work that takes a moment: a spinner turns while
        the body runs (on a terminal that can redraw a line), then the line
        settles to whatever the body reported with `step.done(...)`."""
        step = Step()
        spinner = _Spinner(self, label) if self.caps.vt else None
        if spinner is not None:
            spinner.start()
        try:
            yield step
        finally:
            if spinner is not None:
                spinner.stop()
        self.check(label, step.detail, step.tone, step.fix)

    def ready(self, url: str, note: str) -> None:
        s = self.style
        bar = s(s.glyphs["bar"], "accent")
        self.write()
        self.write(f"  {bar} {s('ready', 'good', bold=True)}  {s.link(url, s(url, 'accent', underline=True))}")
        self.write(f"  {bar} {s(note, dim=True)}")
        self.write()

    def goodbye(self, text: str) -> None:
        self.write(f"  {self.style.glyph('ok')} {text}")

    def show_cursor(self) -> None:
        if self.caps.vt:
            with _SCREEN:
                self.stream.write("\x1b[?25h")
                self.stream.flush()


@dataclass
class Step:
    """What a `Console.step` body reports; the line shows it when the body ends."""
    detail: str = ""
    tone: str = "ok"
    fix: str | None = None

    def done(self, detail: str, tone: str = "ok", fix: str | None = None) -> None:
        self.detail, self.tone, self.fix = detail, tone, fix


class _Spinner(threading.Thread):
    """Turns a spinner on the step's line until stopped. It waits a beat before
    the first frame, so work that finishes at once never flickers; it hides the
    cursor while it draws and always shows it again."""

    FRAME = 0.08

    def __init__(self, console: Console, label: str) -> None:
        super().__init__(daemon=True)
        self.console = console
        self.label = label
        self.halt = threading.Event()
        self.hidden = False  # the cursor is hidden and must come back
        self.drawn = False   # a frame is on the line and must be wiped

    def run(self) -> None:
        global _live_spinner
        frames = SPINNER[self.console.caps.unicode]
        if self.halt.wait(0.1):
            return
        stream, s = self.console.stream, self.console.style
        with _SCREEN:
            _live_spinner = self
            stream.write("\x1b[?25l")
            self.hidden = True
        i = 0
        while not self.halt.is_set():
            with _SCREEN:
                frame = s(frames[i % len(frames)], "info")
                line = self.console._check_lines(self.label, "", "info", glyph=frame)[0]
                stream.write("\r" + line + "\x1b[K")
                stream.flush()
                self.drawn = True
            i += 1
            self.halt.wait(self.FRAME)

    def clear_line(self) -> None:
        """Wipe the spinner's line (called under _SCREEN before a log line prints)."""
        if self.drawn:
            self.console.stream.write("\r\x1b[2K")
            self.console.stream.flush()

    def stop(self) -> None:
        global _live_spinner
        self.halt.set()
        self.join()
        with _SCREEN:
            if _live_spinner is self:
                _live_spinner = None
            self.clear_line()
            if self.hidden:
                self.console.stream.write("\x1b[?25h")
                self.console.stream.flush()


# ---------------------------------------------------------------- log lines
_LEVEL_TONES = ((logging.ERROR, "bad"), (logging.WARNING, "warn"), (logging.INFO, "info"))
_TONE_LEVELS = {"ok": logging.INFO, "info": logging.INFO, "warn": logging.WARNING,
                "bad": logging.ERROR, "debug": logging.DEBUG}


class LogFormatter(logging.Formatter):
    """`HH:MM:SS ✓ tag  message`: a dim local time, a glyph coloured by tone (the
    editor's ok/info/warn/bad), an optional accent tag (a graph, a subsystem),
    then the message. A record can set its tone with `extra={"tone": "ok"}` and
    its tag with `extra={"tag": ...}`. An exception prints as one line plus a
    dim location and hint; the full traceback only when verbose."""

    def __init__(self, verbose: bool = False, stream: TextIO | None = None) -> None:
        super().__init__()
        self.verbose = verbose
        self.style = Style(detect(sys.stderr if stream is None else stream))

    def tone(self, record: logging.LogRecord) -> str:
        explicit = getattr(record, "tone", None)
        if explicit in TONES:
            return explicit
        return next((tone for level, tone in _LEVEL_TONES if record.levelno >= level), "debug")

    def text(self, record: logging.LogRecord) -> str:
        return record.getMessage()

    def format(self, record: logging.LogRecord) -> str:
        s = self.style
        stamp = s(time.strftime("%H:%M:%S", time.localtime(record.created)), dim=True)
        message = self.text(record)
        tag = getattr(record, "tag", None)
        if tag:
            message = f"{s(str(tag), 'accent')}  {message}"
        line = f"  {stamp} {s.glyph(self.tone(record))} {message}"
        if record.exc_info and record.exc_info[1] is not None:
            line += self._exception(record.exc_info)
        return line

    def _exception(self, exc_info) -> str:
        if self.verbose:
            return "\n" + self.formatException(exc_info)
        exc = exc_info[1]
        indent = " " * 13
        lines = f": {type(exc).__name__}: {exc}" if str(exc) else f": {type(exc).__name__}"
        where = error_location(exc_info[2])
        if where:
            lines += "\n" + indent + self.style(f"at {where}", dim=True)
        return lines + "\n" + indent + self.style("--verbose prints the full traceback", dim=True)


class AccessFormatter(LogFormatter):
    """uvicorn's access lines (shown with --verbose): the status coloured by its
    class, the method and the path."""

    @staticmethod
    def _status(record: logging.LogRecord) -> int | None:
        try:
            return int(record.args[4])  # (client, method, path, http version, status)
        except (TypeError, ValueError, IndexError):
            return None

    def tone(self, record: logging.LogRecord) -> str:
        status = self._status(record)
        if status is None:
            return super().tone(record)
        return "ok" if status < 300 else "info" if status < 400 else "warn" if status < 500 else "bad"

    def text(self, record: logging.LogRecord) -> str:
        status = self._status(record)
        if status is None:
            return record.getMessage()
        _client, method, path = record.args[:3]
        return f"{self.style(str(status), TONES[self.tone(record)])} {method} {path}"


def error_location(tb) -> str:
    """`boltjar/server.py:123 in get_graph`: the innermost frame in Boltjar's own
    code (else the innermost frame), relative to the repo."""
    frames = traceback.extract_tb(tb) if tb is not None else []
    if not frames:
        return ""
    ours = [f for f in frames if pathlib.Path(f.filename).resolve().is_relative_to(ROOT / "boltjar")]
    frame = (ours or frames)[-1]
    path = pathlib.Path(frame.filename).resolve()
    shown = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
    return f"{shown}:{frame.lineno} in {frame.name}"


class ConsoleHandler(logging.StreamHandler):
    """A stderr handler that clears a turning spinner's line before it prints."""

    def emit(self, record: logging.LogRecord) -> None:
        with _SCREEN:
            if _live_spinner is not None:
                _live_spinner.clear_line()
            super().emit(record)


def log_config(verbose: bool = False) -> dict:
    """The logging setup for `python -m boltjar serve`, also handed to uvicorn.

    Quiet by default: uvicorn only reports warnings and errors (no startup
    chatter, no websocket open/close, no access line per request, which the
    editor's polling would flood), Boltjar's own loggers report at info, and
    other libraries at warning. `verbose` restores uvicorn's info lines, the
    access log and full tracebacks."""
    uvicorn_level = "INFO" if verbose else "WARNING"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {"()": "boltjar.console.LogFormatter", "verbose": verbose},
            "access": {"()": "boltjar.console.AccessFormatter", "verbose": verbose},
        },
        "handlers": {
            "console": {"class": "boltjar.console.ConsoleHandler", "formatter": "console",
                        "stream": "ext://sys.stderr"},
            "access": {"class": "boltjar.console.ConsoleHandler", "formatter": "access",
                       "stream": "ext://sys.stderr"},
        },
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": uvicorn_level, "propagate": False},
            "uvicorn.error": {"level": uvicorn_level},
            "uvicorn.access": {"handlers": ["access"], "level": uvicorn_level, "propagate": False},
            "boltjar": {"handlers": ["console"], "level": "DEBUG" if verbose else "INFO",
                        "propagate": False},
        },
        "root": {"handlers": ["console"], "level": "WARNING"},
    }


# ---------------------------------------------------------------- graph lines
_GRAPH_KINDS = frozenset({"live_graph", "status", "invalid", "error", "node_error", "log"})


class GraphLines:
    """The live graph lines. Fed every event the runtime broadcasts (and each
    rejected power-on), it logs what a person watching the terminal needs: a
    graph turning on or off, a graph rejected by validation, a graph that failed
    to build, a node error, and what a Log node echoes. Wire values are never
    printed. Each (graph, node, kind) may burst a few lines and is then held to
    one a second; the next line that prints says how many were skipped."""

    BURST = 5
    PER_SECOND = 1.0

    def __init__(self, logger: logging.Logger | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.log = logging.getLogger("boltjar.graph") if logger is None else logger
        self.clock = clock
        # (slug, node, kind) -> (tokens, last refill time, lines skipped)
        self._buckets: dict[tuple, tuple[float, float, int]] = {}

    def feed(self, event: dict) -> None:
        kind = event.get("kind")
        if kind not in _GRAPH_KINDS:  # values, node status, tool hops: never printed
            return
        slug = str(event.get("slug") or "_default")
        if kind == "live_graph":
            # sent right after every power change; non-empty means it just turned
            # on (a Restart included, which sends no "off" first).
            nodes = event.get("nodes") or []
            if nodes:
                self._release(slug)
                self._line(slug, "ok", f"power on ({len(nodes)} node{'' if len(nodes) == 1 else 's'})")
        elif kind == "status":
            if event.get("power") == "off":
                self._release(slug)
                self._line(slug, "info", "power off")
        elif kind == "invalid":
            problems = event.get("problems") or []
            text = f"cannot start: {len(problems)} problem{'' if len(problems) == 1 else 's'}"
            first = problems[0].get("message") if problems and isinstance(problems[0], dict) else None
            self._line(slug, "warn", f"{text} ({summarize(first, 120)})" if first else text)
        elif kind == "error":
            self._line(slug, "bad", f"failed to start: {summarize(event.get('error'), 200)}")
        elif kind == "node_error":
            node = event.get("node")
            self._limited((slug, node, kind), "bad", f"{node}: {summarize(event.get('error'), 200)}")
        elif kind == "log" and event.get("echo") is not None:
            self._limited((slug, event.get("node"), kind), "info", summarize(event["echo"], 200))

    def _limited(self, key: tuple, tone: str, text: str) -> None:
        now = self.clock()
        tokens, last, skipped = self._buckets.get(key, (float(self.BURST), now, 0))
        tokens = min(float(self.BURST), tokens + (now - last) * self.PER_SECOND)
        if tokens < 1:
            self._buckets[key] = (tokens, now, skipped + 1)
            return
        self._buckets[key] = (tokens - 1, now, 0)
        if skipped:
            text += f" ({skipped} similar line{'' if skipped == 1 else 's'} skipped)"
        self._line(key[0], tone, text)

    def _release(self, slug: str) -> None:
        """A graph turned off: report what its limits held back, then forget them."""
        for key in [k for k in self._buckets if k[0] == slug]:
            skipped = self._buckets.pop(key)[2]
            if skipped:
                what = "errors" if key[2] == "node_error" else "log lines"
                self._line(slug, "info", f"{key[1]}: {skipped} more {what} skipped")

    def _line(self, slug: str, tone: str, text: str) -> None:
        self.log.log(_TONE_LEVELS[tone], text, extra={"tone": tone, "tag": slug})
