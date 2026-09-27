"""`python -m boltjar serve` and the command line around it: argument parsing,
the bind policy, the busy-port check, the checklist, and the start/stop order
around uvicorn. Nothing here listens for real connections."""
from __future__ import annotations

import asyncio
import logging.config
import os
import socket
import types

import pytest

from boltjar import __main__ as cli
from boltjar import console, serve


@pytest.fixture
def boot(monkeypatch):
    """serve() as the terminal runs it, minus the process-wide setup (logging
    config, stream encodings) that would leak into the rest of the suite, on a
    unicode terminal without colour 80 columns wide (the word and the
    checklist, no flask), whatever terminal runs the suite."""
    monkeypatch.setattr(logging.config, "dictConfig", lambda config: None)
    monkeypatch.setattr(console, "prepare_streams", lambda: None)
    monkeypatch.setattr(console, "detect", lambda stream=None, **kw: console.Caps(unicode=True))
    monkeypatch.setattr(console, "terminal_width", lambda stream=None: 80)
    monkeypatch.setattr(console, "_zone_shown", None)  # the Graphs rule sets it
    return serve.serve


# ---------------------------------------------------------------- the command line
def test_serve_defaults():
    args = cli.parse_args(["serve"])
    assert (args.command, args.host, args.port) == ("serve", "127.0.0.1", 8770)
    assert (args.no_browser, args.verbose, args.allow_remote) == (False, False, False)


def test_serve_flags():
    args = cli.parse_args(["serve", "--host", "0.0.0.0", "--port", "9000", "--no-browser",
                           "--verbose", "--allow-remote"])
    assert (args.host, args.port, args.no_browser, args.verbose, args.allow_remote) == \
        ("0.0.0.0", 9000, True, True, True)


@pytest.mark.parametrize("port", ["0", "65536", "http"])
def test_a_bad_port_is_a_usage_error(port, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["serve", "--port", port])
    assert exc.value.code == 2
    assert "--port" in capsys.readouterr().err


def test_run_command_and_the_original_headless_form():
    run = cli.parse_args(["run", "examples/demo.json", "2"])
    legacy = cli.parse_args(["examples/demo.json", "2"])
    for args in (run, legacy):
        assert (args.command, args.graph, args.seconds) == ("run", "examples/demo.json", 2.0)
    assert cli.parse_args(["examples/demo.json"]).seconds == 6.0


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 2
    assert "serve" in capsys.readouterr().out


def test_serve_dispatches_with_its_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(serve, "serve", lambda **kw: seen.update(kw) or 0)
    assert cli.main(["serve", "--port", "8771", "--no-browser"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 8771, "open_browser": False,
                    "verbose": False, "allow_remote": False, "resume": True}


def test_no_resume_reaches_serve(monkeypatch):
    seen = {}
    monkeypatch.setattr(serve, "serve", lambda **kw: seen.update(kw) or 0)
    assert cli.main(["serve", "--no-resume"]) == 0
    assert seen["resume"] is False


def test_the_help_explains_no_resume(capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(["serve", "--help"])
    shown = " ".join(capsys.readouterr().out.split())
    assert "--no-resume" in shown
    assert "graphs that were On" in shown
    assert "for this launch only" in shown and "the next launch" in shown


# ---------------------------------------------------------------- the bind policy
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.2"])
def test_loopback_hosts_need_no_flag(host):
    assert serve.is_loopback(host)
    assert serve.host_problem(host, allow_remote=False) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "myhost.lan"])
def test_a_reachable_host_needs_allow_remote(host):
    assert serve.host_problem(host, allow_remote=False)
    assert serve.host_problem(host, allow_remote=True) is None


def test_allowed_hosts_for_a_loopback_bind_are_the_loopback_names():
    assert serve.allowed_hosts("127.0.0.1") == ["localhost", "127.0.0.1", "::1"]


def test_allowed_hosts_add_the_bind_address():
    assert serve.allowed_hosts("192.168.1.20")[-1] == "192.168.1.20"


def test_a_wildcard_bind_allows_this_machines_names(monkeypatch):
    monkeypatch.setattr(serve, "_machine_names", lambda: ["studio", "192.168.1.20"])
    assert serve.allowed_hosts("0.0.0.0") == ["localhost", "127.0.0.1", "::1", "studio", "192.168.1.20"]


def test_the_lan_address_is_found_when_the_hostname_maps_to_loopback(monkeypatch):
    # Debian and Ubuntu: /etc/hosts gives the hostname 127.0.1.1, never the LAN address
    monkeypatch.setattr(socket, "gethostname", lambda: "studio")
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.1.1", 0))])
    monkeypatch.setattr(serve, "_outbound_addresses", lambda: ["192.168.1.20", "2001:db8::20"])
    assert serve._machine_names() == ["studio", "192.168.1.20", "2001:db8::20"]


def test_machine_names_keep_ipv6_and_drop_what_no_other_machine_can_use(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "studio")
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::20%eth0", 0, 0, 2)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 0, 0, 2)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))])
    monkeypatch.setattr(serve, "_outbound_addresses", lambda: [])
    assert serve._machine_names() == ["studio", "fd00::20", "10.0.0.5"]


def test_the_route_out_names_the_local_address_of_each_family(monkeypatch):
    class Probe:
        def __init__(self, family, kind):
            self.family = family

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def connect(self, address):
            if self.family == socket.AF_INET6:
                raise OSError("no IPv6 route")
            self.target = address

        def getsockname(self):
            return ("192.168.1.20", 50000)

    monkeypatch.setattr(socket, "socket", Probe)
    assert serve._outbound_addresses() == ["192.168.1.20"]


def test_names_already_allowed_are_kept_once():
    hosts = serve.allowed_hosts("127.0.0.1", existing="proxy.example, LOCALHOST")
    assert hosts == ["proxy.example", "LOCALHOST", "127.0.0.1", "::1"]


@pytest.mark.parametrize("host, url", [
    ("127.0.0.1", "http://127.0.0.1:8770"),
    ("0.0.0.0", "http://127.0.0.1:8770"),
    ("::1", "http://[::1]:8770"),
    ("localhost", "http://localhost:8770"),
])
def test_local_url(host, url):
    assert serve.local_url(host, 8770) == url


@pytest.mark.parametrize("platform, env, opens", [
    ("win32", {}, True),
    ("darwin", {}, True),
    ("linux", {"DISPLAY": ":0"}, True),
    ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, True),
    ("linux", {"TERM": "xterm-256color"}, False),  # a server's console: only lynx or w3m
    ("linux", {"DISPLAY": "localhost:10.0", "SSH_CONNECTION": "10.0.0.2 50000 10.0.0.5 22"}, False),
    ("darwin", {"SSH_CONNECTION": "10.0.0.2 50000 10.0.0.5 22"}, False),
])
def test_the_browser_opens_only_on_a_screen_in_front_of_the_user(platform, env, opens):
    assert serve.browser_can_open(platform, env) is opens


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "0.0.0.0", "::"])
def test_a_loopback_or_wildcard_bind_opens_loopback_without_the_token(host):
    assert serve.editor_url(host, 8770) == serve.local_url(host, 8770)
    assert "token" not in serve.editor_url(host, 8770)


def test_any_other_bind_opens_the_one_time_token_link():
    from boltjar import security
    assert serve.editor_url("192.168.1.20", 8770) == \
        f"http://192.168.1.20:8770/?token={security.get_token()}"


# ---------------------------------------------------------------- the port
def test_a_busy_port_is_caught_before_anything_starts():
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    try:
        with pytest.raises(OSError) as exc:
            serve.bind("127.0.0.1", port)
        row = serve.port_problem(exc.value, port)
        assert (row.label, row.tone) == ("Port", "bad")
        assert f"{port} is in use" in row.detail
        assert console.unbroken(f"--port {port + 1}") in row.fixes[0]
    finally:
        holder.close()


def test_a_free_port_is_bound_and_handed_over():
    sock = serve.bind("127.0.0.1", 0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()


def test_a_reserved_port_says_so():
    denied = OSError(10013, "access denied")
    denied.winerror = 10013
    row = serve.port_problem(denied, 8000)
    assert "reserved or blocked" in row.detail and console.unbroken("--port 8001") in row.fixes[0]


# ---------------------------------------------------------------- the checklist
VENV = str(serve.ROOT / ".venv")


def test_python_row_names_the_version_and_the_virtualenv(tmp_path):
    row = serve.python_row((3, 12, 9), VENV, str(tmp_path), "current")
    assert row == console.Row("Python", "3.12.9", "ok", aside=".venv", gap="  ")
    assert serve.python_row((3, 12, 9), VENV, str(tmp_path), "untracked").tone == "ok"


def test_python_row_warns_about_an_untested_version(tmp_path):
    row = serve.python_row((3, 14, 0), VENV, str(tmp_path), "current")
    assert row.tone == "warn" and "3.11 to 3.13" in row.detail and row.fixes


def test_python_row_warns_without_a_virtualenv(tmp_path):
    row = serve.python_row((3, 12, 9), str(tmp_path), str(tmp_path), "current")
    assert (row.tone, row.aside) == ("warn", "no virtualenv")
    assert "creates .venv" in row.fixes[0]


def test_python_row_warns_when_the_requirements_changed(tmp_path):
    row = serve.python_row((3, 12, 9), VENV, str(tmp_path), "changed")
    assert row.tone == "warn" and "requirements.txt changed" in row.fixes[0]


def test_a_virtualenv_outside_the_install_shows_only_its_name(tmp_path):
    # a full path would put the account name in every screenshot of the terminal
    row = serve.python_row((3, 12, 9), str(tmp_path / "envs" / "boltjar"), str(tmp_path), "current")
    assert row.aside == "boltjar"


def test_editor_row(tmp_path):
    index = tmp_path / "index.html"
    assert serve.editor_row(index).tone == "warn"
    index.write_text("<html></html>")
    assert serve.editor_row(index) == console.Row("Editor", "bundle ready")


def test_port_row():
    assert serve.port_row("127.0.0.1", 8770, ["localhost", "127.0.0.1", "::1"]) == \
        console.Row("Port", "8770 free")
    row = serve.port_row("0.0.0.0", 8770, ["localhost", "127.0.0.1", "::1", "studio"])
    assert row.tone == "warn" and row.detail == "0.0.0.0:8770, reachable from other machines as studio"


def test_a_wildcard_bind_says_how_another_machine_opens_the_editor():
    # the ready line opens loopback; without the token link, another machine
    # would get the page and a 401 from /api/session
    from boltjar import security

    row = serve.port_row("0.0.0.0", 8770, ["localhost", "127.0.0.1", "::1", "studio", "192.168.1.20"])
    assert row.fixes[0] == ("from another machine, open http://studio:8770/?token=<token> "
                            "with the token from user/data/token")
    assert security.get_token() not in " ".join(row.fixes)
    assert serve._TOKEN_QUERY == security.LINK_PARAM
    ipv6 = serve.port_row("::", 8770, ["localhost", "127.0.0.1", "::1", "fd00::5"])
    assert "http://[fd00::5]:8770/?token=<token>" in ipv6.fixes[0]


def test_a_bind_to_one_address_needs_no_token_fix():
    # its ready line already prints the whole link, which any machine can open
    row = serve.port_row("192.168.1.20", 8770, ["localhost", "127.0.0.1", "::1", "192.168.1.20"])
    assert not any("token" in fix for fix in row.fixes)


def test_packs_row_counts_the_nodes_of_every_pack():
    report = {"loaded": [{"id": "core", "nodes": 63}, {"id": "hello", "nodes": 2}], "failed": []}
    assert serve.packs_row(report) == console.Row("Packs", "core, hello", aside="(65 nodes)")


def test_packs_row_names_a_pack_that_failed():
    report = {"loaded": [{"id": "core", "nodes": 63}], "failed": [{"id": None, "folder": "broken"}]}
    row = serve.packs_row(report)
    assert (row.tone, row.detail, row.aside) == ("warn", "core; failed: broken", "(63 nodes)")
    assert "/api/packs" in row.fixes[0]


def test_packs_row_reads_the_loaded_packs():
    from boltjar import packs
    packs.load_all()
    row = serve.packs_row()
    assert row.detail.startswith("core") and row.aside.endswith(" nodes)")


# ---------------------------------------------------------------- start and stop order
def test_the_server_ends_the_apps_connections_before_uvicorn_waits_on_them(monkeypatch):
    import uvicorn

    order: list[str] = []

    class Listener:
        def close(self) -> None:
            order.append("stop accepting")

    async def uvicorn_shutdown(self, sockets=None):
        order.append("uvicorn waits for connections")

    async def uvicorn_startup(self, sockets=None):
        self.started = True

    async def shutdown_app():
        order.append("graphs stopped, editors closed")
        return 2

    monkeypatch.setattr(uvicorn.Server, "shutdown", uvicorn_shutdown)
    monkeypatch.setattr(uvicorn.Server, "startup", uvicorn_startup)
    server = serve.make_server(uvicorn.Config(app=None), shutdown_app,
                               on_ready=lambda: order.append("ready"),
                               on_stop=lambda: order.append("stopping"),
                               connections=lambda: 2)
    server.servers = [Listener()]

    asyncio.run(server.startup())
    asyncio.run(server.shutdown())
    assert order == ["ready", "stopping", "stop accepting", "graphs stopped, editors closed",
                     "uvicorn waits for connections"]
    assert server.graphs_stopped == 2
    assert server.connections_closed == 2  # counted before the stop closed them


def _stuck_server(monkeypatch, seconds: float):
    """A server whose app shutdown never finishes, with `seconds` of grace; it
    records what happens around that stop."""
    import uvicorn

    seen: dict = {"cancelled": False, "uvicorn shutdown": False}

    async def never_stops():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise

    async def uvicorn_shutdown(self, sockets=None):
        seen["uvicorn shutdown"] = True

    monkeypatch.setattr(serve, "GRACEFUL_SECONDS", seconds)
    monkeypatch.setattr(uvicorn.Server, "shutdown", uvicorn_shutdown)
    server = serve.make_server(uvicorn.Config(app=None), never_stops, on_ready=lambda: None,
                               on_stop=lambda: None, running=lambda: ["sd-stuck"])
    return server, seen


def test_a_stop_that_never_finishes_is_left_after_the_grace_time(monkeypatch, caplog):
    server, seen = _stuck_server(monkeypatch, 0.2)

    async def scenario():
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(server.shutdown(), 5)
        await asyncio.sleep(0)  # let the cancel land
        return asyncio.get_running_loop().time() - started

    with caplog.at_level(logging.WARNING, logger="boltjar.serve"):
        took = asyncio.run(scenario())
    assert 0.2 <= took < 1.5
    assert seen == {"cancelled": True, "uvicorn shutdown": True}
    # uvicorn's own shutdown skips the app's lifespan, which would wait on it again
    assert server.force_exit
    assert server.graphs_stopped == 0
    assert "sd-stuck did not stop within 0.2 s" in caplog.text


def test_a_second_ctrl_c_ends_the_wait_at_once(monkeypatch, caplog):
    server, seen = _stuck_server(monkeypatch, 60)

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, setattr, server, "force_exit", True)  # what uvicorn's handler does
        started = loop.time()
        await asyncio.wait_for(server.shutdown(), 5)
        return loop.time() - started

    with caplog.at_level(logging.WARNING, logger="boltjar.serve"):
        took = asyncio.run(scenario())
    assert took < 1.5
    assert seen["uvicorn shutdown"]
    assert "Ctrl+C again: exiting without waiting for sd-stuck" in caplog.text


def test_ready_waits_for_a_real_start(monkeypatch):
    import uvicorn

    async def failed_startup(self, sockets=None):
        self.should_exit = True  # e.g. the app's startup raised

    monkeypatch.setattr(uvicorn.Server, "startup", failed_startup)
    ready = []
    server = serve.make_server(uvicorn.Config(app=None), None, on_ready=lambda: ready.append(1),
                               on_stop=lambda: None)
    asyncio.run(server.startup())
    assert ready == []


def test_a_busy_port_stops_the_boot_with_exit_code_1(boot, monkeypatch, capsys):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    monkeypatch.setattr(serve, "make_server", lambda *a, **k: pytest.fail("the boot went on"))
    try:
        assert boot(port=port, open_browser=False) == 1
    finally:
        holder.close()
    assert f"{port} is in use" in capsys.readouterr().out


def test_a_remote_host_without_the_flag_stops_the_boot(boot, monkeypatch, capsys):
    monkeypatch.setattr(serve, "bind", lambda *a: pytest.fail("bound anyway"))
    assert boot(host="0.0.0.0", open_browser=False) == 2
    assert "--allow-remote" in capsys.readouterr().out


def test_allow_remote_hands_the_bind_to_the_server(boot, monkeypatch):
    import boltjar.server  # noqa: F401  (imported up front, so the boot's import step is instant)

    def stop_here():
        raise RuntimeError("stop before uvicorn")

    monkeypatch.setattr(serve, "_machine_names", lambda: ["studio"])
    monkeypatch.setattr(serve, "bind", lambda host, port: socket.socket())
    monkeypatch.setattr(serve, "packs_row", stop_here)  # the step right after the env is set
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "")
    monkeypatch.setenv("BOLTJAR_PORT", "")
    assert boot(host="0.0.0.0", port=9001, open_browser=False, allow_remote=True) == 1
    assert os.environ["BOLTJAR_ALLOWED_HOSTS"] == "localhost,127.0.0.1,::1,studio"
    assert os.environ["BOLTJAR_PORT"] == "9001"


def test_a_remote_bind_lists_the_names_it_answers_to(boot, monkeypatch, capsys):
    import boltjar.server  # noqa: F401  (imported up front, so the boot's import step is instant)

    def stop_here():
        raise RuntimeError("stop before uvicorn")

    monkeypatch.setattr(serve, "_machine_names", lambda: ["studio", "192.168.1.20"])
    monkeypatch.setattr(serve, "bind", lambda host, port: socket.socket())
    monkeypatch.setattr(serve, "packs_row", stop_here)
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "proxy.example")
    monkeypatch.setenv("BOLTJAR_PORT", "")
    boot(host="0.0.0.0", port=9001, open_browser=False, allow_remote=True)
    shown = " ".join(capsys.readouterr().out.split())
    assert "0.0.0.0:9001" in shown
    assert "reachable from other machines as proxy.example, studio, 192.168.1.20" in shown
    assert "from another machine, open http://proxy.example:9001/?token=<token> with the token" in shown
    assert "add it to BOLTJAR_ALLOWED_HOSTS" in shown


def test_a_boot_over_ssh_leaves_the_browser_closed(boot, monkeypatch):
    import boltjar.server  # noqa: F401  (imported up front, so the boot's import step is instant)

    class Server:
        graphs_stopped = 0
        connections_closed = 0

        def __init__(self, on_ready):
            self.on_ready = on_ready

        def run(self, sockets=None):
            self.on_ready()

    class Thread:  # runs the browser call at once, so the assertion never races it
        def __init__(self, target, args, daemon):
            self.run = lambda: target(*args)

        def start(self):
            self.run()

    opened: list[str] = []
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 50000 10.0.0.5 22")
    monkeypatch.setattr(serve.webbrowser, "open", opened.append)
    monkeypatch.setattr(serve, "threading", types.SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(serve, "bind", lambda host, port: socket.socket())
    monkeypatch.setattr(serve, "packs_row", lambda: console.Row("Packs", "core"))
    monkeypatch.setattr(serve, "make_server", lambda config, shutdown_app, on_ready, **kw: Server(on_ready))
    assert boot(port=9001) == 0
    assert opened == []


class ReadyServer:
    """Stands in for uvicorn: reports ready at once, then returns as after a Ctrl+C."""
    graphs_stopped = 0
    connections_closed = 0

    def __init__(self, on_ready, on_stop=None):
        self.on_ready = on_ready
        self.on_stop = on_stop

    def run(self, sockets=None):
        self.on_ready()


def ready_boot(boot, monkeypatch, server=ReadyServer, **options):
    """A whole boot on a stand-in socket and server; returns the exit code."""
    import boltjar.server  # noqa: F401  (imported up front, so the boot's import step is instant)
    monkeypatch.setattr(serve, "bind", lambda host, port: socket.socket())
    monkeypatch.setattr(serve, "make_server",
                        lambda config, shutdown_app, on_ready, on_stop, **kw: server(on_ready, on_stop))
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "")
    monkeypatch.setenv("BOLTJAR_PORT", "")
    return boot(**{"port": 9001, "open_browser": False, **options})


def test_the_checklist_is_python_editor_port_and_packs(boot, monkeypatch):
    shown: list = []
    monkeypatch.setattr(console.Console, "banner", lambda self, version, rows: shown.extend(rows))
    assert ready_boot(boot, monkeypatch) == 0
    assert [row.label for row in shown] == ["Python", "Editor", "Port", "Packs"]


def test_the_ready_block_and_the_graphs_rule(boot, monkeypatch, capsys):
    assert ready_boot(boot, monkeypatch) == 0
    lines = capsys.readouterr().out.splitlines()
    ready = next(i for i, line in enumerate(lines) if "Ready" in line)
    assert lines[ready].endswith("Ready  →  http://127.0.0.1:9001")
    assert lines[ready + 1].endswith("Ctrl+C stops everything · --verbose shows requests")
    assert lines[ready + 3].startswith(f"── Graphs  times in {console.local_zone()} ─")


def test_the_ready_note_says_only_what_applies(boot, monkeypatch, capsys):
    monkeypatch.setattr(serve, "browser_can_open", lambda: True)
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: None)
    ready_boot(boot, monkeypatch, open_browser=True, verbose=True)
    note = next(line for line in capsys.readouterr().out.splitlines() if "Ctrl+C" in line)
    assert note.endswith("opening your browser · Ctrl+C stops everything")


def test_a_lan_bind_prints_the_token_link(boot, monkeypatch, capsys):
    from boltjar import security
    ready_boot(boot, monkeypatch, host="192.168.1.20", allow_remote=True)
    assert f"http://192.168.1.20:9001/?token={security.get_token()}" in capsys.readouterr().out


def test_a_wildcard_bind_shows_the_link_for_other_machines_without_the_token(boot, monkeypatch, capsys):
    from boltjar import security
    monkeypatch.setattr(serve, "_machine_names", lambda: ["studio"])
    ready_boot(boot, monkeypatch, host="0.0.0.0", allow_remote=True)
    out = capsys.readouterr().out
    assert "Ready  →  http://127.0.0.1:9001\n" in out
    assert "http://studio:9001/?token=<token>" in out and security.get_token() not in out


def test_ctrl_c_prints_the_shutdown_steps_and_bye(boot, monkeypatch, capsys):
    import boltjar.server as app_module

    class StoppedServer(ReadyServer):
        graphs_stopped = 1
        connections_closed = 2

        def run(self, sockets=None):
            self.on_ready()
            self.on_stop()  # what the Ctrl+C sets off

    monkeypatch.setattr(app_module, "running_graphs", lambda: ["chat"])
    assert ready_boot(boot, monkeypatch, server=StoppedServer) == 0
    lines = capsys.readouterr().out.splitlines()
    shutdown = next(i for i, line in enumerate(lines) if "Shutdown" in line)
    assert lines[shutdown].startswith("── Shutdown ─")
    assert lines[shutdown + 1:] == [
        "▌ ▶ stopping 1 graph",
        "▌ ✓ closed 2 editor connections",
        "▌ bye",
    ]


def test_the_boot_never_lists_providers_or_keys(boot, monkeypatch, capsys):
    # providers and keys change while the server runs (the editor's Settings),
    # so a line about them would go stale: the terminal never asks, never lists.
    import boltjar.server  # noqa: F401  (imported up front, so the boot's import step is instant)
    from boltjar import secrets

    def refuse(*args, **kwargs):
        raise AssertionError("the boot asked about providers")

    monkeypatch.setattr(secrets, "provider_status", refuse)
    monkeypatch.setattr(secrets, "provider_connected", refuse)
    monkeypatch.setattr(serve, "bind", lambda host, port: socket.socket())
    monkeypatch.setattr(serve, "make_server",
                        lambda config, shutdown_app, on_ready, **kw: ReadyServer(on_ready))
    assert boot(port=9001, open_browser=False) == 0
    shown = capsys.readouterr().out.lower()
    assert "9001" in shown  # the boot got as far as the ready line
    assert "provider" not in shown and "key" not in shown
