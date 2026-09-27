"""
boltjar.media: one-line summaries of wire values, for the terminal.

A value on a wire can be a multi-megabyte `data:` URL (a spoken reply, a camera
frame) or pages of text. The console never prints that raw: `summarize` turns a
data URL into what a person needs at a glance, `audio/wav · 42 KB · 3.1 s` or
`image/png · 12 KB · 640x480`, and cuts long text to one line with its full
length noted. Only the first few KB of a payload are decoded (enough for a WAV
or image header), never the whole clip. A control character in the text prints
as its escape (`printable`), so a value can never drive the terminal. Stdlib only.
"""
from __future__ import annotations

import binascii
import json
import re
import urllib.parse
from typing import Any

# How much of a base64 payload is decoded to read its header: WAV and PNG need a
# few dozen bytes, but a JPEG's size sits after its EXIF/ICC segments, which can
# run to tens of KB. 64K base64 chars = 48 KB decoded (a multiple of 4, so the
# slice always decodes cleanly).
_HEAD_CHARS = 65536

# A data URL embedded in longer text (a dict repr, an error message): the
# media type, optional params, then the payload up to whitespace or a quote.
_DATA_URL_RE = re.compile(r"data:[\w.+-]+/[\w.+-]+(?:;[\w.+=-]+)*,[^\s'\"<>]*")
_MIME_RE = re.compile(r"^[\w.+-]+/[\w.+-]+$")

_WAV_MIMES = {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}

# C0 and C1 control characters and DEL. A terminal acts on them: ESC and CSI
# open sequences that move the cursor, clear the screen or set the clipboard,
# BEL rings, CR goes back to the start of the line. Text from outside (an HTTP
# response, an LLM reply, a webhook body) reaches the terminal without them.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def format_bytes(n: int) -> str:
    """A byte count as a person reads it: `812 B`, `4.2 KB`, `42 KB`, `1.3 MB`."""
    if n < 1024:
        return f"{n} B"
    size, unit = n / 1024, "KB"
    for bigger in ("MB", "GB"):
        if size < 1024:
            break
        size, unit = size / 1024, bigger
    return f"{size:.1f} {unit}" if size < 10 else f"{size:.0f} {unit}"


def format_seconds(seconds: float) -> str:
    """A duration: `3.1 s` under a minute, `2:05 min` above."""
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}:{rest:02d} min"


def describe_data_url(url: str) -> str | None:
    """`image/png · 12 KB · 640x480` for a data URL, or None when `url` is not one."""
    if not url.startswith("data:"):
        return None
    comma = url.find(",")
    if comma < 0:
        return None
    params = url[5:comma].split(";")
    mime = (params[0] or "text/plain").lower()
    if not _MIME_RE.match(mime):  # "data: see below, ..." is prose, not a URL
        return None
    if "base64" in (p.strip().lower() for p in params[1:]):
        size = _base64_size(url, comma + 1)
        try:
            head = binascii.a2b_base64(url[comma + 1: comma + 1 + _HEAD_CHARS])
        except (binascii.Error, ValueError):
            head = b""
    else:
        head = urllib.parse.unquote_to_bytes(url[comma + 1:])
        size = len(head)
    return " · ".join([mime, format_bytes(size), *_details(mime, head, size)])


def describe_bytes(data: bytes | bytearray) -> str:
    """Raw bytes: the sniffed media type when the header is a known one."""
    head = bytes(data[:_HEAD_CHARS])
    mime = _sniff(head) or "bytes"
    return " · ".join([mime, format_bytes(len(data)), *_details(mime, head, len(data))])


def printable(text: str) -> str:
    """`text` with every control character written out as its escape, ESC as
    the four characters `\\x1b`, so a terminal shows it and never acts on it."""
    return _CONTROL_RE.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def summarize(value: Any, limit: int = 160) -> str:
    """One console-safe line for any wire value.

    A data URL becomes its description; data URLs inside longer text (a dict,
    an error message) are each replaced by `<description>`; whitespace runs
    collapse to one space and any other control character is written out
    (`printable`); text over `limit` characters is cut with an ellipsis and its
    full length noted."""
    if isinstance(value, (bytes, bytearray)):
        return describe_bytes(value)
    text = value if isinstance(value, str) else _to_text(value)
    whole = describe_data_url(text)
    if whole is not None:
        return whole
    full = len(text)
    if "data:" in text:
        text = _DATA_URL_RE.sub(lambda m: f"<{describe_data_url(m.group(0))}>", text)
    text = printable(" ".join(text.split()))
    if len(text) <= limit:
        return text
    note = f"… ({full:,} chars)"
    return text[: max(1, limit - len(note))].rstrip() + note


def _to_text(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            pass
    return str(value)


def _base64_size(url: str, start: int) -> int:
    """Decoded size of the base64 payload that starts at `start`, measured on
    the string (no copy of a multi-MB payload)."""
    chars = len(url) - start
    pad = 2 if url.endswith("==") else 1 if url.endswith("=") else 0
    return max(0, chars * 3 // 4 - pad)


def _sniff(head: bytes) -> str | None:
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:2] == b"BM":
        return "image/bmp"
    return None


def _details(mime: str, head: bytes, size: int) -> list[str]:
    """The extra facts a header gives cheaply: a WAV's duration, an image's size."""
    if mime in _WAV_MIMES or _sniff(head) == "audio/wav":
        seconds = _wav_seconds(head, size)
        return [format_seconds(seconds)] if seconds is not None else []
    if mime.startswith("image/"):
        dims = _image_size(head)
        return [f"{dims[0]}x{dims[1]}"] if dims else []
    return []


def _le(b: bytes) -> int:
    return int.from_bytes(b, "little")


def _be(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _wav_seconds(head: bytes, total: int) -> float | None:
    """Duration of a RIFF/WAVE clip from its `fmt ` byte rate and `data` size.
    A streamed WAV often leaves the data size at 0 or 0xFFFFFFFF; the bytes that
    follow the header are measured instead."""
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    pos, byte_rate = 12, 0
    while pos + 8 <= len(head):
        chunk, size = head[pos:pos + 4], _le(head[pos + 4:pos + 8])
        body = pos + 8
        if chunk == b"fmt " and body + 12 <= len(head):
            byte_rate = _le(head[body + 8:body + 12])
        elif chunk == b"data":
            if not byte_rate:
                return None
            available = max(0, total - body)
            if size in (0, 0xFFFFFFFF) or size > available:
                size = available
            return size / byte_rate
        pos = body + size + (size & 1)
    return None


def _image_size(head: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG, GIF, BMP, WebP or JPEG header, else None."""
    kind = _sniff(head)
    if kind == "image/png" and head[12:16] == b"IHDR" and len(head) >= 24:
        return _be(head[16:20]), _be(head[20:24])
    if kind == "image/gif" and len(head) >= 10:
        return _le(head[6:8]), _le(head[8:10])
    if kind == "image/bmp" and len(head) >= 26:
        width = int.from_bytes(head[18:22], "little", signed=True)
        height = int.from_bytes(head[22:26], "little", signed=True)
        return abs(width), abs(height)
    if kind == "image/webp" and len(head) >= 30:
        return _webp_size(head)
    if kind == "image/jpeg":
        return _jpeg_size(head)
    return None


def _webp_size(head: bytes) -> tuple[int, int] | None:
    chunk = head[12:16]
    if chunk == b"VP8X":
        return 1 + _le(head[24:27]), 1 + _le(head[27:30])
    if chunk == b"VP8L" and head[20] == 0x2F:
        bits = _le(head[21:25])
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
        return _le(head[26:28]) & 0x3FFF, _le(head[28:30]) & 0x3FFF
    return None


# JPEG start-of-frame markers carry the size; C4 (DHT), C8 (JPG) and CC (DAC)
# share the range but are not frames.
_JPEG_SOF = {m for m in range(0xC0, 0xD0) if m not in (0xC4, 0xC8, 0xCC)}


def _jpeg_size(head: bytes) -> tuple[int, int] | None:
    pos = 2
    while pos + 4 <= len(head):
        if head[pos] != 0xFF:
            pos += 1
            continue
        marker = head[pos + 1]
        if marker == 0xFF:  # fill byte before a marker
            pos += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # standalone, no length
            pos += 2
            continue
        if marker == 0xDA:  # start of scan: the frame header was not seen
            return None
        if marker in _JPEG_SOF:
            if pos + 9 > len(head):
                return None
            return _be(head[pos + 7:pos + 9]), _be(head[pos + 5:pos + 7])
        pos += 2 + _be(head[pos + 2:pos + 4])
    return None
