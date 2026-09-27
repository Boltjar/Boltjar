"""
boltjar.console: the terminal a person watches while Boltjar runs.

Stdlib only: the start scripts import it before any requirement is known to be
installed. It detects what the terminal can do (a TTY, NO_COLOR, TERM=dumb,
COLORTERM, Windows VT processing, the output encoding, the width) and draws to
match: truecolor, 256, 16 or no colours, unicode glyphs or an ASCII set. It owns
the banner with the boot checklist, the ready block, the section rules, one log
line format for Boltjar's loggers and uvicorn's, and the live graph lines read
off the runtime's event stream.

The look is a side accent: every line after the banner hangs off a bar in the
logo gradient, and a section opens with a rule (`── Graphs ────`). Status colours
are the editor's (editor/src/styles/tokens.css), so the terminal and the editor
console agree on what green means. Names and the URL are bold in the terminal's
own foreground, never a hard-coded white that vanishes on a light theme.
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
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, Sequence, TextIO

from boltjar import banner_art
from boltjar.media import printable, summarize
from boltjar.secrets import redact

# ---------------------------------------------------------------- palette
RGB = tuple[int, int, int]

# RGB per role. The status roles are the editor tokens (--good, --warn, --bad,
# --info); `brand` is the logo cyan, `title` and `rule` draw the section rules,
# `soft` is the version in the banner.
ROLES: dict[str, RGB] = {
    "good": (0x4A, 0xDE, 0x80),
    "warn": (0xEA, 0xB3, 0x08),
    "bad": (0xEF, 0x44, 0x44),
    "info": (0x60, 0xA5, 0xFA),
    "brand": (0x00, 0xE5, 0xFF),
    "title": (0x3B, 0x82, 0xF6),
    "rule": (0x33, 0x41, 0x55),
    "soft": (0xF8, 0xFA, 0xFC),
}
# A 16-colour terminal gets the ANSI colour that MEANS the same thing, not the
# nearest RGB; `soft` becomes the terminal's own foreground (39).
ROLES_16: dict[str, int] = {
    "good": 32, "warn": 33, "bad": 31, "info": 94, "brand": 96, "title": 94, "rule": 90, "soft": 39,
}
# The logo gradient, cyan to indigo: (position, RGB) stops. The side bars take
# their colour from it.
GRADIENT: tuple[tuple[float, RGB], ...] = (
    (0.0, (0x06, 0xF2, 0xF9)), (0.3, (0x05, 0xE7, 0xFA)), (0.45, (0x08, 0xDD, 0xFA)),
    (0.57, (0x0F, 0xC6, 0xFA)), (0.7, (0x1E, 0xA6, 0xF8)), (0.82, (0x2D, 0x80, 0xFA)),
    (1.0, (0x36, 0x51, 0xFA)),
)
# The xterm 16-colour palette, for art and bars on a 16-colour terminal.
_ANSI_16: tuple[tuple[int, RGB], ...] = (
    (30, (0, 0, 0)), (31, (205, 0, 0)), (32, (0, 205, 0)), (33, (205, 205, 0)),
    (34, (0, 0, 238)), (35, (205, 0, 205)), (36, (0, 205, 205)), (37, (229, 229, 229)),
    (90, (127, 127, 127)), (91, (255, 0, 0)), (92, (0, 255, 0)), (93, (255, 255, 0)),
    (94, (92, 92, 255)), (95, (255, 0, 255)), (96, (0, 255, 255)), (97, (255, 255, 255)),
)

# A line's tone (the editor console's levels) and the role that colours it.
TONES: dict[str, str | None] = {
    "ok": "good", "info": "info", "warn": "warn", "bad": "bad", "debug": None,
    "on": "good", "off": "rule", "stop": "warn",
}

GLYPHS: dict[bool, dict[str, str]] = {
    True: {"ok": "✓", "info": "ℹ", "warn": "!", "bad": "✗", "debug": "·", "on": "●", "off": "○",
           "stop": "▶", "bar": "▌", "sep": "·", "rule": "─", "arrow": "→"},
    False: {"ok": "+", "info": "i", "warn": "!", "bad": "x", "debug": ".", "on": "*", "off": "o",
            "stop": ">", "bar": "|", "sep": "-", "rule": "-", "arrow": "->"},
}
SPINNER: dict[bool, str] = {True: "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", False: "|/-\\"}

# the line after the version in the banner
CREDIT_BY, CREDIT_NAME = "Designed by", "DKLRD"
CREDIT = f"{CREDIT_BY} {CREDIT_NAME}"
LABEL_WIDTH = 11   # a checklist label, "Python" and the rest
RULE_WIDTH = 48    # a section rule, `── Title ───...`
NAME_WIDTH = 12    # a graph name in a graph line
EVENT_WIDTH = 5    # what happened to it: On, Off, Log or a node's name
ART_GAP = 4        # between the flask and the column beside it

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\]8;;[^\x1b]*\x1b\\")

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Serialises every write to the screen: the spinner thread, the boot screen and
# the log handler. A log record that lands mid-spinner clears the spinner line
# first, so the two never print over each other.
_SCREEN = threading.RLock()
_live_spinner: "_Spinner | None" = None
# Where the open side-accent block is: the next bar takes its colour from here.
_block_line = 0
# The UTC offset the log times were last said to be in: the Graphs rule names it
# (times_note), then each change. None until the rule is drawn.
_zone_shown: str | None = None


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


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


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


def rgb_to_16(r: int, g: int, b: int) -> int:
    """The SGR foreground code of the nearest xterm 16-colour entry."""
    return min(_ANSI_16, key=lambda entry: sum((x - y) ** 2 for x, y in zip(entry[1], (r, g, b))))[0]


def gradient(t: float) -> RGB:
    """The logo gradient at `t` (0 cyan, 1 indigo)."""
    t = min(1.0, max(0.0, t))
    for (a, ca), (b, cb) in zip(GRADIENT, GRADIENT[1:]):
        if t <= b:
            k = (t - a) / (b - a)
            return tuple(int(round(x + (y - x) * k)) for x, y in zip(ca, cb))  # type: ignore[return-value]
    return GRADIENT[-1][1]


def bar_position(line: int) -> float:
    """Where on the gradient the bar of a block's `line` (0 first) sits: from
    0.15 down to 0.95 over five lines, then back up, so a block of any length,
    a live one included, keeps an even sweep."""
    step = line % 8
    return 0.15 + 0.2 * (step if step <= 4 else 8 - step)


def local_zone(when: float | None = None) -> str:
    """The machine's UTC offset at `when` (a time.time() value, now by default)
    as `UTC+10:00`, the zone the log times use."""
    moment = (datetime.datetime.now(datetime.timezone.utc) if when is None
              else datetime.datetime.fromtimestamp(when, datetime.timezone.utc))
    offset = moment.astimezone().strftime("%z") or "+0000"
    return f"UTC{offset[:3]}:{offset[3:]}"


def times_note() -> str:
    """`times in UTC+10:00`, the note on the rule the log lines follow. The
    offset is remembered, so the first line stamped after it changes (daylight
    saving starts or ends while the server runs) says `times now in ...`."""
    global _zone_shown
    with _SCREEN:
        _zone_shown = local_zone()
        return f"times in {_zone_shown}"


# ---------------------------------------------------------------- painting
class Style:
    """Paints text for one stream's capabilities. With no colour it returns the
    text untouched, so callers write the same code for every terminal."""

    def __init__(self, caps: Caps) -> None:
        self.caps = caps
        self.glyphs = GLYPHS[caps.unicode]

    def __call__(self, text: str, role: str | None = None, *, bold: bool = False,
                 dim: bool = False, underline: bool = False) -> str:
        return self._paint(text, ROLES[role] if role else None, role, bold, dim, underline)

    def rgb(self, text: str, color: RGB, *, bold: bool = False) -> str:
        """`text` in an exact colour (art, a bar), stepped down to the terminal's tier."""
        return self._paint(text, color, None, bold, False, False)

    def _paint(self, text: str, color: RGB | None, role: str | None, bold: bool, dim: bool,
               underline: bool) -> str:
        if self.caps.color == "none" or not text:
            return text
        codes = [c for c, on in (("1", bold), ("2", dim), ("4", underline)) if on]
        if color is not None:
            codes.append(self.fg(color, role))
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if codes else text

    def fg(self, color: RGB, role: str | None = None) -> str:
        """The SGR foreground parameters for `color` on this terminal. A role on
        a 16-colour terminal takes the colour that means the same thing."""
        if self.caps.color == "truecolor":
            return "38;2;{};{};{}".format(*color)
        if self.caps.color == "256":
            return f"38;5;{rgb_to_256(*color)}"
        return str(ROLES_16[role] if role else rgb_to_16(*color))

    def glyph(self, tone: str) -> str:
        """The coloured glyph for a tone (ok, info, warn, bad, debug, on, off, stop)."""
        role = TONES.get(tone)
        return self(self.glyphs.get(tone, self.glyphs["info"]), role, dim=role is None)

    def bar(self, line: int) -> str:
        """The side-accent bar of a block's `line`, with the space after it."""
        return self.rgb(self.glyphs["bar"], gradient(bar_position(line))) + " "

    def link(self, url: str, text: str | None = None) -> str:
        """`text` as an OSC 8 hyperlink to `url` where the terminal supports it."""
        text = url if text is None else text
        if not self.caps.links:
            return text
        return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"

    def rule(self, title: str, note: str = "", width: int = RULE_WIDTH) -> str:
        """A section rule: `── Title ──────`, the title bold, `note` dim after it,
        `width` columns in all."""
        dash = self.glyphs["rule"]
        head = f"{dash * 2} "
        used = len(head) + len(title) + (2 + len(note) + 1 if note else 1)
        tail = dash * max(3, width - used)
        note = f"  {self(note, dim=True)} " if note else " "
        return f"{self(head, 'title')}{self(title, 'title', bold=True)}{note}{self(tail, 'rule')}"

    def art(self, rows: Sequence[str], colors: Sequence[Sequence[str]]) -> list[str]:
        """Rows of banner art painted one colour per character (`colors` holds a
        hex RGB per visible character, "" for a blank). A run of characters that
        land on the same terminal colour shares one escape."""
        if self.caps.color == "none":
            return list(rows)
        painted = []
        for row, hexes in zip(rows, colors):
            out, current = [], None
            for ch, hx in zip(row, list(hexes) + [""] * (len(row) - len(hexes))):
                if ch != " " and hx:
                    code = self.fg((int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)))
                    if code != current:
                        out.append(f"\x1b[{code}m")
                        current = code
                out.append(ch)
            painted.append("".join(out) + ("\x1b[0m" if current else ""))
        return painted


# ---------------------------------------------------------------- the boot screen
@dataclass(frozen=True)
class Row:
    """One boot checklist line: `✓ Python     3.12.9  .venv`. `aside` prints dim
    after the detail, `gap` apart; each of `fixes` prints dim underneath. A
    command in a fix is marked with `unbroken`, so the row never wraps inside it."""
    label: str
    detail: str
    tone: str = "ok"
    aside: str = ""
    gap: str = " "
    fixes: tuple[str, ...] = ()


# joins the words of an unbroken() run; it prints as a plain space
_NO_BREAK = "\xa0"


def unbroken(text: str) -> str:
    """`text` (a command, a flag and its value) kept on one line when a
    checklist row wraps, unless it is wider than the column itself."""
    return text.replace(" ", _NO_BREAK)


def _wrap(text: str, width: int) -> list[str]:
    # a pack id, a flag or a path keeps its hyphens: it breaks only at spaces.
    # An unbroken() run moves to the next line whole; one wider than the column
    # breaks at its own spaces after all.
    lines = []
    for line in textwrap.wrap(text, width, break_on_hyphens=False, break_long_words=False):
        line = line.replace(_NO_BREAK, " ")
        lines += textwrap.wrap(line, width, break_on_hyphens=False) if len(line) > width else [line]
    return lines


def _longest_word(rows: Sequence[Row]) -> int:
    """The widest run a checklist row can not break: a word, a link, an
    unbroken() command, an aside (which moves down whole)."""
    runs = [row.aside for row in rows]
    for row in rows:
        for text in (row.detail, *(f"fix: {fix}" for fix in row.fixes)):
            runs += text.split(" ")
    return max(map(len, runs), default=0)


def next_bar(style: Style) -> str:
    """The bar for the next line of the open block (call under _SCREEN)."""
    global _block_line
    bar = style.bar(_block_line)
    _block_line += 1
    return bar


def open_block() -> None:
    """Start a new side-accent block: its first bar is the top of the gradient."""
    global _block_line
    with _SCREEN:
        _block_line = 0


class Console:
    """The boot sequence's writer: the banner, the ready block, section rules
    and the side-accent lines under them."""

    def __init__(self, stream: TextIO | None = None, caps: Caps | None = None,
                 width: int | None = None) -> None:
        self.stream = sys.stdout if stream is None else stream
        self.caps = detect(self.stream) if caps is None else caps
        self.style = Style(self.caps)
        self._width = width

    def width(self) -> int:
        """The columns a line may fill: one short of the window, since a line
        that reaches the last column makes some consoles add a blank line."""
        return (terminal_width(self.stream) if self._width is None else self._width) - 1

    def write(self, line: str = "") -> None:
        with _SCREEN:
            self.stream.write(line + "\n")
            self.stream.flush()

    # ------------------------------------------------ the banner
    def banner(self, version: str, rows: Sequence[Row]) -> None:
        """The flask and the `Boltjar` wordmark (both braille), the version with
        the tagline and the boot checklist, side by side. A window too narrow for
        the flask drops it; one too narrow for the word, or a terminal without
        the unicode set, gets a single line instead."""
        for line in self.banner_lines(version, rows):
            self.write(line)

    def banner_lines(self, version: str, rows: Sequence[Row]) -> list[str]:
        s = self.style
        width = self.width()
        credit = f"{s(CREDIT_BY, dim=True)} {s(CREDIT_NAME, 'soft')}"
        heading = f"{s(f'v{version}', 'soft', bold=True)}  {credit}"
        need = max(max(map(len, banner_art.WORD)), visible_len(heading))
        flask_width = max(map(len, banner_art.FLASK))
        room = width - 2 - flask_width - ART_GAP
        art = self.caps.unicode  # the flask and the word are drawn in braille
        # the flask is decoration: it steps aside before a link or a command
        # beside it would have to break
        if art and room >= need and _longest_word(rows) <= room - 2 - LABEL_WIDTH:
            right = self._word() + ["", heading, ""] + self._checklist(rows, room)
            return ["", *self._beside(s.art(banner_art.FLASK, banner_art.FLASK_RGB), right), ""]
        if art and width >= 2 + need:
            lines = self._word() + ["", heading, ""]
        else:
            name = f"{s('Boltjar', bold=True)} {s(f'v{version}', 'soft', bold=True)}"
            fits = 2 + visible_len(name) + 2 + len(CREDIT) <= width
            lines = [f"{name}  {credit}" if fits else name, ""]
        lines += self._checklist(rows, width - 2)
        return ["", *(f"  {line}".rstrip() for line in lines), ""]

    def _word(self) -> list[str]:
        return self.style.art(banner_art.WORD, banner_art.WORD_RGB)

    @staticmethod
    def _beside(left: list[str], right: list[str]) -> list[str]:
        """`left` (the flask) with `right` beside it, both from the top row, so
        the flask sits level with the word however long the checklist runs."""
        flask_width = max(map(len, banner_art.FLASK))
        lines = []
        for i in range(max(len(left), len(right))):
            art = left[i] if i < len(left) else ""
            shown = banner_art.FLASK[i] if i < len(banner_art.FLASK) else ""
            beside = right[i] if i < len(right) else ""
            pad = " " * (flask_width - len(shown) + ART_GAP)
            lines.append(f"  {art}{pad}{beside}".rstrip() if beside else f"  {art}".rstrip())
        return lines

    def _checklist(self, rows: Sequence[Row], room: int) -> list[str]:
        """The checklist rows, each wrapped to `room` columns under its detail."""
        s = self.style
        indent = " " * (2 + LABEL_WIDTH)
        text_room = max(20, room - len(indent))
        lines = []
        for row in rows:
            parts = _wrap(row.detail, text_room) or [""]
            if row.aside:
                # the aside follows the detail, or takes a line of its own when
                # it does not fit after it
                if len(parts[-1]) + len(row.gap) + len(row.aside) <= text_room:
                    parts[-1] += row.gap + s(row.aside, dim=True)
                else:
                    parts.append(s(row.aside, dim=True))
            lines.append(f"{s.glyph(row.tone)} {row.label.ljust(LABEL_WIDTH)}{parts[0]}".rstrip())
            lines += [indent + part for part in parts[1:]]
            for fix in row.fixes:
                lines += [indent + s(part, dim=True) for part in _wrap(f"fix: {fix}", text_room)]
        return lines

    # ------------------------------------------------ after the banner
    def ready(self, url: str, note: str) -> None:
        """`Ready → <url>` (a link where the terminal makes one) and a dim note."""
        s = self.style
        arrow = s(s.glyphs["arrow"], "rule")
        open_block()
        with _SCREEN:
            self.write(f"{next_bar(s)}{s('Ready', 'brand', bold=True)}  {arrow}  {s.link(url, s(url, bold=True))}")
            self.write(f"{next_bar(s)}{s(note, dim=True)}")

    def section(self, title: str, note: str = "") -> None:
        """A blank line and a section rule; the lines after it are a new block."""
        open_block()
        self.write()
        self.write(self.style.rule(title, note, min(RULE_WIDTH, self.width())))

    def line(self, tone: str, text: str, hint: str = "") -> None:
        """A status line on the open block, with an optional dim hint under it."""
        s = self.style
        with _SCREEN:
            self.write(f"{next_bar(s)}{s.glyph(tone)} {text}")
            if hint:
                self.write(f"{next_bar(s)}  {s(hint, dim=True)}")

    def goodbye(self) -> None:
        with _SCREEN:
            self.write(f"{next_bar(self.style)}{self.style('bye', 'brand', bold=True)}")

    # ------------------------------------------------ waiting
    @contextlib.contextmanager
    def pending(self, text: str) -> Iterator[None]:
        """A spinner with `text` while the body runs, on a terminal that can
        redraw a line; the line is wiped after, leaving nothing behind."""
        spinner = None
        if self.caps.vt:
            s = self.style
            spinner = _Spinner(self, lambda frame: f"  {s(frame, 'info')} {s(text, dim=True)}")
            spinner.start()
        try:
            yield
        finally:
            if spinner is not None:
                spinner.stop()

    def show_cursor(self) -> None:
        if self.caps.vt:
            with _SCREEN:
                self.stream.write("\x1b[?25h")
                self.stream.flush()


class _Spinner(threading.Thread):
    """Turns a spinner on its own line until stopped. It waits a beat before
    the first frame, so work that finishes at once never flickers; it hides the
    cursor while it draws and always shows it again."""

    FRAME = 0.08

    def __init__(self, console: Console, render: Callable[[str], str]) -> None:
        super().__init__(daemon=True)
        self.console = console
        self.render = render
        self.halt = threading.Event()
        self.hidden = False  # the cursor is hidden and must come back
        self.drawn = False   # a frame is on the line and must be wiped

    def run(self) -> None:
        global _live_spinner
        frames = SPINNER[self.console.caps.unicode]
        if self.halt.wait(0.1):
            return
        stream = self.console.stream
        with _SCREEN:
            _live_spinner = self
            stream.write("\x1b[?25l")
            self.hidden = True
        i = 0
        while not self.halt.is_set():
            with _SCREEN:
                stream.write("\r" + self.render(frames[i % len(frames)]) + "\x1b[K")
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
# the columns before a message: the bar, the time and the glyph.
_MESSAGE_INDENT = " " * 12


def _terminal_safe(text: str) -> str:
    """Text the formatter did not write, as the terminal may show it: a known
    secret as its {{secret.NAME}} token (the same redaction the editor's live
    events get) and every control character as its escape."""
    return printable(redact(text))


class LogFormatter(logging.Formatter):
    """`▌ HH:MM:SS  ✓ message`: a bar on the open block, a dim local time, a
    glyph coloured by tone (the editor's ok/info/warn/bad), then the message. A
    record can set its tone with `extra={"tone": "ok"}`. A graph line (GraphLines)
    also carries `tag` (the graph), `event`, `detail` and `hints`, and lines up
    in columns. An exception prints as one line plus a dim location and hint;
    the full traceback only when verbose. Text the formatter did not write (a
    message, an exception, a graph line's fields) can come from anywhere, so it
    shows a known secret as its token and its control characters as escapes
    (_terminal_safe); a line break in a message starts a new line under it."""

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
        """The message as the terminal shows it, one line of it per line."""
        return "\n".join(_terminal_safe(line) for line in record.getMessage().splitlines())

    def format(self, record: logging.LogRecord) -> str:
        s = self.style
        stamp = s(time.strftime("%H:%M:%S", time.localtime(record.created)), dim=True)
        head = f"{stamp}  {s.glyph(getattr(record, 'glyph', None) or self.tone(record))} "
        if getattr(record, "event", None) is not None:
            lines = self._graph_line(record, head)
        else:
            first, *rest = self.text(record).split("\n")
            lines = [head + first] + [_MESSAGE_INDENT + line for line in rest]
        if record.exc_info and record.exc_info[1] is not None:
            lines = self._exception(lines, record.exc_info)
        with _SCREEN:
            lines = self._zone_change(record) + lines
            return "\n".join(next_bar(s) + line for line in lines)

    def _zone_change(self, record: logging.LogRecord) -> list[str]:
        """A dim `times now in UTC+11:00` line when `record` is stamped in
        another offset than the one last shown (call under _SCREEN)."""
        global _zone_shown
        if _zone_shown is None:
            return []
        zone = local_zone(record.created)
        if zone == _zone_shown:
            return []
        _zone_shown = zone
        return [self.style(f"times now in {zone}", dim=True)]

    def _graph_line(self, record: logging.LogRecord, head: str) -> list[str]:
        """`chat        On   17 nodes`: the graph bold, the event, the detail (dim
        unless it is the problem itself), and each hint dim under the event."""
        s = self.style
        name = _terminal_safe(str(getattr(record, "tag", "") or ""))
        event = _terminal_safe(str(record.event))
        detail = _terminal_safe(str(getattr(record, "detail", "") or ""))
        name_col = s(name, bold=True) + " " * max(1, NAME_WIDTH - len(name))
        event_col = event + " " * max(1, EVENT_WIDTH - len(event)) if detail else event
        quiet = self.tone(record) not in ("warn", "bad")
        line = head + name_col + event_col + (s(detail, dim=True) if quiet else detail)
        indent = _MESSAGE_INDENT + " " * NAME_WIDTH
        hints = [_terminal_safe(str(hint)) for hint in getattr(record, "hints", ())]
        return [line.rstrip()] + [indent + s(hint, dim=True) for hint in hints]

    def _exception(self, lines: list[str], exc_info) -> list[str]:
        if self.verbose:
            return lines + [_terminal_safe(line) for line in self.formatException(exc_info).splitlines()]
        exc = exc_info[1]
        reason = _terminal_safe(" ".join(str(exc).split()))
        lines = list(lines)
        lines[0] += f": {type(exc).__name__}: {reason}" if reason else f": {type(exc).__name__}"
        where = error_location(exc_info[2])
        if where:
            lines.append(_MESSAGE_INDENT + self.style(f"at {where}", dim=True))
        return lines + [_MESSAGE_INDENT + self.style("--verbose prints the full traceback", dim=True)]


class AccessFormatter(LogFormatter):
    """uvicorn's access lines (shown with --verbose): the status coloured by its
    class, the method and the path. The editor's one-time link carries the
    install token in its query, so the path shows it as `<token>`."""

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
            return super().text(record)
        _client, method, path = record.args[:3]
        shown = self.style(str(status), TONES[self.tone(record)])
        return f"{shown} {_terminal_safe(str(method))} {_terminal_safe(_mask_token(str(path)))}"


# The query parameter of the editor's one-time link: boltjar.security.LINK_PARAM,
# named again here because security imports starlette.
_LINK_PARAM = "token"


def _mask_token(path: str) -> str:
    """`path` with the value of its token parameter replaced by `<token>`. The
    parameter is matched by its decoded name, as the server reads it."""
    base, sep, query = path.partition("?")
    if not sep:
        return path
    parts = []
    for part in query.split("&"):
        name, eq, _value = part.partition("=")
        is_token = eq and urllib.parse.unquote_plus(name) == _LINK_PARAM
        parts.append(f"{name}=<token>" if is_token else part)
    return f"{base}?{'&'.join(parts)}"


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
# a refused power-on lists this many of its problems under the line
_PROBLEMS_SHOWN = 3


class GraphLines:
    """The live graph lines under `── Graphs ──`. Fed every event the runtime
    broadcasts (and each refused power-on), it logs what a person watching the
    terminal needs: a graph turning on or off, a graph refused by validation, a
    graph that failed to build, a node error, and what a Log node echoes. Wire
    values are never printed. Each (graph, node, kind) may burst a few lines and
    is then held to one a second; the next line that prints says how many were
    skipped."""

    BURST = 5
    PER_SECOND = 1.0

    def __init__(self, logger: logging.Logger | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.log = logging.getLogger("boltjar.graph") if logger is None else logger
        self.clock = clock
        # (slug, node, kind) -> (tokens, last refill time, lines skipped)
        self._buckets: dict[tuple, tuple[float, float, int]] = {}
        # the graphs seen turning On and not off since
        self._live: set[str] = set()

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
                self._live.add(slug)
                self._line(slug, "ok", "On", _count(len(nodes), "node"), glyph="on")
        elif kind == "status":
            # an Off for a graph that was not running (the editor, the API or the
            # MCP server can send one anyway) changes nothing, so it prints nothing
            if event.get("power") == "off" and slug in self._live:
                self._live.discard(slug)
                self._release(slug)
                self._line(slug, "info", "Off", glyph="off")
        elif kind == "invalid":
            problems = [p for p in event.get("problems") or [] if isinstance(p, dict)]
            hints = [summarize(_problem_text(p), 120) for p in problems[:_PROBLEMS_SHOWN]]
            if len(problems) > _PROBLEMS_SHOWN:
                hints.append(f"and {len(problems) - _PROBLEMS_SHOWN} more, listed in the editor")
            self._line(slug, "warn", "On", f"refused: {_count(len(problems), 'problem')}", hints=hints)
        elif kind == "error":
            # the power-on stopped whatever ran before it, then failed: nothing runs
            self._live.discard(slug)
            self._release(slug)
            self._line(slug, "bad", "On", f"failed: {summarize(event.get('error'), 200)}")
        elif kind == "node_error":
            node = str(event.get("node"))
            self._limited((slug, node, kind), "bad", node, summarize(event.get("error"), 200))
        elif kind == "log" and event.get("echo") is True:
            # summarized from the message as broadcast, never from the raw value:
            # what the server keeps out of a live event stays out of the terminal.
            self._limited((slug, event.get("node"), kind), "info", "Log",
                          summarize(event.get("message"), 200))

    # ------------------------------------------------ resuming after a launch
    def not_resumed(self, slug: str, problem: dict | str) -> None:
        """A graph that was On could not be powered back On: one line, the
        graph and its first problem (the editor lists them all)."""
        text = _problem_text(problem) if isinstance(problem, dict) else str(problem)
        self._line(slug, "warn", "On", f"not resumed: {summarize(text, 200)}")

    def resume_dropped(self, slug: str) -> None:
        """A graph that was On whose graph file is gone: it is no longer resumed."""
        self._line(slug, "info", "Off", "its graph file is gone: no longer resumed", glyph="off")

    def resume_summary(self, resumed: Sequence[str], failed: Sequence[str],
                       dropped: Sequence[str] = ()) -> None:
        """`resumed 2: chat, digest`, or `resumed 1 of 2: chat; not resumed:
        digest`. `dropped`: graphs that were On but are gone (each printed its
        own line), so the summary never says none was On."""
        total = len(resumed) + len(failed)
        if not total:
            self.note("nothing left to resume" if dropped
                      else "no graph was On when Boltjar last stopped: nothing to resume")
            return
        text = f"resumed {len(resumed)}" + (f" of {total}" if failed else "")
        if resumed:
            text += ": " + ", ".join(resumed)
        if failed:
            text += "; not resumed: " + ", ".join(failed)
        self.note(text, "warn" if failed else "ok")

    def note(self, text: str, tone: str = "info") -> None:
        """A plain line under the Graphs rule, about no one graph."""
        self.log.log(_TONE_LEVELS[tone], text, extra={"tone": tone})

    def _limited(self, key: tuple, tone: str, event: str, detail: str) -> None:
        now = self.clock()
        tokens, last, skipped = self._buckets.get(key, (float(self.BURST), now, 0))
        tokens = min(float(self.BURST), tokens + (now - last) * self.PER_SECOND)
        if tokens < 1:
            self._buckets[key] = (tokens, now, skipped + 1)
            return
        self._buckets[key] = (tokens - 1, now, 0)
        if skipped:
            detail += f" ({skipped} similar line{'' if skipped == 1 else 's'} skipped)"
        self._line(key[0], tone, event, detail)

    def _release(self, slug: str) -> None:
        """A graph turned off: report what its limits held back, then forget them."""
        for key in [k for k in self._buckets if k[0] == slug]:
            skipped = self._buckets.pop(key)[2]
            if skipped:
                what = "errors" if key[2] == "node_error" else "log lines"
                self._line(slug, "info", str(key[1]), f"{skipped} more {what} skipped")

    def _line(self, slug: str, tone: str, event: str, detail: str = "", *, glyph: str | None = None,
              hints: Sequence[str] = ()) -> None:
        # the message reads on its own for any other handler; the console lays
        # the same facts out in columns from the extras.
        message = "\n".join([f"{event}  {detail}".rstrip(), *hints])
        self.log.log(_TONE_LEVELS[tone], message, extra={
            "tone": tone, "tag": slug, "event": event, "detail": detail, "hints": list(hints),
            "glyph": glyph or tone,
        })


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _problem_text(problem: dict) -> str:
    node = problem.get("node")
    message = str(problem.get("message") or problem.get("kind") or "problem")
    return f"{node}: {message}" if node else message
