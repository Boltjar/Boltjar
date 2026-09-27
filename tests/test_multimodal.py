"""Tests for real image input on the LLM node and the Image source node.

All network is mocked (httpx is patched) so NO real call leaves the box:
- The Image node (core.value.image) passes an https URL through and turns a
  local file inside user/data/files or examples/ into a `data:image/...;base64,`
  URL; a path anywhere else is refused.
- `_image_to_b64` splits a data: URL in place and (mock-)fetches an http URL.
- `_xai` builds the OpenAI-style image_url content array when given media.
- `_anthropic` builds a base64 image block BEFORE the text block.
- `_ollama` puts BARE base64 (no data: prefix) in `images`.
"""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
import boltjar.nodes.core.builtin as b
from boltjar.file_store import PathEscapeError
from boltjar.sdk import NODE_REGISTRY


# a 1x1 PNG (smallest valid), used as the temp-file fixture.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9"
    "awAAAABJRU5ErkJggg=="
)


def _run(coro):
    return asyncio.run(coro)


def _fake_async_client(json_val: dict, captured: dict):
    """A mock httpx.AsyncClient context manager whose .post() captures the
    outgoing json body and returns a fake response with json_val."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = json_val
    fake_resp.raise_for_status.return_value = None

    async def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return fake_resp

    mock_client = AsyncMock()
    mock_client.post = fake_post
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Image source node (core.value.image)
# ---------------------------------------------------------------------------

def test_image_node_is_registered_with_image_output():
    assert "core.value.image" in NODE_REGISTRY
    spec = NODE_REGISTRY["core.value.image"]
    assert [p.type for p in spec.outputs] == ["image"]
    # the src knob is a real text widget.
    assert {w.name for w in spec.widgets} == {"src"}


def test_image_node_passes_https_url_through():
    spec = NODE_REGISTRY["core.value.image"]
    inst = spec.cls()
    inst.src = "https://example.com/cat.png"
    assert inst.value() == {"out": "https://example.com/cat.png"}


@pytest.fixture
def media_roots(tmp_path, monkeypatch):
    """A tmp repo: user/data/files is the Files sandbox, examples/ ships graphs."""
    from boltjar import server
    from boltjar.file_store import FileStore
    files = tmp_path / "user" / "data" / "files"
    examples = tmp_path / "examples"
    examples.mkdir()
    monkeypatch.setattr(server, "FILE_STORE", FileStore(files))
    monkeypatch.setattr(server, "EXAMPLES_DIR", examples)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    return tmp_path


def _image(src: str) -> str:
    inst = NODE_REGISTRY["core.value.image"].cls()
    inst.src = src
    return inst.value()["out"]


def _png_payload(out: str) -> bytes:
    assert out.startswith("data:image/png;base64,")
    return base64.b64decode(out.split(";base64,", 1)[1])


def test_image_node_encodes_local_file_to_data_url(media_roots):
    png = media_roots / "user" / "data" / "files" / "pics" / "pixel.png"
    png.parent.mkdir()
    png.write_bytes(_PNG_BYTES)
    # a relative path lands in the Files sandbox; the absolute path works too.
    assert _png_payload(_image("pics/pixel.png")) == _PNG_BYTES
    assert _png_payload(_image(str(png))) == _PNG_BYTES


def test_image_node_reads_shipped_examples(media_roots):
    (media_roots / "examples" / "pixel.png").write_bytes(_PNG_BYTES)
    assert _png_payload(_image("examples/pixel.png")) == _PNG_BYTES


@pytest.mark.parametrize("where", ["absolute", "dotdot"])
def test_image_node_refuses_a_path_outside_the_roots(media_roots, where):
    secret = media_roots / "private" / "key.png"
    secret.parent.mkdir()
    secret.write_bytes(b"TOP-SECRET")
    src = str(secret) if where == "absolute" else "../../../private/key.png"
    with pytest.raises(PathEscapeError) as exc:
        _image(src)
    message = str(exc.value)
    assert message.startswith("Image: ") and "user/data/files and examples/" in message
    assert "TOP-SECRET" not in message
    assert base64.b64encode(b"TOP-SECRET").decode() not in message


def test_image_node_refuses_a_symlink_out_of_the_sandbox(media_roots):
    outside = media_roots / "private.png"
    outside.write_bytes(b"TOP-SECRET")
    link = media_roots / "user" / "data" / "files" / "link.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is not permitted here")
    with pytest.raises(PathEscapeError):
        _image("link.png")


def test_image_node_never_reads_the_repo_root(media_roots):
    # ".env" is not in the sandbox; the name passes through, the file is never read.
    (media_roots / ".env").write_text("XAI_API_KEY=secret", encoding="utf-8")
    assert _image(".env") == ".env"


def test_image_node_passes_data_urls_and_bare_base64_through(media_roots):
    data_url = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()
    assert _image(data_url) == data_url
    bare = base64.b64encode(_PNG_BYTES).decode()
    assert _image(bare) == bare
    long_bare = base64.b64encode(bytes(range(256)) * 400).decode()  # too long to be a path
    assert _image(long_bare) == long_bare


def test_audio_node_follows_the_same_roots(media_roots):
    clip = media_roots / "user" / "data" / "files" / "clip.wav"
    clip.write_bytes(b"RIFF")
    audio = NODE_REGISTRY["core.value.audio"].cls()
    audio.src = "clip.wav"
    assert audio.value()["out"] == "data:audio/wav;base64," + base64.b64encode(b"RIFF").decode()
    audio.src = str(media_roots / "elsewhere.wav")
    with pytest.raises(PathEscapeError, match="^Audio: "):
        audio.value()


def test_image_node_empty_and_unknown_pass_through():
    spec = NODE_REGISTRY["core.value.image"]
    inst = spec.cls()
    inst.src = ""
    assert inst.value() == {"out": ""}
    # a non-URL, non-file string is returned unchanged (lean: never raises).
    inst.src = "not-a-real-path-or-url"
    assert inst.value() == {"out": "not-a-real-path-or-url"}


# ---------------------------------------------------------------------------
# _image_to_b64
# ---------------------------------------------------------------------------

def test_image_to_b64_splits_data_url():
    raw = base64.b64encode(b"hello-bytes").decode("ascii")
    data_url = f"data:image/png;base64,{raw}"
    media_type, b64 = b._image_to_b64(data_url)
    assert media_type == "image/png"
    assert b64 == raw  # split in place, no re-encode


def test_image_to_b64_fetches_http_url():
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        resp = MagicMock()
        resp.content = _PNG_BYTES
        resp.headers = {"content-type": "image/png"}
        resp.raise_for_status.return_value = None
        return resp

    with patch("httpx.get", fake_get):
        media_type, b64 = b._image_to_b64("https://example.com/p.png")

    assert captured["url"] == "https://example.com/p.png"
    assert media_type == "image/png"
    assert base64.b64decode(b64) == _PNG_BYTES


def test_image_to_b64_http_defaults_jpeg_when_content_type_missing():
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.content = b"\xff\xd8\xff"
        resp.headers = {}  # no content-type
        resp.raise_for_status.return_value = None
        return resp

    with patch("httpx.get", fake_get):
        media_type, _ = b._image_to_b64("http://x/y")
    assert media_type == "image/jpeg"


# ---------------------------------------------------------------------------
# _xai: image_url content array
# ---------------------------------------------------------------------------

def test_xai_builds_image_url_content_array():
    captured = {}
    cm = _fake_async_client(
        {"choices": [{"message": {"content": "a cat", "reasoning_content": ""}}]},
        captured,
    )
    data_url = "data:image/png;base64,QUJD"
    with patch("httpx.AsyncClient", return_value=cm):
        text, _ = _run(b._xai("grok", "what is this?", {}, {"image": data_url}))

    assert text == "a cat"
    content = captured["json"]["messages"][0]["content"]
    assert isinstance(content, list)
    # image_url block carries the value verbatim (no re-encode); text follows.
    assert content[0] == {"type": "image_url", "image_url": {"url": data_url}}
    assert content[1] == {"type": "text", "text": "what is this?"}


def test_xai_plain_string_content_without_image():
    captured = {}
    cm = _fake_async_client(
        {"choices": [{"message": {"content": "hi", "reasoning_content": ""}}]},
        captured,
    )
    with patch("httpx.AsyncClient", return_value=cm):
        _run(b._xai("grok", "hello", {}, {}))
    # no image -> the user content stays a plain string.
    assert captured["json"]["messages"][0]["content"] == "hello"


# ---------------------------------------------------------------------------
# _anthropic: base64 image block BEFORE text
# ---------------------------------------------------------------------------

def test_anthropic_builds_base64_image_block_before_text():
    captured = {}
    cm = _fake_async_client(
        {"content": [{"type": "text", "text": "a dog"}]},
        captured,
    )
    raw = base64.b64encode(_PNG_BYTES).decode("ascii")
    data_url = f"data:image/png;base64,{raw}"
    with patch("httpx.AsyncClient", return_value=cm):
        text, _ = _run(b._anthropic("claude", "describe", {}, {"image": data_url}))

    assert text == "a dog"
    content = captured["json"]["messages"][0]["content"]
    assert isinstance(content, list)
    # image block comes FIRST, with a decoded base64 source; text is second.
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": raw},
    }
    assert content[1] == {"type": "text", "text": "describe"}
    # version header preserved.
    assert captured["headers"]["anthropic-version"] == "2023-06-01"


def test_anthropic_uses_url_source_for_https():
    captured = {}
    cm = _fake_async_client({"content": [{"type": "text", "text": "ok"}]}, captured)
    url = "https://example.com/img.jpg"
    with patch("httpx.AsyncClient", return_value=cm):
        _run(b._anthropic("claude", "look", {}, {"image": url}))

    content = captured["json"]["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {"type": "url", "url": url}}
    assert content[1] == {"type": "text", "text": "look"}


def test_anthropic_plain_string_content_without_image():
    captured = {}
    cm = _fake_async_client({"content": [{"type": "text", "text": "ok"}]}, captured)
    with patch("httpx.AsyncClient", return_value=cm):
        _run(b._anthropic("claude", "hello", {}, {}))
    assert captured["json"]["messages"][0]["content"] == "hello"


# ---------------------------------------------------------------------------
# _ollama: bare base64 (no data: prefix) in `images`
# ---------------------------------------------------------------------------

def test_ollama_puts_bare_base64_in_images():
    captured = {}
    cm = _fake_async_client({"response": "a fish", "thinking": ""}, captured)
    raw = base64.b64encode(_PNG_BYTES).decode("ascii")
    data_url = f"data:image/png;base64,{raw}"
    with patch("httpx.AsyncClient", return_value=cm):
        text, _ = _run(b._ollama("gemma", "what?", {}, {"image": data_url}))

    assert text == "a fish"
    images = captured["json"]["images"]
    # BARE base64: the data: prefix is stripped, payload only.
    assert images == [raw]
    assert not images[0].startswith("data:")


def test_ollama_accepts_a_list_of_images():
    captured = {}
    cm = _fake_async_client({"response": "ok", "thinking": ""}, captured)
    raw = base64.b64encode(_PNG_BYTES).decode("ascii")
    with patch("httpx.AsyncClient", return_value=cm):
        _run(b._ollama("gemma", "q", {}, {"image": [f"data:image/png;base64,{raw}", raw]}))
    # both entries land as bare base64.
    assert captured["json"]["images"] == [raw, raw]


def test_ollama_no_image_sends_no_images_key():
    captured = {}
    cm = _fake_async_client({"response": "ok", "thinking": ""}, captured)
    with patch("httpx.AsyncClient", return_value=cm):
        _run(b._ollama("gemma", "q", {}, {}))
    assert "images" not in captured["json"]
