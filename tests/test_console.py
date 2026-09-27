"""The terminal console: capability detection, painting, the checklist, the log
line format and the live graph lines."""
from __future__ import annotations

import io
import logging
import sys

import pytest

from boltjar import console
from boltjar.console import Caps, Console, GraphLines, LogFormatter, Style


class FakeStream(io.StringIO):
    def __init__(self, tty: bool = True, encoding: str = "utf-8") -> None:
        super().__init__()
        self._tty = tty
        self._encoding = encoding

    def isatty(self) -> bool:
        return self._tty

    @property
    def encoding(self) -> str:  # StringIO's own is read-only None
        return self._encoding


def caps_for(env: dict, platform: str = "linux", tty: bool = True, vt: bool = True,
             encoding: str = "utf-8") -> Caps:
    return console.detect(FakeStream(tty, encoding), env=env, platform=platform,
                          vt_probe=lambda _stream: vt)


# ---------------------------------------------------------------- detection
@pytest.mark.parametrize("env, platform, expected", [
    ({"TERM": "xterm-256color", "COLORTERM": "truecolor"}, "linux", "truecolor"),
    ({"TERM": "xterm-256color", "COLORTERM": "24bit"}, "darwin", "truecolor"),
    ({"TERM": "xterm-256color"}, "darwin", "256"),
    ({"TERM": "xterm"}, "linux", "16"),
    ({}, "win32", "truecolor"),
    ({"TERM": "xterm-256color", "COLORTERM": "truecolor", "NO_COLOR": "1"}, "linux", "none"),
    ({"TERM": "dumb"}, "linux", "none"),
])
def test_colour_tier(env, platform, expected):
    assert caps_for(env, platform).color == expected


def test_an_empty_no_color_does_not_disable_colour():
    # no-color.org: only a NON-EMPTY value turns colour off.
    assert caps_for({"TERM": "xterm", "NO_COLOR": ""}).color == "16"


def test_no_terminal_means_no_colour_and_no_redraw():
    caps = caps_for({"COLORTERM": "truecolor"}, tty=False)
    assert (caps.color, caps.vt, caps.links) == ("none", False, False)


def test_windows_console_without_vt_gets_no_colour():
    caps = caps_for({"WT_SESSION": "x"}, platform="win32", vt=False)
    assert (caps.color, caps.vt) == ("none", False)


def test_unicode_needs_an_encoding_that_holds_the_glyphs():
    assert caps_for({"TERM": "xterm"}, encoding="utf-8").unicode is True
    assert caps_for({"TERM": "xterm"}, encoding="ascii").unicode is False
    assert caps_for({"TERM": "xterm"}, encoding="cp1252").unicode is False


def test_unicode_on_windows_waits_for_a_modern_terminal():
    assert caps_for({}, platform="win32").unicode is False  # the classic console host
    assert caps_for({"WT_SESSION": "1"}, platform="win32").unicode is True
    assert caps_for({"TERM_PROGRAM": "vscode"}, platform="win32").unicode is True


def test_hyperlinks_only_where_known_to_render():
    assert caps_for({"WT_SESSION": "1"}, platform="win32").links is True
    assert caps_for({"TERM_PROGRAM": "iTerm.app"}).links is True
    assert caps_for({"VTE_VERSION": "7200"}).links is True
    assert caps_for({"VTE_VERSION": "4800"}).links is False
    assert caps_for({"TERM": "xterm-256color"}).links is False


# ---------------------------------------------------------------- painting
def test_rgb_to_256_picks_the_cube_or_the_grey_ramp():
    assert console.rgb_to_256(255, 0, 0) == 196
    assert console.rgb_to_256(0, 0, 0) == 16
    assert console.rgb_to_256(128, 128, 128) == 244


def test_style_writes_each_tier():
    text = "ok"
    true = Style(Caps(tty=True, vt=True, color="truecolor"))("ok", "good")
    assert true == "\x1b[38;2;74;222;128mok\x1b[0m"
    assert Style(Caps(tty=True, vt=True, color="256"))(text, "good").startswith("\x1b[38;5;")
    assert Style(Caps(tty=True, vt=True, color="16"))(text, "good") == "\x1b[32mok\x1b[0m"
    assert Style(Caps())(text, "good", bold=True) == "ok"


def test_emphasis_is_bold_in_the_terminal_foreground():
    assert Style(Caps(tty=True, vt=True, color="truecolor"))("Boltjar", bold=True) == "\x1b[1mBoltjar\x1b[0m"


def test_links_fall_back_to_the_plain_url():
    url = "http://127.0.0.1:8770"
    assert Style(Caps())("x") == "x"
    assert Style(Caps()).link(url) == url
    linked = Style(Caps(tty=True, vt=True, color="16", links=True)).link(url)
    assert linked == f"\x1b]8;;{url}\x1b\\{url}\x1b]8;;\x1b\\"
    assert console.visible_len(linked) == len(url)


def test_glyphs_have_an_ascii_set():
    assert Style(Caps(unicode=True)).glyph("ok") == "✓"
    assert Style(Caps(unicode=False)).glyph("ok") == "+"
    assert Style(Caps(unicode=False)).glyph("bad") == "x"


# ---------------------------------------------------------------- the checklist
def plain_console(width: int = 80) -> tuple[Console, FakeStream]:
    stream = FakeStream(tty=False)
    return Console(stream, Caps(unicode=True)), stream


def test_check_lines_align_and_carry_a_fix(monkeypatch):
    monkeypatch.setattr(console, "terminal_width", lambda _s=None: 80)
    out, stream = plain_console()
    out.check("python", "3.12.9")
    out.check("port", "8770 is in use", "bad", fix="use another port: --port 8771")
    assert stream.getvalue().splitlines() == [
        "    ✓ python       3.12.9",
        "    ✗ port         8770 is in use",
        "                   fix: use another port: --port 8771",
    ]


def test_long_text_wraps_under_the_detail_column(monkeypatch):
    monkeypatch.setattr(console, "terminal_width", lambda _s=None: 40)
    out, stream = plain_console()
    out.check("editor", "not built: the API runs, the editor page does not", "warn")
    lines = stream.getvalue().splitlines()
    assert len(lines) > 1
    assert all(len(line) <= 40 for line in lines)
    assert lines[1].startswith(" " * 19) and lines[1].strip()


def test_a_step_settles_to_what_its_body_reported(monkeypatch):
    monkeypatch.setattr(console, "terminal_width", lambda _s=None: 80)
    out, stream = plain_console()
    with out.step("providers") as step:
        step.done("ollama, xai")
    assert stream.getvalue() == "    ✓ providers    ollama, xai\n"


def test_a_step_spins_only_on_a_terminal_that_can_redraw(monkeypatch):
    monkeypatch.setattr(console, "terminal_width", lambda _s=None: 80)
    stream = FakeStream(tty=True)
    out = Console(stream, Caps(tty=True, vt=True, color="none", unicode=True))
    with out.step("packs") as step:
        import time
        time.sleep(0.35)  # long enough for frames to draw
        step.done("core")
    text = stream.getvalue()
    assert "\r" in text and "\x1b[?25l" in text  # frames drew with the cursor hidden
    assert text.endswith("\x1b[?25h    ✓ packs        core\n")  # cursor back, then the line


def test_banner_names_the_product_and_version():
    out, stream = plain_console()
    out.banner("0.1.0", "a tagline")
    assert "Boltjar 0.1.0" in stream.getvalue()
    assert "a tagline" in stream.getvalue()


# ---------------------------------------------------------------- log lines
def record(msg: str, level: int = logging.INFO, **extra) -> logging.LogRecord:
    rec = logging.LogRecord("boltjar.test", level, __file__, 1, msg, (), None)
    rec.created = 0
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def plain_formatter(verbose: bool = False) -> LogFormatter:
    return LogFormatter(verbose=verbose, stream=FakeStream(tty=False))


def test_log_line_is_time_glyph_tag_message():
    line = plain_formatter().format(record("power on", tone="ok", tag="chat"))
    stamp, glyph, rest = line.strip().split(" ", 2)
    assert len(stamp) == 8 and stamp.count(":") == 2
    assert glyph == "+" or glyph == "✓"
    assert rest == "chat  power on"


def test_levels_map_to_the_editor_tones():
    fmt = plain_formatter()
    assert fmt.tone(record("x", logging.INFO)) == "info"
    assert fmt.tone(record("x", logging.WARNING)) == "warn"
    assert fmt.tone(record("x", logging.ERROR)) == "bad"
    assert fmt.tone(record("x", logging.DEBUG)) == "debug"
    assert fmt.tone(record("x", logging.INFO, tone="ok")) == "ok"


def _raise_here():
    raise ValueError("the reason")


def test_an_error_is_one_line_with_a_location_and_a_hint():
    try:
        _raise_here()
    except ValueError:
        rec = record("could not load", logging.ERROR, exc_info=sys.exc_info())
    lines = plain_formatter().format(rec).splitlines()
    assert lines[0].endswith("could not load: ValueError: the reason")
    assert lines[1].strip() == f"at tests/test_console.py:{_raise_here.__code__.co_firstlineno + 1} in _raise_here"
    assert "--verbose" in lines[2]
    assert "Traceback" not in "\n".join(lines)


def test_verbose_prints_the_full_traceback():
    try:
        _raise_here()
    except ValueError:
        rec = record("could not load", logging.ERROR, exc_info=sys.exc_info())
    assert "Traceback (most recent call last)" in plain_formatter(verbose=True).format(rec)


def test_access_lines_show_status_method_and_path():
    fmt = console.AccessFormatter(stream=FakeStream(tty=False))
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5000", "GET", "/api/graphs", "1.1", 404), None)
    assert fmt.format(rec).endswith(" 404 GET /api/graphs")
    assert fmt.tone(rec) == "warn"


def test_log_config_is_quiet_unless_verbose():
    quiet, loud = console.log_config(False), console.log_config(True)
    assert quiet["loggers"]["uvicorn.access"]["level"] == "WARNING"
    assert quiet["loggers"]["uvicorn.error"]["level"] == "WARNING"
    assert loud["loggers"]["uvicorn.access"]["level"] == "INFO"
    assert quiet["loggers"]["boltjar"]["level"] == "INFO"


def test_local_zone_names_the_offset():
    zone = console.local_zone()
    assert zone.startswith("UTC") and zone[3] in "+-" and zone[6] == ":"


# ---------------------------------------------------------------- graph lines
class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.DEBUG, logger="test.graph")
    clock = Clock()
    graph = GraphLines(logging.getLogger("test.graph"), clock=clock)

    def seen() -> list[tuple[str, str, str]]:
        return [(r.tag, r.tone, r.getMessage()) for r in caplog.records]

    return graph, clock, seen


def test_power_changes_and_failures_become_lines(lines):
    graph, _clock, seen = lines
    graph.feed({"kind": "status", "power": "on", "slug": "chat"})  # the count comes next
    graph.feed({"kind": "live_graph", "nodes": ["a", "b", "c"], "edges": [], "slug": "chat"})
    graph.feed({"kind": "invalid", "slug": "chat",
                "problems": [{"node": "x", "message": "required input 'in' is not connected"}]})
    graph.feed({"kind": "error", "error": "ValueError('unknown node type: x')", "slug": "chat"})
    graph.feed({"kind": "node_error", "node": "LLM", "error": "RuntimeError('boom')", "slug": "chat"})
    graph.feed({"kind": "status", "power": "off", "slug": "chat"})
    graph.feed({"kind": "live_graph", "nodes": [], "edges": [], "slug": "chat"})
    assert seen() == [
        ("chat", "ok", "power on (3 nodes)"),
        ("chat", "warn", "cannot start: 1 problem (required input 'in' is not connected)"),
        ("chat", "bad", "failed to start: ValueError('unknown node type: x')"),
        ("chat", "bad", "LLM: RuntimeError('boom')"),
        ("chat", "info", "power off"),
    ]


def test_wire_values_are_never_printed(lines):
    graph, _clock, seen = lines
    for event in ({"kind": "value", "node": "a", "port": "out", "value": "secret text"},
                  {"kind": "node_status", "node": "a", "status": "running"},
                  {"kind": "tool_call", "node": "t", "args": {"q": "secret"}},
                  {"kind": "log", "node": "p", "message": "preview: secret text"}):
        graph.feed({**event, "slug": "chat"})
    assert seen() == []


def test_a_log_node_echo_prints_summarized(lines):
    graph, _clock, seen = lines
    graph.feed({"kind": "log", "node": "log1", "message": "x", "slug": "chat",
                "echo": "log: " + "y" * 500})
    (tag, tone, text), = seen()
    assert (tag, tone) == ("chat", "info")
    assert text.startswith("log: yyy") and text.endswith("chars)") and len(text) <= 200


def test_repeats_are_rate_limited_and_counted(lines):
    graph, clock, seen = lines
    error = {"kind": "node_error", "node": "LLM", "error": "E", "slug": "chat"}
    for _ in range(20):  # a burst: the first five print
        graph.feed(dict(error))
    assert len(seen()) == GraphLines.BURST
    clock.now += 1.0  # a second later one more may print, with the count
    graph.feed(dict(error))
    assert seen()[-1][2] == "LLM: E (15 similar lines skipped)"


def test_power_off_reports_what_was_held_back(lines):
    graph, _clock, seen = lines
    for _ in range(8):
        graph.feed({"kind": "node_error", "node": "LLM", "error": "E", "slug": "chat"})
    graph.feed({"kind": "status", "power": "off", "slug": "chat"})
    assert seen()[-2:] == [("chat", "info", "LLM: 3 more errors skipped"), ("chat", "info", "power off")]


def test_limits_are_per_node_and_per_graph(lines):
    graph, _clock, seen = lines
    for node in ("a", "b"):
        for slug in ("one", "two"):
            for _ in range(GraphLines.BURST):
                graph.feed({"kind": "node_error", "node": node, "error": "E", "slug": slug})
    assert len(seen()) == 4 * GraphLines.BURST
