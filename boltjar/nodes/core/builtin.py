"""
boltjar.nodes.core.builtin: the core node pack.

Lean nodes: content is a node, the engine is lean. Data nodes are `pulled`
(evaluated on demand; the Template also fires on its trigger); work/actor nodes
fire on a trigger input (marked `trigger=True`) and pull their data inputs (see
boltjar.runtime).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sqlite3

from simpleeval import SimpleEval, DEFAULT_FUNCTIONS

from boltjar.sdk import node, Kind, NodeFailure, Port, Widget, code, model, select, slider, tmpl
from boltjar import endpoints, model_discovery, models
from boltjar.secrets import resolve_secrets

_log = logging.getLogger(__name__)

# Sandboxed expression evaluation for Compute / Logic (never Python eval()).
_EVAL_FUNCTIONS = dict(DEFAULT_FUNCTIONS)
_EVAL_FUNCTIONS.update({
    "len": len, "min": min, "max": max, "round": round, "sum": sum, "abs": abs,
    "str": str, "int": int, "float": float, "bool": bool, "lower": str.lower,
    "upper": str.upper,
})


def _safe_eval(expression: str, names: dict):
    ev = SimpleEval(functions=_EVAL_FUNCTIONS)
    ev.names = names
    return ev.eval(expression)


# ============================================================ values (pulled)
@node(id="core.value.text", name="Text", kind=Kind.VALUE, category="Values",
      pulled=True, summary="A constant text block.",
      icon="text-outline", subline="text · {text|clip:16|or:empty}")
class Text:
    text: Widget = code("", expand=True)
    outputs = [Port("out", "text")]

    def value(self):
        return {"out": self.text}


@node(id="core.value.integer", name="Integer", kind=Kind.VALUE, category="Values",
      pulled=True, summary="A constant integer.",
      icon="calculator-outline", subline="int · {number}")
class Integer:
    number: int = 0
    outputs = [Port("out", "int")]

    def value(self):
        return {"out": int(self.number)}


@node(id="core.value.float", name="Float", kind=Kind.VALUE, category="Values",
      pulled=True, summary="A constant float.",
      icon="calculator-outline", subline="float · {number}")
class Float:
    number: float = 0.0
    outputs = [Port("out", "float")]

    def value(self):
        return {"out": float(self.number)}


@node(id="core.value.boolean", name="Boolean", kind=Kind.VALUE, category="Values",
      pulled=True, summary="A constant boolean.",
      icon="toggle-outline", subline="bool · {on|bool}")
class Boolean:
    on: bool = False
    outputs = [Port("out", "bool")]

    def value(self):
        return {"out": bool(self.on)}


# ---- media sources (Image / Audio) ----
# A local path is read only from inside two folders: the Files sandbox
# (user/data/files, where a relative path lands, as with Read File) and the shipped
# examples/. Anywhere else is refused before anything is read, so a graph (or a
# value wired into `src`) can never turn a media node into a reader for .env.
_MEDIA_ROOTS_LABEL = "user/data/files and examples/"
# standard padded base64; line breaks are allowed (`base64` on Linux wraps at 76).
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")


def _is_base64(text: str) -> bool:
    compact = "".join(text.split())
    return len(compact) % 4 == 0 and _BASE64_RE.fullmatch(compact) is not None


def _is_file(path) -> bool:
    """True for a regular file. A name the OS refuses to look up (ENAMETOOLONG
    on Linux for an over-long component, for one) is no local file."""
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


def _media_file(src: str, node_name: str):
    """The readable file `src` names, or None when there is none (the value then
    passes through as-is: a name with no file behind it). Raises PathEscapeError
    for a path outside the media roots. `src` is never bare base64 here."""
    import pathlib
    from boltjar.file_store import PathEscapeError, within
    from boltjar.server import EXAMPLES_DIR, FILE_STORE, ROOT

    roots = [FILE_STORE.root.resolve(), EXAMPLES_DIR.resolve()]
    raw = pathlib.Path(src)
    if raw.anchor:  # absolute, drive-qualified or a UNC share
        candidates = [raw]
    else:  # relative: the Files sandbox first, then the repo root (examples/...)
        candidates = [roots[0] / raw, ROOT / raw]
    allowed = False
    for cand in candidates:
        try:
            # containment is checked on the lexical path first, so a path outside
            # the roots (a network share included) is never touched on disk.
            if not any(within(r, pathlib.Path(os.path.abspath(cand))) for r in roots):
                continue
            real = cand.resolve()  # then again on the real path (symlinks followed)
        except (OSError, ValueError):
            return None  # not a usable path at all
        if any(within(r, real) for r in roots):
            allowed = True
            if _is_file(real):
                return real
    if allowed:
        return None
    raise PathEscapeError(f"{node_name}: {src!r} is outside the folders a graph may read "
                          f"files from ({_MEDIA_ROOTS_LABEL})")


def _media_value(src: str, node_name: str, mimes: dict, fallback_mime: str) -> str:
    """An Image / Audio source value. An http(s) URL, a data: URL or bare base64
    passes straight through (providers fetch a URL themselves); a local file inside
    the media roots is read into a self-contained data: URL; anything else passes
    through."""
    import base64

    s = (src or "").strip()
    if not s or s.lower().startswith(("http://", "https://", "data:")):
        return s
    # bare base64 is data, not a path, so it never reaches the file system: JPEG's
    # starts '/9j/' and headerless MP3's '//u' (both read as absolute), and a long
    # one is a name Linux refuses (ENAMETOOLONG). A path with an extension is never
    # base64, since '.' is outside the alphabet.
    if _is_base64(s):
        return s
    path = _media_file(s, node_name)
    if path is None:
        return s
    mime = mimes.get(path.suffix.lstrip(".").lower(), fallback_mime)
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


# image extension -> MIME type, for turning a local file path into a data: URL.
_IMAGE_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
}


@node(id="core.value.image", name="Image", kind=Kind.VALUE, category="Values",
      pulled=True, summary="An image source: an https URL, or a local file in "
                           "user/data/files or examples/ (read into a data: URL).")
class Image:
    src: Widget = Widget(kind="text", default="", label="URL or path")
    outputs = [Port("out", "image")]

    def value(self):
        return {"out": _media_value(self.src, "Image", _IMAGE_MIME, "image/jpeg")}


_AUDIO_MIME = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "opus": "audio/ogg",
    "flac": "audio/flac", "m4a": "audio/mp4", "aac": "audio/aac",
}


@node(id="core.value.audio", name="Audio", kind=Kind.VALUE, category="Values",
      pulled=True, summary="An audio source: an https URL, or a local file in "
                           "user/data/files or examples/ (read into a data: URL). "
                           "It works like Image.")
class Audio:
    src: Widget = Widget(kind="text", default="", label="URL or path")
    outputs = [Port("out", "audio")]

    def value(self):
        return {"out": _media_value(self.src, "Audio", _AUDIO_MIME, "audio/wav")}


# ---- inline expression tags -> mood / display text (the avatar mood signal) ----
# A single signal (an inline [bracket] tag the LLM writes) drives both the voice
# expression and the avatar face, so mood/action are COMPOSITION (this node), not
# a hardcoded per-model argument. Maps are editable JSON with neutral defaults.
_TAG_RE = re.compile(r"\[([A-Za-z][A-Za-z\s]*?)\]")
_TAG_EMOJI = {
    "laughing": "😂", "chuckling": "😄", "sobbing": "😭", "crying loudly": "😭",
    "sighing": "😮‍💨", "angry": "😠", "sad": "😢", "excited": "🤩", "embarrassed": "😳",
    "delighted": "😊", "grateful": "🥰", "proud": "😎", "curious": "🤔",
    "confused": "😕", "surprised": "😮", "gasping": "😲", "disappointed": "😞",
    "worried": "😟", "frustrated": "😤", "sarcastic": "😏", "calm": "😌",
    "whispering": "🤫", "soft": "🥺",
}
_DEFAULT_MOODS = (
    '{"laughing":"happy","chuckling":"happy","delighted":"happy","grateful":"happy",'
    '"excited":"excited","proud":"excited","curious":"curious","confused":"curious",'
    '"surprised":"surprised","gasping":"surprised","angry":"angry","frustrated":"angry",'
    '"sad":"sad","sighing":"sad","sobbing":"sad","disappointed":"sad","worried":"sad",'
    '"embarrassed":"playful","sarcastic":"playful"}'
)
_DEFAULT_ACTIONS = '{"wave":"wave","nod":"nod","dance":"dance","point":"point","think":"think"}'
# tag -> priority (higher wins when several mood tags appear), so the strongest
# expression drives the avatar face, not just the first one in text order. Also
# caps which emoji survive when too many appear.
_DEFAULT_PRIORITY = (
    '{"laughing":9,"sobbing":9,"crying loudly":9,"sighing":8,"chuckling":7,'
    '"angry":7,"sad":7,"excited":7,"surprised":7,"delighted":7,"disappointed":7,'
    '"frustrated":7,"embarrassed":6,"gasping":6,"grateful":6,"proud":6,'
    '"curious":6,"confused":6,"worried":6,"sarcastic":6,"whispering":5,'
    '"soft":4,"calm":4}'
)


def _safe_json_obj(text: str, fallback: str) -> dict:
    try:
        v = _json.loads(text or fallback)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


@node(id="core.parse.tags", name="Tag Parse", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Read inline [bracket] tags in text: emit a mood (the "
                           "highest-priority mood tag), the clean display "
                           "text (tags -> emoji, capped, or stripped), and an action.")
class TagParse:
    moods: Widget = Widget(kind="code", default=_DEFAULT_MOODS, label="tag -> mood", expand=True)
    actions: Widget = Widget(kind="code", default=_DEFAULT_ACTIONS, label="tag -> action", expand=True)
    priority: Widget = Widget(kind="code", default=_DEFAULT_PRIORITY, label="tag -> priority", expand=True)
    default: Widget = Widget(kind="text", default="neutral", label="default mood")
    max_emoji: Widget = Widget(kind="number", default=3, min=0, max=20, step=1,
                               label="max emoji (0 = no cap)")
    emoji: Widget = Widget(kind="bool", default=True, label="tags -> emoji in clean text")
    inputs = [Port("text", "text")]
    outputs = [Port("mood", "mood"), Port("clean", "text"), Port("action", "action", optional=True)]

    def run(self, text=None, **_):
        cfg = getattr(self, "_node_cfg", {}) or {}
        s = "" if text is None else str(text)
        moods = _safe_json_obj(cfg.get("moods"), _DEFAULT_MOODS)
        actions = _safe_json_obj(cfg.get("actions"), _DEFAULT_ACTIONS)
        prio = _safe_json_obj(cfg.get("priority"), _DEFAULT_PRIORITY)
        default = str(cfg.get("default") or "neutral")
        max_emoji = int(cfg.get("max_emoji", 3) or 0)

        # every tag with its position (in text order).
        tags = [(m.start(), m.end(), m.group(1).strip().lower()) for m in _TAG_RE.finditer(s)]

        # mood: the highest-priority mood-bearing tag (tie -> earliest).
        best = None  # (priority, -pos, mood)
        action = None
        for pos, _end, tag in tags:
            if tag in moods:
                cand = (int(prio.get(tag, 0)), -pos, str(moods[tag]))
                if best is None or cand > best:
                    best = cand
            if action is None and tag in actions:
                action = str(actions[tag])
        mood = best[2] if best is not None else default

        if cfg.get("emoji", True):
            # which emoji-bearing tags survive the cap (highest priority first).
            emoji_pos = [(pos, tag) for pos, _e, tag in tags if _TAG_EMOJI.get(tag)]
            if max_emoji > 0 and len(emoji_pos) > max_emoji:
                keep = {p for p, _t in sorted(emoji_pos, key=lambda pt: -int(prio.get(pt[1], 0)))[:max_emoji]}
            else:
                keep = {p for p, _t in emoji_pos}
            parts, last = [], 0
            for pos, end, tag in tags:
                parts.append(s[last:pos])
                if pos in keep:
                    parts.append(_TAG_EMOJI.get(tag, ""))
                last = end
            parts.append(s[last:])
            clean = "".join(parts)
        else:
            clean = _TAG_RE.sub("", s)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
        return {"mood": mood, "clean": clean, "action": action}


# ---- text cleaner (TTS and beyond) ---------------------------------------------
# A composable cleaner: each concern is an independent toggle so a graph strips
# exactly what a given sink cannot speak/render. Regexes live at module scope so
# they compile once; the [bracket] tag stripper REUSES Tag Parse's `_TAG_RE` (one
# source of truth for the tag syntax, never a duplicated pattern).
#
# Emoji cover the main Unicode pictograph blocks plus the joiners (ZWJ +
# variation selectors) so a compound glyph (e.g. a face + a puff of breath) is
# removed whole, not left as orphan combiners.
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "\U0001F300-\U0001FAFF"   # symbols/pictographs + supplemental + extended-A (faces, etc.)
    "\U00002600-\U000027BF"   # misc symbols + dingbats
    "\U00002300-\U000023FF"   # misc technical (watch, hourglass, media controls)
    "\U00002B00-\U00002BFF"   # misc symbols and arrows (stars, ...)
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0000200D"              # zero-width joiner
    "]+",
    flags=re.UNICODE,
)
# markdown MARKERS (the text between them is kept; only the syntax is removed).
_MD_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)   # ```lang\n code ``` -> code
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")          # [text](url) -> text
_MD_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")                # `code` -> code
_MD_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)      # **bold** / __bold__ -> bold
_MD_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)")   # *italic* -> italic
_MD_ITALIC_US_RE = re.compile(r"(?<!_)_(?!_)([^_\n]+?)_(?!_)")          # _italic_ -> italic
_MD_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*")   # leading # heading markers
_MD_BLOCKQUOTE_RE = re.compile(r"(?m)^[ \t]{0,3}>[ \t]?")     # leading > blockquote markers
_MD_BULLET_RE = re.compile(r"(?m)^[ \t]{0,3}[-+][ \t]+")     # leading - / + list bullets
# a *stage direction* span (single-asterisk pair): removed WHOLE (text and all),
# unlike italic which keeps its text. Single-asterisk only, so **bold** is left
# for the markdown pass.
_ACTION_STAR_RE = re.compile(r"(?<!\*)\*(?!\*)[^*\n]+?\*(?!\*)")
# http(s):// and bare www. URLs.
_URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)


def _strip_text(s: str, emoji: bool, markdown: bool, tags: bool,
                actions: bool, urls: bool) -> str:
    """Apply the enabled cleaners in a fixed, sensible order, then collapse the
    leftover whitespace. Order note: markdown bold/links/code run first (marker
    removal, text kept); `actions` then removes single-`*` spans WHOLE before the
    markdown italic pass, so a `*sighs*` direction is dropped while a `**word**`
    emphasis survives as `word`: the two asterisk syntaxes are otherwise
    identical and cannot be told apart by regex alone."""
    if markdown:
        s = _MD_FENCE_RE.sub(lambda m: m.group(1), s)
        s = _MD_LINK_RE.sub(lambda m: m.group(1), s)
        s = _MD_INLINE_CODE_RE.sub(lambda m: m.group(1), s)
        s = _MD_BOLD_RE.sub(lambda m: m.group(2), s)
    if tags:
        s = _TAG_RE.sub("", s)
    if actions:
        s = _ACTION_STAR_RE.sub("", s)
    if markdown:
        s = _MD_ITALIC_STAR_RE.sub(lambda m: m.group(1), s)
        s = _MD_ITALIC_US_RE.sub(lambda m: m.group(1), s)
        s = _MD_HEADING_RE.sub("", s)
        s = _MD_BLOCKQUOTE_RE.sub("", s)
        s = _MD_BULLET_RE.sub("", s)
    if urls:
        s = _URL_RE.sub("", s)
    if emoji:
        s = _EMOJI_RE.sub("", s)
    # collapse leftover whitespace: any run of 2+ whitespace chars becomes one
    # space (mirrors Tag Parse's clean), so gaps left by removed markup close up.
    return re.sub(r"\s{2,}", " ", s).strip()


@node(id="core.text.strip", name="Strip", kind=Kind.TRANSFORM, category="Text",
      pulled=True, summary="Clean text for TTS and more: drop emoji, markdown "
                           "markers, [bracket] tags, *action* stage directions and "
                           "URLs. Each cleaner has its own toggle, and the leftover "
                           "whitespace collapses.")
class Strip:
    # every cleaner defaults ON and is promotable to a bool input (drive it from
    # the graph). Reuses Tag Parse's tag regex, so the tag syntax has one owner.
    emoji: Widget = Widget(kind="bool", default=True, label="strip emoji", port_type="bool")
    markdown: Widget = Widget(kind="bool", default=True, label="strip markdown", port_type="bool")
    tags: Widget = Widget(kind="bool", default=True, label="strip [tags]", port_type="bool")
    actions: Widget = Widget(kind="bool", default=True, label="strip *actions*", port_type="bool")
    urls: Widget = Widget(kind="bool", default=True, label="strip urls", port_type="bool")
    inputs = [Port("in", "text")]
    outputs = [Port("out", "text")]

    def run(self, **ins):
        cfg = getattr(self, "_node_cfg", {}) or {}
        raw = ins.get("in")
        s = "" if raw is None else str(raw)
        return {"out": _strip_text(
            s,
            emoji=bool(cfg.get("emoji", True)),
            markdown=bool(cfg.get("markdown", True)),
            tags=bool(cfg.get("tags", True)),
            actions=bool(cfg.get("actions", True)),
            urls=bool(cfg.get("urls", True)),
        )}


# ---- perception: screen / window capture (feed a vision LLM) -------------------
# Each capture is a SEPARATE primitive from analysis: a sensor emits a fresh image
# data-URL on every pull, a vision LLM reads it. win32 is guarded so the window
# primitives degrade to "" off-Windows.
def _resize_encode_image(img, max_dim: int, fmt: str, quality: int) -> str:
    import base64
    import io
    from PIL import Image as _PILImage

    w, h = img.size
    if max_dim > 0 and max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), _PILImage.LANCZOS)
    buf = io.BytesIO()
    if (fmt or "jpeg").lower() == "png":
        img.save(buf, format="PNG")
        mime = "image/png"
    else:
        img.save(buf, format="JPEG", quality=int(quality))
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


def _capture_screen(monitor: str, max_dim: int, fmt: str, quality: int) -> str:
    try:
        import mss
        from PIL import Image as _PILImage

        # mss.MSS() is the v10 entry point; fall back to the deprecated mss.mss()
        # factory on older versions. Either yields a platform screen-grabber.
        _factory = getattr(mss, "MSS", None) or mss.mss
        with _factory() as sct:
            monitors = sct.monitors  # [0] = all combined, [1] = primary, ...
            mon = (monitor or "Primary").strip()
            if mon == "All":
                idx = 0
            elif mon in ("Primary", ""):
                idx = 1
            else:
                try:
                    idx = int(mon)
                except ValueError:
                    idx = 1
            if idx >= len(monitors):
                idx = 1 if len(monitors) > 1 else 0
            shot = sct.grab(monitors[idx])
            img = _PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            return _resize_encode_image(img, max_dim, fmt, quality)
    except Exception:
        return ""  # lean: a capture failure is empty data, never a raised graph error


def _capture_window(hint: str, max_dim: int, fmt: str, quality: int) -> tuple[str, str]:
    """(data_url, matched_title); ("", "") on no match / non-Windows / failure."""
    try:
        import win32con
        import win32gui
        import win32ui
        from PIL import Image as _PILImage

        match: list = []

        def cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if t and hint.lower() in t.lower():
                    match.append((hwnd, t))

        if not (hint or "").strip():
            return "", ""
        win32gui.EnumWindows(cb, None)
        if not match:
            return "", ""
        hwnd, title = match[0]
        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            return "", ""
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        img = _PILImage.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return _resize_encode_image(img, max_dim, fmt, quality), title
    except Exception:
        return "", ""


def _foreground_title() -> str:
    try:
        import win32gui

        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        return ""


def _list_windows() -> list:
    try:
        import win32gui

        out: list = []

        def cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if t and len(t) > 1:
                    out.append(t)

        win32gui.EnumWindows(cb, None)
        return out
    except Exception:
        return []


@node(id="core.sensor.screen", name="Screen Capture", kind=Kind.SENSOR, category="Sensors",
      pulled=True, volatile=True, summary="Grab a fresh screenshot on every pull (a "
                                          "live frame for a vision LLM). Cross-platform.")
class ScreenCapture:
    monitor: Widget = select(["Primary", "1", "2", "3", "All"], default="Primary")
    max_dim: Widget = Widget(kind="number", default=1280, min=320, max=3840, step=10, label="max dimension")
    format: Widget = select(["jpeg", "png"], default="jpeg")
    quality: Widget = Widget(kind="number", default=60, min=10, max=95, step=5, label="jpeg quality")
    outputs = [Port("out", "image")]

    def run(self, **_):
        cfg = getattr(self, "_node_cfg", {}) or {}
        return {"out": _capture_screen(str(cfg.get("monitor") or "Primary"),
                                       int(cfg.get("max_dim") or 1280),
                                       str(cfg.get("format") or "jpeg"),
                                       int(cfg.get("quality") or 60))}


@node(id="core.sensor.window", name="Window Capture", kind=Kind.SENSOR, category="Sensors",
      pulled=True, volatile=True, summary="Grab a specific window by title hint (a fresh "
                                          "frame each pull). Windows only; empty elsewhere.")
class WindowCapture:
    title: Widget = tmpl("", kind="text", port_type="text", placeholder="window title hint (substring)")
    max_dim: Widget = Widget(kind="number", default=1280, min=320, max=3840, step=10, label="max dimension")
    format: Widget = select(["jpeg", "png"], default="jpeg")
    quality: Widget = Widget(kind="number", default=60, min=10, max=95, step=5, label="jpeg quality")
    inputs = [Port("title", "text", optional=True)]
    outputs = [Port("out", "image"), Port("matched", "text", optional=True)]

    def run(self, title=None, **_):
        cfg = getattr(self, "_node_cfg", {}) or {}
        hint = str(title if title is not None else (cfg.get("title") or ""))
        url, matched = _capture_window(hint, int(cfg.get("max_dim") or 1280),
                                       str(cfg.get("format") or "jpeg"),
                                       int(cfg.get("quality") or 60))
        return {"out": url, "matched": matched}


@node(id="core.sensor.foreground", name="Foreground Window", kind=Kind.SENSOR, category="Sensors",
      pulled=True, volatile=True, summary="The focused window's title (and optionally the "
                                          "open-window list). Windows only; empty elsewhere.")
class ForegroundWindow:
    list_all: Widget = Widget(kind="bool", default=False, label="list all windows")
    outputs = [Port("title", "text"), Port("windows", "json", optional=True)]

    def run(self, **_):
        cfg = getattr(self, "_node_cfg", {}) or {}
        return {"title": _foreground_title(),
                "windows": _list_windows() if cfg.get("list_all") else None}


# ============================================================ sensors (volatile)
# Friendly time formats: the dropdown shows an EXAMPLE of each output, and the
# node maps the chosen example to its strftime pattern (portable: %d/%I keep a
# leading zero on every platform, no %-d/%#d split). The example dates use
# 2026-06-08 (a Monday) so weekday examples read true.
_CLOCK_FORMATS = {
    # ISO / numeric
    "2026-06-08 14:30:00":        "%Y-%m-%d %H:%M:%S",
    "2026-06-08 14:30":           "%Y-%m-%d %H:%M",
    "2026-06-08":                 "%Y-%m-%d",
    # day-first
    "08/06/2026":                 "%d/%m/%Y",
    "08/06/2026 14:30":           "%d/%m/%Y %H:%M",
    "08-06-2026":                 "%d-%m-%Y",
    # day + month name (long/short)
    "08 Jun 2026":                "%d %b %Y",
    "08 June 2026":               "%d %B %Y",
    "08 Jun":                     "%d %b",
    "Mon, 08 Jun 2026":           "%a, %d %b %Y",
    "Monday, 08 June 2026":       "%A, %d %B %Y",
    "Monday 08 Jun, 02:30 PM":    "%A %d %b, %I:%M %p",
    # month-first (US)
    "Jun 08, 2026":               "%b %d, %Y",
    "Jun 08, 2026 02:30 PM":      "%b %d, %Y %I:%M %p",
    # time only
    "14:30:00":                   "%H:%M:%S",
    "14:30":                      "%H:%M",
    "02:30 PM":                   "%I:%M %p",
    "02:30:00 PM":                "%I:%M:%S %p",
    # combined day-first + time
    "08 Jun 2026, 02:30 PM":      "%d %b %Y, %I:%M %p",
    "08 Jun, 14:30":              "%d %b, %H:%M",
    "2026-06-08 02:30 PM":        "%Y-%m-%d %I:%M %p",
    # weekday / month name alone
    "Monday":                     "%A",
    "Mon":                        "%a",
    "June":                       "%B",
}


# Common timezones for the Time node. "Local" follows the machine's clock; the
# rest are IANA zone names resolved via the stdlib zoneinfo, ordered by UTC offset
# (west to east). Any other IANA name typed into a saved config still resolves.
_CLOCK_ZONES = [
    "Local", "UTC",
    "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York",
    "America/Sao_Paulo", "Europe/London", "Europe/Paris", "Europe/Berlin",
    "Africa/Johannesburg", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore",
    "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
]


@node(id="core.sensor.clock", name="Time", kind=Kind.SENSOR, category="Services",
      pulled=True, volatile=True, summary="The current time, re-read on every pull. "
                                          "Pick the output format and timezone.",
      icon="time-outline", subline="clock · volatile")
class Clock:
    # the dropdown lists example outputs; pick the shape you want.
    format: Widget = select(list(_CLOCK_FORMATS), default="2026-06-08 14:30:00")
    timezone: Widget = select(_CLOCK_ZONES, default="Local")
    outputs = [Port("out", "text")]

    def run(self, **_):
        import datetime
        cfg = getattr(self, "_node_cfg", {}) or {}
        pattern = _CLOCK_FORMATS.get(cfg.get("format") or "", "%Y-%m-%d %H:%M:%S")
        tz = cfg.get("timezone") or "Local"
        if tz and tz != "Local":
            try:
                from zoneinfo import ZoneInfo
                now = datetime.datetime.now(ZoneInfo(tz))
            except Exception:
                now = datetime.datetime.now()  # unknown zone -> fall back to local
        else:
            now = datetime.datetime.now()
        return {"out": now.strftime(pattern)}


# ============================================================ triggers (sources)
@node(id="core.trigger.interval", name="Interval", kind=Kind.TRIGGER, category="Triggers",
      summary="Fire an event every N seconds.",
      icon="timer-outline", subline="every · {seconds}s")
class Interval:
    seconds: float = 2.0
    outputs = [Port("trigger", "event")]

    async def start(self, ctx):
        n = 0
        while ctx.alive:
            await ctx.sleep(max(0.05, float(self.seconds)))
            n += 1
            ctx.emit("trigger", n)


# A tiny cron matcher (minute granularity), so the Schedule trigger needs no dep.
# Five fields: minute hour day-of-month month day-of-week. Each field is
# `*` | n | a-b | a,b,c | */s (and combinations). Day-of-week is 0-6 with Sunday=0
# (7 also Sunday), the cron convention.
def _cron_field_match(field: str, value: int, lo: int = 0, hi: int = 59) -> bool:
    field = (field or "*").strip()
    if field in ("*", "?"):
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            rng, _, s = part.partition("/")
            try:
                step = max(1, int(s))
            except ValueError:
                step = 1
            part = rng or "*"
        if part == "*":
            a, b = lo, hi   # '*/s' anchors at the field minimum, so '*/2' on a
        elif "-" in part:   # 1-based day field means 1,3,5..., not 2,4,6...
            a_s, _, b_s = part.partition("-")
            try:
                a, b = int(a_s), int(b_s)
            except ValueError:
                continue
        else:
            try:
                a = b = int(part)
            except ValueError:
                continue
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


def _cron_match(expr: str, now) -> bool:
    fields = (expr or "").split()
    if len(fields) != 5:
        return False
    mins, hours, dom, mon, dow = fields
    cron_dow = (now.weekday() + 1) % 7  # python Mon=0..Sun=6 -> cron Sun=0..Sat=6
    if not (_cron_field_match(mins, now.minute, 0, 59)
            and _cron_field_match(hours, now.hour, 0, 23)
            and _cron_field_match(mon, now.month, 1, 12)):
        return False
    # Day-of-week: Sunday is 0 OR 7, so on Sunday also test 7 (so a range like 5-7
    # = Fri,Sat,Sun matches, instead of corrupting the field with a blind replace).
    dow_ok = _cron_field_match(dow, cron_dow, 0, 7) or (cron_dow == 0 and _cron_field_match(dow, 7, 0, 7))
    dom_ok = _cron_field_match(dom, now.day, 1, 31)
    # Standard cron: when BOTH day fields are restricted, the entry matches when
    # EITHER matches (OR); otherwise AND. (POSIX/Vixie semantics.)
    dom_set = dom.strip() not in ("*", "?")
    dow_set = dow.strip() not in ("*", "?")
    if dom_set and dow_set:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def _schedule_tick(now, last_minute, cron: str):
    """Per-poll decision for the Schedule trigger (pure, so it is unit-testable):
    fire iff the current minute differs from the last fired minute AND the cron
    matches. Returns (fire, minute_key)."""
    mk = (now.year, now.month, now.day, now.hour, now.minute)
    return (mk != last_minute and _cron_match(cron, now)), mk


@node(id="core.trigger.schedule", name="Schedule", kind=Kind.TRIGGER, category="Triggers",
      summary="Fire on a cron schedule: 'min hour day month weekday', in the zone "
              "its timezone knob picks. '0 9 * * *' fires daily at 09:00, "
              "'*/15 * * * *' every 15 minutes, '0 9 * * 1' on Mondays at 09:00. When "
              "both day fields are set, either one matches, as in standard cron. It "
              "checks each minute and emits a trigger and the firing time once per "
              "matching minute.")
class Schedule:
    cron: Widget = Widget(kind="text", default="0 * * * *",
                          label="cron (min hour day month weekday)")
    timezone: Widget = select(_CLOCK_ZONES, default="Local")
    outputs = [Port("trigger", "event"), Port("time", "text")]

    async def start(self, ctx):
        import datetime
        last_minute = None
        seeded = False
        while ctx.alive:
            cfg = getattr(self, "_node_cfg", {}) or {}
            tz = cfg.get("timezone") or "Local"
            if tz and tz != "Local":
                try:
                    from zoneinfo import ZoneInfo
                    now = datetime.datetime.now(ZoneInfo(tz))
                except Exception:
                    now = datetime.datetime.now()
            else:
                now = datetime.datetime.now()
            fire, mk = _schedule_tick(now, last_minute, str(cfg.get("cron") or ""))
            if not seeded:
                # skip the power-on minute so an Off/On or Restart inside a minute
                # that already fired before the restart does not double-fire.
                last_minute, seeded = mk, True
            elif fire:
                last_minute = mk  # fire at most once per matching minute
                ctx.emit("time", now.isoformat(timespec="seconds"))
                ctx.emit("trigger", 1)
            await ctx.sleep(15)


@node(id="core.trigger.manual", name="Manual", kind=Kind.TRIGGER, category="Triggers",
      summary="Fires once when the graph turns On, and again whenever you press its "
              "Fire button (a run-button you control).",
      icon="play-circle-outline", subline="manual · once")
class Manual:
    outputs = [Port("trigger", "event")]

    async def start(self, ctx):
        # one kick at startup; the editor fires it again on demand via the server
        # (runtime.fire_manual) when the user presses the node's Fire button.
        ctx.emit("trigger", 1)


@node(id="core.trigger.chat", name="Chat Input", kind=Kind.TRIGGER, category="Triggers",
      summary="Type a message and send it into the live graph.",
      icon="chatbubble-ellipses-outline", subline="chat · on send")
class ChatInput:
    # the send box's hint on the canvas, never an input
    placeholder: Widget = Widget(kind="text", default="Type a message...", promotable=False)
    outputs = [Port("trigger", "event"), Port("text", "text")]

    async def start(self, ctx):
        # passive: the editor injects messages via the server (runtime.send_chat).
        return


@node(id="core.trigger.audio_in", name="Audio Input", kind=Kind.TRIGGER, category="Triggers",
      summary="Stream a microphone clip into the live graph (push-to-talk). Emits "
              "the audio data + a trigger, the way Chat Input emits text. Feed the "
              "audio into an STT node to transcribe it.")
class AudioInput:
    placeholder: str = "Hold to talk..."
    outputs = [Port("trigger", "event"), Port("audio", "audio"), Port("lang", "lang", optional=True)]

    async def start(self, ctx):
        # passive: the client injects clips via the server (runtime.send_audio).
        return


@node(id="core.trigger.webhook", name="Webhook", kind=Kind.TRIGGER, category="Triggers",
      summary="Listen for inbound HTTP calls. Fires when POST/GET hits "
              "{origin}/hook/{workflow}/{path}; emits the body, json, headers, query.")
class WebhookTrigger:
    path: Widget = Widget(kind="text", default="my-hook", label="path")
    method: Widget = select(["ANY", "GET", "POST", "PUT", "PATCH", "DELETE"], default="POST")
    # optional shared-secret header check; the value may reference {{secret.NAME}}
    # (accepts_secrets) but is not a {tag} template.
    secret: Widget = Widget(kind="text", default="", label="X-Webhook-Secret (optional)",
                            accepts_secrets=True)
    outputs = [Port("trigger", "event"),
               Port("body", "text"),
               Port("json", "any"),
               Port("headers", "json"),
               Port("query", "json")]

    async def start(self, ctx):
        # passive: the runtime keeps the instance addressable; the server
        # `/hook/...` route emits onto this node when a request arrives.
        return


# ============================================================ data (pulled)
@node(id="core.data.template", name="Template", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Assemble text from {tag} pipes. Each {tag} is a wired input. "
                           "Fire `trigger` to assemble it: `out` carries the text, then "
                           "`trigger` passes on, so the node it fires reads the finished "
                           "text. A node that reads `out` gets the current turn's text.",
      icon="document-text-outline", subline="template · {template|tags}")
class Template:
    # the Template's own body: its text is the node, so it never becomes an input
    template: Widget = Widget(kind="code", default="{in}", expand=True, promotable=False)
    # `trigger` fires the node and must be wired (Chat History -> Template -> LLM):
    # a fire pulls the tags, assembles, emits `out`, then passes the trigger on.
    # The Template is also pulled, so a node that reads `out` gets this turn's
    # text: the fire's own result when the fire ran first (the turn memo), else
    # assembled at the read. A node fired alongside the Template by the same
    # source therefore never reads the previous turn's text, whichever wire was
    # drawn first. Only a fire passes the trigger on. `trigger` is a declared
    # port, never a {tag}.
    inputs = [Port("trigger", "event", trigger=True),
              Port("tag", "any", growable=True)]
    outputs = [Port("out", "text"), Port("trigger", "event")]
    # it sits on the trigger path (Manual -> Template -> LLM), so disabled it
    # passes its trigger through: the node it fires still runs, with no text.
    bypass = {"trigger": "trigger"}

    def run(self, trigger=None, **ins):
        # `trigger` is taken apart from the tags, so a `{trigger}` in the text is
        # never substituted (it stays literal, like any other non-tag text).
        text = self.template
        # every key in `ins` is a wired-or-declared source (the growable `tag` base
        # plus each wired socket). A source that resolves to None substitutes an
        # EMPTY string (missing = blank, as n8n/ComfyUI do), NOT the literal {tag}:
        # a None input must never leak `{tag}` into an assembled LLM prompt. A tag
        # with no source at all is absent from `ins`, so its literal is left intact.
        for key, val in ins.items():
            text = text.replace("{" + key + "}", "" if val is None else str(val))
        # fired through `trigger`: pass it on AFTER the text (insertion order is
        # emit order), so a consumer fired by it reads the finished prompt. A read
        # is not a fire (the runtime clears `_fired_port`), so it never carries it.
        if getattr(self, "_fired_port", None) == "trigger":
            return {"out": text, "trigger": True}
        return {"out": text}


@node(id="core.data.format_list", name="Format List", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Turn a json list into text: render each item with a "
                           "{field} template and join with a separator (one per line by default).")
class FormatList:
    # per-item template: {field} pulls a key from each row; {.} is the whole item.
    item: Widget = code("{sender}: {message}")
    # the join string, picked from common separators (\n / \t become real chars).
    separator: Widget = Widget(kind="select", label="separator", default="\\n", options=[
        {"value": "\\n", "label": "new line"},
        {"value": "\\n\\n", "label": "blank line between"},
        {"value": ", ", "label": "comma"},
        {"value": " ", "label": "space"},
        {"value": " | ", "label": "pipe"},
        {"value": " - ", "label": "dash"},
        {"value": "; ", "label": "semicolon"},
        {"value": " • ", "label": "bullet"},
    ])
    inputs = [Port("list", "json")]
    outputs = [Port("out", "text")]

    def run(self, **ins):
        import json as _json
        cfg = getattr(self, "_node_cfg", {}) or {}
        # the runtime merges the declared defaults in, so an untouched `item` is the
        # `{sender}: {message}` the editor shows; a cleared field runs as shown
        # (empty), never a private fallback template.
        item_tpl = str(cfg.get("item") or "")
        sep = str(cfg.get("separator") or "\\n").replace("\\n", "\n").replace("\\t", "\t")
        rows = ins.get("list")
        if isinstance(rows, str):
            try:
                rows = _json.loads(rows)
            except Exception:
                rows = [rows]
        if not isinstance(rows, list):
            rows = [] if rows is None else [rows]
        lines: list[str] = []
        for it in rows:
            line = item_tpl
            # a None renders blank, never the text "None" (the Template's rule).
            if isinstance(it, dict):
                for k, v in it.items():
                    line = line.replace("{" + str(k) + "}", "" if v is None else str(v))
            whole = "" if it is None else str(it)
            line = line.replace("{.}", whole).replace("{item}", whole)
            lines.append(line)
        return {"out": sep.join(lines)}


def _dynamic_sorted(ins: dict, base: str) -> list:
    """Collect a growable base's wired sockets (item_0, item1, ...) in NUMERIC
    port order, dropping None. Tolerant of either separator (`item_0` from a
    hand-built graph, `item0` from the editor's auto-namer): any input whose name
    starts with the base and is not the base itself, ordered by its trailing
    integer (so item_10 follows item_9, never lexical)."""
    def order(name: str):
        digits = ""
        i = len(name) - 1
        while i >= 0 and name[i].isdigit():
            digits = name[i] + digits
            i -= 1
        return (int(digits) if digits else 0, name)
    keys = [k for k in ins if k != base and k.startswith(base)]
    keys.sort(key=order)
    return [ins[k] for k in keys if ins[k] is not None]


@node(id="core.data.list", name="List", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Assemble wired values into one list (the reverse of "
                           "For-each). A socket grows per wire, and a null value "
                           "is left out.")
class List:
    # type-agnostic: text, an image data-URL, a file path, json, all ride `any`.
    inputs = [Port("item", "any", growable=True)]
    outputs = [Port("out", "list")]

    def run(self, **ins):
        return {"out": _dynamic_sorted(ins, "item")}


@node(id="core.data.compute", name="Compute", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Derive a value from a pure expression over the wired "
                           "inputs. Compute calculates; Logic routes.",
      icon="calculator-outline", subline="compute · {expression|clip:14}")
class Compute:
    expression: Widget = code("value", expand=True)
    inputs = [Port("value", "any", growable=True)]
    outputs = [Port("out", "any")]

    def run(self, **ins):
        try:
            return {"out": _safe_eval(self.expression, dict(ins))}
        except Exception as exc:
            return {"out": f"<compute error: {exc}>"}


# ============================================================ logic (fired, route)
@node(id="core.logic.condition", name="Logic", kind=Kind.LOGIC, category="Logic",
      summary="Route a value true/false on an expression. Routes, never computes.",
      icon="git-branch-outline", subline="route · {expression|clip:14}")
class Logic:
    expression: Widget = code("value")
    inputs = [Port("value", "any", trigger=True), Port("condition", "any", optional=True)]
    outputs = [Port("true", "any"), Port("false", "any")]

    def route(self, value=None, condition=None, **_):
        try:
            ok = bool(_safe_eval(self.expression, {"value": value, "condition": condition}))
        except Exception:
            ok = False
        return "true" if ok else "false"


# ============================================================ AI
def _tool_arg_fields(rt, tool_id: str) -> list[str]:
    """The argument names declared by a Tool Args node wired to this Tool's
    `call` output (they become the tool's JSON-schema params). [] if none."""
    for dst, _dport in rt.edges_from.get((tool_id, "call"), []):
        inst = rt.nodes.get(dst)
        if inst is not None and inst.spec.id == "core.ai.tool_args":
            return _split_keys(_cfg(inst.obj, "fields"))
    return []


def _tool_schema(rt, tool_id: str, tobj) -> dict:
    """A tool's JSON schema. In "auto" mode (default) it is GENERATED from the
    field names of a wired Tool Args node, so you declare the arguments once and
    they become both the model's params and the splitter's output sockets.
    "manual" mode uses the raw `schema` knob. Falls back to an empty object."""
    mode = str(getattr(tobj, "schema_mode", "auto") or "auto")
    if mode != "manual":
        fields = _tool_arg_fields(rt, tool_id)
        if fields:
            return {"type": "object",
                    "properties": {f: {"type": "string"} for f in fields},
                    "required": list(fields)}
    raw = getattr(tobj, "schema", "") or ""
    try:
        return _json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {"type": "object", "properties": {}, "required": []}


# ---- model-driven nodes (LLM / TTS / STT): the selected manifest, its params
def _node_log(obj, message: str) -> None:
    """A visible log line attributed to the node (the editor console), or the
    Python log when the node runs outside a runtime (a direct call)."""
    ctx = getattr(obj, "_ctx", None)
    if ctx is not None:
        ctx.log(message)
    else:
        _log.warning(message)


def _widget_default(obj, name: str):
    """The default a node DECLARES on its widget `name` (the single source, so
    run() never repeats it as a literal)."""
    spec = getattr(type(obj), "_spec", None)
    return next((w.default for w in (spec.widgets if spec else []) if w.name == name), None)


def _node_model(obj, kind: str):
    """The manifest a TTS, STT or Rerank node calls: config.model, else the model
    widget's declared default. An unknown id, or a manifest of another kind (an LLM
    picked on a TTS node), raises and names it: no fallback to some other model."""
    cfg = getattr(obj, "_node_cfg", None) or {}
    model_id = cfg.get("model") or _widget_default(obj, "model")
    manifest = models.get(model_id) if isinstance(model_id, str) else None
    if manifest is None:
        raise RuntimeError(f"{kind.upper()}: unknown model {model_id!r} "
                           "(no manifest in boltjar/nodes/core/models)")
    if manifest.kind != kind:
        raise RuntimeError(f"{kind.upper()}: model {model_id!r} is a {manifest.kind} "
                           f"model, not {kind}")
    return manifest


def _provider_entry(registry: dict, manifest, node_label: str):
    """The registry entry for `manifest.provider`; an unregistered one raises."""
    entry = registry.get(manifest.provider)
    if entry is None:
        raise RuntimeError(f"{node_label}: provider {manifest.provider!r} (model "
                           f"{manifest.id!r}) is not registered; known providers: "
                           f"{', '.join(sorted(registry))}")
    return entry


def _param_value(obj, manifest, param, value):
    """A select param only takes one of its declared options. It is matched by
    text, so a knob's "24000" or a wired "24000" IS the int option 24000 (the
    option's own typed value is used). Anything else is a leftover of another
    model (a Fish voice after a switch to xAI) and falls back to this model's
    default, logged with the param and the rejected value so it is visible."""
    if param.type != "select" or not param.options:
        return value
    for option in param.options:
        if str(option) == str(value):
            return option
    _node_log(obj, f"{manifest.id}: {param.name}={value!r} is not one of its options, "
                   f"using the default {param.default!r}")
    return param.default


def _model_params(obj, manifest, wired: dict) -> dict:
    """The params a model node calls with: ONLY those the selected manifest
    declares, starting from its defaults, then the saved knob (config.params),
    then a wired (promoted) value. A param saved for a previously selected model
    never reaches this one (grok-4.3's `think` must not become grok-4.20's
    reasoning_effort; a Fish `format` means nothing to xAI)."""
    cfg = getattr(obj, "_node_cfg", None) or {}
    saved = cfg.get("params") or {}
    params: dict = {}
    for p in manifest.params:
        value = p.default
        for candidate in (saved.get(p.name), wired.get(p.name)):
            if candidate is not None:
                value = _param_value(obj, manifest, p, candidate)
        params[p.name] = value
    return params


@node(id="core.ai.llm", name="LLM", kind=Kind.TRANSFORM, category="AI",
      summary="A chat / multimodal model. Reshapes its inputs, outputs, and "
              "params to the capabilities of the selected model. A provider "
              "failure fires `error` (with the message) instead of a reply.",
      icon="sparkles-outline", subline="{model|model}")
class LLM:
    # empty runs the offline mock; "auto" picks a runnable model at run time.
    model: Widget = model("llm", auto=True)
    inputs = [Port("trigger", "event", trigger=True),
              Port("prompt", "text"),
              Port("tools", "tool", growable=True, optional=True)]
    # `response` is the assembled reply; `reasoning` carries the thinking trace
    # and is only surfaced (by the editor) for models whose manifest thinks.
    # `trigger` fires when the call completes, so the next node can be sequenced.
    # `error` fires INSTEAD of all three when the provider call fails: an event,
    # so it can drive a failure path (a Queue ack, an in-character Template, a
    # Preview) but can never be wired into a text input such as TTS.
    outputs = [Port("response", "text"),
               Port("reasoning", "text", optional=True),
               Port("trigger", "event"),
               Port("error", "event", optional=True)]

    async def run(self, trigger=None, prompt=None, **kw):
        cfg = getattr(self, "_node_cfg", {})
        model_id = cfg.get("model") or getattr(self, "model", None) or "mock/echo"
        if model_id == model_discovery.AUTO:
            # "auto" runs the first runnable model (see resolve_auto); with none
            # connected it says how to connect one instead of failing.
            manifest = model_discovery.resolve_auto()
            if manifest is None:
                return {"response": model_discovery.AUTO_MOCK_REPLY, "trigger": True}
            model_id = manifest.id
        else:
            manifest = models.get(model_id)
        # only the params the selected model declares (no manifest = the offline
        # mock, which takes none).
        params = _model_params(self, manifest, kw) if manifest else {}
        prompt = "" if prompt is None else str(prompt)
        media = {k: kw[k] for k in ("image", "audio", "video") if kw.get(k) is not None}
        # Collect wired Tool nodes. The runtime stashes the live ctx + the list
        # of connected Tool node ids on us before calling run(); read each
        # Tool's name/description/parsed schema straight off its instance, so
        # the editor knobs are the source of truth.
        ctx = getattr(self, "_ctx", None)
        tool_ids = list(getattr(self, "_tool_node_ids", []) or [])
        tools: list[dict] = []
        if ctx is not None and tool_ids and manifest and manifest.tools:
            for tid in tool_ids:
                tinst = ctx._rt.nodes.get(tid)
                if tinst is None:
                    continue
                tobj = tinst.obj
                tname = str(getattr(tobj, "name", "") or "").strip()
                if not tname:
                    continue
                tdesc = str(getattr(tobj, "description", "") or "")
                parsed_schema = _tool_schema(ctx._rt, tid, tobj)
                tools.append({"id": tid, "name": tname,
                              "description": tdesc, "parameters": parsed_schema})
        try:
            out = await _call_model(manifest, model_id, prompt, params, media, ctx=ctx, tools=tools)
        except Exception as exc:
            # never a reply: fire the `error` branch with the message (no response,
            # reasoning or trigger, so nothing speaks or stores it) and report the
            # failure as this node's error (the runtime logs it + node_error).
            message = str(exc).strip() or type(exc).__name__
            raise NodeFailure(message, outputs={"error": message}) from exc
        out["trigger"] = True
        return out


@node(id="core.ai.tool", name="Tool", kind=Kind.TRANSFORM, category="AI",
      summary="A tool the LLM can call. It declares a name, a description and its "
              "parameters. When the model calls it, `call` carries the arguments into "
              "the body you wired, and the answer that comes back on `result` goes "
              "back to the model.")
class Tool:
    name: Widget = Widget(kind="text", default="my_tool", label="name")
    description: Widget = code("Describe what this tool does for the model.")
    # Where the parameter schema comes from. "auto" (default): the model's params
    # are the field names of a Tool Args node wired to `call`, so you declare the
    # arguments once. "manual": type the raw JSON schema below. The schema field
    # only shows in manual mode (op-shaped, read generically by the editor).
    schema_mode: Widget = select(["auto", "manual"], default="auto")
    schema: Widget = Widget(kind="code",
                            default='{"type":"object","properties":{},"required":[]}',
                            op_field="schema_mode", op_values=("manual",))
    inputs = [Port("result", "text", trigger=True)]
    # `call` declares a scaffold: an unwired-body `call` offers a one-click "Add
    # Tool Args" that spawns + pre-wires the body node. Declarative, read
    # generically by the editor (see boltjar.sdk.Port.scaffold).
    outputs = [Port("call", "tool-call", scaffold="core.ai.tool_args"),
               Port("trigger", "event")]

    def run(self, **_):
        # The Tool node never runs on its own: its lifecycle is driven by the
        # runtime (Ctx.call_tool emits the `call` event; a value landing on
        # `result` is intercepted by _consume and routed to the awaiting LLM).
        # If something falls through to this run (no waiter), do nothing.
        return {}


@node(id="core.ai.tool_args", name="Tool Args", kind=Kind.TRANSFORM, category="AI",
      summary="Where a tool's body starts. It runs when the model calls the tool: "
              "it splits `call` into one typed output per argument and fires `trigger` "
              "to run the rest of the body. Its field names are also the tool's "
              "parameters (in auto mode the Tool builds its schema from them). Wire "
              "the body's answer back into the Tool's `result`.")
class ToolArgs:
    fields: Widget = Widget(kind="text", default="", label="Arguments",
                            placeholder="location, unit")
    # `call` is a TRIGGER: a value landing here (the model invoking the tool) fires
    # this node, so the body runs once per call.
    inputs = [Port("call", "tool-call", trigger=True)]
    # `trigger` drives the body; the per-argument outputs are reshaped by the editor
    # from `fields` (one socket each). Mirrors Split JSON, plus the trigger.
    outputs = [Port("trigger", "event")]

    def run(self, call=None, **_):
        keys = _split_keys(_cfg(self, "fields"))
        value = call
        # tolerate the args arriving as a JSON STRING; a parse error or a
        # non-object resolves every field to None (lean: never raised). Always
        # emit `trigger` so the body runs even for a no-argument tool.
        if isinstance(value, str):
            try:
                value = _json.loads(value)
            except Exception:
                value = None
        out: dict = {"trigger": True}
        src = value if isinstance(value, dict) else {}
        out.update({k: src.get(k) for k in keys})
        return out


@node(id="core.ai.stt", name="STT", kind=Kind.TRANSFORM, category="AI",
      summary="Speech to text. Picks a model from any connected STT provider "
              "(Fish Audio, ElevenLabs, xAI) the same way the LLM picks its model. "
              "`lang` carries the language the provider detected.",
      icon="mic-outline", subline="{model|model}")
class STT:
    # The same rich model picker the LLM uses (kind=model); filtered to kind=stt
    # manifests by the editor. With nothing picked the node uses this default.
    model: Widget = model("stt", "fish/asr")
    # fires on a dedicated `trigger` (like the LLM); `audio` is pulled data.
    inputs = [Port("trigger", "event", trigger=True),
              Port("audio", "audio")]
    # `lang` carries the ASR-detected language (en/pt/...), so the voice path can
    # mirror the speaker's language into TTS/Avatar without the client pre-tagging it.
    outputs = [Port("text", "text"), Port("lang", "lang", optional=True), Port("trigger", "event")]

    async def run(self, audio=None, **kw):
        manifest = _node_model(self, "stt")
        transcribe = _provider_entry(_STT_PROVIDERS, manifest, "STT")
        if not audio:
            return {"text": "", "lang": "", "trigger": True}
        params = _model_params(self, manifest, kw)
        # no language hint yet: the STT node has no `lang` input.
        text, lang = await transcribe(str(audio), params, manifest, "")
        return {"text": text, "lang": lang, "trigger": True}


@node(id="core.ai.tts", name="TTS", kind=Kind.TRANSFORM, category="AI",
      summary="Text to speech. Picks a model from any connected TTS provider "
              "(Fish Audio, ElevenLabs, xAI) the same way the LLM picks its model. "
              "A wired `lang` overrides the language knob for providers that take "
              "one (xAI); Fish and ElevenLabs detect the language themselves and "
              "ignore it.",
      icon="volume-high-outline", subline="{model|model}")
class TTS:
    # The same rich model picker the LLM uses (kind=model); filtered to kind=tts.
    # Per-model params (voice, speed, ...) come from the selected manifest and
    # render as ordinary knobs below, like the LLM's temperature/top_p. With
    # nothing picked the node uses this default.
    model: Widget = model("tts", "xai/tts")
    # fires on a dedicated `trigger` (like the LLM); `text` is pulled data. `lang`
    # wires straight from the STT `lang` output (or an Audio Input's).
    inputs = [Port("trigger", "event", trigger=True),
              Port("text", "text"),
              Port("lang", "lang", optional=True)]
    outputs = [Port("audio", "audio"), Port("trigger", "event")]

    async def run(self, text=None, lang=None, **kw):
        manifest = _node_model(self, "tts")
        speak = _provider_entry(_TTS_PROVIDERS, manifest, "TTS")
        if not text:
            return {"audio": "", "trigger": True}
        params = _model_params(self, manifest, kw)
        audio = await speak(str(text), params, manifest, str(lang or "").strip())
        return {"audio": audio, "trigger": True}


# ============================================================ semantic memory pack
# Small composable primitives that, wired together, reproduce a hybrid memory
# (embeddings + vector search + rerank). Facts / FTS / bitemporal / query-expansion
# are COMPOSITION over the existing core.db + LLM nodes, not new nodes.

def _vector_store():
    """The shared embedded vector store. Imported lazily to avoid a
    boltjar.nodes.core -> boltjar.server import cycle (server imports boltjar.nodes.core)."""
    from boltjar.server import VECTOR_STORE
    return VECTOR_STORE


def _resolve_template(text: str, kw: dict) -> str:
    """Resolve {{secret.X}} then each {tag} from wired inputs (mirrors the DB node)."""
    out = resolve_secrets(text or "")
    for k, v in kw.items():
        if v is None:
            continue
        token = "{" + k + "}"
        if token in out:
            out = out.replace(token, str(v))
    return out


async def _embed_model(manifest, text: str) -> list:
    """Encode text to an embedding vector. Ollama is local (POST /api/embed); other
    providers branch like _call_model. Returns [] on any failure (lean: a downstream
    search just finds nothing rather than crashing the graph)."""
    import httpx
    if manifest is None or not text:
        return []
    provider = manifest.provider
    try:
        if provider == "ollama":
            base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{base}/api/embed",
                                         json={"model": manifest.model, "input": text})
                resp.raise_for_status()
                data = resp.json()
                embs = data.get("embeddings") or []
                return list(embs[0]) if embs else list(data.get("embedding") or [])
    except Exception:
        return []
    return []


class RerankUnavailable(RuntimeError):
    """The rerank HTTP backend could not be reached, errored, or returned an
    unusable response. Raised (never swallowed) so the Rerank node FAILS LOUD via
    the normal node-error path instead of silently passing the candidates through
    in their original order. A silent identity pass hides a broken retrieval
    pipeline; a red node with a message naming the url does not."""


async def _rerank_model(manifest, query: str, docs: list, endpoint: str) -> list:
    """Score each doc against the query via an HTTP /rerank service at `endpoint`.
    Returns a score per doc (input order) on success. RAISES RerankUnavailable if
    the backend is unreachable, errors, or returns an unrecognised response, so the
    caller surfaces a visible node error rather than degrading to identity order."""
    import httpx
    if not docs:
        return []
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(endpoint, json={"query": query, "documents": docs,
                                                     "model": getattr(manifest, "model", "")})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise RerankUnavailable(
            f"no rerank backend reachable at {endpoint!r} ({exc!s})"
        ) from exc
    if isinstance(data.get("scores"), list):
        return [float(s) for s in data["scores"]]
    results = data.get("results")
    if isinstance(results, list):
        scores = [0.0] * len(docs)
        for r in results:
            i = r.get("index")
            if isinstance(i, int) and 0 <= i < len(docs):
                scores[i] = float(r.get("score") or r.get("relevance_score") or 0.0)
        return scores
    raise RerankUnavailable(
        f"rerank backend at {endpoint!r} returned an unrecognised response "
        f"(no 'scores' or 'results' array)"
    )


@node(id="core.ai.embed", name="Embed", kind=Kind.TRANSFORM, category="AI",
      summary="Encode text into an embedding vector. Picks an embed model (Ollama "
              "bge-m3 by default) the way the LLM picks its model. Fires on its "
              "trigger and emits the vector + a trigger to sequence the next node.")
class Embed:
    # With nothing picked the node runs this declared default, so the picker shows
    # the model that actually runs (the TTS/STT precedent).
    model: Widget = model("embed", "ollama/bge-m3")
    inputs = [Port("trigger", "event", trigger=True),
              Port("text", "text")]
    outputs = [Port("embedding", "embedding"), Port("trigger", "event")]

    async def run(self, text=None, **_):
        cfg = getattr(self, "_node_cfg", {}) or {}
        manifest = models.get(cfg.get("model") or _widget_default(self, "model"))
        vec = await _embed_model(manifest, "" if text is None else str(text))
        return {"embedding": vec, "trigger": True}


@node(id="core.ai.rerank", name="Rerank", kind=Kind.TRANSFORM, category="AI",
      summary="A cross-encoder precision pass: score each candidate against the query "
              "and keep the best. Picks a rerank model, served over HTTP at the address "
              "in its `endpoint` setting. When no rerank service answers, the node fails "
              "with an error instead of passing the candidates on unranked.")
class Rerank:
    # a model node like the LLM: the body draws the picker and the picked
    # manifest's params, and nothing else. So the service's address is the
    # manifest's `endpoint` param, the one field the editor shows for it, and
    # `keep` (how many to return) is a param too; either can be converted to an
    # input. The doc text is the memory-set's `text` key. With nothing picked the
    # node runs the declared default model, which the picker therefore shows
    # (the TTS/STT precedent).
    model: Widget = model("rerank", "rerank/bge-v2-m3")
    inputs = [Port("trigger", "event", trigger=True),
              Port("query", "text"),
              Port("candidates", "memory-set")]
    outputs = [Port("results", "memory-set"), Port("trigger", "event")]

    async def run(self, query=None, candidates=None, **kw):
        items = list(candidates) if isinstance(candidates, list) else []
        if not items:
            return {"results": [], "trigger": True}
        manifest = _node_model(self, "rerank")
        # manifest defaults, then the saved settings, then a wired value.
        params = _model_params(self, manifest, kw)
        keep = max(1, int(params.get("keep") or 5))
        endpoint = str(params.get("endpoint") or "").strip()
        if not endpoint:
            raise RerankUnavailable(f"rerank model {manifest.id!r} has no endpoint: "
                                    "set its `endpoint` setting")
        docs = [str((it.get("text") if isinstance(it, dict) else it) or "") for it in items]
        # FAIL LOUD: a down/error/malformed backend raises out of here (the runtime
        # turns the node red and reports it via node_error), never a silent identity
        # pass-through. Fix by starting a rerank service or repointing `endpoint`.
        scores = await _rerank_model(manifest, str(query or ""), docs, endpoint)
        if len(scores) != len(items):
            raise RerankUnavailable(
                f"rerank backend at {endpoint!r} returned {len(scores)} scores for "
                f"{len(items)} candidates; refusing to guess an order (check `endpoint`)"
            )
        order = sorted(range(len(items)), key=lambda i: -scores[i])
        ranked = [items[i] for i in order]
        return {"results": ranked[:keep], "trigger": True}


@node(id="core.data.chunk", name="Chunk", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Split text into overlapping chunks for embedding/"
                           "indexing. Output is a list; feed it through For-each.")
class Chunk:
    size: Widget = Widget(kind="number", default=800, min=50, max=8000, step=50, label="size")
    overlap: Widget = Widget(kind="number", default=120, min=0, max=2000, step=10, label="overlap")
    by: Widget = select(["chars", "words"], default="chars")
    inputs = [Port("text", "text")]
    outputs = [Port("out", "list")]

    def run(self, text=None, **_):
        cfg = getattr(self, "_node_cfg", {}) or {}
        s = "" if text is None else str(text)
        size = max(1, int(cfg.get("size") or 800))
        overlap = max(0, min(int(cfg.get("overlap") or 0), size - 1))
        step = max(1, size - overlap)
        if (cfg.get("by") or "chars") == "words":
            toks = s.split()
            return {"out": [" ".join(toks[i:i + size]) for i in range(0, len(toks), step)] if toks else []}
        return {"out": [s[i:i + size] for i in range(0, len(s), step)] if s else []}


_SENTENCE_RE = re.compile(r".+?(?:[.!?…]+(?:[\"')\]]+)?|\n+|$)", re.DOTALL)


@node(id="core.data.sentences", name="Sentences", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Split text into a list of sentences (ends on . ! ? ... or a "
                           "newline). Feed through For-each to stream the LLM reply one "
                           "sentence at a time into TTS / Avatar (low-latency streaming).")
class Sentences:
    inputs = [Port("text", "text")]
    outputs = [Port("out", "list")]

    def run(self, text=None, **_):
        s = "" if text is None else str(text)
        out = [m.group(0).strip() for m in _SENTENCE_RE.finditer(s)]
        return {"out": [p for p in out if p]}


@node(id="core.store.vectors", name="Vector Store", kind=Kind.STORE, category="Store",
      pulled=True, summary="An embedded vector store (sqlite + cosine). Emits a "
                           "handle the Vectors node indexes into and searches.")
class VectorStoreNode:
    outputs = [Port("vectors", "vectors")]

    def run(self, **_):
        cfg = getattr(self, "_node_cfg", {})
        return {"vectors": cfg.get("vectors_key") or getattr(self, "_node_id", "")}


@node(id="core.vectors", name="Vectors", kind=Kind.TRANSFORM, category="Store",
      summary="Index and search an embedded vector store. The operation knob "
              "reshapes the knobs and outputs. Search takes one or more query "
              "embeddings, keeps each item's best score across them and returns the "
              "top k. It compares every stored vector by cosine similarity, which "
              "suits thousands of items.")
class VectorsNode:
    operation: Widget = select(["search", "index", "delete", "clear"], default="search")
    namespace: Widget = tmpl("default", kind="text", port_type="text",
                             placeholder="facts / messages / summaries")
    top_k: Widget = Widget(kind="number", default=5, min=1, max=100, step=1, label="top k",
                           op_field="operation", op_values=("search",))
    min_score: Widget = Widget(kind="number", default=0.45, min=0.0, max=1.0, step=0.01,
                               label="min score", op_field="operation", op_values=("search",))
    metadata: Widget = tmpl("", op_field="operation", op_values=("index",),
                            placeholder="ref = {key}\nrole = user", expand=True)
    ref: Widget = tmpl("", kind="text", port_type="text", op_field="operation",
                       op_values=("delete",), placeholder="external ref to delete")
    # `embedding` is ONE growable-free port that takes a single vector OR a list of
    # vectors (multi-probe union, e.g. a List of an Embed + a HyDE Embed). One
    # growable base (`tag`) keeps the editor's per-node socket reshaping exact.
    inputs = [Port("trigger", "event", trigger=True),
              Port("vectors", "vectors"),
              Port("embedding", "embedding"),
              Port("text", "text", optional=True),
              Port("tag", "any", growable=True, optional=True, ghost_base="tag")]
    outputs = [Port("results", "memory-set", op_field="operation", op_values=("search",)),
               Port("ids", "json", op_field="operation", op_values=("search",)),
               Port("ref_id", "int", op_field="operation", op_values=("index",)),
               Port("affected", "int", op_field="operation", op_values=("delete", "clear")),
               Port("trigger", "event")]

    async def run(self, trigger=None, vectors=None, embedding=None, text=None, **kw):
        cfg = getattr(self, "_node_cfg", {}) or {}
        op = (cfg.get("operation") or "search").lower()
        if not vectors:
            raise ValueError("Vectors: no vector store wired into the 'vectors' input")
        store = _vector_store()
        ns = _resolve_template(str(cfg.get("namespace") or "default"), kw) or "default"
        # normalise `embedding` to a list of probe vectors: a single vector
        # (list of numbers) -> one probe; a list of vectors -> multi-probe union.
        embs: list = []
        if embedding:
            if isinstance(embedding, (list, tuple)) and embedding and isinstance(embedding[0], (list, tuple)):
                embs = [list(e) for e in embedding if e]
            else:
                embs = [list(embedding)]

        if op == "search":
            top_k = max(1, int(cfg.get("top_k") or 5))
            min_score = float(cfg.get("min_score") or 0.0)
            best: dict = {}  # multi-probe union: best cosine per id
            for e in embs:
                for hit in await asyncio.to_thread(store.search, vectors, ns, e, top_k, min_score):
                    rid = hit["id"]
                    if rid not in best or hit["score"] > best[rid]["score"]:
                        best[rid] = hit
            results = sorted(best.values(), key=lambda h: -h["score"])[:top_k]
            return {"results": results, "ids": [h["id"] for h in results], "trigger": True}
        if op == "index":
            if not embs:
                return {"ref_id": -1, "trigger": True}
            meta = _parse_kv_lines(_resolve_template(str(cfg.get("metadata") or ""), kw))
            ref = str(meta.pop("ref", "") or "")
            rid = await asyncio.to_thread(store.index, vectors, ns, embs[0],
                                          str(text or ""), ref, meta)
            return {"ref_id": rid, "trigger": True}
        if op == "delete":
            ref = _resolve_template(str(cfg.get("ref") or ""), kw)
            return {"affected": await asyncio.to_thread(store.delete, vectors, ns, ref), "trigger": True}
        # clear
        return {"affected": await asyncio.to_thread(store.clear, vectors, ns), "trigger": True}


# ============================================================ data store (sqlite)
def _store():
    """The shared embedded-SQLite store. Imported lazily to avoid a
    boltjar.nodes.core -> boltjar.server import cycle (server imports boltjar.nodes.core)."""
    from boltjar.server import STORE
    return STORE


DATABASE_ID = "core.store.database"


def database_key(node_id: str, cfg: dict | None) -> str:
    """The store key a Database node emits: its stable `db_key` (assigned by the
    editor at creation, persisted with the graph, so it survives a rename), else
    the node id. Its declared tables are made to exist under the same key."""
    return str((cfg or {}).get("db_key") or node_id or "")


def ensure_database_schema(node_id: str, cfg: dict | None) -> tuple[dict | None, list[str]]:
    """Create what one Database node declares (its `schema` knob) and its store
    lacks: missing tables and missing columns, nothing else (ensure_schema never
    drops, retypes or touches a row). Returns (store, warnings): store is
    {key, created, added} once the store was reached, None when the node
    declares nothing or its store could not be opened; each warning is a
    declaration the database cannot match, or the reason the store was not
    reached. It never raises: a graph opens and runs whatever its declaration
    holds, and graphs are shared, so the declaration may be someone else's."""
    if not isinstance(cfg, dict):
        return None, []
    tables = cfg.get("schema")
    if not isinstance(tables, list) or not tables:
        return None, []
    key = database_key(node_id, cfg)
    if not key.strip():
        return None, []
    try:
        out = _store().ensure_schema(key, tables)
    except (sqlite3.Error, OSError) as exc:
        # reported like a conflict: the graph still opens and runs, and its DB
        # nodes name the failure when they run.
        return None, [f"its database could not be opened: {exc}"]
    except Exception as exc:  # a declaration must never stop a graph
        return None, [f"its declared tables could not be checked: {exc!r}"]
    return {"key": key, "created": out["created"], "added": out["added"]}, out["conflicts"]


def ensure_declared_schemas(graph: dict) -> dict:
    """`ensure_database_schema` for every Database node of a graph, which the
    editor asks for when it opens one (POST /api/stores/ensure). Returns
    {stores, warnings}: per store reached, its node, key and what was created or
    added; per warning, the node it names."""
    stores: list[dict] = []
    warnings: list[dict] = []
    for n in graph.get("nodes") or []:
        if not isinstance(n, dict) or n.get("type") != DATABASE_ID:
            continue
        store, messages = ensure_database_schema(str(n.get("id") or ""), n.get("config"))
        if store is not None:
            stores.append({"node": n.get("id"), **store})
        warnings.extend({"node": n.get("id"), "message": m} for m in messages)
    return {"stores": stores, "warnings": warnings}


@node(id=DATABASE_ID, name="Database", kind=Kind.STORE, category="Store",
      pulled=True, summary="An embedded SQLite database. Emits a db connection "
                           "other nodes use.")
class Database:
    outputs = [Port("db", "db")]
    # the tables this graph needs, as [{name, columns: [{name, type, pk}]}] (the
    # shape SqliteStore.schema() returns, row counts left out). Saved with the
    # graph and never drawn as a knob: the schema editor (Edit) keeps it in step
    # with what it builds. Created when the graph runs (open) and when the editor
    # opens the graph (ensure_declared_schemas), so a shared graph carries them.
    schema: Widget = Widget(kind="schema", default=[], surface="hidden", promotable=False)

    async def open(self, ctx):
        """The runtime opens a store before any node fires, however the graph
        runs (On in the editor, `python -m boltjar`, a script that builds a
        Runtime), so the declared tables exist by then. What the database
        cannot match is a warning on the console, never a failure."""
        _, warnings = await asyncio.to_thread(
            ensure_database_schema, ctx.node_id, getattr(self, "_node_cfg", {}))
        for message in warnings:
            ctx.warn(message)

    # Pulled source: emits the store key. The runtime sets _node_id and
    # _node_cfg on each instance at build time.
    def run(self, **_):
        return {"db": database_key(getattr(self, "_node_id", ""), getattr(self, "_node_cfg", {}))}


# ============================================================ db (consolidated)
# The single DB node. Replaces core.data.{query,exec,insert,find,update,delete}.
# The `operation` knob reshapes the visible knobs and the output ports; the
# editor surfaces only the relevant ones. Knobs are templates: {tag} pulls from
# a wired source (mirroring HTTP), {{secret.X}} resolves a secret. Every table/
# column identifier is quoted with `_quote_ident`; every value is a bound param.
from boltjar.sqlite_store import _quote_ident


def _db_friendly_error(exc: Exception, op: str):
    """Translate a raw sqlite error into the DB node's standard visible ValueError
    when it is actionable. The common first-run trap is writing to a table before
    its schema exists: sqlite raises `OperationalError: no such table: NAME`, an
    opaque message. Surface the table name and say where tables come from: the
    DB node never creates one itself, the Database node declares them (its schema
    editor) and creates them when the graph runs or the editor opens it. A
    statement the store's authorizer refuses (ATTACH and friends) gets a named
    reason too.
    Returns None for anything we don't specifically translate, so the caller
    re-raises the original (still visible via the runtime's node_error path)."""
    msg = str(exc)
    if msg in ("not authorized", "authorization denied"):
        return ValueError(
            "DB: ATTACH, DETACH and VACUUM INTO are refused: they reach database "
            "files outside this store.")
    if isinstance(exc, sqlite3.OperationalError) and "no such table" in msg.lower():
        table = msg.split(":", 1)[1].strip() if ":" in msg else "?"
        return ValueError(
            f"DB: no such table {table!r} for this '{op}'. Add it with Edit on the "
            f"Database node; the graph keeps it and creates it whenever it runs.")
    return None


def _cfg(obj, key: str, fallback=""):
    """Read a config value, preferring the live `_node_cfg` (promotion-aware)."""
    return getattr(obj, "_node_cfg", {}).get(key) or getattr(obj, key, fallback)


def _parse_kv_lines(text: str, resolve=None) -> dict:
    """One "key = value" per line: the first '=' splits, key and value are trimmed
    (the placeholder's `role = user` is "user"), blank lines and lines without '='
    are skipped. Pass the RAW template and a `resolve`: it runs on each value AFTER
    the split, so a {tag} / {{secret.X}} can never add a line or a field (a
    multi-line LLM reply stays one value, verbatim, even when a line of it holds
    '='). Used by the DB node (fields/where) and the Vectors node (metadata)."""
    out: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k:
            v = v.strip()
            out[k] = resolve(v) if resolve is not None else v
    return out


_SQL_TAG_RE = re.compile(r"\{([A-Za-z_]\w*)\}")
# SQLite's INTEGER is 64-bit; a wider Python int cannot be bound at all.
_SQL_INT_RANGE = range(-2**63, 2**63)


def _bare_sql_value(value):
    """What a bare {tag} binds. A number (or bytes) binds as itself, and so does
    text that spells a number exactly (str(int(s)) == s, or repr(float(s)) == s),
    the numeric literal the pasted template used to be: an untyped column or
    `json_extract(...) = {id}` still matches '7'. Anything else binds as text."""
    if isinstance(value, (int, float, bytes)):
        return value
    text = value if isinstance(value, str) else str(value)
    try:
        number = int(text)
        if str(number) == text and number in _SQL_INT_RANGE:
            return number
    except ValueError:
        pass
    try:
        number = float(text)
        if repr(number) == text and math.isfinite(number):
            return number
    except ValueError:
        pass
    return text


def _sql_quoted_end(sql: str, start: int, quote: str) -> int:
    """The index just past the quote that closes the one opened at `start` (a
    doubled quote is an escaped one), or -1 when it never closes."""
    k = start + 1
    while True:
        k = sql.find(quote, k)
        if k == -1:
            return -1
        if sql.startswith(quote, k + 1):
            k += 2
            continue
        return k + 1


def _bind_sql_tags(sql: str, tags: dict) -> tuple[str, dict]:
    """Turn the wired {tag}s of a raw SQL template into bound parameters, so a
    wired value (a webhook body, an LLM reply) is always data, never SQL: it can
    never add a clause or name a table or column. By where the tag sits, read the
    way SQLite's own tokenizer reads the text:
      - bare, `id = {row_id}`: one parameter holding the value as wired, text
        that spells a number exactly bound as that number (_bare_sql_value);
      - in quotes, `name = '{name}'`, `"{name}"` or `'%{q}%'`: the quoted text
        becomes its pieces joined to text parameters with ||, the same string
        the old pasted template spelled out (a double-quoted one included, which
        SQLite read as a string once no column matched);
      - in a comment, a [bracketed] or `backticked` name: left alone.
    A tag with no wired value stays literal text. Returns (sql, params)."""
    out: list[str] = []
    params: dict = {}

    def bind(value) -> str:
        name = f"_tag{len(params)}"
        params[name] = value
        return ":" + name

    def pieces(text: str) -> list[tuple[bool, str]]:
        """(is_tag, text) runs of `text`, split at its wired tags."""
        runs, last = [], 0
        for m in _SQL_TAG_RE.finditer(text):
            if m.group(1) in tags:
                runs += [(False, text[last:m.start()]), (True, m.group(1))]
                last = m.end()
        return runs + [(False, text[last:])]

    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"`":
            end = _sql_quoted_end(sql, i, c)
            if end == -1:  # unterminated: SQLite rejects it, never substitute
                out.append(sql[i:])
                break
            runs = pieces(sql[i + 1:end - 1].replace(c * 2, c))
            if c == "`" or len(runs) == 1:
                out.append(sql[i:end])
            else:
                terms = [bind(str(tags[t])) if is_tag else "'" + t.replace("'", "''") + "'"
                         for is_tag, t in runs if is_tag or t]
                out.append(terms[0] if len(terms) == 1 else "(" + " || ".join(terms) + ")")
            i = end
            continue
        if c == "[" or sql.startswith(("--", "/*"), i):
            # a [name] or a comment runs to its closer, left as-is.
            closer = {"[": "]", "-": "\n", "/": "*/"}[c]
            end = sql.find(closer, i + 1 if c == "[" else i + 2)
            end = n if end == -1 else end + len(closer)
            out.append(sql[i:end])
            i = end
            continue
        m = _SQL_TAG_RE.match(sql, i) if c == "{" else None
        if m is not None and m.group(1) in tags:
            out.append(bind(_bare_sql_value(tags[m.group(1)])))
            i = m.end()
            continue
        out.append(c)
        i += 1
    return "".join(out), params


@node(id="core.db", name="DB", kind=Kind.TRANSFORM, category="Store",
      summary="Read and write a SQLite store. The operation knob reshapes the "
              "knobs and outputs. Knobs are templates: {tag} pulls from a "
              "wired source and {{secret.X}} resolves a secret. In SQL a wired "
              "{tag} is bound as a parameter, so it is always a value, never SQL. "
              "An insert or update stores a data: URL value as text.")
class DBNode:
    # op-shaped: each field declares which operations it belongs to (replaces the
    # frontend dbKnobNamesFor table). The editor reads op_field/op_values.
    operation: Widget = select(["query", "exec", "insert", "find", "update", "delete"], default="query")
    sql: Widget = tmpl("", port_type="text", op_field="operation", op_values=("query", "exec"),
                       placeholder="SELECT * FROM chat_history WHERE role = {tag}", expand=True)
    # a live dropdown of the wired db's real tables (options_from), so you pick
    # rather than type. Falls back to a text input when no db / no tables.
    table: Widget = tmpl("", kind="text", port_type="text", op_field="operation",
                         op_values=("insert", "find", "update", "delete"),
                         options_from="db.tables", placeholder="pick a table")
    # "col = value" per line; {tag} pulls a wired source into the value.
    fields: Widget = tmpl("", op_field="operation", op_values=("insert", "update"),
                          placeholder="role = user\ncontent = {chat}", expand=True)
    where: Widget = tmpl("", op_field="operation", op_values=("find", "update", "delete"),
                         placeholder="id = {row_id}", expand=True)
    inputs = [Port("trigger", "event", trigger=True),
              Port("db", "db"),
              Port("tag", "any", growable=True, optional=True, ghost_base="tag")]
    # op-shaped outputs (replaces dbOutputNamesFor): rows for query/find, affected
    # for exec/update/delete, row_id for insert; trigger always.
    outputs = [Port("rows", "json", op_field="operation", op_values=("query", "find")),
               Port("affected", "int", op_field="operation", op_values=("exec", "update", "delete")),
               Port("row_id", "int", op_field="operation", op_values=("insert",)),
               Port("trigger", "event")]

    async def run(self, trigger=None, db=None, **kw):
        cfg = getattr(self, "_node_cfg", {}) or {}
        op = (cfg.get("operation") or "query").lower()
        if not db:
            raise ValueError("DB: no db connection wired into the 'db' input")
        # template resolve: {{secret.X}} first, then {tag} from wired inputs.
        # Every wired input that is not the trigger is a candidate tag, named
        # after the source (the dst_port name IS the tag), mirroring HTTP.
        tag_kwargs = {k: v for k, v in kw.items() if v is not None and k != "trigger"}

        def resolve(text: str) -> str:
            out = resolve_secrets(text)
            for k, v in tag_kwargs.items():
                token = "{" + k + "}"
                if token in out:
                    out = out.replace(token, str(v))
            return out

        store = _store()

        async def _do(fn, *args):
            """Run a store op off-thread, translating a raw sqlite error (e.g. a
            missing table) into the node's actionable visible error."""
            try:
                return await asyncio.to_thread(fn, *args)
            except sqlite3.DatabaseError as exc:
                friendly = _db_friendly_error(exc, op)
                if friendly is not None:
                    raise friendly from exc
                raise

        if op in ("query", "exec"):
            # raw SQL: a wired {tag} binds as a parameter, never pasted as text.
            sql, params = _bind_sql_tags(resolve_secrets(str(cfg.get("sql") or "")), tag_kwargs)
            if op == "query":
                return {"rows": await _do(store.query, db, sql, params), "trigger": True}
            r = await _do(store.execute, db, sql, params)
            return {"affected": r["changes"], "trigger": True}
        # insert / find / update / delete: parse the "col=value" lines, then map
        # to the SqliteStore engine. A value that looks like a data: URL stays a
        # string (saved as TEXT in V1); BLOB binding is a later concern.
        table = resolve(str(cfg.get("table") or ""))
        if not table:
            raise ValueError(f"DB: '{op}' needs a table")
        # parse the RAW lines first, then resolve inside each value: a wired value
        # (a multi-line reply, a line of it holding '=') never adds lines/columns.
        fields_dict = _parse_kv_lines(str(cfg.get("fields") or ""), resolve)
        where_dict = _parse_kv_lines(str(cfg.get("where") or ""), resolve)
        qt = _quote_ident(table)

        if op == "insert":
            if not fields_dict:
                raise ValueError("DB: 'insert' needs at least one field=value line")
            names = list(fields_dict)
            col_sql = ", ".join(_quote_ident(c) for c in names)
            val_sql = ", ".join(f":{c}" for c in names)
            sql = f"INSERT INTO {qt}({col_sql}) VALUES({val_sql})"
            r = await _do(store.execute, db, sql, fields_dict)
            return {"row_id": r["lastId"], "trigger": True}

        if op == "find":
            sql = f"SELECT * FROM {qt}"
            if where_dict:
                parts = " AND ".join(f"{_quote_ident(c)}=:{c}" for c in where_dict)
                sql += f" WHERE {parts}"
            rows = await _do(store.query, db, sql, where_dict)
            return {"rows": rows, "trigger": True}

        if op == "update":
            if not fields_dict:
                raise ValueError("DB: 'update' needs at least one field=value line")
            assignments = ", ".join(f"{_quote_ident(c)}=:{c}" for c in fields_dict)
            sql = f"UPDATE {qt} SET {assignments}"
            # bind the SET values plus the where params; re-key any where param
            # that collides with a SET column so the binds stay distinct.
            bound = dict(fields_dict)
            if where_dict:
                where_parts: list[str] = []
                for c, v in where_dict.items():
                    bind_name = c if c not in bound else f"_w_{c}"
                    where_parts.append(f"{_quote_ident(c)}=:{bind_name}")
                    bound[bind_name] = v
                sql += " WHERE " + " AND ".join(where_parts)
            r = await _do(store.execute, db, sql, bound)
            return {"affected": r["changes"], "trigger": True}

        if op == "delete":
            sql = f"DELETE FROM {qt}"
            if where_dict:
                parts = " AND ".join(f"{_quote_ident(c)}=:{c}" for c in where_dict)
                sql += f" WHERE {parts}"
            r = await _do(store.execute, db, sql, where_dict)
            return {"affected": r["changes"], "trigger": True}

        return {"trigger": True}


# ============================================================ json (pulled)
# Generic data primitives for structured data: text<->json plus a json builder.
# All pulled (evaluated on demand, no trigger in, no `trigger` out). Lean error handling:
# a parse error is surfaced AS DATA (`{"error": ...}`), never raised, so a graph
# keeps flowing and the error is inspectable on the wire.
import json as _json


@node(id="core.data.parse", name="Parse", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Parse JSON text into a structured value. A parse "
                           "error comes back as data ({\"error\": ...}).")
class Parse:
    inputs = [Port("text", "text")]
    outputs = [Port("json", "json")]

    def run(self, text=None, **_):
        if text is None:
            return {"json": None}
        try:
            return {"json": _json.loads(text if isinstance(text, str) else str(text))}
        except Exception as exc:
            return {"json": {"error": str(exc)}}


@node(id="core.data.stringify", name="Stringify", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Serialise a value to a pretty (2-space) JSON string.")
class Stringify:
    inputs = [Port("json", "json")]
    outputs = [Port("text", "text")]

    def run(self, json=None, **_):
        try:
            return {"text": _json.dumps(json, indent=2, ensure_ascii=False)}
        except Exception:
            # non-serialisable (sets, custom objects, …): fall back to str().
            return {"text": str(json)}


@node(id="core.data.build", name="Build JSON", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Build a JSON object from wired inputs: each input's "
                           "port name becomes a key holding that input's value.")
class BuildJson:
    # ghost_base="field": a minted socket is named after the wired source node
    # (a meaningful json key), not auto-numbered. Declared on the port so the
    # editor needs no per-id FIELD_NAMED_IDS table.
    inputs = [Port("field", "any", growable=True, optional=True, ghost_base="field")]
    outputs = [Port("json", "json")]

    def run(self, **fields):
        # every wired named input is a key; the wired value is the value. The
        # dst_port name (the growable field name) is the object key, mirroring the
        # Insert CRUD field pattern. Unwired ports never reach here.
        return {"json": {k: v for k, v in fields.items() if v is not None}}


def _walk_path(value, path: str):
    """A small dotted/bracket path walker: `user.name`, `items.0.id`,
    `items[0].id`. Missing keys / out-of-range indices return None. No external
    dependency: each segment is a dict key or a list index."""
    if not path:
        return value
    # normalise `a[0].b` -> `a.0.b` so one split handles dot and bracket forms.
    normalised = path.replace("[", ".").replace("]", "")
    cur = value
    for seg in normalised.split("."):
        if seg == "":
            continue
        if isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        elif isinstance(cur, (list, tuple)):
            try:
                idx = int(seg)
            except (TypeError, ValueError):
                return None
            # allow Python-style negative indices (items.-1 == last); out of range -> None.
            if not (-len(cur) <= idx < len(cur)):
                return None
            cur = cur[idx]
        else:
            return None
    return cur


@node(id="core.data.get", name="Get", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Read the value at a dotted or bracket path "
                           "(user.name, items.0.id). A missing path gives null.")
class Get:
    path: Widget = Widget(kind="text", default="", label="Path")
    inputs = [Port("json", "json")]
    outputs = [Port("value", "any")]

    def run(self, json=None, **_):
        path = _cfg(self, "path")
        return {"value": _walk_path(json, str(path or ""))}


def _split_keys(raw: str) -> list[str]:
    """Parse the configured key list for Split JSON: split on commas, spaces and
    newlines, drop blanks, dedupe while preserving first-seen order. Mirrors the
    editor's parser so the per-key output ports line up exactly."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in str(raw or "").replace(",", " ").split():
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


@node(id="core.data.split", name="Split JSON", kind=Kind.TRANSFORM, category="Data",
      pulled=True, summary="Split a json object onto one output per configured key "
                           "(the reverse of Build JSON): each key routes its value.")
class SplitJson:
    keys: Widget = Widget(kind="text", default="", label="Keys")
    inputs = [Port("json", "json")]
    # outputs are reshaped by the editor from the `keys` config (one `any` port per
    # key); declared empty so the static def carries no phantom ports.
    outputs: list = []

    def run(self, json=None, **_):
        keys = _split_keys(_cfg(self, "keys"))
        value = json
        # tolerate a json STRING input: parse it; a parse error or a non-object
        # value resolves every key to None (lean: never raised).
        if isinstance(value, str):
            try:
                value = _json.loads(value)
            except Exception:
                value = None
        if not isinstance(value, dict):
            return {k: None for k in keys}
        # one key -> its value (a missing key resolves to None). The runtime pulls
        # each output port independently and reads out.get(port), so emit them all.
        return {k: value.get(k) for k in keys}


# ============================================================ files (sandboxed)
# Generic filesystem I/O primitives. Every path is resolved RELATIVE to the
# server's sandbox root (user/data/files/) and may never escape it; the resolution +
# read/write/append/delete/list live in boltjar.file_store (FileStore), so the
# nodes here are thin wrappers. Reads are PULLED (volatile, fresh each pull) and
# lean (a missing file/dir surfaces "" / []); writes are FIRED on a trigger and
# emit `trigger` so a graph can sequence after the write. JSON files compose via the
# JSON family (Read -> Parse, Stringify -> Write); there is no json-load/save node.
def _files():
    """The shared sandboxed file store. Imported lazily to avoid a
    boltjar.nodes.core -> boltjar.server import cycle (server imports boltjar.nodes.core)."""
    from boltjar.server import FILE_STORE
    return FILE_STORE


@node(id="core.file.read", name="Read File", kind=Kind.SERVICE, category="Files",
      pulled=True, volatile=True,
      summary="Read a file's text from the sandbox. Pulled, fresh; a missing "
              "file reads as empty.",
      icon="document-text-outline")
class ReadFile:
    path: Widget = Widget(kind="text", default="", label="Path")
    inputs = [Port("path", "text", optional=True)]
    outputs = [Port("text", "text")]

    def run(self, path=None, **_):
        # a wired `path` input wins over the knob, mirroring the write family.
        target = path if path is not None else _cfg(self, "path")
        if not target:
            return {"text": ""}
        try:
            return {"text": _files().read(str(target))}
        except Exception as exc:
            # lean: a rejected path (escape) or read error comes back AS DATA,
            # never raised, so a pulled graph keeps flowing and the error shows.
            return {"text": f"<file error: {exc}>"}


@node(id="core.file.write", name="Write File", kind=Kind.SERVICE, category="Files",
      summary="Write text to a file in the sandbox on a trigger, creating parent "
              "dirs. Emits the resolved relative path.",
      icon="create-outline")
class WriteFile:
    path: Widget = Widget(kind="text", default="", label="Path")
    inputs = [Port("trigger", "event", trigger=True),
              Port("path", "text", optional=True),
              Port("content", "text", optional=True)]
    outputs = [Port("trigger", "event"), Port("path", "text")]

    async def run(self, trigger=None, path=None, content=None, **_):
        # a wired `path` input overrides the knob (universal promotion); the knob
        # is read promotion-aware via _cfg, but an explicit wire wins.
        target = path if path is not None else _cfg(self, "path")
        if not target:
            raise ValueError("Write File: no path configured")
        written = await asyncio.to_thread(_files().write, str(target), "" if content is None else content)
        return {"trigger": True, "path": written}


@node(id="core.file.append", name="Append File", kind=Kind.SERVICE, category="Files",
      summary="Append text to a file in the sandbox on a trigger, creating it if "
              "absent. Emits the resolved relative path.",
      icon="add-circle-outline")
class AppendFile:
    path: Widget = Widget(kind="text", default="", label="Path")
    inputs = [Port("trigger", "event", trigger=True),
              Port("path", "text", optional=True),
              Port("content", "text", optional=True)]
    outputs = [Port("trigger", "event"), Port("path", "text")]

    async def run(self, trigger=None, path=None, content=None, **_):
        target = path if path is not None else _cfg(self, "path")
        if not target:
            raise ValueError("Append File: no path configured")
        written = await asyncio.to_thread(_files().append, str(target), "" if content is None else content)
        return {"trigger": True, "path": written}


@node(id="core.file.delete", name="Delete File", kind=Kind.SERVICE, category="Files",
      summary="Delete a file in the sandbox on a trigger (idempotent; a missing "
              "file is a no-op). Emits the resolved relative path.",
      icon="trash-outline")
class DeleteFile:
    path: Widget = Widget(kind="text", default="", label="Path")
    inputs = [Port("trigger", "event", trigger=True),
              Port("path", "text", optional=True)]
    outputs = [Port("trigger", "event"), Port("path", "text")]

    async def run(self, trigger=None, path=None, **_):
        target = path if path is not None else _cfg(self, "path")
        if not target:
            raise ValueError("Delete File: no path configured")
        deleted = await asyncio.to_thread(_files().delete, str(target))
        return {"trigger": True, "path": deleted}


@node(id="core.file.list", name="List Dir", kind=Kind.SERVICE, category="Files",
      pulled=True, volatile=True,
      summary="List entry names under a directory in the sandbox. Pulled, fresh; "
              "a missing dir lists as []. Default path is the sandbox root.",
      icon="folder-open-outline")
class ListDir:
    path: Widget = Widget(kind="text", default="", label="Path")
    inputs = [Port("path", "text", optional=True)]
    outputs = [Port("json", "json")]

    def run(self, path=None, **_):
        target = path if path is not None else _cfg(self, "path")
        try:
            return {"json": _files().list(str(target or ""))}
        except Exception:
            # a rejected path (escape) lists as empty, matching the missing-dir
            # case: a read never throws.
            return {"json": []}


# ============================================================ key-value store
# A real, PERSISTENT key-value store: the genuine version of the degenerate
# in-RAM State/Memory. The KV Store node OWNS the store (one JSON file per
# stable kv_key under user/data/kv/, via boltjar.kv_store.KvStore); the get/set/
# delete/has nodes resolve that handle. It mirrors the SQLite family exactly:
# the store node emits a typed handle (a `kv` pipe), reads are PULLED (fresh),
# and writes are FIRED on a trigger and emit `trigger` so a graph can sequence.
def _kv():
    """The shared persistent KV store. Imported lazily to avoid a
    boltjar.nodes.core -> boltjar.server import cycle (server imports boltjar.nodes.core)."""
    from boltjar.server import KV_STORE
    return KV_STORE


@node(id="core.store.kv", name="KV Store", kind=Kind.STORE, category="Store",
      pulled=True, summary="A persistent key-value store. Emits a kv handle "
                           "other nodes read and write through.")
class KvStoreNode:
    # Pulled source: emits the store key. A stable `kv_key` (assigned by the
    # editor at creation, persisted with the graph) survives rename; until the
    # editor supplies one we fall back to the node id. Mirrors the Database node.
    outputs = [Port("kv", "kv")]

    def run(self, **_):
        cfg = getattr(self, "_node_cfg", {})
        return {"kv": cfg.get("kv_key") or getattr(self, "_node_id", "")}


@node(id="core.kv", name="KV", kind=Kind.TRANSFORM, category="Store",
      summary="Read/write a kv store. The 'operation' knob reshapes the inputs "
              "and outputs. Knobs are templates: {tag} pulls from a wired "
              "source, {{secret.X}} resolves a secret.")
class KV:
    # op-shaped (replaces kvKnobNamesFor): key for get/set/delete/has, value for
    # set, prefix for keys. The editor reads op_field/op_values off each widget.
    operation: Widget = select(["get", "set", "delete", "has", "keys"], default="get")
    key: Widget = tmpl("", kind="text", op_field="operation", op_values=("get", "set", "delete", "has"),
                       options_from="kv.keys", placeholder="key (pick or type a new one)")
    value: Widget = tmpl("", op_field="operation", op_values=("set",), expand=True)
    prefix: Widget = tmpl("", kind="text", port_type="text", op_field="operation", op_values=("keys",))
    inputs = [Port("trigger", "event", trigger=True),
              Port("kv", "kv"),
              Port("tag", "any", growable=True, optional=True, ghost_base="tag")]
    # op-shaped outputs (replaces kvOutputNamesFor): value for get, ok for has,
    # keys for keys; trigger always.
    outputs = [Port("value", "any", op_field="operation", op_values=("get",)),
               Port("ok", "bool", op_field="operation", op_values=("has",)),
               Port("keys", "json", op_field="operation", op_values=("keys",)),
               Port("trigger", "event")]

    async def run(self, trigger=None, kv=None, **kw):
        cfg = getattr(self, "_node_cfg", {}) or {}
        op = (cfg.get("operation") or "get").lower()
        if not kv:
            raise ValueError("KV: no kv store wired into the 'kv' input")
        # template resolve: identical pattern to DB.run / Http.run.
        tag_kwargs = {k: v for k, v in kw.items() if v is not None and k != "trigger"}

        def resolve(text: str) -> str:
            out = resolve_secrets(text)
            for k, v in tag_kwargs.items():
                token = "{" + k + "}"
                if token in out:
                    out = out.replace(token, str(v))
            return out

        key = resolve(str(cfg.get("key") or ""))
        value = resolve(str(cfg.get("value") or ""))
        prefix = resolve(str(cfg.get("prefix") or ""))
        store = _kv()
        if op == "get":
            return {"value": store.get(kv, key, None), "trigger": True}
        if op == "set":
            await asyncio.to_thread(store.set, kv, key, value)
            return {"trigger": True}
        if op == "delete":
            await asyncio.to_thread(store.delete, kv, key)
            return {"trigger": True}
        if op == "has":
            return {"ok": store.has(kv, key), "trigger": True}
        if op == "keys":
            ks = store.keys(kv)
            if prefix:
                ks = [k for k in ks if k.startswith(prefix)]
            return {"keys": ks, "trigger": True}
        return {"trigger": True}


# ============================================================ state (meter)
# The stateful accumulator primitive: a float that persists across fires and
# relaxes toward a `rest` value over time. Drift is LAZY: computed from elapsed
# wall-clock time whenever the meter is touched (a nudge/set fire), never advanced
# by a background timer. Wall-clock (not monotonic) so a `persist` meter's drift is
# meaningful across a Restart / process gap and its stored timestamp stays valid.
import time as _time


def _meter_now() -> float:
    """Wall-clock seconds since the epoch. A module function so tests can patch it
    for a deterministic clock (the drift math is time-based)."""
    return _time.time()


def _meter_drift(value: float, rest: float, rate: float, elapsed: float) -> float:
    """Relax `value` toward `rest` by `rate` units per second over `elapsed`
    seconds, never overshooting rest. rate<=0 or elapsed<=0 leaves it unchanged."""
    if rate <= 0 or elapsed <= 0 or value == rest:
        return value
    step = rate * elapsed
    if value > rest:
        return max(rest, value - step)
    return min(rest, value + step)


def _meter_clamp(value: float, lo: float, hi: float) -> float:
    if hi < lo:
        hi = lo
    return min(max(value, lo), hi)


def _crossed_dir(old: float, new: float, threshold: float):
    """'up' if the value rose from below the threshold to at/above it, 'down' if it
    fell from at/above to below, else None. The threshold belongs to the high side
    (crossing UP includes landing exactly on it)."""
    if old < threshold <= new:
        return "up"
    if old >= threshold > new:
        return "down"
    return None


def _meter_f(cfg: dict, key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return float(default)


@node(id="core.state.meter", name="Meter", kind=Kind.TRANSFORM, category="State",
      summary="A float that persists across fires and drifts toward `rest` at "
              "`rate`/sec (lazy, no timer). Fire `nudge` to add `amount`; fire "
              "`set` to overwrite with `to` (both triggers must be wired); always "
              "clamped to [min,max]. Emits `value` and a `crossed` event (with "
              "direction up/down) when a fire moves it across `threshold`. A mood / "
              "energy / attention accumulator.")
class Meter:
    # every knob is promotable to a typed input. amount/to carry the two operations'
    # operands (pulled when the matching trigger fires), so any PULL-only source
    # (Float / Integer / Compute) can drive them; the palette has no pushed-number
    # producer, so the operands must ride pulled knobs, not pushed data ports.
    # rest/rate/min/max/threshold/start shape the drift + range; persist survives a
    # Restart via the shared KV store (the same store layer the KV nodes use).
    amount: Widget = Widget(kind="number", default=1.0, label="nudge amount", port_type="number")
    to: Widget = Widget(kind="number", default=0.0, label="set to", port_type="number")
    rest: Widget = Widget(kind="number", default=0, label="rest", port_type="number")
    rate: Widget = Widget(kind="number", default=0, label="drift rate / sec", port_type="number")
    min: Widget = Widget(kind="number", default=0, label="min", port_type="number")
    max: Widget = Widget(kind="number", default=100, label="max", port_type="number")
    threshold: Widget = Widget(kind="number", default=50, label="threshold", port_type="number")
    start: Widget = Widget(kind="number", default=0, label="start", port_type="number")
    persist: Widget = Widget(kind="bool", default=False,
                             label="persist across restart", port_type="bool")
    # `nudge` adds the `amount` knob; `set` overwrites with the `to` knob. Both are
    # triggering EVENT inputs (an event arriving fires the node), mirroring the
    # settled idiom (STT/TTS/LLM fire on a dedicated trigger and PULL their data),
    # and both must be wired. `value` is the current level (readable by a
    # downstream pull); `crossed` is an event carrying the direction.
    inputs = [Port("nudge", "event", trigger=True),
              Port("set", "event", trigger=True)]
    outputs = [Port("value", "number"), Port("crossed", "event")]

    def __init__(self):
        self._value = None
        self._last_ts = None
        self._loaded = False

    # ---- persistence (reuse the shared KV store; no parallel mechanism) --------
    def _store_key(self) -> str:
        cfg = getattr(self, "_node_cfg", {}) or {}
        return f"meter:{cfg.get('state_key') or getattr(self, '_node_id', 'meter')}"

    def _load(self):
        try:
            return _kv().get(self._store_key(), "state", None)
        except Exception:
            return None

    def _save(self, value: float, ts: float) -> None:
        try:
            _kv().set(self._store_key(), "state", {"value": value, "ts": ts})
        except Exception:
            pass

    def _ensure_loaded(self, persist: bool, start: float, now: float) -> None:
        if self._loaded:
            return
        self._loaded = True
        if persist:
            stored = self._load()
            if isinstance(stored, dict):
                self._value = _meter_f(stored, "value", start)
                self._last_ts = _meter_f(stored, "ts", now)
                return
        # persist off, or nothing stored yet: reset to `start` as of now (this is
        # why persist=False resets on Restart: a fresh instance re-inits here).
        self._value = float(start)
        self._last_ts = now

    def run(self, **ins):
        cfg = getattr(self, "_node_cfg", {}) or {}
        rest = _meter_f(cfg, "rest", 0.0)
        rate = max(0.0, _meter_f(cfg, "rate", 0.0))
        lo = _meter_f(cfg, "min", 0.0)
        hi = _meter_f(cfg, "max", 100.0)
        threshold = _meter_f(cfg, "threshold", 50.0)
        start = _meter_f(cfg, "start", 0.0)
        persist = bool(cfg.get("persist", False))
        now = _meter_now()
        self._ensure_loaded(persist, start, now)
        # committed value at the start of this fire (pre-drift). The crossing is
        # measured across the WHOLE transition (drift + operation) from here to the
        # final value, so a drift that carries the meter across `threshold` counts.
        old = self._value
        elapsed = max(0.0, now - self._last_ts)
        drifted = _meter_clamp(_meter_drift(old, rest, rate, elapsed), lo, hi)
        self._last_ts = now
        fired = getattr(self, "_fired_port", "")
        # the operands ride the knobs (pulled: a wired/promoted `amount`/`to` has
        # been folded into cfg by _apply_promoted before run()). No pushed number
        # producer exists in the palette, so a trigger + a pulled knob is the only
        # shape a user graph can actually drive.
        if fired == "set":
            new = _meter_f(cfg, "to", 0.0)              # overwrite with `to`
        elif fired == "nudge":
            new = drifted + _meter_f(cfg, "amount", 1.0)  # add `amount`
        else:
            new = drifted                # a direct call, no trigger named: drift only
        new = _meter_clamp(new, lo, hi)
        self._value = new
        if persist:
            self._save(new, now)
        out = {"value": new}
        direction = _crossed_dir(old, new, threshold)
        if direction is not None:
            out["crossed"] = {"direction": direction, "value": new,
                              "threshold": threshold, "from": old}
        return out


# ============================================================ trigger (agenda)
# A data-driven trigger: instead of a static cron it POLLS a wired store table of
# {when, payload} rows and fires each row when its `when` becomes due. It receives
# the store the same way the DB node does (a Database node emits a `db` handle
# into the `db` input) and reads it each poll via ctx.pull. Done-marking (or a
# delete fallback) stops a fired row from ever firing again, across polls or a
# Restart.
def _parse_when(value):
    """Liberal parse of a row's `when` into epoch seconds (wall clock). Accepts an
    int/float epoch, a numeric string, an ISO-8601 datetime (a trailing Z = UTC),
    or a few common 'YYYY-MM-DD[ HH:MM[:SS]]' shapes. A naive datetime is read in
    local time. Returns None when it cannot be parsed (the row is skipped, never
    fired blind)."""
    import datetime
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)                       # a bare epoch in a string
    except ValueError:
        pass
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        return datetime.datetime.fromisoformat(iso).timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def _maybe_json(value):
    """Parse a JSON string into structure; pass anything else through unchanged, so
    a payload stored as TEXT json reaches downstream as a dict/list, not a string."""
    if not isinstance(value, str):
        return value
    s = value.strip()
    if s and s[0] in "[{\"":
        try:
            return _json.loads(s)
        except Exception:
            return value
    return value


@node(id="core.trigger.agenda", name="Agenda", kind=Kind.TRIGGER, category="Triggers",
      summary="Fire from data instead of a fixed clock: poll a wired store table of "
              "{when, payload} rows and fire each row when its `when` is due. `when` "
              "is an ISO datetime or epoch seconds. It fires `due` with the payload "
              "and emits `payload`, then marks the row done (or deletes it) so it "
              "never fires twice. Several due rows fire one at a time, earliest first.")
class Agenda:
    poll: Widget = Widget(kind="number", default=60, min=1, max=86400, step=1,
                          label="poll (seconds)", port_type="number")
    # a live dropdown of the wired db's tables (options_from), or type a name. The
    # table holds rows shaped {when, payload}; an optional `done` column is used for
    # done-marking (the node adds nothing to your schema; it falls back to DELETE).
    table: Widget = tmpl("agenda", kind="text", port_type="text",
                         options_from="db.tables", placeholder="agenda table")
    inputs = [Port("db", "db")]
    outputs = [Port("due", "event"), Port("payload", "json")]

    def __init__(self):
        # rows fired this session (by rowid): a backstop so a row never double-fires
        # even if the store write (done-mark / delete) failed. The store marking is
        # the cross-Restart guarantee; this set is the within-session one.
        self._fired_rids: set = set()

    async def start(self, ctx):
        while ctx.alive:
            try:
                await self._poll_once(ctx)
            except Exception as exc:
                ctx.log(f"agenda poll error: {exc!r}")
            cfg = getattr(self, "_node_cfg", {}) or {}
            try:
                secs = max(1.0, float(cfg.get("poll") or 60))
            except (TypeError, ValueError):
                secs = 60.0
            await ctx.sleep(secs)

    async def _poll_once(self, ctx):
        cfg = getattr(self, "_node_cfg", {}) or {}
        table = str(cfg.get("table") or "").strip()
        if not table:
            return
        db = ctx.pull("db")
        if not db:
            return
        store = _store()
        qt = _quote_ident(table)
        try:
            rows = await asyncio.to_thread(store.query, db, f"SELECT rowid AS _rid, * FROM {qt}")
        except Exception as exc:
            ctx.log(f"agenda: cannot read table {table!r}: {exc}")
            return
        now = _meter_now()
        due = []
        for row in rows or []:
            rid = row.get("_rid")
            if rid in self._fired_rids or row.get("done"):
                continue
            when = _parse_when(row.get("when"))
            if when is None or when > now:
                continue
            due.append((when, rid, row))
        # fire the earliest first; break ties by rowid (insertion order).
        due.sort(key=lambda t: (t[0], t[1]))
        for _when, rid, row in due:
            payload = _maybe_json(row.get("payload"))
            # data before trigger on ONE turn; `due` carries the payload too so a
            # consumer firing on the event still has it when several rows fire in a
            # burst (the `payload` latch alone would show only the last row).
            ctx.emit_many({"payload": payload, "due": payload})
            self._fired_rids.add(rid)
            await self._mark_done(store, db, qt, rid)

    async def _mark_done(self, store, db, qt: str, rid) -> None:
        """Mark a fired row done so it never fires again. Prefer a `done` flag (the
        row is preserved for audit); fall back to DELETE when the table has no
        `done` column. Best-effort: any failure here is still covered within the
        session by `_fired_rids`."""
        try:
            await asyncio.to_thread(store.execute, db,
                                    f'UPDATE {qt} SET "done"=1 WHERE rowid=:rid', {"rid": rid})
            return
        except Exception:
            pass
        try:
            await asyncio.to_thread(store.execute, db,
                                    f"DELETE FROM {qt} WHERE rowid=:rid", {"rid": rid})
        except Exception:
            pass


# ============================================================ network
_MIME_EXT_MAP = {
    "jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp",
    "mpeg": "mp3", "mp3": "mp3", "wav": "wav", "x-wav": "wav",
    "ogg": "ogg", "opus": "opus", "octet-stream": "bin",
}


def _mime_to_ext(mime: str) -> str:
    """Map a MIME type like 'image/png' or 'audio/mpeg' to a filename extension."""
    if not mime:
        return "bin"
    sub = mime.split("/", 1)[-1].split(";")[0].strip().lower()
    return _MIME_EXT_MAP.get(sub, sub or "bin")


def _parse_form_lines(text: str) -> list[tuple[str, str]]:
    """One 'key=value' per line, first '=' is the split, blank lines ignored."""
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out.append((k.strip(), v))
    return out


async def _resolve_multipart_value(value: str):
    """Return a string (form field) or (filename, bytes, mime) for a file part.

    - data:<mime>;base64,... -> decoded to bytes, becomes a file part.
    - http(s)://... -> fetched via httpx (60s), becomes a file part using its content-type.
    - anything else -> returned as-is (form field string).
    """
    import base64
    import httpx

    v = value.strip()
    if v.startswith("data:") and ";base64," in v:
        try:
            header, b64 = v.split(",", 1)
            mime = header[len("data:"):].split(";", 1)[0].strip() or "application/octet-stream"
            data = base64.b64decode(b64)
            return (f"file.{_mime_to_ext(mime)}", data, mime)
        except Exception:
            return value
    if v.startswith("http://") or v.startswith("https://"):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(v, timeout=60.0)
            mime = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
            return (f"file.{_mime_to_ext(mime)}", resp.content, mime)
        except Exception:
            return value
    return value


# Binary data URL detection: an image/audio/video data: URL must NOT be coerced
# to a string when {tag}-substituted into a body, so the multipart detector can
# recognise it as a file part. Everything else (json, text, http(s) URLs) is fine
# as a string.
def _is_binary_data_url(v) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s.startswith("data:"):
        return False
    head = s[len("data:"):].split(",", 1)[0].lower()
    mime = head.split(";", 1)[0]
    return mime.startswith(("image/", "audio/", "video/")) or mime == "application/octet-stream"


# Response-type defaults for the binary modes: when the server forgot a
# content-type header (or sent a generic one) and the user asked for a specific
# modality, we use this to label the emitted data: URL.
_RESPONSE_TYPE_DEFAULT_MIME = {
    "image": "image/png",
    "audio": "audio/mpeg",
    "video": "video/mp4",
    "binary": "application/octet-stream",
}


@node(id="core.net.http", name="HTTP Request", kind=Kind.TRANSFORM, category="Network",
      summary="Call an external API. Fires on a trigger; resolves {{secret.NAME}} in "
              "url / headers / body and substitutes {tag} pipes from wired inputs. "
              "Auto-detects the request body shape (json / form / multipart / raw).")
class Http:
    method: Widget = select(["GET", "POST", "PUT", "PATCH", "DELETE"], default="GET")
    # url / headers / query / body are templates ({tag} + {{secret}}); declared so
    # the editor no longer needs the TEMPLATE_FIELDS / SECRET_AC_NODES tables.
    url: Widget = tmpl("https://", kind="text", port_type="text")
    headers: Widget = tmpl("")    # one "Key: Value" per line
    query: Widget = tmpl("")      # one "key=value" per line (optional)
    body: Widget = tmpl("")       # request body (auto-detected: json / form / multipart / raw)
    response_type: Widget = Widget(kind="select", default="auto",
                                   options=["auto", "text", "json", "image", "audio", "video", "binary"],
                                   label="response")
    # the `tag` growable port mirrors the Template node: each connection appears as
    # a socket named after the source and is substituted into {name} in the fields.
    inputs = [Port("trigger", "event", trigger=True),
              Port("tag", "any", growable=True, optional=True, ghost_base="tag")]
    outputs = [Port("status", "int"), Port("body", "text"), Port("json", "any"), Port("trigger", "event")]

    async def run(self, trigger=None, **inputs):
        import base64
        import httpx
        import json as _json
        cfg = getattr(self, "_node_cfg", {}) or {}

        # The runtime always seeds _node_cfg with the resolved field values; reading
        # `self.<name>` as a fallback would surface the class-level Widget object
        # (e.g. "Widget(name='query', kind='code', ...)") when the config is naked.
        # Missing cfg keys default to empty / "GET" / "auto" instead.
        method = str(cfg.get("method") or "GET").upper()
        url_raw = str(cfg.get("url") or "")
        headers_raw = str(cfg.get("headers") or "")
        query_raw = str(cfg.get("query") or "")
        body_raw = str(cfg.get("body") or "")
        response_type = str(cfg.get("response_type") or "auto").lower()

        # ---- template resolve helper: {{secret.X}} first, then {tag} from inputs.
        # A binary data: URL stays as itself (multipart needs to see the data: prefix);
        # every other value coerces to str.
        tag_kwargs = {k: v for k, v in inputs.items() if k != "trigger" and v is not None}

        def resolve(text: str) -> str:
            out = resolve_secrets(text)
            for k, v in tag_kwargs.items():
                token = "{" + k + "}"
                if token not in out:
                    continue
                rep = v if _is_binary_data_url(v) else str(v)
                out = out.replace(token, rep)
            return out

        url = resolve(url_raw)
        raw_headers = resolve(headers_raw)
        raw_query = resolve(query_raw)
        raw_body = resolve(body_raw)

        # Parse "Key: Value" header lines (split on first colon).
        headers: dict[str, str] = {}
        for line in raw_headers.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip()] = v.strip()

        # Parse "key=value" query lines (split on first =).
        params: dict[str, str] = {}
        for line in raw_query.splitlines():
            line = line.strip()
            if not line:
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                params[k.strip()] = v.strip()

        # ---- Auto-detect the request body shape.
        kwargs: dict = {"headers": headers, "params": params, "timeout": 60.0}
        # explicit Content-Type wins: send body raw, no detection.
        explicit_ct = None
        for hk in headers:
            if hk.lower() == "content-type":
                explicit_ct = headers[hk]
                break

        if raw_body and method != "GET":
            if explicit_ct is not None:
                kwargs["content"] = raw_body
            else:
                # 1) scan body lines for binary data: URLs -> multipart.
                form_lines = _parse_form_lines(raw_body)
                has_binary = any(_is_binary_data_url(v) for _, v in form_lines)
                stripped = raw_body.strip()
                if has_binary:
                    data_parts: dict[str, str] = {}
                    file_parts: dict[str, tuple] = {}
                    for name, raw_val in form_lines:
                        resolved = await _resolve_multipart_value(raw_val)
                        if isinstance(resolved, tuple):
                            file_parts[name] = resolved
                        else:
                            data_parts[name] = resolved
                    if file_parts:
                        kwargs["files"] = file_parts
                    if data_parts:
                        kwargs["data"] = data_parts
                # 2) JSON-shaped body
                elif stripped.startswith("{") or stripped.startswith("["):
                    try:
                        kwargs["json"] = _json.loads(raw_body)
                    except Exception:
                        # not real json, but starts with { or [: fall through to raw text.
                        kwargs["content"] = raw_body
                # 3) every non-blank line matches `name=value` -> form
                elif form_lines and all(
                    "=" in line and line.split("=", 1)[0].strip()
                    for line in (l.strip() for l in raw_body.splitlines()) if line
                ):
                    kwargs["data"] = {k: v for k, v in form_lines}
                # 4) any other non-empty body -> raw text.
                else:
                    kwargs["content"] = raw_body

        # ---- Fire the request.
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            return {"status": 0, "body": f"<request failed: {exc}>", "json": None, "trigger": True}

        # ---- Parse response per response_type.
        status = resp.status_code
        resp_ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()

        def _b64_data_url(mime: str) -> str:
            return f"data:{mime};base64,{base64.b64encode(resp.content).decode('ascii')}"

        if response_type == "text":
            body_out = resp.text
            try:
                parsed = resp.json()
            except Exception:
                parsed = None
        elif response_type == "json":
            try:
                parsed = resp.json()
            except Exception:
                parsed = None
            body_out = resp.text if parsed is None else parsed
        elif response_type in ("image", "audio", "video", "binary"):
            mime = resp_ct or _RESPONSE_TYPE_DEFAULT_MIME[response_type]
            body_out = _b64_data_url(mime)
            parsed = None
        else:  # auto
            try:
                parsed = resp.json()
            except Exception:
                parsed = None
            if resp_ct.startswith(("image/", "audio/", "video/")) or resp_ct == "application/octet-stream":
                body_out = _b64_data_url(resp_ct or "application/octet-stream")
            else:
                body_out = resp.text

        return {"status": status, "body": body_out, "json": parsed, "trigger": True}


# ============================================================ outputs (sinks)
@node(id="core.output.log", name="Log", kind=Kind.OUTPUT, category="Inspect",
      summary="Write the incoming value to the editor console and the server terminal.",
      icon="terminal-outline", subline="log · {label|clip:14}")
class Log:
    label: str = "log"
    inputs = [Port("in", "any", trigger=True)]
    outputs = [Port("trigger", "event")]

    def deliver(self, value, ctx, inputs=None):
        # the editor gets the value whole; the terminal prints the same line
        # summarized (a clip or an image as its type, size and length, long text cut).
        ctx.log(f"{self.label}: {value}", echo=True)
        return {"trigger": True}


@node(id="core.output.preview", name="Preview", kind=Kind.OUTPUT, category="Inspect",
      summary="Tap a wire and show its value live (adapts to the type). Sits "
              "mid-flow: when `trigger` fires it passes `in` on `out`, then the "
              "trigger on.",
      icon="eye-outline", subline="preview · live tap")
class Preview:
    # A transparent tap, so it can sit in the MIDDLE of a flow (in -> preview ->
    # out). `in` is the value to show and fires the node so a tap updates live;
    # it emits nothing (a value arriving is not a control event). `trigger` (e.g.
    # an LLM's done) must be wired: it re-emits the latest `in` on `out`, then
    # forwards exactly one `trigger`. Wiring one source into both (value, then
    # done) therefore passes one value and one trigger per turn, never two.
    inputs = [Port("in", "any", trigger=True),
              Port("trigger", "event", trigger=True)]
    outputs = [Port("out", "any"), Port("trigger", "event")]
    # disabled, Preview wires through (in -> out, trigger -> trigger) so it can be
    # bypassed mid-flow without breaking the line.
    bypass = {"in": "out", "trigger": "trigger"}

    def deliver(self, value, ctx, inputs=None):
        # the runtime sets `_fired_port` before deliver; a value handed straight
        # to deliver with no port named is a tap on `in`.
        if getattr(self, "_fired_port", "in") != "trigger":
            ctx.log(f"preview: {value}")
            self._in_since_trigger = True
            return {}
        # a `trigger` fire: `in` was latched by its own fire, or (fed by a PULLED
        # source such as a Text, which never fires `in`) is pulled right now. Emit
        # it on `out` BEFORE the trigger so a consumer fired by the trigger reads
        # it. Log it only when no `in` fire has already logged it since the last
        # trigger (the value came by pull), so the console shows each value once.
        in_val = (inputs or {}).get("in")
        if not getattr(self, "_in_since_trigger", False):
            ctx.log(f"preview: {in_val}")
        self._in_since_trigger = False
        return {"out": in_val, "trigger": True}


# ============================================================ wireless (virtual wire)
# A channel-addressed virtual wire so a signal can cross the canvas without a long
# cable. Wireless In broadcasts whatever is wired into it on a channel; Wireless Out
# on the same channel mirrors those same ports. The runtime never instantiates these
# (boltjar.runtime flattens each channel into direct source -> consumer edges at
# build), so they are pure editor-side routing with zero runtime cost.
_WIRELESS_CHANNELS = [str(i) for i in range(1, 101)]


@node(id="core.flow.wireless_in", name="Wireless In", kind=Kind.TRANSFORM, category="Flow",
      summary="Broadcast any wires on a channel (1 to 100). A Wireless Out on the "
              "same channel mirrors them, like a virtual wire across the canvas.")
class WirelessIn:
    channel: Widget = select(_WIRELESS_CHANNELS, default="1")
    # any wire of any kind; each socket is named after its source (like a tag).
    inputs = [Port("in", "any", growable=True, optional=True, ghost_base="wire")]
    # no outputs and no run: the runtime flattens this away at build.


@node(id="core.flow.wireless_out", name="Wireless Out", kind=Kind.TRANSFORM, category="Flow",
      summary="Receive the wires broadcast by the Wireless In on the same channel "
              "(1 to 100). Its outputs mirror that In's sockets.")
class WirelessOut:
    channel: Widget = select(_WIRELESS_CHANNELS, default="1")
    # outputs are dynamic: the editor mirrors the matching Wireless In's sockets.
    # The runtime flattens this away at build (direct source -> consumer edges).


@node(id="core.flow.router", name="Router", kind=Kind.TRANSFORM, category="Flow",
      summary="Reroute a wire anywhere on the canvas: one in, one out, no change. "
              "It is a pure bypass, so the wire keeps its source name and type even "
              "through several routers in a row. Optional centred label.")
class Router:
    # a free-text label shown centred in the pill (empty by default; duplicates are
    # fine - it never affects the wire). The runtime ALWAYS flattens the router away
    # (in -> out), so it is pure editor-side routing with zero runtime cost.
    label: Widget = Widget(kind="text", default="", label="label")
    inputs = [Port("in", "any")]
    outputs = [Port("out", "any")]
    bypass = {"in": "out"}


def _coerce_list(value) -> list:
    """Turn whatever lands on For-each's `list` (type `any`) into a real list:
    a list stays; a str is tried as json then wrapped; None is []; anything else
    is wrapped as a single-item list. So List Dir's json, a hand-built `list`,
    or a stray scalar all iterate without wiring friction."""
    import json as _json
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = _json.loads(value)
        except Exception:
            parsed = None
        return parsed if isinstance(parsed, list) else [value]
    if isinstance(value, tuple):
        return list(value)
    return [value]


@node(id="core.flow.for_each", name="For-each", kind=Kind.TRANSFORM, category="Flow",
      opens_turn=True,
      summary="Dispense a list one item at a time. Fire `trigger` to start; wire the "
              "body's done back into `loop` to release the next (both triggers must "
              "be wired); `after_last` fires when the list is exhausted. "
              "Type-agnostic (text, image, file, json).")
class ForEach:
    # `trigger` starts the loop; `loop` is the back-edge ("body finished this item,
    # release the next"). Both must be wired: without the back-edge the loop would
    # stop after its first item.
    inputs = [Port("trigger", "event", trigger=True),
              Port("loop", "event", trigger=True),
              Port("list", "any")]
    # `each` pulses per item, `item` carries the current element (the body pulls it),
    # `after_last` fires once when the list runs out.
    outputs = [Port("each", "event"),
               Port("item", "any"),
               Port("after_last", "event")]

    def __init__(self):
        self._items: list = []
        self._cursor: int = 0
        self._active: bool = False

    def run(self, **ins):
        fired = getattr(self, "_fired_port", "trigger")
        if fired == "loop":
            # the body finished an item: release the next, or signal exhaustion.
            if self._cursor < len(self._items):
                item = self._items[self._cursor]
                self._cursor += 1
                # `item` BEFORE `each`: the data latch is set before the body fires.
                return {"item": item, "each": True}
            self._active = False
            return {"after_last": True}
        # `trigger` (or any non-loop start): single-flight. While a loop is in flight
        # (active until after_last), ignore a new start so its cursor/items are not
        # clobbered (pace concurrent producers through a Queue). Lean: no raise.
        if self._active:
            return {}
        self._items = _coerce_list(ins.get("list"))
        self._cursor = 0
        if not self._items:
            return {"after_last": True}
        self._active = True
        item = self._items[0]
        self._cursor = 1
        return {"item": item, "each": True}


_CHANGED_UNSET = object()  # sentinel so a Changed node's first value always counts


@node(id="core.flow.changed", name="Changed", kind=Kind.TRANSFORM, category="Flow",
      summary="Pass a value on only when it differs from the last one (a dedup "
              "gate). Interval, HTTP Request, Changed and LLM in a row poll a source "
              "and run the LLM only on a real change. The first value always passes.")
class Changed:
    inputs = [Port("trigger", "event", trigger=True),
              Port("value", "any")]
    outputs = [Port("changed", "event"),
               Port("value", "any"),
               Port("same", "event", optional=True)]

    def __init__(self):
        self._last = _CHANGED_UNSET

    def run(self, value=None, **_):
        # content equality (so dicts/lists compare by value, not identity); a
        # non-serialisable value falls back to its str.
        try:
            key = _json.dumps(value, sort_keys=True, default=str)
        except Exception:
            key = str(value)
        if self._last is _CHANGED_UNSET or key != self._last:
            self._last = key
            return {"changed": True, "value": value}
        return {"same": True}


@node(id="core.flow.sync", name="Sync", kind=Kind.TRANSFORM, category="Flow",
      opens_turn=True,
      summary="A control barrier: it fires one trigger once every wired input has "
              "fired, then resets. It waits for the slowest branch, and `in` needs at "
              "least one wire. It touches no data; data rides its own wires.")
class Sync:
    # growable trigger inputs; emits `out` once every wired socket has arrived.
    # A trigger, so at least one socket must be wired.
    inputs = [Port("in", "event", growable=True, trigger=True)]
    outputs = [Port("out", "event")]

    def __init__(self):
        self._seen: set = set()

    def run(self, **ins):
        fired = getattr(self, "_fired_port", None)
        if fired is not None:
            self._seen.add(fired)
        ctx = getattr(self, "_ctx", None)
        wired = ctx.wired_input_ports() if ctx is not None else set(ins)
        # every socket of the growable `in` is a branch, whatever its name, found
        # the way the runtime fires it (NodeSpec.growable_base), so a future
        # static input would not be miscounted as one.
        total = sum(1 for p in wired if self._spec.growable_base(p) is not None)
        if total > 0 and len(self._seen) >= total:
            self._seen = set()
            return {"out": True}
        return {}


@node(id="core.flow.wait", name="Wait", kind=Kind.TRANSFORM, category="Flow",
      summary="Relay a trigger after a delay. 1:1 (trigger in -> trigger out). Pick "
              "the amount and the unit; paces a For-each loop or throttles a chain.")
class Wait:
    # use ready widgets only: a number box + a unit dropdown.
    amount: Widget = Widget(kind="number", default=1, label="amount")
    unit: Widget = select(["Seconds", "Minutes", "Hours"], default="Seconds")
    inputs = [Port("trigger", "event", trigger=True)]
    outputs = [Port("trigger", "event")]
    # passthrough: disabling a Wait wires its trigger straight through.
    bypass = {"trigger": "trigger"}

    _UNIT_SECONDS = {"Seconds": 1, "Minutes": 60, "Hours": 3600}

    async def run(self, trigger=None, **_):
        cfg = getattr(self, "_node_cfg", {}) or {}
        amount = cfg.get("amount", getattr(self, "amount", 1))
        unit = cfg.get("unit", "Seconds")
        try:
            secs = float(amount) * self._UNIT_SECONDS.get(unit, 1)
        except (TypeError, ValueError):
            secs = 0.0
        if secs > 0:
            await asyncio.sleep(secs)
        # 1:1 relay: re-emit the trigger after the wait (a For-each loop advances).
        return {"trigger": True}


@node(id="core.flow.queue", name="Queue", kind=Kind.TRANSFORM, category="Flow",
      opens_turn=True,
      summary="Run many producers through one lane. Each arrival on `in` waits in "
              "a buffer, `out` releases one item, and the next goes only when `ack` "
              "fires, so two turns reach a shared LLM one after the other. Both "
              "triggers must be wired: the producers into `in`, and the node that ends "
              "the turn into `ack`.")
class Queue:
    # a watchdog so a job whose `ack` never fires (a tool body that raised, a broken
    # chain) cannot wedge the lane forever: if the current job has been outstanding
    # past `timeout` seconds, the lane self-heals and releases the next. 0 = wait
    # forever (strict backpressure). A real turn finishes well under the default.
    timeout: Widget = Widget(kind="number", default=300, min=0, max=86400, step=10,
                             label="ack timeout (s, 0 = wait forever)")
    # growable trigger `in`: every producer pushes here (Chat, Audio, Interval, ...),
    # each on a socket named after it. `ack` is the back-edge: 'the current job
    # finished, release the next'. Both must be wired.
    inputs = [Port("in", "any", growable=True, trigger=True, ghost_base="in"),
              Port("ack", "event", trigger=True)]
    # `out` is the released item (same shape in as out); `count` is the pending depth.
    outputs = [Port("out", "any"), Port("count", "int")]

    def __init__(self):
        self._buf: list = []
        self._busy: bool = False
        self._busy_since: float = 0.0

    def run(self, **ins):
        import time
        cfg = getattr(self, "_node_cfg", {}) or {}
        timeout = float(cfg.get("timeout") or 0)
        fired = getattr(self, "_fired_port", "")
        # self-heal: free a lane that has been busy past the timeout (a missing ack).
        if self._busy and timeout > 0 and (time.monotonic() - self._busy_since) > timeout:
            self._busy = False
        if fired == "ack":
            self._busy = False               # the lane is free again
        elif fired:
            self._buf.append(ins.get(fired))  # a socket of `in` fired: buffer its payload
        # release the FIFO head only when the lane is free; opens_turn means the
        # released job runs on a fresh epoch (re-pulls / the LLM re-runs per item).
        if not self._busy and self._buf:
            item = self._buf.pop(0)
            self._busy = True
            self._busy_since = time.monotonic()
            return {"out": item, "count": len(self._buf)}
        return {"count": len(self._buf)}


@node(id="core.output.chat", name="Chat", kind=Kind.OUTPUT, category="Inspect",
      summary="Show the running conversation: your message and the model's reply. "
              "A live viewer, like Preview, that keeps history. Each side commits "
              "when its trigger fires, so both triggers must be wired.",
      icon="chatbubbles-outline")
class ChatOutput:
    # a viewer with TWO sides, each its own DATA + TRIGGER: `user`/`user_trigger`
    # commit your turn, `reply`/`reply_trigger` commit the model's turn. Each run
    # fires its own trigger, so turns are explicit and never aggregated onto a data
    # port. Both triggers must be wired. A pure sink: no output (it is the end of
    # the flow).
    # each side keeps its trigger ABOVE its data (trigger, text, trigger, text),
    # matching the "trigger on top" standard applied per side.
    inputs = [Port("user_trigger", "event", trigger=True),
              Port("user", "text", optional=True),
              Port("reply_trigger", "event", trigger=True),
              Port("reply", "text", optional=True)]
    outputs: list = []

    def deliver(self, value, ctx, inputs=None):
        # the editor renders the conversation from the wired streams, committing a
        # turn when its side's trigger fires. The sink just lights up; no output.
        return {}


@node(id="core.output.avatar", name="Avatar", kind=Kind.OUTPUT, category="Output",
      summary="Stream one avatar chunk per fire (text, audio, mood, action and lang) "
              "to an avatar client, such as a VRM renderer, over the /stream SSE. For "
              "one chunk per sentence, fire it once per sentence (a For-each over "
              "Sentences). Wire the separate ports or one combined `utterance` JSON; "
              "the separate ports win. Lip-sync stays on the client.")
class Avatar:
    channel: Widget = Widget(kind="text", default="avatar", label="stream channel")
    inputs = [Port("trigger", "event", trigger=True),
              Port("text", "text", optional=True),
              Port("audio", "audio", optional=True),
              Port("mood", "mood", optional=True),
              Port("action", "action", optional=True),
              Port("lang", "lang", optional=True),
              # wire a truthy value here on the LAST chunk of a turn (e.g. For-each's
              # after_last) so the client knows the utterance ended.
              Port("done", "bool", optional=True),
              Port("utterance", "utterance", optional=True)]
    # passthrough trigger so an avatar frame can sequence/pace the next node
    # (e.g. release a Queue's next job after the sentence is committed).
    outputs = [Port("trigger", "event")]

    def deliver(self, value, ctx, inputs=None):
        ins = inputs or {}
        cfg = getattr(self, "_node_cfg", {}) or {}
        channel = str(cfg.get("channel") or "avatar")
        # a combined `utterance` json supplies defaults; the separate ports win.
        u = ins.get("utterance")
        base = dict(u) if isinstance(u, dict) else {}

        def pick(name):
            v = ins.get(name)
            return v if v is not None else base.get(name)

        chunk = {
            "text": pick("text"),
            "audio": pick("audio"),
            "mood": pick("mood"),
            "action": pick("action"),
            "lang": pick("lang"),
            "is_chunk_start": True,
            "is_chunk_final": True,
            "done": bool(pick("done")),
        }
        ctx.publish(channel, chunk)
        ctx.log(f"-> avatar: {str(chunk.get('text') or '')[:60]}")
        return {"trigger": True}


# ============================================================ helpers
_MOCK_OPENERS = (
    "Got it.",
    "Sure.",
    "Understood.",
    "Okay.",
    "Noted.",
)


def _mock_reply(prompt: str) -> str:
    """A believable stand-in reply used when no model keys are configured.

    If the prompt is chat-formatted (a trailing ``User:`` turn), answer like an
    assistant and quote the message back so the data flow is visible. For any
    other prompt, echo a trimmed snippet so a pull stays observable in tests.
    """
    user_line = ""
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("user:"):
            user_line = stripped[len("user:"):].strip()
    if user_line:
        opener = _MOCK_OPENERS[len(user_line) % len(_MOCK_OPENERS)]
        return f"{opener} (you said: “{user_line}”) · mock"
    snippet = " ".join(prompt.split())[:160]
    return f"[mock] {snippet}"


_PROVIDER_KEYS = {"xai": "XAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY",
                  "openai": "OPENAI_API_KEY"}


def _provider_ready(provider: str) -> bool:
    """Mock is always ready; Ollama is assumed local; cloud needs its key set; a
    custom OpenAI-compatible endpoint needs to exist (and its key, if it takes one)."""
    if provider in ("mock", "ollama"):
        return True
    if provider in _PROVIDER_KEYS:
        return bool(os.environ.get(_PROVIDER_KEYS[provider]))
    return endpoints.resolve(provider) is not None


# The synthetic capability knobs (see boltjar.models): the thinking lever and
# the JSON-output toggle. Their values arrive in `params`; each provider call
# reshapes them to the right wire format.
_THINK = "think"
_JSON = "json"


def _think_on(params: dict) -> bool:
    """Whether the thinking lever is engaged (handles bool, level, and effort)."""
    v = params.get(_THINK)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        return v.lower() not in ("", "off", "none", "false", "0", "disabled")
    return False


def _json_on(params: dict) -> bool:
    return bool(params.get(_JSON))


async def _call_model(manifest, model_id: str, prompt: str, params: dict, media: dict,
                      ctx=None, tools: list | None = None) -> dict:
    """Dispatch to the selected model's provider. Falls back to the mock reply
    when a cloud provider has no key, so a graph always runs offline. Returns
    `response` (the reply) and optionally `reasoning` (the thinking trace).

    `tools` is the list of {id, name, description, parameters} for every wired
    Tool node; `ctx` is the LLM's runtime Ctx, used by the provider call to
    invoke a tool via ctx.call_tool(). Google ignores tools; Ollama, xAI,
    Anthropic and the OpenAI-compatible endpoints run the full tool loop.
    """
    provider = manifest.provider if manifest else "mock"
    model = manifest.model if manifest else (model_id.split("/", 1)[-1] or "echo")

    if provider == "mock" or not _provider_ready(provider):
        return {"response": _mock_reply(prompt)}

    if provider == "ollama":
        text, reasoning = await _ollama(model, prompt, params, media, ctx=ctx, tools=tools)
    elif provider == "xai":
        text, reasoning = await _xai(model, prompt, params, media, ctx=ctx, tools=tools)
    elif provider == "anthropic":
        text, reasoning = await _anthropic(model, prompt, params, media, ctx=ctx, tools=tools)
    elif provider == "google":
        text, reasoning = await _google(model, prompt, params)
    elif (endpoint := endpoints.resolve(provider)) is not None:
        text, reasoning = await _openai_chat(f"{endpoint.base_url}/chat/completions",
                                             endpoint.key(), model, prompt, params, media,
                                             ctx=ctx, tools=tools)
    else:
        text, reasoning = _mock_reply(prompt), ""

    out = {"response": text}
    if reasoning:
        out["reasoning"] = reasoning
    return out


def _opts(params: dict, keys: tuple) -> dict:
    return {k: params[k] for k in keys if params.get(k) is not None}


def _image_to_b64(image: str) -> tuple[str, str]:
    """Resolve an image value to `(media_type, base64_without_prefix)`.

    A `data:image/...;base64,...` URL is split in place (no re-encode). An
    http(s) URL is fetched and base64-encoded, with the media type guessed from
    the response Content-Type (defaulting to image/jpeg). Used by the Anthropic
    (base64 image block) and Ollama (bare base64 in `images`) paths."""
    import base64
    import httpx

    s = (image or "").strip()
    if s.startswith("data:") and ";base64," in s:
        head, b64 = s.split(";base64,", 1)
        media_type = head[len("data:"):] or "image/jpeg"
        return media_type, b64
    if s.lower().startswith(("http://", "https://")):
        resp = httpx.get(s, timeout=60)
        resp.raise_for_status()
        media_type = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
        if not media_type.startswith("image/"):
            media_type = "image/jpeg"
        b64 = base64.b64encode(resp.content).decode("ascii")
        return media_type, b64
    # already-bare base64 (or some other string): treat as jpeg base64.
    return "image/jpeg", s


async def _ollama(model: str, prompt: str, params: dict, media: dict,
                  ctx=None, tools: list | None = None) -> tuple[str, str]:
    """Ollama. Thinking is top-level `think: bool` and returns the trace in
    `thinking`; JSON output is top-level `format: "json"`.

    Without tools we hit /api/generate (the lean, single-shot path). With wired
    tools we switch to /api/chat (the OpenAI-style tool-loop endpoint), declare
    `tools=[{type:function, function:{name, description, parameters}}]`, and on
    a `message.tool_calls` response dispatch each call via `ctx.call_tool(id,
    args)`, then append the assistant turn verbatim plus one `{role:tool,
    content:<result>}` message per call (Ollama matches by order/role, NOT id)
    and re-POST. Loops up to 6 hops so a model that hallucinates calls cannot
    spin forever, mirroring the xAI cap."""
    import httpx
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    tools = tools or []
    # ---- single-shot path: no tools, keep /api/generate exactly as before.
    if not tools:
        body = {"model": model, "prompt": prompt, "stream": False,
                "options": _opts(params, ("temperature", "top_p", "top_k", "num_ctx", "seed"))}
        if params.get("keep_alive"):
            body["keep_alive"] = params["keep_alive"]
        if _think_on(params):
            body["think"] = True
        if _json_on(params):
            body["format"] = "json"
        if media.get("image"):
            imgs = media["image"]
            imgs = imgs if isinstance(imgs, list) else [imgs]
            # Ollama wants bare base64 (NO data: prefix) per image; strip/encode each.
            # to_thread so an http(s) image fetch never blocks the event loop.
            body["images"] = [(await asyncio.to_thread(_image_to_b64, img))[1] for img in imgs]
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{base}/api/generate", json=body)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", ""), data.get("thinking", "") or ""

    # ---- tool-loop path: /api/chat with a name -> Tool-node-id index.
    name_to_id = {t["name"]: t["id"] for t in tools}
    messages: list[dict] = [{"role": "user", "content": prompt}]
    if media.get("image"):
        imgs = media["image"]
        imgs = imgs if isinstance(imgs, list) else [imgs]
        messages[0]["images"] = [(await asyncio.to_thread(_image_to_b64, img))[1] for img in imgs]
    base_body: dict = {
        "model": model, "stream": False,
        "options": _opts(params, ("temperature", "top_p", "top_k", "num_ctx", "seed")),
        "tools": [
            {"type": "function",
             "function": {"name": t["name"], "description": t.get("description", ""),
                          "parameters": t.get("parameters") or {"type": "object", "properties": {}, "required": []}}}
            for t in tools
        ],
    }
    if params.get("keep_alive"):
        base_body["keep_alive"] = params["keep_alive"]
    if _think_on(params):
        base_body["think"] = True
    if _json_on(params):
        base_body["format"] = "json"

    reasoning_text = ""
    final_text = ""
    async with httpx.AsyncClient(timeout=120) as client:
        for _ in range(6):
            body = dict(base_body)
            body["messages"] = messages
            resp = await client.post(f"{base}/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message") or {}
            if msg.get("thinking"):
                reasoning_text = msg.get("thinking") or reasoning_text
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls or ctx is None:
                final_text = msg.get("content", "") or ""
                break
            # Append the assistant turn verbatim, then one role:tool message per
            # call. Ollama has no tool_call_id; it matches by message order/role.
            messages.append(dict(msg))
            for call in tool_calls:
                fn = call.get("function") or {}
                tname = fn.get("name") or ""
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        args = _json.loads(raw_args)
                    except Exception:
                        args = {}
                else:
                    args = dict(raw_args or {})
                tid = name_to_id.get(tname)
                if tid is None:
                    result = f"<no tool named {tname!r}>"
                else:
                    try:
                        result = await ctx.call_tool(tid, args)
                    except Exception as exc:
                        result = f"<tool {tname} error: {exc}>"
                messages.append({"role": "tool", "content": result})
    return final_text, reasoning_text


async def _xai(model: str, prompt: str, params: dict, media: dict | None = None,
               ctx=None, tools: list | None = None) -> tuple[str, str]:
    """xAI Grok, over its OpenAI-compatible chat completions (see _openai_chat)."""
    return await _openai_chat("https://api.x.ai/v1/chat/completions",
                              os.environ.get("XAI_API_KEY", ""), model, prompt, params,
                              media, ctx=ctx, tools=tools)


# the think values that send no reasoning effort (the provider default).
_NO_EFFORT = ("", "off", "false", "0")


async def _openai_chat(url: str, key: str, model: str, prompt: str, params: dict,
                       media: dict | None = None, ctx=None,
                       tools: list | None = None) -> tuple[str, str]:
    """An OpenAI-compatible chat completion: xAI, OpenAI and every custom endpoint
    (OpenRouter, Groq, LM Studio, llama.cpp, vLLM). `key` rides a Bearer header
    and is left out when empty (a local server takes none).

    Thinking is top-level `reasoning_effort` (the think knob's value, e.g. none|
    low|medium|high; "off" or empty sends nothing, the provider default); the
    trace comes back as `reasoning_content` (xAI) or `reasoning` (OpenRouter).
    JSON output is `response_format: json_object`. Sampling knobs (temperature,
    top_p, max_completion_tokens, max_tokens, seed) are sent only when the model
    declares them.

    With an image (`media["image"]`), the user content is an OpenAI-style array:
    an `image_url` block (an https URL or a `data:image/...;base64,...` URL,
    passed through without a re-encode) followed by the text block. No image ->
    plain str.

    With wired `tools` (each {id, name, description, parameters}) the body
    declares `tools=[{"type":"function","function":{...}}]`; when the model
    responds with `tool_calls`, each call is dispatched to its Tool node via
    `ctx.call_tool(id, args)`, the result is appended as a `role:tool` message
    (with `tool_call_id`), and the endpoint is hit again. The loop runs up to 6
    hops before returning whatever text the last response carries, so a
    misbehaving model can never spin forever."""
    import httpx
    media = media or {}
    image = media.get("image")
    if image:
        content: list | str = [
            {"type": "image_url", "image_url": {"url": image}},
            {"type": "text", "text": prompt},
        ]
    else:
        content = prompt
    messages: list[dict] = [{"role": "user", "content": content}]

    base_body: dict = {"model": model}
    base_body.update(_opts(params, ("temperature", "top_p", "max_completion_tokens",
                                    "max_tokens", "seed")))
    effort = params.get(_THINK)
    if isinstance(effort, str) and effort.strip().lower() not in _NO_EFFORT:
        base_body["reasoning_effort"] = effort.strip().lower()
    if _json_on(params):
        base_body["response_format"] = {"type": "json_object"}

    # Tools: build the wire payload and a name -> node-id index for dispatch.
    tools = tools or []
    if tools:
        base_body["tools"] = [
            {"type": "function",
             "function": {"name": t["name"], "description": t.get("description", ""),
                          "parameters": t.get("parameters") or {"type": "object", "properties": {}, "required": []}}}
            for t in tools
        ]
    name_to_id = {t["name"]: t["id"] for t in tools}
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    reasoning_text = ""
    final_text = ""
    async with httpx.AsyncClient(timeout=60) as client:
        for _ in range(6):
            body = dict(base_body)
            body["messages"] = messages
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
            trace = msg.get("reasoning_content") or msg.get("reasoning")
            if isinstance(trace, str) and trace:
                # the last non-empty trace wins; intermediate hops can carry one too.
                reasoning_text = trace
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls or not tools or ctx is None:
                final_text = msg.get("content", "") or ""
                break
            # Append the assistant turn that carries the tool_calls EXACTLY as
            # returned, then one role:tool message per call with the dispatch
            # result. The API requires both for the next call.
            messages.append({"role": "assistant",
                             "content": msg.get("content", "") or "",
                             "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function") or {}
                tname = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = _json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    args = {}
                tid = name_to_id.get(tname)
                if tid is None:
                    result = f"<no tool named {tname!r}>"
                else:
                    try:
                        result = await ctx.call_tool(tid, args)
                    except Exception as exc:
                        result = f"<tool {tname} error: {exc}>"
                messages.append({"role": "tool",
                                 "tool_call_id": call.get("id", ""),
                                 "content": result})
    return final_text, reasoning_text


def _raise_for_vendor(vendor: str, resp) -> None:
    """Raise with the vendor's own words when a call failed:
    `<Vendor> <status>: <body[:300]>`. The body is where vendors put the reason;
    a bare raise_for_status() drops it (Fish's chunk_length 400 read as a plain
    "400 Bad Request"). A streamed response must be read (aread) before this."""
    if not resp.is_success:
        raise RuntimeError(f"{vendor} {resp.status_code}: {resp.text[:300]}")


# Fish Audio S2 Pro TTS (https://api.fish.audio/v1/tts). A friendly `voice` name
# maps through _FISH_VOICES (empty by default); a raw 32-char reference id passes
# through unchanged. Returns a base64 data URL so the editor's audio player plays
# the real clip inline. Needs FISH_API_KEY (in .env).
_FISH_VOICES: dict[str, str] = {}
_FISH_MIME = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg",
              "opus": "audio/ogg", "pcm": "audio/wave"}
# Fish rejects a chunk_length outside [100, 300] with a 400 (live, 2026-09-23).
_FISH_CHUNK_LENGTH = (100, 300)


def _fish_reference(voice: str) -> str:
    """A known voice name maps through _FISH_VOICES and a raw 20+ char hex
    reference id passes through. An empty voice asks for a reference id; anything
    else raises and names the voice: an unknown voice must never silently fall
    back to some other voice."""
    v = voice.strip()
    if not v:
        raise RuntimeError("Fish: set a voice reference_id")
    if voice in _FISH_VOICES:
        return _FISH_VOICES[voice]
    if len(v) >= 20 and all(c in "0123456789abcdefABCDEF" for c in v):
        return v
    names = f"a name from _FISH_VOICES ({', '.join(sorted(_FISH_VOICES))}) or " if _FISH_VOICES else ""
    raise RuntimeError(f"Fish: unknown voice {voice!r} (use {names}a Fish reference id)")


async def _fish_tts(text: str, voice: str, fmt: str = "wav", chunk_length: int = 100) -> str:
    import base64
    import httpx
    key = os.environ.get("FISH_API_KEY", "")
    if not key:
        raise RuntimeError("FISH_API_KEY not set in .env")
    reference_id = _fish_reference(voice)
    lo, hi = _FISH_CHUNK_LENGTH
    payload = {"text": text, "reference_id": reference_id, "format": fmt,
               "normalize": True, "latency": "normal",
               "chunk_length": min(hi, max(lo, int(chunk_length)))}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "model": "s2-pro"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post("https://api.fish.audio/v1/tts", json=payload, headers=headers)
        _raise_for_vendor("Fish", resp)
        b64 = base64.b64encode(resp.content).decode("ascii")
    return f"data:{_FISH_MIME.get(fmt, 'audio/wav')};base64,{b64}"


def _audio_to_bytes(audio: str) -> bytes:
    """Decode a clip value to raw bytes: a base64 `data:` url (what the TTS emits)
    is decoded; anything else is not a playable clip and cannot be transcribed."""
    import base64
    a = audio.strip()
    if a.startswith("data:") and "," in a:
        return base64.b64decode(a.split(",", 1)[1])
    raise RuntimeError("STT needs a clip (a data: audio value), not plain text")


def _audio_mime(audio: str) -> str:
    """The MIME a data: clip declares (data:audio/mpeg;base64,... -> audio/mpeg)."""
    head = audio.strip().split(",", 1)[0]
    return head[len("data:"):].split(";", 1)[0].strip() or "application/octet-stream"


async def _fish_stt(audio: str) -> tuple[str, str]:
    """Fish Audio ASR: transcribe a clip (the TTS data url) back to text through
    POST api.fish.audio/v1/asr (multipart upload). Needs FISH_API_KEY. Returns
    (text, detected_language) - language is "" when the provider omits it."""
    import httpx
    key = os.environ.get("FISH_API_KEY", "")
    if not key:
        raise RuntimeError("FISH_API_KEY not set in .env")
    audio_bytes = _audio_to_bytes(audio)
    files = {"audio": ("clip.wav", audio_bytes, "audio/wav")}
    data = {"ignore_timestamps": "false"}
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post("https://api.fish.audio/v1/asr", headers=headers,
                                 files=files, data=data)
        _raise_for_vendor("Fish", resp)
        body = resp.json()
        return (body.get("text") or "").strip(), str(body.get("language") or "")


async def _eleven_tts(text: str, voice_id: str, model_id: str, settings: dict) -> str:
    """ElevenLabs TTS: POST /v1/text-to-speech/{voice_id} -> raw mp3 bytes ->
    a data: URL the editor's audio player can decode. xi-api-key auth.
    voice_settings carries stability/similarity_boost/speed."""
    import httpx, base64
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not set in .env")
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": float(settings.get("stability", 0.5)),
            "similarity_boost": float(settings.get("similarity_boost", 0.75)),
            "speed": float(settings.get("speed", 1.0)),
        },
    }
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post(url, headers=headers, json=payload)
        _raise_for_vendor("ElevenLabs", resp)
        b64 = base64.b64encode(resp.content).decode("ascii")
        return f"data:audio/mpeg;base64,{b64}"


async def _eleven_stt(audio: str, model_id: str = "scribe_v2") -> tuple[str, str]:
    """ElevenLabs Scribe: POST /v1/speech-to-text (multipart). xi-api-key auth.
    Returns (text, detected_language) from the response's text + language_code."""
    import httpx
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not set in .env")
    audio_bytes = _audio_to_bytes(audio)
    files = {"file": ("clip.wav", audio_bytes, "audio/wav")}
    data = {"model_id": model_id}
    headers = {"xi-api-key": key}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post("https://api.elevenlabs.io/v1/speech-to-text",
                                 headers=headers, files=files, data=data)
        _raise_for_vendor("ElevenLabs", resp)
        body = resp.json()
        return (body.get("text") or "").strip(), str(body.get("language_code") or "")


# xAI voice (https://api.x.ai/v1/tts and /v1/stt, Bearer XAI_API_KEY). xAI's TTS
# REQUIRES a language; _XAI_TTS_LANGUAGES is its list (docs.x.ai, 2026-09-23). A
# bare code maps to a default region: pt -> pt-BR, es -> es-ES. The other bare
# codes (en zh fr de hi id it ja ko ru bn tr vi) are on the list as they are.
_XAI_TTS_URL = "https://api.x.ai/v1/tts"
_XAI_STT_URL = "https://api.x.ai/v1/stt"
_XAI_TTS_LANGUAGES = ("auto", "en", "ar-EG", "ar-SA", "ar-AE", "bn", "zh", "fr", "de",
                      "hi", "id", "it", "ja", "ko", "pt-BR", "pt-PT", "ru", "es-MX",
                      "es-ES", "tr", "vi")
_XAI_TTS_BARE = {"pt": "pt-BR", "es": "es-ES"}
_XAI_TTS_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav"}


def _xai_language(code) -> str:
    """A language code as xAI's `language`: matched case-insensitively against
    its list (pt-br -> pt-BR), bare pt -> pt-BR and es -> es-ES, anything else
    -> auto (xAI detects it rather than a wrong language being forced). xAI's own
    STT reports lowercase regions ("pt-br", seen live 2026-09-23), so the STT
    `lang` wires straight into the TTS `lang` through this match."""
    c = str(code or "").strip().lower()
    for known in _XAI_TTS_LANGUAGES:
        if known.lower() == c:
            return known
    return _XAI_TTS_BARE.get(c, "auto")


async def _xai_tts(text: str, voice: str, language: str, codec: str = "mp3",
                   sample_rate: int = 44100, speed: float = 1.0) -> str:
    """xAI TTS: POST /v1/tts -> the whole clip as a data: URL (mp3 or wav). The
    body is read as a stream and joined, so emitting one fire per chunk later
    only changes what is done with each chunk. Empty text makes no call."""
    import base64
    import httpx
    if not text:
        return ""
    mime = _XAI_TTS_MIME.get(codec)
    if mime is None:
        raise RuntimeError(f"xAI TTS: unsupported codec {codec!r} "
                           f"(one of: {', '.join(_XAI_TTS_MIME)})")
    key = os.environ.get("XAI_API_KEY", "")
    if not key:
        raise RuntimeError("XAI_API_KEY not set in .env")
    payload = {"text": text, "voice_id": voice, "language": language,
               "output_format": {"codec": codec, "sample_rate": int(sample_rate)},
               "speed": float(speed)}
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        async with client.stream("POST", _XAI_TTS_URL, headers=headers, json=payload) as resp:
            if not resp.is_success:
                await resp.aread()
                _raise_for_vendor("xAI", resp)
            audio = b"".join([chunk async for chunk in resp.aiter_bytes()])
    return f"data:{mime};base64,{base64.b64encode(audio).decode('ascii')}"


async def _xai_stt(audio: str, model: str) -> tuple[str, str]:
    """xAI STT: POST /v1/stt (multipart) -> (text, detected language). xAI ignores
    any field sent AFTER the file, so `file` must be the LAST part: httpx writes
    the `data` fields before the `files`, so `model` goes first. The upload is
    named and typed from the clip's own MIME (an xAI mp3 is sent as an mp3)."""
    import httpx
    key = os.environ.get("XAI_API_KEY", "")
    if not key:
        raise RuntimeError("XAI_API_KEY not set in .env")
    audio_bytes = _audio_to_bytes(audio)
    mime = _audio_mime(audio)
    data = {"model": model}
    files = {"file": (f"clip.{_mime_to_ext(mime)}", audio_bytes, mime)}
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post(_XAI_STT_URL, headers=headers, data=data, files=files)
        _raise_for_vendor("xAI", resp)
        body = resp.json()
    return (body.get("text") or "").strip(), str(body.get("language") or "")


# ---- TTS / STT provider registries, keyed by manifest.provider. Every entry
# takes (text or audio, params, manifest, lang): a TTS entry returns the clip as
# a data: URL, an STT entry returns (text, lang). `params` are already resolved
# against the manifest (see _model_params), so each declared param is present.
# A new provider is a manifest plus one entry here.
async def _tts_fish(text: str, params: dict, manifest, lang: str) -> str:
    # Fish detects the language itself, so `lang` is not sent.
    return await _fish_tts(text, str(params["voice"]), str(params["format"]),
                           int(params["chunk_length"]))


async def _tts_elevenlabs(text: str, params: dict, manifest, lang: str) -> str:
    # ElevenLabs detects the language itself, so `lang` is not sent.
    voice_id = str(params["voice_id"] or "").strip()
    if not voice_id:
        raise RuntimeError(f"ElevenLabs: no voice_id set for {manifest.id}")
    settings = {k: float(params[k]) for k in ("stability", "similarity_boost", "speed")}
    return await _eleven_tts(text, voice_id, manifest.model, settings)


async def _tts_xai(text: str, params: dict, manifest, lang: str) -> str:
    # a wired `lang` wins over the language knob; either is mapped onto xAI's list.
    language = _xai_language(lang or params["language"])
    return await _xai_tts(text, str(params["voice"]), language, str(params["codec"]),
                          int(params["sample_rate"]), float(params["speed"]))


async def _stt_fish(audio: str, params: dict, manifest, lang: str) -> tuple[str, str]:
    return await _fish_stt(audio)


async def _stt_elevenlabs(audio: str, params: dict, manifest, lang: str) -> tuple[str, str]:
    return await _eleven_stt(audio, manifest.model)


async def _stt_xai(audio: str, params: dict, manifest, lang: str) -> tuple[str, str]:
    return await _xai_stt(audio, manifest.model)


_TTS_PROVIDERS = {"fish": _tts_fish, "elevenlabs": _tts_elevenlabs, "xai": _tts_xai}
_STT_PROVIDERS = {"fish": _stt_fish, "elevenlabs": _stt_elevenlabs, "xai": _stt_xai}


async def _anthropic(model: str, prompt: str, params: dict, media: dict | None = None,
                     ctx=None, tools: list | None = None) -> tuple[str, str]:
    """Anthropic Messages API. Thinking is top-level `thinking:{type:...}` and
    returns `thinking` content blocks; JSON via `output_config.format`. Opus 4.7+
    rejects temperature/top_p/top_k, so they are only sent when present in params
    (the curated Opus manifest omits them).

    With an image (`media["image"]`), the user content is a block array with the
    image block BEFORE the text block. An http(s) URL uses a `url` source; a data:
    URL is decoded to a base64 source (media_type + raw base64). anthropic-version
    stays 2023-06-01.

    With wired `tools` (each {id, name, description, parameters}) the body
    declares `tools=[{name, description, input_schema}]`; when the response's
    `content` carries a `tool_use` block, each call is dispatched to its Tool
    node via `ctx.call_tool(id, input)` (the Anthropic `input` is ALREADY a
    parsed object), the assistant turn is appended verbatim and a new user turn
    with `{type:tool_result, tool_use_id, content}` blocks is appended, and the
    /v1/messages endpoint is hit again. The loop runs up to 6 hops so a
    misbehaving model can never spin forever, mirroring the xAI cap."""
    import httpx
    media = media or {}
    image = media.get("image")
    if image:
        img = (image or "").strip()
        if img.lower().startswith(("http://", "https://")):
            image_block = {"type": "image", "source": {"type": "url", "url": img}}
        else:
            media_type, b64 = await asyncio.to_thread(_image_to_b64, img)
            image_block = {"type": "image", "source": {
                "type": "base64", "media_type": media_type, "data": b64}}
        content: list | str = [image_block, {"type": "text", "text": prompt}]
    else:
        content = prompt
    messages: list[dict] = [{"role": "user", "content": content}]
    base_body: dict = {"model": model,
                       "max_tokens": int(params.get("max_tokens", 1024) or 1024)}
    # Anthropic rejects temperature + top_p together ("use only one"); when both
    # arrive from a manifest's defaults, prefer temperature (the friendlier knob).
    has_temp = params.get("temperature") is not None
    for k in ("temperature", "top_p", "top_k"):
        if params.get(k) is not None:
            if k == "top_p" and has_temp:
                continue
            base_body[k] = params[k]
    if params.get("stop_sequences"):
        seqs = params["stop_sequences"]
        base_body["stop_sequences"] = seqs if isinstance(seqs, list) else [seqs]
    think = params.get(_THINK)
    # the level lever maps "adaptive" -> adaptive thinking; a bare bool -> adaptive.
    if (isinstance(think, str) and think.lower() == "adaptive") or think is True:
        base_body["thinking"] = {"type": "adaptive"}
    if _json_on(params):
        base_body["output_config"] = {"format": {"type": "json_schema"}}

    tools = tools or []
    if tools:
        base_body["tools"] = [
            {"name": t["name"], "description": t.get("description", ""),
             "input_schema": t.get("parameters") or {"type": "object", "properties": {}, "required": []}}
            for t in tools
        ]
    name_to_id = {t["name"]: t["id"] for t in tools}

    reasoning_text = ""
    final_text = ""
    async with httpx.AsyncClient(timeout=60) as client:
        for _ in range(6):
            body = dict(base_body)
            body["messages"] = messages
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                         "anthropic-version": "2023-06-01"}, json=body)
            resp.raise_for_status()
            data = resp.json()
            blocks = data.get("content", []) or []
            # accumulate any thinking trace from this hop.
            hop_reasoning = "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
            if hop_reasoning:
                reasoning_text = hop_reasoning
            tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not tool_uses or not tools or ctx is None:
                final_text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                break
            # Append the assistant turn verbatim (carries the tool_use blocks),
            # then one user turn whose content is a list of tool_result blocks
            # keyed by tool_use_id. Anthropic requires both for the next POST.
            messages.append({"role": "assistant", "content": blocks})
            results_content: list = []
            for tu in tool_uses:
                tname = tu.get("name") or ""
                tu_id = tu.get("id") or ""
                # Anthropic gives `input` as an already-parsed object, never a JSON string.
                args = tu.get("input") or {}
                if not isinstance(args, dict):
                    try:
                        args = _json.loads(args) if isinstance(args, str) else {}
                    except Exception:
                        args = {}
                tid = name_to_id.get(tname)
                if tid is None:
                    result = f"<no tool named {tname!r}>"
                else:
                    try:
                        result = await ctx.call_tool(tid, args)
                    except Exception as exc:
                        result = f"<tool {tname} error: {exc}>"
                results_content.append({"type": "tool_result",
                                        "tool_use_id": tu_id, "content": result})
            messages.append({"role": "user", "content": results_content})
    return final_text, reasoning_text


async def _google(model: str, prompt: str, params: dict) -> tuple[str, str]:
    """Google Gemini (kept for completeness though no Gemini manifest ships now).
    Thinking via thinkingConfig.includeThoughts; JSON via responseMimeType."""
    import httpx
    gen = _opts(params, ("temperature",))
    if params.get("top_p") is not None: gen["topP"] = params["top_p"]
    if params.get("top_k") is not None: gen["topK"] = params["top_k"]
    if params.get("max_output_tokens") is not None: gen["maxOutputTokens"] = params["max_output_tokens"]
    if _think_on(params):
        gen["thinkingConfig"] = {"includeThoughts": True}
    if _json_on(params):
        gen["responseMimeType"] = "application/json"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
    # the key rides a header, never the URL: an HTTP error message quotes the URL,
    # and that message reaches the node's error output and the editor.
    headers = {"x-goog-api-key": os.environ.get("GOOGLE_API_KEY", "")}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        cands = resp.json().get("candidates", [])
        if not cands:
            return "", ""
        parts = cands[0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        reasoning = "".join(p.get("text", "") for p in parts if p.get("thought"))
        return text, reasoning
