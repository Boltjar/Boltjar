"""LocalGuard: the Host, Origin and token checks in front of the server, and the
per-install token behind them (boltjar.security)."""
from __future__ import annotations

import os
import stat

import pytest
from starlette.testclient import WebSocketDenialResponse

from boltjar import security
from boltjar.server import HUBS
from local_client import local_client

# a page on another site, and a page served by the Vite dev server.
EVIL = "http://evil.example"
DEV = "http://localhost:5173"


@pytest.fixture
def anon():
    """A loopback client that carries no token."""
    return local_client(token=False)


@pytest.fixture
def client():
    return local_client()


# ---------------------------------------------------------------- host

@pytest.mark.parametrize("host", ["127.0.0.1:8770", "localhost:8770", "[::1]:8770", "LOCALHOST"])
def test_loopback_hosts_pass(client, host):
    assert client.get("/api/graphs", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8770", "127.0.0.1.evil.example", ""])
def test_foreign_host_is_rejected(client, host):
    # a DNS-rebound page reaches 127.0.0.1 under its own name: the Host gives it away.
    r = client.get("/api/graphs", headers={"host": host})
    assert r.status_code == 400
    assert r.json() == {"error": "invalid host header"}


def test_foreign_host_never_gets_the_cookie(anon):
    r = anon.get("/api/session", headers={"host": "evil.example:8770"})
    assert r.status_code == 400
    assert "set-cookie" not in r.headers


def test_allowed_hosts_env_adds_names(client, monkeypatch):
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "box.lan, 192.168.1.20:8770, fe80::1")
    for host in ("box.lan:8770", "192.168.1.20:8770", "[fe80::1]:8770", "127.0.0.1:8770"):
        assert client.get("/api/graphs", headers={"host": host}).status_code == 200, host
    assert client.get("/api/graphs", headers={"host": "other.lan"}).status_code == 400


def test_hook_skips_the_host_check(anon):
    # a webhook arrives through a tunnel under the tunnel's name; its own secret guards it.
    r = anon.post("/hook/no-such-graph/x", headers={"host": "abc.tunnel.example"})
    assert r.status_code == 404


# ---------------------------------------------------------------- origin

@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_state_change_from_another_site_is_rejected(client, method):
    kwargs = {"json": {}} if method != "delete" else {}
    r = getattr(client, method)("/api/graphs/guard-test", headers={"origin": EVIL}, **kwargs)
    assert r.status_code == 403
    assert r.json() == {"error": "origin not allowed"}


@pytest.mark.parametrize("origin", ["null", "file://", "http://127.0.0.1.evil.example"])
def test_opaque_and_lookalike_origins_are_rejected(client, origin):
    assert client.post("/api/validate", json={}, headers={"origin": origin}).status_code == 403


@pytest.mark.parametrize("origin", [None, DEV, "http://127.0.0.1:8770", "http://[::1]:9000", "https://localhost"])
def test_loopback_or_absent_origin_passes(client, origin):
    headers = {"origin": origin} if origin else {}
    assert client.post("/api/validate", json={}, headers=headers).status_code == 200


def test_allowed_host_origin_passes(client, monkeypatch):
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "box.lan")
    r = client.post("/api/validate", json={}, headers={"host": "box.lan:8770", "origin": "http://box.lan:8770"})
    assert r.status_code == 200


def test_reads_are_not_origin_checked(client):
    # a cross-site GET cannot carry the SameSite=Strict cookie, so the token stops it.
    assert client.get("/api/graphs", headers={"origin": EVIL}).status_code == 200


def test_hook_skips_the_origin_check(anon):
    assert anon.post("/hook/no-such-graph/x", headers={"origin": EVIL}).status_code == 404


def test_websocket_from_another_site_is_rejected(client):
    with pytest.raises(WebSocketDenialResponse) as denied:
        with client.websocket_connect("/ws?slug=guard-ws", headers={"origin": EVIL}):
            pass
    assert denied.value.status_code == 403
    HUBS.pop("guard-ws", None)


def test_websocket_from_the_editor_connects(client):
    with client.websocket_connect("/ws?slug=guard-ws", headers={"origin": DEV}) as ws:
        assert ws.receive_json()["kind"] == "status"
    HUBS.pop("guard-ws", None)


# ---------------------------------------------------------------- token

@pytest.mark.parametrize("path", ["/api/graphs", "/api/secrets", "/api/runtime/x/state", "/stream/x/c"])
def test_protected_reads_need_the_token(anon, path):
    r = anon.get(path)
    assert r.status_code == 401
    assert r.json() == {"error": "missing or invalid token"}


def test_audio_needs_the_token(anon):
    assert anon.post("/audio/x/n", json={"audio": "a"}).status_code == 401


def test_websocket_needs_the_token(anon):
    with pytest.raises(WebSocketDenialResponse) as denied:
        with anon.websocket_connect("/ws?slug=guard-ws"):
            pass
    assert denied.value.status_code == 401
    HUBS.pop("guard-ws", None)


def test_wrong_token_is_rejected(anon):
    wrong = "x" * len(security.get_token())
    assert anon.get("/api/graphs", headers={"authorization": f"Bearer {wrong}"}).status_code == 401
    assert anon.get("/api/graphs", headers={"authorization": "Bearer "}).status_code == 401
    assert anon.get("/api/graphs", headers={"cookie": f"{security.COOKIE_NAME}={wrong}"}).status_code == 401


def test_bearer_header_is_accepted(anon):
    token = security.get_token()
    assert anon.get("/api/graphs", headers={"authorization": f"Bearer {token}"}).status_code == 200


def test_session_sets_a_strict_http_only_cookie(anon):
    r = anon.get("/api/session")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{security.COOKIE_NAME}={security.get_token()};")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # plain http on loopback


def test_session_cookie_then_authorises_api_and_websocket(anon):
    # the editor's boot sequence: GET /api/session, then everything else.
    anon.get("/api/session")
    assert anon.get("/api/graphs").status_code == 200
    with anon.websocket_connect("/ws?slug=guard-ws", headers={"origin": DEV}) as ws:
        assert ws.receive_json()["kind"] == "status"
    HUBS.pop("guard-ws", None)


def test_session_refuses_another_site(anon):
    r = anon.get("/api/session", headers={"origin": EVIL})
    assert r.status_code == 403
    assert "set-cookie" not in r.headers


def test_editor_shell_sets_the_cookie_without_a_token(anon):
    r = anon.get("/")
    assert r.status_code != 401
    assert r.headers["set-cookie"].startswith(f"{security.COOKIE_NAME}=")


def test_static_files_need_no_token(anon):
    assert anon.get("/assets/no-such-file.js").status_code in (200, 404)


# ---------------------------------------------------------------- token file

@pytest.fixture
def token_path(tmp_path, monkeypatch):
    path = tmp_path / "data" / "token"
    monkeypatch.setattr(security, "TOKEN_PATH", path)
    return path


def test_token_is_created_once_and_reused(token_path, monkeypatch):
    assert security.read_token() is None
    token = security.get_token()
    assert len(token) >= 43  # 32 random bytes, url-safe base64
    assert token_path.read_text(encoding="utf-8") == token
    monkeypatch.setattr(security, "_cached", None)  # a fresh process reads the same file
    assert security.get_token() == token


def test_empty_token_file_is_replaced(token_path):
    token_path.parent.mkdir(parents=True)
    token_path.write_text("", encoding="utf-8")
    token = security.get_token()
    assert token and token_path.read_text(encoding="utf-8") == token


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX only")
def test_token_file_is_owner_only(token_path):
    security.get_token()
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
