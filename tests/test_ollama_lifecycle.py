"""Ollama on this computer: found where its installer puts it, reported as not
installed, stopped or running, started on request (detached, its output in a
log), and stopped on exit only when this Boltjar started it. No test runs
Ollama: the process and its answers are stood in for."""
from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess

import pytest

from boltjar import ollama, server
from local_client import local_client


class FakeProc:
    """A stand-in for the `ollama serve` process."""
    started: list["FakeProc"] = []

    def __init__(self, args, **options):
        self.args = args
        self.options = options
        self.pid = 4242
        self.returncode = None
        FakeProc.started.append(self)

    def poll(self):
        return self.returncode


@pytest.fixture
def fake_ollama(monkeypatch, tmp_path):
    """Ollama installed at a stand-in path, not answering until `state.up`."""
    FakeProc.started = []
    exe = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    class State:
        up = False
        answers_after = 0  # asks before a started Ollama answers
        asked = 0

    state = State()

    async def answers(timeout: float = 1.0) -> bool:
        state.asked += 1
        if state.up:
            return True
        if FakeProc.started and FakeProc.started[-1].returncode is None \
                and state.asked > state.answers_after:
            state.up = True
        return state.up

    monkeypatch.setattr(ollama, "find", lambda *a, **k: exe)
    monkeypatch.setattr(ollama, "answers", answers)
    monkeypatch.setattr(ollama, "_popen", FakeProc)
    monkeypatch.setattr(ollama, "_started", None)
    state.ended = []  # the pids an exit ended
    monkeypatch.setattr(ollama, "_end_tree", lambda proc, grace: state.ended.append(proc.pid))
    monkeypatch.setattr(ollama, "POLL_EVERY", 0.001)
    monkeypatch.setattr(ollama, "LOG_PATH", tmp_path / "logs" / "ollama.log")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    state.exe = exe
    return state


# ---------------------------------------------------------------- finding it
def test_ollama_on_path_is_found_first(tmp_path):
    exe = tmp_path / "bin" / "ollama"
    assert ollama.find("linux", {"PATH": "x"}, which=lambda name, path=None: str(exe)) == exe


def test_the_windows_installer_folder_is_found(tmp_path):
    exe = tmp_path / "Local" / "Programs" / "Ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    env = {"PATH": "", "LOCALAPPDATA": str(tmp_path / "Local")}
    assert ollama.find("win32", env, which=lambda name, path=None: None) == exe


def test_the_macos_app_is_where_its_installer_puts_it():
    assert ollama.default_paths("darwin", {}) == [
        pathlib.Path("/Applications/Ollama.app/Contents/Resources/ollama")]


def test_linux_looks_on_path_only_and_nothing_found_is_none(tmp_path):
    assert ollama.default_paths("linux", {}) == []
    env = {"PATH": "", "LOCALAPPDATA": str(tmp_path / "nowhere")}
    assert ollama.find("win32", env, which=lambda name, path=None: None) is None


# ---------------------------------------------------------------- its state
def test_the_three_states(monkeypatch, tmp_path):
    exe = tmp_path / "ollama.exe"
    monkeypatch.setattr(ollama, "find", lambda *a, **k: exe)
    assert ollama.status(running=True)["state"] == "running"
    stopped = ollama.status(running=False)
    assert stopped["state"] == "stopped" and stopped["startable"] is True
    monkeypatch.setattr(ollama, "find", lambda *a, **k: None)
    missing = ollama.status(running=False)
    assert missing["state"] == "not_installed" and missing["startable"] is False
    assert missing["download"] == "https://ollama.com/download"
    assert ollama.status(running=True)["state"] == "running"  # answers, from anywhere


@pytest.mark.parametrize("installed_here", [True, False])
def test_a_remote_base_url_that_does_not_answer_is_not_answering(monkeypatch, tmp_path,
                                                                  installed_here):
    """Whether Ollama is installed on this computer says nothing about the
    machine OLLAMA_BASE_URL names: never "not installed", never started here."""
    monkeypatch.setattr(ollama, "find",
                        lambda *a, **k: (tmp_path / "ollama.exe") if installed_here else None)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.50:11434")
    shown = ollama.status(running=False)
    assert shown["state"] == "unreachable" and shown["startable"] is False
    assert shown["reason"] == ("not answering at http://192.168.1.50:11434 (OLLAMA_BASE_URL): "
                               "start Ollama on that machine")
    assert ollama.status(running=True)["state"] == "running"


def test_start_leaves_a_remote_base_url_alone(fake_ollama, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.50:11434")
    with local_client() as client:
        r = client.post("/api/connections/ollama/start")
    assert r.status_code == 409
    assert r.json()["state"] == "unreachable" and "192.168.1.50" in r.json()["error"]
    assert FakeProc.started == []


@pytest.mark.parametrize("running, installed, state", [
    (True, True, "running"), (False, True, "stopped"), (False, False, "not_installed"),
])
def test_connections_tells_the_card_the_state(monkeypatch, tmp_path, running, installed, state):
    monkeypatch.setattr(server._secrets, "_ollama_connected", lambda: running)
    monkeypatch.setattr(ollama, "find", lambda *a, **k: (tmp_path / "ollama.exe") if installed else None)
    with local_client() as client:
        providers = client.get("/api/connections").json()["providers"]
    entry = next(p for p in providers if p["provider"] == "ollama")
    assert entry["connected"] is running
    assert entry["local"]["state"] == state
    assert all("local" not in p for p in providers if p["provider"] != "ollama")


# ---------------------------------------------------------------- starting it
def test_start_runs_ollama_serve_detached_and_waits_until_it_answers(fake_ollama):
    fake_ollama.answers_after = 3
    with local_client() as client:
        r = client.post("/api/connections/ollama/start")
        assert ollama.started_here() is True
    assert fake_ollama.ended == [4242]  # the exit stopped it: this Boltjar started it
    assert r.status_code == 200
    assert r.json() == {"ok": True, "state": "running", "started": True, "error": None}
    (proc,) = FakeProc.started
    assert proc.args == [str(fake_ollama.exe), "serve"]
    assert proc.options["stdin"] is subprocess.DEVNULL
    assert proc.options["stderr"] is subprocess.STDOUT
    assert pathlib.Path(proc.options["stdout"].name) == ollama.LOG_PATH
    if os.name == "nt":  # no console window, and out of the terminal's Ctrl+C group
        assert proc.options["creationflags"] & 0x08000000
        assert proc.options["creationflags"] & 0x00000200
    else:
        assert proc.options["start_new_session"] is True
    assert "started by Boltjar at" in ollama.LOG_PATH.read_text(encoding="utf-8")
    assert "OLLAMA_HOST" not in proc.options["env"] or \
        proc.options["env"]["OLLAMA_HOST"] == os.environ.get("OLLAMA_HOST")


def test_start_on_a_custom_local_port_tells_ollama_where_to_listen(fake_ollama, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11500")
    asyncio.run(ollama.start())
    assert FakeProc.started[0].options["env"]["OLLAMA_HOST"] == "127.0.0.1:11500"


def test_start_leaves_a_running_ollama_alone(fake_ollama):
    fake_ollama.up = True
    with local_client() as client:
        r = client.post("/api/connections/ollama/start")
    assert r.json()["started"] is False and r.json()["ok"] is True
    assert FakeProc.started == []
    assert fake_ollama.ended == []  # and the exit leaves it running


def test_start_without_ollama_installed_is_a_409(fake_ollama, monkeypatch):
    monkeypatch.setattr(ollama, "find", lambda *a, **k: None)
    with local_client() as client:
        r = client.post("/api/connections/ollama/start")
    assert r.status_code == 409
    assert r.json()["state"] == "not_installed"
    assert "ollama.com/download" in r.json()["error"]
    assert FakeProc.started == []


def test_an_ollama_that_stops_at_once_is_a_502_naming_the_log(fake_ollama, monkeypatch):
    real = FakeProc.__init__

    def dies(self, args, **options):
        real(self, args, **options)
        self.returncode = 1

    monkeypatch.setattr(FakeProc, "__init__", dies)
    with local_client() as client:
        r = client.post("/api/connections/ollama/start")
    assert r.status_code == 502
    assert "exit code 1" in r.json()["error"] and "ollama.log" in r.json()["error"]
    assert ollama.started_here() is False


def test_an_ollama_that_never_answers_is_reported_after_the_wait(fake_ollama):
    fake_ollama.answers_after = 10 ** 9
    result = asyncio.run(ollama.start(wait=0.05))
    assert result["ok"] is False and result["started"] is True
    assert "did not answer within 0.05 s" in result["error"]
    assert ollama.started_here() is True  # still starting: it is ours to stop on exit


def test_two_starts_at_once_start_one(fake_ollama):
    fake_ollama.answers_after = 5

    async def both():
        return await asyncio.gather(ollama.start(), ollama.start())

    results = asyncio.run(both())
    assert len(FakeProc.started) == 1
    assert all(r["ok"] for r in results)


# ---------------------------------------------------------------- stopping it
def test_boltjar_stops_the_ollama_it_started(fake_ollama, monkeypatch):
    ended = []
    monkeypatch.setattr(ollama, "_end_tree", lambda proc, grace: ended.append(proc.pid))
    asyncio.run(ollama.start())
    assert asyncio.run(ollama.stop_if_started()) is True
    assert ended == [4242]
    assert ollama.started_here() is False
    assert asyncio.run(ollama.stop_if_started()) is False  # once


def test_boltjar_never_stops_an_ollama_it_did_not_start(fake_ollama, monkeypatch):
    ended = []
    monkeypatch.setattr(ollama, "_end_tree", lambda proc, grace: ended.append(proc.pid))
    fake_ollama.up = True  # already running when asked
    asyncio.run(ollama.start())
    assert asyncio.run(ollama.stop_if_started()) is False
    assert ended == []


def test_an_ollama_that_already_ended_is_not_stopped_again(fake_ollama, monkeypatch):
    ended = []
    monkeypatch.setattr(ollama, "_end_tree", lambda proc, grace: ended.append(proc.pid))
    asyncio.run(ollama.start())
    FakeProc.started[0].returncode = 0
    assert asyncio.run(ollama.stop_if_started()) is False
    assert ended == []


def test_the_server_exit_stops_the_ollama_it_started(fake_ollama, monkeypatch):
    ended = []
    monkeypatch.setattr(ollama, "_end_tree", lambda proc, grace: ended.append(proc.pid))
    monkeypatch.setattr(server, "HUBS", {})
    with local_client() as client:
        assert client.post("/api/connections/ollama/start").json()["started"] is True
    # leaving the app's lifespan is the exit
    assert ended == [4242]


# ---------------------------------------------------------------- the launch line
@pytest.mark.parametrize("setup, tone, words", [
    ("running", "info", "already running"),
    ("stopped", "ok", "started Ollama"),
    ("missing", "warn", "not installed: https://ollama.com/download"),
])
def test_the_launch_step_prints_one_line_either_way(fake_ollama, monkeypatch, setup, tone, words):
    if setup == "running":
        fake_ollama.up = True
    if setup == "missing":
        monkeypatch.setattr(ollama, "find", lambda *a, **k: None)
    got_tone, text = asyncio.run(ollama.launch_start())
    assert got_tone == tone and words in text


# ---------------------------------------------------------------- every way out
def test_an_exit_whose_graph_stop_runs_late_still_stops_ollama(fake_ollama, monkeypatch):
    import uvicorn

    from boltjar import serve

    monkeypatch.setattr(server, "HUBS", {})
    asyncio.run(ollama.start())
    assert ollama.started_here()

    class HangingRuntime:
        async def stop(self):
            await asyncio.Event().wait()

    server.get_hub("slow").runtime = HangingRuntime()
    monkeypatch.setattr(serve, "GRACEFUL_SECONDS", 0.3)
    exiting = serve.make_server(uvicorn.Config(app=server.app), server.shutdown_all,
                                on_ready=lambda: None, on_stop=lambda: None,
                                running=server.running_graphs)
    asyncio.run(exiting.stop_app())
    assert exiting.force_exit  # uvicorn skips the lifespan shutdown after this
    assert fake_ollama.ended == [4242]
    assert not ollama.started_here()


def test_the_serve_command_stops_ollama_however_the_server_returns(fake_ollama, monkeypatch):
    """A second Ctrl+C cancels the stop before it began: the serve command
    stops the Ollama it started once the server has returned, whatever ran."""
    import logging.config
    import socket

    from boltjar import console, serve

    asyncio.run(ollama.start())

    class Server:
        graphs_stopped = 0
        connections_closed = (0, 0)

        def __init__(self, on_ready):
            self.on_ready = on_ready

        def run(self, sockets=None):
            pass  # returns as a forced exit does, with nothing stopped

    monkeypatch.setattr(logging.config, "dictConfig", lambda config: None)
    monkeypatch.setattr(console, "prepare_streams", lambda: None)
    monkeypatch.setattr(console, "detect", lambda stream=None, **kw: console.Caps(unicode=True))
    monkeypatch.setattr(console, "terminal_width", lambda stream=None: 80)
    monkeypatch.setattr(console, "_zone_shown", None)
    monkeypatch.setattr(serve, "bind", lambda host, port: socket.socket())
    monkeypatch.setattr(serve, "make_server",
                        lambda config, shutdown_app, on_ready, **kw: Server(on_ready))
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "")
    monkeypatch.setenv("BOLTJAR_PORT", "")
    assert serve.serve(port=9001, open_browser=False) == 0
    assert fake_ollama.ended == [4242]


# ---------------------------------------------------------------- another machine
REMOTE_PEER = ("192.168.1.20", 50000)  # a browser on the LAN, through the token link


def test_a_remote_peer_cannot_start_ollama(fake_ollama):
    with local_client(client=REMOTE_PEER) as client:
        r = client.post("/api/connections/ollama/start")
    assert r.status_code == 403
    assert "only on this computer" in r.json()["error"]
    assert FakeProc.started == []


@pytest.mark.parametrize("peer, editable", [(("127.0.0.1", 50000), True), (REMOTE_PEER, False)])
def test_the_ollama_card_knows_whether_this_browser_may_start_it(fake_ollama, monkeypatch,
                                                                 peer, editable):
    monkeypatch.setattr(server._secrets, "_ollama_connected", lambda: False)
    with local_client(client=peer) as client:
        providers = client.get("/api/connections").json()["providers"]
    entry = next(p for p in providers if p["provider"] == "ollama")
    assert entry["local"]["state"] == "stopped"
    assert entry["local"]["editable"] is editable
