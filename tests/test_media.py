"""The media summary formatter: data URLs and long text become one short line."""
from __future__ import annotations

import base64
import struct

from boltjar import media


def _data_url(mime: str, payload: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(payload).decode()


def _wav(seconds: float, rate: int = 16000, channels: int = 1, bits: int = 16,
         data_size: int | None = None, extra_chunk: bytes = b"") -> bytes:
    block = channels * bits // 8
    frames = int(seconds * rate)
    data = b"\x00" * (frames * block)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, bits)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra_chunk
            + b"data" + struct.pack("<I", len(data) if data_size is None else data_size) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 4


def _jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    exif = b"\xff\xe1" + struct.pack(">H", 2 + 4000) + b"\x00" * 4000  # a bulky segment first
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + exif + sof0 + b"\xff\xda" + b"\x00" * 10


def test_format_bytes_reads_like_a_person_writes_it():
    assert media.format_bytes(812) == "812 B"
    assert media.format_bytes(4300) == "4.2 KB"
    assert media.format_bytes(42 * 1024) == "42 KB"
    assert media.format_bytes(int(1.3 * 1024 * 1024)) == "1.3 MB"


def test_wav_data_url_reports_type_size_and_duration():
    clip = _wav(3.1)
    line = media.summarize(_data_url("audio/wav", clip))
    assert line == f"audio/wav · {media.format_bytes(len(clip))} · 3.1 s"


def test_wav_duration_skips_extra_chunks_and_handles_streamed_sizes():
    # a LIST chunk before `data`, and a streamed header that leaves the data size
    # at 0xFFFFFFFF: the duration comes from the bytes actually present.
    listing = b"LIST" + struct.pack("<I", 10) + b"INFOabcdef"
    clip = _wav(2.0, rate=24000, extra_chunk=listing, data_size=0xFFFFFFFF)
    assert media.summarize(_data_url("audio/x-wav", clip)).endswith("· 2.0 s")


def test_long_clip_uses_minutes():
    assert media.format_seconds(125) == "2:05 min"


def test_image_data_urls_report_their_size_from_the_header():
    assert media.summarize(_data_url("image/png", _png(640, 480))).endswith("· 640x480")
    assert media.summarize(_data_url("image/jpeg", _jpeg(1920, 1080))).endswith("· 1920x1080")
    gif = b"GIF89a" + struct.pack("<HH", 32, 16) + b"\x00" * 8
    assert media.summarize(_data_url("image/gif", gif)).endswith("· 32x16")
    webp = b"RIFF" + struct.pack("<I", 30) + b"WEBPVP8X" + struct.pack("<I", 10) + b"\x00" * 4 \
        + (799).to_bytes(3, "little") + (599).to_bytes(3, "little")
    assert media.summarize(_data_url("image/webp", webp)).endswith("· 800x600")


def test_unknown_media_keeps_type_and_size_only():
    line = media.summarize(_data_url("video/mp4", b"\x00" * 3000))
    assert line == "video/mp4 · 2.9 KB"


def test_a_broken_header_never_raises():
    assert media.summarize("data:image/png;base64,!!!!") == "image/png · 3 B"
    assert media.summarize(_data_url("audio/wav", b"RIFF")) == "audio/wav · 4 B"


def test_plain_text_data_url_is_measured_decoded():
    assert media.summarize("data:text/plain,hello%20world") == "text/plain · 11 B"


def test_prose_starting_with_data_is_not_a_url():
    assert media.summarize("data: see the table, row 3") == "data: see the table, row 3"


def test_long_text_is_cut_with_its_full_length_noted():
    text = "word " * 400
    line = media.summarize(text, limit=60)
    assert len(line) <= 60
    assert line.endswith(f"… ({len(text):,} chars)")


def test_whitespace_collapses_to_one_line():
    assert media.summarize("one\n\ntwo\tthree") == "one two three"


def test_control_characters_print_as_escapes():
    # an LLM reply or an HTTP body holding ESC, CSI or BEL would otherwise move
    # the cursor over earlier lines, clear the screen or set the clipboard
    line = media.summarize("o: \x1b[1A\x1b[2Kforged\x07 \x9b2J \x1b]52;c;SGk=\x07\x7f\x00", 200)
    assert line == "o: \\x1b[1A\\x1b[2Kforged\\x07 \\x9b2J \\x1b]52;c;SGk=\\x07\\x7f\\x00"


def test_a_cut_line_counts_its_escapes():
    line = media.summarize("\x1b" * 100, limit=60)
    assert len(line) <= 60 and "\x1b" not in line and line.startswith("\\x1b\\x1b")


def test_data_urls_inside_structures_are_summarized_in_place():
    clip = _data_url("audio/wav", _wav(1.0))
    line = media.summarize({"text": "hi", "audio": clip})
    assert "base64" not in line
    assert line.startswith('{"text": "hi", "audio": "<audio/wav · ')
    assert "· 1.0 s>" in line


def test_bytes_are_sniffed():
    assert media.summarize(_png(10, 20)).startswith("image/png · ")
    assert media.summarize(b"\x01\x02\x03") == "bytes · 3 B"


def test_short_values_pass_through():
    assert media.summarize("hello") == "hello"
    assert media.summarize(42) == "42"
    assert media.summarize(None) == "None"
