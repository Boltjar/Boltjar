"""The terminal console: capability detection, painting, the banner with the
boot checklist, the side-accent blocks, the log line format and the live graph
lines."""
from __future__ import annotations

import io
import logging
import re
import sys
import time

import pytest

from boltjar import banner_art, console, secrets
from boltjar.console import Caps, Console, GraphLines, LogFormatter, Row, Style

SGR = re.compile(r"\x1b\[([0-9;]*)m")


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


COLOUR = {tier: Caps(tty=True, vt=True, color=tier, unicode=True) for tier in ("truecolor", "256", "16")}


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


def test_rgb_to_16_picks_the_nearest_ansi_colour():
    assert console.rgb_to_16(0x06, 0xF2, 0xF9) == 96  # the logo cyan: bright cyan
    assert console.rgb_to_16(0x36, 0x51, 0xFA) == 94  # the logo indigo: bright blue
    assert console.rgb_to_16(250, 10, 10) == 91


def test_style_writes_each_tier():
    assert Style(COLOUR["truecolor"])("ok", "good") == "\x1b[38;2;74;222;128mok\x1b[0m"
    assert Style(COLOUR["256"])("ok", "good").startswith("\x1b[38;5;")
    assert Style(COLOUR["16"])("ok", "good") == "\x1b[32mok\x1b[0m"
    assert Style(Caps())("ok", "good", bold=True) == "ok"


def test_a_role_on_16_colours_keeps_its_meaning():
    # the slate rule would be nearest to black, which vanishes on a dark theme
    assert Style(COLOUR["16"])("x", "rule") == "\x1b[90mx\x1b[0m"
    assert Style(COLOUR["16"])("x", "soft", bold=True) == "\x1b[1;39mx\x1b[0m"


def test_emphasis_is_bold_in_the_terminal_foreground():
    assert Style(COLOUR["truecolor"])("Boltjar", bold=True) == "\x1b[1mBoltjar\x1b[0m"


def test_links_fall_back_to_the_plain_url():
    url = "http://127.0.0.1:8770"
    assert Style(Caps()).link(url) == url
    linked = Style(Caps(tty=True, vt=True, color="16", links=True)).link(url)
    assert linked == f"\x1b]8;;{url}\x1b\\{url}\x1b]8;;\x1b\\"
    assert console.visible_len(linked) == len(url)


def test_status_glyphs_and_their_ascii_set():
    unicode, ascii_ = Style(Caps(unicode=True)), Style(Caps(unicode=False))
    assert [unicode.glyph(t) for t in ("ok", "warn", "bad", "info")] == ["✓", "!", "✗", "ℹ"]
    assert [ascii_.glyph(t) for t in ("ok", "warn", "bad", "info")] == ["+", "!", "x", "i"]


def test_status_colours():
    s = Style(COLOUR["truecolor"])
    assert s.glyph("ok") == "\x1b[38;2;74;222;128m✓\x1b[0m"
    assert s.glyph("warn") == "\x1b[38;2;234;179;8m!\x1b[0m"
    assert s.glyph("bad") == "\x1b[38;2;239;68;68m✗\x1b[0m"
    assert s.glyph("info") == "\x1b[38;2;96;165;250mℹ\x1b[0m"


# ---------------------------------------------------------------- the side accent
def test_bars_run_down_the_logo_gradient_and_back():
    positions = [console.bar_position(i) for i in range(10)]
    assert positions[:5] == pytest.approx([0.15, 0.35, 0.55, 0.75, 0.95])
    assert positions[5:10] == pytest.approx([0.75, 0.55, 0.35, 0.15, 0.35])
    assert console.gradient(0) == (0x06, 0xF2, 0xF9)
    assert console.gradient(1) == (0x36, 0x51, 0xFA)


def test_a_bar_is_a_half_block_in_the_gradient_colour():
    top = console.gradient(0.15)
    assert Style(COLOUR["truecolor"]).bar(0) == "\x1b[38;2;{};{};{}m▌\x1b[0m ".format(*top)
    assert Style(Caps(unicode=False)).bar(0) == "| "


def test_a_section_rule_is_the_title_between_dashes():
    rule = Style(Caps(unicode=True)).rule("Graphs")
    assert rule == "── Graphs " + "─" * 38 and len(rule) == console.RULE_WIDTH
    assert Style(Caps(unicode=False)).rule("Graphs").startswith("-- Graphs ---")
    noted = Style(Caps(unicode=True)).rule("Graphs", "times in UTC+10:00")
    assert noted.startswith("── Graphs  times in UTC+10:00 ─") and len(noted) == console.RULE_WIDTH


def test_a_section_rule_paints_its_title_blue_and_its_dashes_slate():
    rule = Style(COLOUR["truecolor"]).rule("Graphs")
    assert "\x1b[1;38;2;59;130;246mGraphs\x1b[0m" in rule
    assert "\x1b[38;2;51;65;85m─" in rule


def plain_console(width: int = 120) -> tuple[Console, FakeStream]:
    stream = FakeStream(tty=False)
    return Console(stream, Caps(unicode=True), width=width), stream


def test_the_ready_block():
    out, stream = plain_console()
    out.ready("http://127.0.0.1:8770", "opening your browser · Ctrl+C stops everything")
    assert stream.getvalue().splitlines() == [
        "▌ Ready  →  http://127.0.0.1:8770",
        "▌ opening your browser · Ctrl+C stops everything",
    ]


def test_the_ready_url_is_a_link_where_the_terminal_makes_one():
    stream = FakeStream()
    out = Console(stream, Caps(tty=True, vt=True, color="truecolor", unicode=True, links=True), width=120)
    out.ready("http://127.0.0.1:8770", "note")
    first = stream.getvalue().splitlines()[0]
    assert "\x1b[1;38;2;0;229;255mReady\x1b[0m" in first  # bold brand cyan
    assert "\x1b]8;;http://127.0.0.1:8770\x1b\\\x1b[1mhttp://127.0.0.1:8770\x1b[0m\x1b]8;;\x1b\\" in first


def test_a_section_opens_a_new_block():
    out, stream = plain_console()
    out.section("Shutdown")
    out.line("stop", "stopping 1 graph")
    out.line("ok", "1 graph turned off", hint="a dim hint")
    out.goodbye()
    assert stream.getvalue().splitlines() == [
        "",
        "── Shutdown " + "─" * 36,
        "▌ ▶ stopping 1 graph",
        "▌ ✓ 1 graph turned off",
        "▌   a dim hint",
        "▌ bye",
    ]


def test_bye_is_brand_cyan():
    stream = FakeStream()
    Console(stream, COLOUR["truecolor"], width=120).goodbye()
    assert "\x1b[1;38;2;0;229;255mbye\x1b[0m" in stream.getvalue()


def test_pending_spins_only_on_a_terminal_that_can_redraw_and_leaves_nothing():
    quiet, stream = plain_console()
    with quiet.pending("loading the node packs"):
        time.sleep(0.2)
    assert stream.getvalue() == ""

    stream = FakeStream(tty=True)
    out = Console(stream, Caps(tty=True, vt=True, color="none", unicode=True), width=120)
    with out.pending("loading the node packs"):
        time.sleep(0.35)  # long enough for frames to draw
    text = stream.getvalue()
    assert "\x1b[?25l" in text and "loading the node packs" in text
    assert text.endswith("\r\x1b[2K\x1b[?25h")  # the line wiped, the cursor back


# ---------------------------------------------------------------- the banner
ROWS = [
    Row("Python", "3.12.9", aside=".venv", gap="  "),
    Row("Editor", "bundle ready"),
    Row("Port", "8770 free"),
    Row("Packs", "core", aside="(63 nodes)"),
]


HEADING = "v0.1.0  Catch the spark. Keep it running."
# the widest window that still leaves the flask out (its last column stays free)
NO_FLASK = 2 + max(map(len, banner_art.FLASK)) + console.ART_GAP + len(HEADING)


def banner(width: int, rows=ROWS, caps: Caps | None = None) -> list[str]:
    out = Console(FakeStream(tty=False), caps or Caps(unicode=True), width=width)
    return out.banner_lines("0.1.0", rows)


def test_the_art_data_has_a_colour_for_every_character():
    for rows, colors in ((banner_art.FLASK, banner_art.FLASK_RGB), (banner_art.WORD, banner_art.WORD_RGB)):
        assert len(rows) == len(colors)
        for row, hexes in zip(rows, colors):
            assert len(hexes) == len(row)
            assert all(bool(hx) == (ch != " ") for ch, hx in zip(row, hexes))
            assert all(re.fullmatch(r"[0-9A-F]{6}", hx) for hx in hexes if hx)


def test_a_wide_window_shows_the_flask_beside_the_word_and_the_checklist():
    lines = banner(120)
    (word_line,) = [line for line in lines if line.endswith(banner_art.WORD[2])]
    assert any(word_line.startswith("  " + row + " ") for row in banner_art.FLASK)
    assert any(line.endswith("v0.1.0  Catch the spark. Keep it running.") for line in lines)
    assert any(line.endswith("✓ Python     3.12.9  .venv") for line in lines)
    assert any(line.endswith("✓ Packs      core (63 nodes)") for line in lines)
    # the flask and the word start on the same row, under the blank that opens the banner
    assert lines[1].startswith("  " + banner_art.FLASK[0]) and lines[1].endswith(banner_art.WORD[0].rstrip())


def test_the_word_is_followed_by_a_blank_the_version_a_blank_and_the_checklist():
    column = 2 + max(map(len, banner_art.FLASK)) + console.ART_GAP
    lines = [line[column:] for line in banner(120)]
    start = lines.index(banner_art.WORD[0])
    word = len(banner_art.WORD)
    assert lines[start:start + word] == banner_art.WORD
    assert lines[start + word] == ""
    assert lines[start + word + 1] == "v0.1.0  Catch the spark. Keep it running."
    assert lines[start + word + 2] == ""
    assert lines[start + word + 3].startswith("✓ Python")


def test_a_window_too_narrow_for_the_flask_keeps_the_word_and_the_checklist():
    lines = banner(NO_FLASK)
    assert not any(banner_art.FLASK[5].strip() in line for line in lines)
    assert "  " + banner_art.WORD[3] in lines
    assert "  ✓ Editor     bundle ready" in lines


def test_a_window_narrower_still_gets_one_plain_line():
    lines = banner(40)
    assert not any(banner_art.WORD[3] in line for line in lines)
    assert "  Boltjar v0.1.0" in lines
    assert "  ✓ Port       8770 free" in lines


BUILD = "npm run build --prefix editor"
EDITOR_FIX = Row("Editor", "not built", "warn", fixes=(
    f"{console.unbroken('npm ci --prefix editor')}, then {console.unbroken(BUILD)} (needs Node.js 18+)",))


@pytest.mark.parametrize("width", [40, 43, 44, 60, 80, 89, 90, 91, 120, 200])
def test_no_banner_line_reaches_the_last_column(width):
    long = Row("Packs", "core; failed: " + ", ".join(f"pack-{i}" for i in range(12)), "warn",
               aside="(63 nodes)", fixes=("the reason is in the log line above, and at /api/packs",))
    lines = banner(width, [*ROWS, EDITOR_FIX, long])
    assert max(console.visible_len(line) for line in lines) < width


@pytest.mark.parametrize("width", [60, 80, 96, 120, 200])
def test_a_command_in_a_fix_stays_on_one_line(width):
    # at 120 columns the build command used to wrap as `npm run build --prefix` / `editor`
    lines = banner(width, [*ROWS, EDITOR_FIX])
    assert any(BUILD in line for line in lines)
    assert "\xa0" not in "".join(lines)  # it prints with plain spaces, so it pastes


def test_a_command_wider_than_the_column_breaks_at_its_spaces():
    lines = banner(40, [*ROWS, EDITOR_FIX])  # a 24 column checklist, the command is 29
    assert max(console.visible_len(line) for line in lines) < 40
    assert BUILD in " ".join(" ".join(lines).split()) and "\xa0" not in "".join(lines)


def test_the_flask_needs_room_for_the_whole_column_beside_it():
    flask = max(map(len, banner_art.FLASK))
    fits = 2 + flask + console.ART_GAP + len(HEADING) + 1  # the last column stays free
    assert any(banner_art.FLASK[5] in line for line in banner(fits))
    assert not any(banner_art.FLASK[5] in line for line in banner(fits - 1))


def test_a_checklist_row_aligns_and_carries_its_fixes():
    rows = [Row("Port", "8770 is in use", "bad", fixes=("use another port: --port 8771",))]
    assert banner(NO_FLASK, rows)[-3:] == [
        "  ✗ Port       8770 is in use",
        "               fix: use another port: --port 8771",
        "",
    ]


def test_an_aside_that_does_not_fit_takes_its_own_line():
    rows = [Row("Packs", "core; failed: " + "x" * 40, "warn", aside="(63 nodes)")]
    assert banner(NO_FLASK, rows)[-3:] == [f"  ! Packs      core; failed: {'x' * 40}", "               (63 nodes)", ""]


def test_the_flask_steps_aside_before_a_link_beside_it_would_break():
    link = "http://studio.example:9001/?token=<token>"
    rows = [*ROWS, Row("Remote", "0.0.0.0:9001", "warn", fixes=(f"open {link} there",))]
    beside = 2 + max(map(len, banner_art.FLASK)) + console.ART_GAP + 2 + console.LABEL_WIDTH
    narrow = beside + len(link)  # one column short of room for the link beside the flask
    for width in (narrow, narrow + 1):
        lines = banner(width, rows)
        assert any(link in line for line in lines)
        assert any(banner_art.FLASK[5] in line for line in lines) == (width > narrow)


def test_asides_and_fixes_are_dim_and_the_version_bold_soft_white():
    text = "\n".join(banner(120, [Row("Python", "3.12.9", "warn", aside=".venv", gap="  ",
                                      fixes=("run it again",))], COLOUR["truecolor"]))
    assert "\x1b[2m.venv\x1b[0m" in text
    assert "\x1b[2mfix: run it again\x1b[0m" in text
    assert "\x1b[1;38;2;248;250;252mv0.1.0\x1b[0m  \x1b[2mCatch the spark. Keep it running.\x1b[0m" in text


@pytest.mark.parametrize("tier, allowed", [
    ("truecolor", r"38;2;\d+;\d+;\d+"),
    ("256", r"38;5;\d+"),
    ("16", r"3[0-7]|9[0-7]"),
])
def test_the_art_steps_down_to_the_terminals_colours(tier, allowed):
    painted = Style(COLOUR[tier]).art(banner_art.WORD, banner_art.WORD_RGB)
    codes = {code for row in painted for code in SGR.findall(row)} - {"0"}
    assert codes and all(re.fullmatch(allowed, code) for code in codes)
    assert [console.strip_ansi(row) for row in painted] == banner_art.WORD


def test_the_art_shares_an_escape_across_a_run_of_one_colour():
    # 16 colours give a handful of runs per row, not an escape per character
    painted = Style(COLOUR["16"]).art(banner_art.FLASK, banner_art.FLASK_RGB)
    assert max(len(SGR.findall(row)) for row in painted) < 8


def test_without_colour_the_art_is_the_plain_text():
    assert Style(Caps()).art(banner_art.FLASK, banner_art.FLASK_RGB) == banner_art.FLASK


def test_the_whole_banner_steps_down_with_the_console():
    for tier, banned in (("256", "38;2;"), ("16", "38;")):
        assert banned not in "\n".join(banner(120, caps=COLOUR[tier]))
    assert "\x1b" not in "\n".join(banner(120))


# ---------------------------------------------------------------- log lines
LINE = re.compile(r"^(\S) (\d\d:\d\d:\d\d)  (\S) (.*)$")


def record(msg: str, level: int = logging.INFO, **extra) -> logging.LogRecord:
    rec = logging.LogRecord("boltjar.test", level, __file__, 1, msg, (), None)
    rec.created = 0
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def plain_formatter(verbose: bool = False) -> LogFormatter:
    fmt = LogFormatter(verbose=verbose, stream=FakeStream(tty=False))
    fmt.style = Style(Caps(unicode=True))
    return fmt


@pytest.fixture(autouse=True)
def no_zone_shown(monkeypatch):
    """No Graphs rule has named a zone yet, and none a test names outlives it."""
    monkeypatch.setattr(console, "_zone_shown", None)


def test_log_line_is_bar_time_glyph_message():
    bar, stamp, glyph, rest = LINE.match(plain_formatter().format(record("ready", tone="ok"))).groups()
    assert (bar, glyph, rest) == ("▌", "✓", "ready")
    assert stamp == time.strftime("%H:%M:%S", time.localtime(0))


def test_levels_map_to_the_editor_tones():
    fmt = plain_formatter()
    assert fmt.tone(record("x", logging.INFO)) == "info"
    assert fmt.tone(record("x", logging.WARNING)) == "warn"
    assert fmt.tone(record("x", logging.ERROR)) == "bad"
    assert fmt.tone(record("x", logging.DEBUG)) == "debug"
    assert fmt.tone(record("x", logging.INFO, tone="ok")) == "ok"


def test_the_time_is_dim():
    fmt = LogFormatter(stream=FakeStream())
    fmt.style = Style(COLOUR["truecolor"])
    assert f"\x1b[2m{time.strftime('%H:%M:%S', time.localtime(0))}\x1b[0m" in fmt.format(record("x"))


def _raise_here():
    raise ValueError("the reason")


def test_an_error_is_one_line_with_a_location_and_a_hint():
    try:
        _raise_here()
    except ValueError:
        rec = record("could not load", logging.ERROR, exc_info=sys.exc_info())
    lines = plain_formatter().format(rec).splitlines()
    assert lines[0].endswith("could not load: ValueError: the reason")
    assert lines[1].lstrip("▌ ") == \
        f"at tests/test_console.py:{_raise_here.__code__.co_firstlineno + 1} in _raise_here"
    assert "--verbose" in lines[2]
    assert all(line.startswith("▌ ") for line in lines)
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


# every control character but the line break the formatter writes itself
RAW_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def test_a_message_never_drives_the_terminal():
    out = plain_formatter().format(record("got \x1b[2J\x1b[1A\x9b2Kforged\x07 line\rReady"))
    assert not RAW_CONTROL.search(out)
    assert out.splitlines() == [
        f"▌ {time.strftime('%H:%M:%S', time.localtime(0))}  ℹ got \\x1b[2J\\x1b[1A\\x9b2Kforged\\x07 line",
        "▌ " + " " * 12 + "Ready",
    ]


def test_an_exception_never_drives_the_terminal():
    try:
        raise RuntimeError("401 from the API:\n\x1b]52;c;SGk=\x07 body")
    except RuntimeError:
        rec = record("could not stop graph chat cleanly", logging.ERROR, exc_info=sys.exc_info())
    short = plain_formatter().format(rec)
    assert not RAW_CONTROL.search(short)
    assert short.splitlines()[0].endswith("RuntimeError: 401 from the API: \\x1b]52;c;SGk=\\x07 body")
    assert not RAW_CONTROL.search(plain_formatter(verbose=True).format(rec))


def test_a_graph_line_never_drives_the_terminal():
    rec = graph_record("bad", "n\x1b[2J", "E\x1b[1A", hints=["h\x07"])
    rec.tag = "g\x9b2K"
    out = plain_formatter().format(rec)
    assert not RAW_CONTROL.search(out)
    assert "g\\x9b2K" in out and "n\\x1b[2J" in out and "E\\x1b[1A" in out and "h\\x07" in out


def test_an_access_line_never_drives_the_terminal():
    fmt = console.AccessFormatter(stream=FakeStream(tty=False))
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5000", "GET", "/x\x1b[2J", "1.1", 404), None)
    out = fmt.format(rec)
    assert not RAW_CONTROL.search(out) and out.endswith(" 404 GET /x\\x1b[2J")


def test_an_access_line_hides_the_token_of_the_editor_link():
    fmt = console.AccessFormatter(stream=FakeStream(tty=False))
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("192.168.1.30:5000", "GET", "/?token=abcDEF123_-xyz", "1.1", 303), None)
    out = fmt.format(rec)
    assert "abcDEF123" not in out and out.endswith(" 303 GET /?token=<token>")


@pytest.mark.parametrize("path, shown", [
    ("/api/graphs", "/api/graphs"),
    ("/?a=1&token=abc&b=2", "/?a=1&token=<token>&b=2"),
    ("/?%74oken=abc", "/?%74oken=<token>"),  # the server decodes the name too
    ("/?tokens=abc&token", "/?tokens=abc&token"),
])
def test_the_token_is_found_by_the_name_the_server_reads(path, shown):
    from boltjar import security

    assert console._LINK_PARAM == security.LINK_PARAM
    assert console._mask_token(path) == shown


@pytest.mark.parametrize("verbose", [False, True])
def test_a_log_line_shows_a_known_secret_as_its_token(monkeypatch, verbose):
    key = "sk-test-0123456789abcdef"
    monkeypatch.setitem(secrets._store, "CONSOLE_TEST_KEY", key)
    try:
        raise RuntimeError(f"401 for https://api.example.com/v1?key={key}")
    except RuntimeError:
        rec = record(f"sent {key}", logging.ERROR, exc_info=sys.exc_info())
    out = plain_formatter(verbose).format(rec)
    assert key not in out and out.count("{{secret.CONSOLE_TEST_KEY}}") == 2


def test_log_config_is_quiet_unless_verbose():
    quiet, loud = console.log_config(False), console.log_config(True)
    assert quiet["loggers"]["uvicorn.access"]["level"] == "WARNING"
    assert quiet["loggers"]["uvicorn.error"]["level"] == "WARNING"
    assert loud["loggers"]["uvicorn.access"]["level"] == "INFO"
    assert quiet["loggers"]["boltjar"]["level"] == "INFO"


def test_local_zone_names_the_offset():
    zone = console.local_zone()
    assert zone.startswith("UTC") and zone[3] in "+-" and zone[6] == ":"
    assert console.local_zone(time.time()) == zone


def test_a_zone_change_while_running_prints_the_new_zone_once(monkeypatch):
    # daylight saving starts at t=2: the stamps move an hour, and the rule's
    # note would be wrong from then on without a line that says so
    def zone(when=None):
        return "UTC+11:00" if (when or 0) >= 2 else "UTC+10:00"

    def stamp(when):
        return time.strftime("%H:%M:%S", time.localtime(when))

    monkeypatch.setattr(console, "local_zone", zone)
    fmt = plain_formatter()
    early = record("before the rule")
    early.created = 5
    assert "times now" not in fmt.format(early)  # no rule has named a zone yet
    assert console.times_note() == "times in UTC+10:00"
    lines = []
    for created in (1, 2, 3):
        rec = record(f"at {created}")
        rec.created = created
        lines += fmt.format(rec).splitlines()
    assert lines == [
        f"▌ {stamp(1)}  ℹ at 1",
        "▌ times now in UTC+11:00",
        f"▌ {stamp(2)}  ℹ at 2",
        f"▌ {stamp(3)}  ℹ at 3",
    ]


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

    def seen() -> list[tuple]:
        return [(r.tag, r.tone, r.event, r.detail) for r in caplog.records]

    return graph, clock, seen, caplog


def test_power_changes_and_failures_become_lines(lines):
    graph, _clock, seen, caplog = lines
    graph.feed({"kind": "status", "power": "on", "slug": "chat"})  # the count comes next
    graph.feed({"kind": "live_graph", "nodes": ["a", "b", "c"], "edges": [], "slug": "chat"})
    graph.feed({"kind": "invalid", "slug": "chat",
                "problems": [{"node": "x", "message": "required input 'in' is not connected"}]})
    graph.feed({"kind": "node_error", "node": "LLM", "error": "RuntimeError('boom')", "slug": "chat"})
    graph.feed({"kind": "status", "power": "off", "slug": "chat"})
    graph.feed({"kind": "live_graph", "nodes": [], "edges": [], "slug": "chat"})
    graph.feed({"kind": "error", "error": "ValueError('unknown node type: x')", "slug": "chat"})
    assert seen() == [
        ("chat", "ok", "On", "3 nodes"),
        ("chat", "warn", "On", "refused: 1 problem"),
        ("chat", "bad", "LLM", "RuntimeError('boom')"),
        ("chat", "info", "Off", ""),
        ("chat", "bad", "On", "failed: ValueError('unknown node type: x')"),
    ]
    assert [r.glyph for r in caplog.records] == ["on", "warn", "bad", "off", "bad"]


def test_a_refused_power_on_lists_its_problems_as_hints(lines):
    graph, _clock, _seen, caplog = lines
    problems = [{"node": f"n{i}", "message": f"problem {i}"} for i in range(5)]
    graph.feed({"kind": "invalid", "slug": "chat", "problems": problems})
    (rec,) = caplog.records
    assert rec.detail == "refused: 5 problems"
    assert rec.hints == ["n0: problem 0", "n1: problem 1", "n2: problem 2", "and 2 more, listed in the editor"]


def test_a_graph_line_reads_on_its_own_without_the_console(lines):
    graph, _clock, _seen, caplog = lines
    graph.feed({"kind": "invalid", "slug": "chat", "problems": [{"node": "x", "message": "m"}]})
    assert caplog.records[0].getMessage() == "On  refused: 1 problem\nx: m"


def test_wire_values_are_never_printed(lines):
    graph, _clock, seen, _caplog = lines
    for event in ({"kind": "value", "node": "a", "port": "out", "value": "secret text"},
                  {"kind": "node_status", "node": "a", "status": "running"},
                  {"kind": "tool_call", "node": "t", "args": {"q": "secret"}},
                  {"kind": "log", "node": "p", "message": "preview: secret text"}):
        graph.feed({**event, "slug": "chat"})
    assert seen() == []


def test_a_log_node_echo_prints_its_message_summarized(lines):
    graph, _clock, seen, _caplog = lines
    graph.feed({"kind": "log", "node": "log1", "message": "log: " + "y" * 500, "slug": "chat",
                "echo": True})
    (tag, tone, event, detail), = seen()
    assert (tag, tone, event) == ("chat", "info", "Log")
    assert detail.startswith("log: yyy") and detail.endswith("chars)") and len(detail) <= 200


def test_a_log_node_echo_reaches_the_terminal_as_escapes():
    # the whole path a Log node's message takes: GraphLines, then the console handler
    stream = FakeStream(tty=False)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(plain_formatter())
    logger = logging.getLogger("test.graph.escapes")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        GraphLines(logger).feed({"kind": "log", "node": "log1", "slug": "chat", "echo": True,
                                 "message": "log: \x1b[1A\x1b[2KReady \x1b]52;c;SGk=\x07"})
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert not RAW_CONTROL.search(out.replace("\n", ""))
    assert "log: \\x1b[1A\\x1b[2KReady \\x1b]52;c;SGk=\\x07" in out


def test_only_a_true_echo_prints(lines):
    graph, _clock, seen, _caplog = lines
    for echo in ("log: a copy of the value", 1, None):
        graph.feed({"kind": "log", "node": "log1", "message": "log: x", "slug": "chat", "echo": echo})
    assert seen() == []


def test_repeats_are_rate_limited_and_counted(lines):
    graph, clock, seen, _caplog = lines
    error = {"kind": "node_error", "node": "LLM", "error": "E", "slug": "chat"}
    for _ in range(20):  # a burst: the first five print
        graph.feed(dict(error))
    assert len(seen()) == GraphLines.BURST
    clock.now += 1.0  # a second later one more may print, with the count
    graph.feed(dict(error))
    assert seen()[-1][3] == "E (15 similar lines skipped)"


def test_off_prints_only_for_a_graph_that_was_on(lines):
    graph, _clock, seen, _caplog = lines
    off = {"kind": "status", "power": "off"}
    graph.feed({**off, "slug": "idle"})  # an Off sent to a graph that never ran
    graph.feed({"kind": "live_graph", "nodes": ["a"], "edges": [], "slug": "chat"})
    graph.feed({**off, "slug": "chat"})
    graph.feed({**off, "slug": "chat"})  # and again, once it is already off
    assert seen() == [("chat", "ok", "On", "1 node"), ("chat", "info", "Off", "")]


def test_a_failed_power_on_leaves_nothing_to_turn_off(lines):
    graph, _clock, seen, _caplog = lines
    graph.feed({"kind": "live_graph", "nodes": ["a"], "edges": [], "slug": "chat"})
    for _ in range(8):  # the running graph's errors, three of them held back
        graph.feed({"kind": "node_error", "node": "LLM", "error": "E", "slug": "chat"})
    graph.feed({"kind": "error", "error": "ValueError('x')", "slug": "chat"})  # a Restart that failed
    graph.feed({"kind": "status", "power": "off", "slug": "chat"})
    assert seen()[-2:] == [("chat", "info", "LLM", "3 more errors skipped"),
                           ("chat", "bad", "On", "failed: ValueError('x')")]


def test_power_off_reports_what_was_held_back(lines):
    graph, _clock, seen, _caplog = lines
    graph.feed({"kind": "live_graph", "nodes": ["LLM"], "edges": [], "slug": "chat"})
    for _ in range(8):
        graph.feed({"kind": "node_error", "node": "LLM", "error": "E", "slug": "chat"})
    graph.feed({"kind": "status", "power": "off", "slug": "chat"})
    assert seen()[-2:] == [("chat", "info", "LLM", "3 more errors skipped"), ("chat", "info", "Off", "")]


def test_limits_are_per_node_and_per_graph(lines):
    graph, _clock, seen, _caplog = lines
    for node in ("a", "b"):
        for slug in ("one", "two"):
            for _ in range(GraphLines.BURST):
                graph.feed({"kind": "node_error", "node": node, "error": "E", "slug": slug})
    assert len(seen()) == 4 * GraphLines.BURST


def graph_record(tone: str, event: str, detail: str = "", glyph: str | None = None, hints=()):
    return record(f"{event}  {detail}", console._TONE_LEVELS[tone], tone=tone, tag="chat",
                  event=event, detail=detail, glyph=glyph or tone, hints=list(hints))


def test_a_graph_line_lines_up_in_columns():
    fmt = plain_formatter()
    stamp = time.strftime("%H:%M:%S", time.localtime(0))
    assert fmt.format(graph_record("ok", "On", "17 nodes", "on")) == f"▌ {stamp}  ● chat        On   17 nodes"
    assert fmt.format(graph_record("info", "Off", glyph="off")) == f"▌ {stamp}  ○ chat        Off"
    assert fmt.format(graph_record("bad", "LLM", "Ollama not reachable", hints=["start Ollama"])).splitlines() == [
        f"▌ {stamp}  ✗ chat        LLM  Ollama not reachable",
        "▌ " + " " * 24 + "start Ollama",
    ]


def test_a_graph_line_names_the_graph_bold_and_dims_a_quiet_detail():
    fmt = plain_formatter()
    fmt.style = Style(COLOUR["truecolor"])
    on = fmt.format(graph_record("ok", "On", "17 nodes", "on"))
    assert "\x1b[1mchat\x1b[0m" in on and "\x1b[2m17 nodes\x1b[0m" in on
    error = fmt.format(graph_record("bad", "LLM", "Ollama not reachable"))
    assert "Ollama not reachable" in error and "\x1b[2mOllama" not in error
