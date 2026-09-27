"""Resume workflows after launch: a graph turned On is recorded with the JSON it
runs, kept through a Restart and through the server stopping, and dropped only
when a person turns it Off (or its graph file is gone). A launch with the
setting on powers each recorded graph back On, one at a time, through the
normal validation; one that fails stays Off, stays recorded, and says why in
the terminal and in the editor."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import logging.config

import pytest

from boltjar import console, model_discovery, ollama, resume, serve, server, settings
from boltjar.graph_format import migrate
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from local_client import local_client

MANUAL_LOG = {
    "nodes": [
        {"id": "m", "type": "core.trigger.manual", "config": {}},
        {"id": "lg", "type": "core.output.log", "config": {"label": "o"}},
    ],
    "edges": [{"src": "m", "src_port": "trigger", "dst": "lg", "dst_port": "in"}],
}
# a draft of the same graph, never saved: one more Log node
MANUAL_LOG_DRAFT = {
    "nodes": MANUAL_LOG["nodes"] + [{"id": "lg2", "type": "core.output.log", "config": {"label": "d"}}],
    "edges": MANUAL_LOG["edges"] + [{"src": "m", "src_port": "trigger", "dst": "lg2", "dst_port": "in"}],
}
# nothing can fire it: validation refuses it
NO_TRIGGER = {"nodes": [{"id": "lg", "type": "core.output.log", "config": {"label": "o"}}], "edges": []}


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """This test's own Hubs, resume record, settings and saved graphs."""
    monkeypatch.setattr(server, "HUBS", {})
    monkeypatch.setattr(server, "LAUNCH_NOTICES", {})
    monkeypatch.setattr(resume, "PATH", tmp_path / "data" / "resume.json")
    monkeypatch.setattr(settings, "PATH", tmp_path / "data" / "settings.json")
    graphs = tmp_path / "graphs"
    graphs.mkdir()
    monkeypatch.setattr(server, "GRAPHS_DIR", graphs)
    monkeypatch.setattr(server, "EXAMPLES_DIR", tmp_path / "examples")
    return graphs


def save(graphs, slug: str, graph: dict) -> None:
    (graphs / f"{slug}.json").write_text(json.dumps(graph), encoding="utf-8")


def power(client, slug: str, action: str, graph: dict | None = None) -> dict:
    body = {"action": action, **({"graph": graph} if graph is not None else {})}
    return client.post(f"/api/runtime/{slug}/power", json=body).json()


def restart_server(monkeypatch) -> None:
    """A new server process: no Hubs, no notices; the files on disk stay."""
    monkeypatch.setattr(server, "HUBS", {})
    monkeypatch.setattr(server, "LAUNCH_NOTICES", {})


# ---------------------------------------------------------------- the record
def test_on_records_the_graph_with_the_json_it_runs(fresh):
    with local_client() as client:
        assert power(client, "chat", "on", MANUAL_LOG)["power"] == "on"
        assert resume.recorded() == [("chat", migrate(copy.deepcopy(MANUAL_LOG)))]
        power(client, "chat", "off")


def test_a_refused_on_records_nothing(fresh):
    with local_client() as client:
        assert power(client, "broken", "on", NO_TRIGGER)["problems"]
    assert resume.recorded() == []


def test_restart_keeps_it_recorded_with_the_new_json(fresh):
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        power(client, "digest", "on", MANUAL_LOG)
        power(client, "chat", "restart", MANUAL_LOG_DRAFT)
        assert resume.slugs() == ["chat", "digest"]  # its place in the order is kept
        assert dict(resume.recorded())["chat"] == migrate(copy.deepcopy(MANUAL_LOG_DRAFT))
        power(client, "chat", "off")
        power(client, "digest", "off")


def test_a_persons_off_drops_it(fresh):
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        power(client, "chat", "off")
    assert resume.recorded() == []


def test_the_editors_off_drops_it_too(fresh):
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        with client.websocket_connect("/ws?slug=chat") as ws:
            assert ws.receive_json()["kind"] == "status"
            ws.send_json({"action": "off"})
            while ws.receive_json()["kind"] != "status":
                pass
    assert resume.recorded() == []


def test_an_off_for_a_graph_never_on_writes_nothing(fresh):
    with local_client() as client:
        power(client, "idle", "off")
    assert not resume.PATH.exists()


def test_the_server_stopping_is_not_an_off(fresh):
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        assert client.portal.call(server.shutdown_all) == 1
        assert server.HUBS["chat"].runtime is None
    assert resume.slugs() == ["chat"]


def test_leaving_the_lifespan_is_not_an_off_either(fresh):
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
    assert server.HUBS["chat"].runtime is None
    assert resume.slugs() == ["chat"]


def test_the_record_file_is_written_whole(fresh):
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        power(client, "chat", "off")
    assert [p.name for p in resume.PATH.parent.iterdir()] == ["resume.json"]
    assert json.loads(resume.PATH.read_text(encoding="utf-8")) == {"version": 1, "graphs": {}}


# ---------------------------------------------------------------- resuming
def test_a_restart_resumes_the_json_that_was_running(fresh, monkeypatch):
    save(fresh, "chat", MANUAL_LOG)  # the saved graph...
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG_DRAFT)  # ...and the draft that ran
    restart_server(monkeypatch)
    with local_client() as client:
        resumed, failed = client.portal.call(server.resume_graphs)
        assert (resumed, failed) == (["chat"], [])
        hub = server.HUBS["chat"]
        assert hub.runtime is not None
        assert [n["id"] for n in hub.graph["nodes"]] == ["m", "lg", "lg2"]  # the draft
        assert client.get("/api/runtime/chat/state").json()["power"] == "on"
    assert resume.slugs() == ["chat"]


def test_graphs_resume_one_at_a_time_in_the_order_they_turned_on(fresh, monkeypatch):
    for slug in ("b-second", "a-first"):
        save(fresh, slug, MANUAL_LOG)
    with local_client() as client:
        power(client, "b-second", "on", MANUAL_LOG)
        power(client, "a-first", "on", MANUAL_LOG)
    restart_server(monkeypatch)
    order, active = [], []
    real = server.Hub.power_on

    async def watched(self, graph, **kw):
        active.append(self.slug)
        assert len(active) == 1  # never two at once
        order.append(self.slug)
        try:
            return await real(self, graph, **kw)
        finally:
            active.remove(self.slug)

    monkeypatch.setattr(server.Hub, "power_on", watched)
    with local_client() as client:
        assert client.portal.call(server.resume_graphs) == (["b-second", "a-first"], [])
    assert order == ["b-second", "a-first"]


def test_a_graph_that_fails_to_resume_stays_off_and_recorded_and_says_why(fresh, monkeypatch, caplog):
    save(fresh, "chat", MANUAL_LOG)
    save(fresh, "broken", MANUAL_LOG)
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        power(client, "broken", "on", MANUAL_LOG)
    # what it ran can no longer run: the recorded JSON lost its trigger
    graphs = json.loads(resume.PATH.read_text(encoding="utf-8"))
    graphs["graphs"]["broken"]["graph"] = NO_TRIGGER
    resume.PATH.write_text(json.dumps(graphs), encoding="utf-8")
    restart_server(monkeypatch)

    with caplog.at_level(logging.INFO, logger="boltjar.graph"):
        with local_client() as client:
            assert client.portal.call(server.resume_graphs) == (["chat"], ["broken"])
            assert server.HUBS["broken"].runtime is None
            # the editor that opens it is told why, as the power-on refusal it is
            with client.websocket_connect("/ws?slug=broken") as ws:
                kinds = [ws.receive_json() for _ in range(3)]
            assert kinds[0]["kind"] == "status" and kinds[0]["power"] == "off"
            assert kinds[2]["kind"] == "invalid" and kinds[2]["resume"] is True
            assert kinds[2]["problems"] == server.validate_graph(NO_TRIGGER)
            lines = [r for r in caplog.records if r.name == "boltjar.graph"]
    assert resume.slugs() == ["chat", "broken"]  # nobody turned it Off
    failure = [r for r in lines if getattr(r, "tag", None) == "broken"]
    assert len(failure) == 1  # one line: the graph and its first problem
    first = server.validate_graph(NO_TRIGGER)[0]
    assert failure[0].detail == f"not resumed: {first['node']}: {first['message']}"
    shown = [r.getMessage() for r in lines]
    summary = shown.index("resumed 1 of 2: chat; not resumed: broken")
    assert shown.index("On  2 nodes") < summary  # chat's own On line, then the summary


def test_a_graph_that_fails_to_build_stays_off_and_recorded(fresh, monkeypatch, caplog):
    save(fresh, "chat", MANUAL_LOG)
    resume.record("chat", MANUAL_LOG)

    def refuses(self, graph):
        raise RuntimeError("the store is locked")

    monkeypatch.setattr(server.Runtime, "build", refuses)
    with caplog.at_level(logging.INFO, logger="boltjar.graph"):
        with local_client() as client:
            assert client.portal.call(server.resume_graphs) == ([], ["chat"])
            with client.websocket_connect("/ws?slug=chat") as ws:
                replay = [ws.receive_json() for _ in range(3)][2]
    assert replay["kind"] == "error" and replay["resume"] is True
    assert "the store is locked" in replay["error"]
    assert resume.slugs() == ["chat"]
    chat_lines = [r for r in caplog.records if getattr(r, "tag", None) == "chat"]
    assert [r.detail.split(":")[0] for r in chat_lines] == ["failed"]  # its one line


def test_the_notice_goes_once_the_graph_turns_on(fresh, monkeypatch):
    save(fresh, "broken", MANUAL_LOG)
    resume.record("broken", NO_TRIGGER)
    with local_client() as client:
        client.portal.call(server.resume_graphs)
        assert "broken" in server.LAUNCH_NOTICES
        power(client, "broken", "on", MANUAL_LOG)  # fixed, and On
        assert "broken" not in server.LAUNCH_NOTICES
        with client.websocket_connect("/ws?slug=broken") as ws:
            assert [ws.receive_json()["kind"] for _ in range(2)] == ["status", "live_graph"]
        power(client, "broken", "off")


def test_a_graph_whose_file_is_gone_is_dropped_with_one_dim_line(fresh, caplog):
    resume.record("deleted", MANUAL_LOG)
    with caplog.at_level(logging.INFO, logger="boltjar.graph"):
        with local_client() as client:
            assert client.portal.call(server.resume_graphs) == ([], [])
    assert resume.recorded() == []
    (line,) = [r for r in caplog.records if getattr(r, "tag", None) == "deleted"]
    assert line.detail == "its graph file is gone: no longer resumed"
    assert line.glyph == "off"


def test_the_summary_line(caplog):
    lines = console.GraphLines(logging.getLogger("boltjar.graph.test"))
    with caplog.at_level(logging.INFO, logger="boltjar.graph.test"):
        lines.resume_summary(["chat", "digest"], [])
        lines.resume_summary([], ["chat"])
        lines.resume_summary([], [])
    assert [r.getMessage() for r in caplog.records] == [
        "resumed 2: chat, digest",
        "resumed 0 of 1; not resumed: chat",
        "no graph was On when Boltjar last stopped: nothing to resume",
    ]
    assert [r.tone for r in caplog.records] == ["ok", "warn", "info"]


# ---------------------------------------------------------------- the launch
@pytest.fixture
def steps(monkeypatch):
    """The three launch steps, stood in for; returns the order they ran in."""
    ran: list[str] = []

    async def start_ollama():
        ran.append("ollama")
        return "ok", "started Ollama"

    async def refresh(names, changed=False):
        ran.append(f"refresh {sorted(names)}")

    async def resume_graphs():
        ran.append("resume")
        return [], []

    monkeypatch.setattr(ollama, "launch_start", start_ollama)
    monkeypatch.setattr(model_discovery, "local_providers", lambda: ["ollama", "lmstudio"])
    monkeypatch.setattr(model_discovery, "refresh", refresh)
    monkeypatch.setattr(server, "resume_graphs", resume_graphs)
    return ran


def test_the_launch_starts_ollama_then_refreshes_local_models_then_resumes(fresh, steps):
    settings.update({"start_ollama": True, "resume_workflows": True})
    asyncio.run(server.launch_sequence())
    assert steps == ["ollama", "refresh ['lmstudio', 'ollama']", "resume"]


def test_a_fresh_install_starts_nothing_and_resumes_nothing(fresh, steps):
    asyncio.run(server.launch_sequence())
    assert steps == ["refresh ['lmstudio', 'ollama']"]


def test_no_resume_skips_resuming_for_one_launch(fresh, steps, caplog):
    settings.update({"resume_workflows": True})
    with caplog.at_level(logging.INFO, logger="boltjar.graph"):
        asyncio.run(server.launch_sequence(resume=False))
    assert steps == ["refresh ['lmstudio', 'ollama']"]
    assert any("--no-resume" in r.getMessage() for r in caplog.records)
    settings_after = settings.load()
    assert settings_after["resume_workflows"] is True  # for one launch only


def test_a_failing_step_does_not_stop_the_next(fresh, steps, monkeypatch):
    async def breaks():
        raise RuntimeError("no such file")

    monkeypatch.setattr(ollama, "launch_start", breaks)
    settings.update({"start_ollama": True, "resume_workflows": True})
    asyncio.run(server.launch_sequence())
    assert steps == ["refresh ['lmstudio', 'ollama']", "resume"]


def test_the_local_refresh_never_asks_a_cloud_provider(monkeypatch):
    bases = {"ollama": "http://localhost:11434", "xai": model_discovery.XAI_MODELS_URL,
             "anthropic": model_discovery.ANTHROPIC_MODELS_URL,
             "lmstudio": "http://127.0.0.1:1234/v1", "openrouter": "https://openrouter.ai/api/v1"}
    monkeypatch.setattr(model_discovery, "_usable_names", lambda: list(bases))
    monkeypatch.setattr(model_discovery, "_base_url", bases.get)
    assert model_discovery.local_providers() == ["ollama", "lmstudio"]


def test_the_local_refresh_is_bounded(fresh, monkeypatch):
    async def hangs(names, changed=False):
        await asyncio.Event().wait()

    monkeypatch.setattr(model_discovery, "local_providers", lambda: ["ollama"])
    monkeypatch.setattr(model_discovery, "refresh", hangs)
    monkeypatch.setattr(server, "LOCAL_REFRESH_WAIT", 0.05)
    asyncio.run(asyncio.wait_for(server.refresh_local_models(), 2))


def test_an_exit_mid_launch_resumes_nothing(fresh, monkeypatch):
    save(fresh, "chat", MANUAL_LOG)
    resume.record("chat", MANUAL_LOG)
    settings.update({"start_ollama": True, "resume_workflows": True})

    async def slow_ollama():
        await asyncio.Event().wait()  # the exit comes while Ollama is still starting
        return "ok", "started"

    monkeypatch.setattr(ollama, "launch_start", slow_ollama)
    monkeypatch.setattr(model_discovery, "local_providers", lambda: [])

    async def scenario():
        task = server.start_launch()
        await asyncio.sleep(0.01)
        await server.shutdown_all()
        assert task.cancelled()

    asyncio.run(scenario())
    assert "chat" not in server.HUBS or server.HUBS["chat"].runtime is None
    assert resume.slugs() == ["chat"]


def test_without_a_running_loop_there_is_no_launch():
    assert server.start_launch() is None


# ---------------------------------------------------------------- the serve command
def test_the_launch_starts_after_the_ready_line_with_the_flag(monkeypatch, capsys):
    import socket

    import boltjar.server as app_module

    seen = []

    def start_launch(resume=True):
        shown = capsys.readouterr().out
        seen.append((resume, "Ready" in shown, "Graphs" in shown))

    class Server:
        graphs_stopped = 0
        connections_closed = (0, 0)

        def __init__(self, on_ready):
            self.on_ready = on_ready

        def run(self, sockets=None):
            self.on_ready()

    monkeypatch.setattr(logging.config, "dictConfig", lambda config: None)
    monkeypatch.setattr(console, "prepare_streams", lambda: None)
    monkeypatch.setattr(console, "detect", lambda stream=None, **kw: console.Caps(unicode=True))
    monkeypatch.setattr(console, "terminal_width", lambda stream=None: 80)
    monkeypatch.setattr(console, "_zone_shown", None)
    monkeypatch.setattr(app_module, "start_launch", start_launch)
    monkeypatch.setattr(serve, "bind", lambda host, port: socket.socket())
    monkeypatch.setattr(serve, "make_server",
                        lambda config, shutdown_app, on_ready, **kw: Server(on_ready))
    monkeypatch.setenv("BOLTJAR_ALLOWED_HOSTS", "")
    monkeypatch.setenv("BOLTJAR_PORT", "")
    assert serve.serve(port=9001, open_browser=False, resume=False) == 0
    assert seen == [(False, True, True)]
