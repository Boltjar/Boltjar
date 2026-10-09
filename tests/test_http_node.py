"""Tests for the HTTP Request node (core.net.http).

All tests patch httpx so NO real network call is made. The fake
AsyncClient captures outgoing request details and returns a
configurable fake response.

The node has no `body_type` knob: the request shape is auto-detected from the
body content (multipart-binary / json / form / raw, with an explicit
Content-Type header bypassing detection). The response is parsed per the
`response_type` knob (auto / text / json / image / audio / video / binary).
"""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import boltjar.nodes.core  # noqa: F401 (registers all core nodes)
import boltjar.secrets as secrets
from boltjar.sdk import NODE_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _inst(cfg: dict | None = None):
    """Return a fresh Http node instance with optional _node_cfg."""
    spec = NODE_REGISTRY["core.net.http"]
    inst = spec.cls()
    inst._node_cfg = cfg or {}
    return inst


def _fake_client(status: int = 200, text: str = '{"ok": true}', json_val=None,
                 raise_exc: Exception | None = None,
                 content: bytes | None = None,
                 response_headers: dict | None = None):
    """Build a mock httpx.AsyncClient context manager.

    If raise_exc is set, client.request() raises it; otherwise it returns a
    fake response with the given status / text. json_val overrides what
    response.json() returns (default: parse text). `content` sets the raw bytes
    (for binary response tests); `response_headers` sets resp.headers.
    """
    fake_resp = MagicMock()
    fake_resp.status_code = status
    fake_resp.text = text
    fake_resp.content = content if content is not None else text.encode("utf-8")
    fake_resp.headers = response_headers or {}
    if json_val is not None:
        fake_resp.json.return_value = json_val
    else:
        import json as _json
        try:
            parsed = _json.loads(text)
        except Exception:
            fake_resp.json.side_effect = ValueError("not json")
        else:
            fake_resp.json.return_value = parsed

    mock_client = AsyncMock()
    if raise_exc:
        mock_client.request.side_effect = raise_exc
    else:
        mock_client.request.return_value = fake_resp

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock_client


# ---------------------------------------------------------------------------
# Fixture: isolate secrets store so real user/data/secrets.json is never touched.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path, monkeypatch):
    tmp_file = tmp_path / "secrets.json"
    monkeypatch.setattr(secrets, "_PATH", tmp_file)
    secrets._load()
    yield
    secrets._store.clear()


# ---------------------------------------------------------------------------
# Registry + node shape
# ---------------------------------------------------------------------------

def test_node_is_registered():
    assert "core.net.http" in NODE_REGISTRY


def test_node_has_correct_ports():
    spec = NODE_REGISTRY["core.net.http"]
    in_names = {p.name for p in spec.inputs}
    out_names = {p.name for p in spec.outputs}
    # trigger + growable tag (mirrors Template) on the input side.
    assert in_names == {"trigger", "tag"}
    assert out_names == {"status", "body", "json", "trigger"}
    tag_port = next(p for p in spec.inputs if p.name == "tag")
    assert tag_port.growable is True


def test_body_type_widget_is_gone():
    """The legacy body_type knob has been removed (shape is auto-detected)."""
    spec = NODE_REGISTRY["core.net.http"]
    widget_names = {w.name for w in spec.widgets}
    assert "body_type" not in widget_names
    assert "response_type" in widget_names


# ---------------------------------------------------------------------------
# (1) secret.NAME resolution in url AND header value
# ---------------------------------------------------------------------------

def test_secret_resolved_in_url_and_header():
    secrets.set_secret("MY_TOKEN", "tok123")
    secrets.set_secret("MY_HOST", "example.com")

    inst = _inst({
        "url": "https://{{secret.MY_HOST}}/api",
        "headers": "Authorization: Bearer {{secret.MY_TOKEN}}",
        "method": "GET",
    })

    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    call_kwargs = mock_client.request.call_args
    assert call_kwargs[0][1] == "https://example.com/api"  # resolved url
    assert call_kwargs[1]["headers"]["Authorization"] == "Bearer tok123"


# ---------------------------------------------------------------------------
# (2) header and query line parsing
# ---------------------------------------------------------------------------

def test_headers_and_query_parse_correctly():
    inst = _inst({
        "url": "https://httpbin.org/get",
        "method": "GET",
        "headers": "X-App: myapp\nAccept: application/json",
        "query": "page=1\nsize=20",
    })

    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    args, kw = mock_client.request.call_args
    assert kw["headers"] == {"X-App": "myapp", "Accept": "application/json"}
    assert args[1] == "https://httpbin.org/get?page=1&size=20"
    assert "params" not in kw  # httpx's params= would replace the URL's own query


def test_blank_lines_in_headers_and_query_are_ignored():
    inst = _inst({
        "url": "https://example.com",
        "method": "GET",
        "headers": "\n\nX-Only: val\n\n",
        "query": "\nkey=v\n\n",
    })

    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    args, kw = mock_client.request.call_args
    assert kw["headers"] == {"X-Only": "val"}
    assert args[1] == "https://example.com?key=v"


def test_header_value_may_contain_colon():
    """A header value like 'http://x' should not be split on the second colon."""
    inst = _inst({
        "url": "https://example.com",
        "headers": "Location: http://example.com/path",
        "method": "GET",
    })

    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    assert kw["headers"]["Location"] == "http://example.com/path"


# ---------------------------------------------------------------------------
# (3) output shape: {status, body, json, done}
# ---------------------------------------------------------------------------

def test_output_shape_200():
    inst = _inst({"url": "https://example.com", "method": "GET"})
    cm, _ = _fake_client(status=200, text='{"key": "val"}',
                         response_headers={"content-type": "application/json"})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["status"] == 200
    assert out["body"] == '{"key": "val"}'
    assert out["json"] == {"key": "val"}
    assert out["trigger"] is True


def test_non_json_body_gives_none_json():
    inst = _inst({"url": "https://example.com", "method": "GET"})
    cm, _ = _fake_client(status=200, text="plain text",
                         response_headers={"content-type": "text/plain"})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["body"] == "plain text"
    assert out["json"] is None
    assert out["trigger"] is True


# ---------------------------------------------------------------------------
# (4) non-2xx response still returns status+body (no raise)
# ---------------------------------------------------------------------------

def test_non_2xx_returns_status_and_body():
    inst = _inst({"url": "https://example.com/missing", "method": "GET"})
    cm, _ = _fake_client(status=404, text="Not Found",
                         response_headers={"content-type": "text/plain"})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["status"] == 404
    assert out["body"] == "Not Found"
    assert out["trigger"] is True


def test_500_returns_status_and_body():
    inst = _inst({"url": "https://example.com/error", "method": "POST"})
    cm, _ = _fake_client(status=500, text='{"error":"oops"}',
                         response_headers={"content-type": "application/json"})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["status"] == 500
    assert out["trigger"] is True


# ---------------------------------------------------------------------------
# (5) network error returns status 0 + error message
# ---------------------------------------------------------------------------

def test_network_error_returns_status_0():
    import httpx as _httpx
    inst = _inst({"url": "https://unreachable.example.com", "method": "GET"})
    exc = _httpx.ConnectError("connection refused")
    cm, _ = _fake_client(raise_exc=exc)
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["status"] == 0
    assert "request failed" in out["body"]
    assert out["json"] is None
    assert out["trigger"] is True


def test_timeout_error_returns_status_0():
    import httpx as _httpx
    inst = _inst({"url": "https://slow.example.com", "method": "GET"})
    exc = _httpx.TimeoutException("timed out")
    cm, _ = _fake_client(raise_exc=exc)
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["status"] == 0
    assert out["trigger"] is True


# ---------------------------------------------------------------------------
# POST: GET-with-body skipped, body sent on POST per detected shape
# ---------------------------------------------------------------------------

def test_get_does_not_send_body():
    inst = _inst({
        "url": "https://example.com/api",
        "method": "GET",
        "body": "should be ignored",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    assert "content" not in kw
    assert "json" not in kw
    assert "data" not in kw


# ---------------------------------------------------------------------------
# secret resolution in body
# ---------------------------------------------------------------------------

def test_secret_resolved_in_body():
    secrets.set_secret("API_SECRET", "supersecret")
    inst = _inst({
        "url": "https://example.com/api",
        "method": "POST",
        "body": "key={{secret.API_SECRET}}",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    # `key=value` form, single line -> auto-detected as form data
    assert kw.get("data") == {"key": "supersecret"}


# ---------------------------------------------------------------------------
# Auto-detect: JSON / form / multipart / raw
# ---------------------------------------------------------------------------

def test_autodetect_json_body():
    inst = _inst({
        "url": "https://example.com/api",
        "method": "POST",
        "body": '{"a": 1, "b": "two"}',
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    assert kw.get("json") == {"a": 1, "b": "two"}
    assert "content" not in kw
    assert "data" not in kw


def test_autodetect_json_array_body():
    inst = _inst({
        "url": "https://example.com/api",
        "method": "POST",
        "body": '[1, 2, 3]',
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    assert kw.get("json") == [1, 2, 3]


def test_autodetect_form_body():
    inst = _inst({
        "url": "https://example.com/api",
        "method": "POST",
        "body": "name=alice\nrole=admin\n",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    assert kw.get("data") == {"name": "alice", "role": "admin"}
    assert "content" not in kw
    assert "json" not in kw


def test_autodetect_multipart_with_binary_data_url():
    png_bytes = b"\x89PNG\r\n\x1a\nFAKEPNGDATA"
    data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")

    inst = _inst({
        "url": "https://example.com/upload",
        "method": "POST",
        "body": f"photo={data_url}\ncaption=Hello world",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    files = kw.get("files")
    data = kw.get("data")
    assert files is not None and "photo" in files
    fname, fbytes, fmime = files["photo"]
    assert fname == "file.png"
    assert fbytes == png_bytes
    assert fmime == "image/png"
    assert data == {"caption": "Hello world"}


def test_autodetect_multipart_text_only_falls_back_to_form():
    """A body of name=value lines with NO binary data: URL is form, not multipart."""
    inst = _inst({
        "url": "https://example.com/api",
        "method": "POST",
        "body": "name=alice\nrole=admin",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    assert "files" not in kw
    assert kw.get("data") == {"name": "alice", "role": "admin"}


def test_autodetect_raw_text_body():
    """Body that is neither json-shaped nor name=value form -> raw text."""
    inst = _inst({
        "url": "https://example.com/api",
        "method": "POST",
        "body": "just some plain text here",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    assert kw.get("content") == "just some plain text here"
    assert "json" not in kw
    assert "data" not in kw


def test_explicit_content_type_bypasses_detection():
    """An explicit Content-Type header sends the body raw, regardless of shape."""
    inst = _inst({
        "url": "https://example.com/api",
        "method": "POST",
        "headers": "Content-Type: application/octet-stream",
        "body": '{"looks":"like json"}',
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run())

    kw = mock_client.request.call_args[1]
    assert kw.get("content") == '{"looks":"like json"}'
    assert "json" not in kw
    assert "data" not in kw


def test_multipart_with_http_url_field():
    """An http(s):// value is fetched and sent as a file part using its Content-Type.

    Triggered by a sibling binary in the body (URL fetches alone do not trigger
    multipart: the value would have to look like a data: URL to flip detection).
    """
    fake_audio = b"OggS\x00FAKEAUDIOBYTES"
    fake_mime = "audio/ogg"
    flag_bytes = b"\x89PNG\r\n\x1a\nFLAG"
    flag_url = "data:image/png;base64," + base64.b64encode(flag_bytes).decode("ascii")

    fetch_resp = MagicMock()
    fetch_resp.content = fake_audio
    fetch_resp.headers = {"content-type": fake_mime}
    fetch_client = AsyncMock()
    fetch_client.get.return_value = fetch_resp
    fetch_cm = MagicMock()
    fetch_cm.__aenter__ = AsyncMock(return_value=fetch_client)
    fetch_cm.__aexit__ = AsyncMock(return_value=False)

    out_resp = MagicMock()
    out_resp.status_code = 200
    out_resp.text = '{"ok": true}'
    out_resp.json.return_value = {"ok": True}
    out_resp.content = b'{"ok": true}'
    out_resp.headers = {"content-type": "application/json"}
    out_client = AsyncMock()
    out_client.request.return_value = out_resp
    out_cm = MagicMock()
    out_cm.__aenter__ = AsyncMock(return_value=out_client)
    out_cm.__aexit__ = AsyncMock(return_value=False)

    inst = _inst({
        "url": "https://example.com/upload",
        "method": "POST",
        "body": f"voice=https://files.example.com/clip.ogg\nflag={flag_url}\nchat_id=42",
    })
    with patch("httpx.AsyncClient", side_effect=[fetch_cm, out_cm]):
        _run(inst.run())

    fetch_client.get.assert_called_once()
    assert fetch_client.get.call_args[0][0] == "https://files.example.com/clip.ogg"

    kw = out_client.request.call_args[1]
    files = kw.get("files")
    data = kw.get("data") or {}
    assert files is not None and "voice" in files
    fname, fbytes, fmime = files["voice"]
    assert fbytes == fake_audio
    assert fmime == fake_mime
    assert fname == "file.ogg"
    assert "flag" in files  # the binary data URL is a file part too.
    assert "voice" not in data
    assert data.get("chat_id") == "42"


# ---------------------------------------------------------------------------
# {tag} substitution from wired inputs
# ---------------------------------------------------------------------------

def test_tag_substitution_in_body():
    inst = _inst({
        "url": "https://example.com/api",
        "method": "POST",
        "body": "city={tg_city}",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run(tg_city="Paris"))

    kw = mock_client.request.call_args[1]
    assert kw.get("data") == {"city": "Paris"}


def test_tag_substitution_in_url_and_headers():
    inst = _inst({
        "url": "https://api.example.com/{path}",
        "method": "GET",
        "headers": "X-Tenant: {tenant}",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run(path="users/42", tenant="acme"))

    call_kwargs = mock_client.request.call_args
    assert call_kwargs[0][1] == "https://api.example.com/users/42"
    assert call_kwargs[1]["headers"]["X-Tenant"] == "acme"


def test_tag_binary_data_url_triggers_multipart():
    """A {tag} substituted with a binary data: URL must flip detection to multipart."""
    png_bytes = b"\x89PNG\r\n\x1a\nFAKE"
    data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
    inst = _inst({
        "url": "https://example.com/upload",
        "method": "POST",
        "body": "photo={pic}\ncaption=hi",
    })
    cm, mock_client = _fake_client()
    with patch("httpx.AsyncClient", return_value=cm):
        _run(inst.run(pic=data_url))

    kw = mock_client.request.call_args[1]
    files = kw.get("files")
    assert files is not None and "photo" in files
    _, fbytes, fmime = files["photo"]
    assert fbytes == png_bytes
    assert fmime == "image/png"
    assert kw.get("data") == {"caption": "hi"}


# ---------------------------------------------------------------------------
# response_type
# ---------------------------------------------------------------------------

def test_response_type_auto_image_returns_data_url():
    png_bytes = b"\x89PNG\r\n\x1a\nIMG"
    inst = _inst({
        "url": "https://example.com/cat.png",
        "method": "GET",
        "response_type": "auto",
    })
    cm, _ = _fake_client(status=200, text="<binary>", content=png_bytes,
                         response_headers={"content-type": "image/png"})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert isinstance(out["body"], str)
    assert out["body"].startswith("data:image/png;base64,")
    decoded = base64.b64decode(out["body"].split(",", 1)[1])
    assert decoded == png_bytes


def test_response_type_text_returns_raw_text_even_for_json():
    inst = _inst({
        "url": "https://example.com/api",
        "method": "GET",
        "response_type": "text",
    })
    cm, _ = _fake_client(status=200, text='{"a":1}',
                         response_headers={"content-type": "application/json"})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["body"] == '{"a":1}'
    assert out["json"] == {"a": 1}


def test_response_type_json_with_invalid_json_gives_none_and_text_body():
    inst = _inst({
        "url": "https://example.com/api",
        "method": "GET",
        "response_type": "json",
    })
    cm, _ = _fake_client(status=200, text="not json at all",
                         response_headers={"content-type": "text/plain"})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["json"] is None
    assert out["body"] == "not json at all"


def test_response_type_audio_returns_data_url():
    audio_bytes = b"ID3\x03\x00FAKEMP3"
    inst = _inst({
        "url": "https://example.com/speech",
        "method": "POST",
        "response_type": "audio",
    })
    cm, _ = _fake_client(status=200, text="<binary>", content=audio_bytes,
                         response_headers={"content-type": "audio/mpeg"})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["body"].startswith("data:audio/mpeg;base64,")
    decoded = base64.b64decode(out["body"].split(",", 1)[1])
    assert decoded == audio_bytes


def test_response_type_binary_without_content_type_uses_default_mime():
    raw = b"\x00\x01\x02RAWBYTES"
    inst = _inst({
        "url": "https://example.com/blob",
        "method": "GET",
        "response_type": "binary",
    })
    cm, _ = _fake_client(status=200, text="<binary>", content=raw,
                         response_headers={})
    with patch("httpx.AsyncClient", return_value=cm):
        out = _run(inst.run())

    assert out["body"].startswith("data:application/octet-stream;base64,")


# ---------------------------------------------------------------------------
# Query: the URL's own query string and the Query knob, through real httpx
# (MockTransport, no network): the request httpx actually builds is checked.
# ---------------------------------------------------------------------------

def _mock_httpx():
    """Patch httpx.AsyncClient so every client the node opens talks to a
    MockTransport; returns (patcher, seen) where `seen` collects the requests."""
    import httpx as _httpx
    real_client = _httpx.AsyncClient
    seen: list = []

    def handler(request):
        seen.append(request)
        return _httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    def factory(*a, **kw):
        return real_client(*a, transport=_httpx.MockTransport(handler), **kw)

    return patch("httpx.AsyncClient", side_effect=factory), seen


def _sent(cfg: dict, **inputs):
    patcher, seen = _mock_httpx()
    with patcher:
        out = _run(_inst(cfg).run(**inputs))
    assert out["status"] == 200
    assert len(seen) == 1
    return seen[0].url


def test_query_written_in_the_url_is_kept():
    url = _sent({"url": "https://wttr.in/Paris?format=3", "method": "GET", "query": ""})
    assert str(url) == "https://wttr.in/Paris?format=3"


def test_query_knob_pairs_are_added_to_the_url_query():
    url = _sent({"url": "https://wttr.in/Paris?format=3", "method": "GET",
                 "query": "lang=fr\nm="})
    assert url.params.multi_items() == [("format", "3"), ("lang", "fr"), ("m", "")]


def test_query_knob_value_wins_over_the_same_key_in_the_url():
    url = _sent({"url": "https://wttr.in/Paris?format=3&lang=en", "method": "GET",
                 "query": "format=4"})
    assert url.params.multi_items() == [("format", "4"), ("lang", "en")]


def test_repeated_url_keys_survive_a_query_knob():
    url = _sent({"url": "https://api.example.com/items?tag=a&tag=b", "method": "GET",
                 "query": "page=2"})
    assert url.params.multi_items() == [("tag", "a"), ("tag", "b"), ("page", "2")]


def test_tags_in_url_and_query_substitute_and_encode():
    url = _sent({"url": "https://wttr.in/{city}?format=3", "method": "GET",
                 "query": "q={term}"}, city="New York", term="fish & chips")
    assert url.path == "/New York"
    assert url.raw_path.startswith(b"/New%20York?")
    assert url.params.multi_items() == [("format", "3"), ("q", "fish & chips")]
