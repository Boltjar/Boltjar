"""LocalGuard: the Host, Origin and token checks in front of the server, and the
per-install token behind them (boltjar.security)."""
from __future__ import annotations

import os
import stat

import pytest
from starlette.testclient import WebSocketDenialResponse

from boltjar import security
from boltjar.server import HUBS, app
from local_client import BASE_URL, LOOPBACK_PEER, LocalClient, local_client

# a page on another site, and a page served by the Vite dev server.
EVIL = "http://evil.example"
DEV = "http://localhost:5173"
# another machine on the network.
LAN_PEER = ("192.168.1.50", 51000)


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


@pytest.mark.parametrize("origin, host", [
    (None, "127.0.0.1:8770"),
    ("http://127.0.0.1:8770", "127.0.0.1:8770"),
    ("http://[::1]:8770", "[::1]:8770"),
    ("http://localhost", "localhost"),  # the scheme's default port on both sides
    (DEV, "localhost:5173"),  # the Vite dev proxy passes the page's own Host on
])
def test_the_pages_own_or_an_absent_origin_passes(client, origin, host):
    headers = {"host": host, **({"origin": origin} if origin else {})}
    assert client.post("/api/validate", json={}, headers=headers).status_code == 200


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:3000", DEV, "http://localhost:8770", "http://[::1]:8770", "https://127.0.0.1",
])
def test_a_page_from_another_local_address_is_rejected(client, origin):
    # the cookie ignores ports, so a page another local server serves (a dev
    # server, a notebook, an app's web UI) sends it too: its Origin gives it away.
    r = client.post("/api/validate", json={}, headers={"host": "127.0.0.1:8770", "origin": origin})
    assert r.status_code == 403


@pytest.mark.parametrize("origin, host", [
    ("http://box.lan:8770", "box.lan:8770"),
    ("https://box.lan", "box.lan"),  # behind a TLS proxy that passes the Host on
])
def test_allowed_host_origin_passes(client, monkeypatch, origin, host):
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "box.lan")
    r = client.post("/api/validate", json={}, headers={"host": host, "origin": origin})
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


def test_websocket_from_another_local_port_is_rejected(client):
    with pytest.raises(WebSocketDenialResponse) as denied:
        with client.websocket_connect("/ws?slug=guard-ws", headers={"origin": "http://127.0.0.1:3000"}):
            pass
    assert denied.value.status_code == 403
    HUBS.pop("guard-ws", None)


@pytest.mark.parametrize("origin, host", [("http://127.0.0.1:8770", "127.0.0.1:8770"), (DEV, "localhost:5173")])
def test_websocket_from_the_editor_connects(client, origin, host):
    with client.websocket_connect("/ws?slug=guard-ws", headers={"origin": origin, "host": host}) as ws:
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
    with anon.websocket_connect("/ws?slug=guard-ws", headers={"origin": BASE_URL}) as ws:
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


# ---------------------------------------------------------------- who gets the cookie

def test_a_network_client_gets_no_cookie(monkeypatch):
    # --allow-remote puts the machine's names in BOLTJAR_ALLOWED_HOSTS: a client on
    # the LAN passes the Host check with one, and must still prove the token.
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "box.lan")
    lan = LocalClient(app, base_url="http://box.lan:8770", client=LAN_PEER)
    r = lan.get("/api/session")
    assert r.status_code == 401
    assert "set-cookie" not in r.headers
    assert "set-cookie" not in lan.get("/").headers
    assert lan.get("/api/secrets").status_code == 401


@pytest.mark.parametrize("peer, host, extra", [
    (LAN_PEER, "127.0.0.1:8770", {}),  # a loopback name, sent from another machine
    (LOOPBACK_PEER, "box.lan:8770", {}),  # a proxy on this machine that keeps the Host
    (LOOPBACK_PEER, "127.0.0.1:8770", {"x-forwarded-for": "203.0.113.9"}),  # one that rewrites it
    (LOOPBACK_PEER, "127.0.0.1:8770", {"forwarded": "for=203.0.113.9"}),
    (("testclient", 50000), "127.0.0.1:8770", {}),  # a peer that is no address at all
])
def test_the_cookie_needs_a_direct_browser_on_this_machine(monkeypatch, peer, host, extra):
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "box.lan")
    other = LocalClient(app, base_url=BASE_URL, client=peer)
    for path in ("/", "/api/session"):
        assert "set-cookie" not in other.get(path, headers={"host": host, **extra}).headers, path
    assert other.get("/api/session", headers={"host": host, **extra}).status_code == 401


@pytest.mark.parametrize("peer, host", [
    (LOOPBACK_PEER, "127.0.0.1:8770"),
    (("::1", 50000), "[::1]:8770"),
    (("::ffff:127.0.0.1", 50000), "localhost:8770"),  # an IPv4 peer on a dual-stack socket
])
def test_a_browser_on_this_machine_gets_the_cookie(peer, host):
    here = LocalClient(app, base_url=BASE_URL, client=peer)
    r = here.get("/api/session", headers={"host": host})
    assert r.status_code == 200
    assert r.headers["set-cookie"].startswith(f"{security.COOKIE_NAME}={security.get_token()};")


def test_the_link_sets_the_cookie_and_leaves_the_address_bar(monkeypatch):
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "box.lan")
    lan = LocalClient(app, base_url="http://box.lan:8770", client=LAN_PEER)
    r = lan.get(f"/?token={security.get_token()}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert r.headers["cache-control"] == "no-store"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{security.COOKIE_NAME}={security.get_token()};")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    # the browser holds the cookie now: the editor's boot and its API calls pass.
    assert lan.get("/api/session").status_code == 200
    assert lan.get("/api/graphs").status_code == 200


@pytest.mark.parametrize("query", ["token=wrong", "token="])
def test_the_link_with_a_wrong_token_is_refused(anon, query):
    r = anon.get(f"/?{query}", follow_redirects=False)
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


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


# ---------------------------------------------------------------- content type

def test_json_routes_refuse_a_text_plain_body(client, tmp_path, monkeypatch):
    from boltjar import secrets
    monkeypatch.setattr(secrets, "_PATH", tmp_path / "secrets.json")  # never the real file
    # what a cross-site form or no-preflight fetch can send; FastAPI 0.132+ refuses it.
    r = client.post("/api/secrets", content=b'{"name": "PLANTED", "value": "x"}',
                    headers={"content-type": "text/plain"})
    assert r.status_code == 422
    assert "PLANTED" not in client.get("/api/secrets").text
