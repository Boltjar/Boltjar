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
    # this test's own launch: new_launch() is undone after it
    monkeypatch.setattr(resume, "LAUNCH", resume.LAUNCH)
    monkeypatch.setattr(resume, "_claimed", set())
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
    """A new server process: no Hubs, no notices, a new launch; the files on
    disk stay."""
    monkeypatch.setattr(server, "HUBS", {})
    monkeypatch.setattr(server, "LAUNCH_NOTICES", {})
    resume.new_launch()


def left_on(monkeypatch, *graphs: tuple[str, dict]) -> None:
    """The last run ended with these graphs On: recorded, then a new launch."""
    for slug, graph in graphs:
        resume.record(slug, graph)
    restart_server(monkeypatch)


@pytest.fixture
def no_local_providers(monkeypatch):
    monkeypatch.setattr(model_discovery, "local_providers", lambda: [])


def running() -> list[str]:
    return sorted(slug for slug, hub in server.HUBS.items() if hub.runtime is not None)


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
    real = server.Hub.resume

    async def watched(self, graph):
        active.append(self.slug)
        assert len(active) == 1  # never two at once
        order.append(self.slug)
        try:
            return await real(self, graph)
        finally:
            active.remove(self.slug)

    monkeypatch.setattr(server.Hub, "resume", watched)
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
    left_on(monkeypatch, ("chat", MANUAL_LOG))

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
    left_on(monkeypatch, ("broken", NO_TRIGGER))
    with local_client() as client:
        client.portal.call(server.resume_graphs)
        assert "broken" in server.LAUNCH_NOTICES
        power(client, "broken", "on", MANUAL_LOG)  # fixed, and On
        assert "broken" not in server.LAUNCH_NOTICES
        with client.websocket_connect("/ws?slug=broken") as ws:
            assert [ws.receive_json()["kind"] for _ in range(2)] == ["status", "live_graph"]
        power(client, "broken", "off")


def test_a_graph_whose_file_is_gone_is_dropped_with_one_dim_line(fresh, monkeypatch, caplog):
    left_on(monkeypatch, ("deleted", MANUAL_LOG))
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
    left_on(monkeypatch, ("chat", MANUAL_LOG))
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


# ---------------------------------------------------------------- the file itself
def _unreadable(path):
    """Path.read_text that fails on `path` the way Windows does while another
    program (an antivirus, a backup, an indexer) holds the file open."""
    import pathlib

    real = pathlib.Path.read_text

    def read_text(self, *args, **kwargs):
        if self == path:
            raise PermissionError(13, "The process cannot access the file")
        return real(self, *args, **kwargs)

    return read_text


def test_a_record_that_cannot_be_read_is_never_written_over(fresh, monkeypatch):
    import pathlib

    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        power(client, "digest", "on", MANUAL_LOG)
        before = resume.PATH.read_bytes()
        with monkeypatch.context() as m:
            m.setattr(pathlib.Path, "read_text", _unreadable(resume.PATH))
            # the graph still turns On and Off: only the record waits
            assert power(client, "third", "on", MANUAL_LOG)["power"] == "on"
            assert power(client, "chat", "off")["power"] == "off"
        assert resume.PATH.read_bytes() == before  # chat and digest are still there
        for slug in ("digest", "third"):
            power(client, slug, "off")


def test_a_broken_record_is_set_aside_before_a_write(fresh):
    resume.PATH.parent.mkdir(parents=True)
    resume.PATH.write_text("{not json", encoding="utf-8")
    assert resume.recorded() == []
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        power(client, "chat", "off")
    assert (resume.PATH.parent / "resume.json.bad").read_text(encoding="utf-8") == "{not json"


def test_a_launch_that_cannot_read_the_record_resumes_nothing_and_says_so(fresh, monkeypatch, caplog):
    import pathlib

    save(fresh, "chat", MANUAL_LOG)
    left_on(monkeypatch, ("chat", MANUAL_LOG))
    before = resume.PATH.read_bytes()
    with caplog.at_level(logging.INFO, logger="boltjar.graph"):
        with local_client() as client:
            with monkeypatch.context() as m:
                m.setattr(pathlib.Path, "read_text", _unreadable(resume.PATH))
                assert client.portal.call(server.resume_graphs) == ([], [])
            assert "chat" not in server.HUBS or server.HUBS["chat"].runtime is None
    assert any("could not read the graphs that were On" in r.getMessage() for r in caplog.records)
    assert resume.PATH.read_bytes() == before  # tried again at the next launch


# ---------------------------------------------------------------- On at the last exit
def test_only_the_graphs_on_at_the_last_exit_come_back(fresh, monkeypatch, no_local_providers):
    save(fresh, "old", MANUAL_LOG)
    save(fresh, "today", MANUAL_LOG)
    # launch 1, Resume off (the default): "old" is On when Boltjar stops
    with local_client() as client:
        client.portal.call(server.launch_sequence)
        power(client, "old", "on", MANUAL_LOG)
    restart_server(monkeypatch)
    # launch 2, still off: "old" stays Off all along, "today" is On at the exit,
    # and the person turns Resume on before stopping Boltjar
    with local_client() as client:
        client.portal.call(server.launch_sequence)
        power(client, "today", "on", MANUAL_LOG)
        settings.update({"resume_workflows": True})
    restart_server(monkeypatch)
    with local_client() as client:
        client.portal.call(server.launch_sequence)
        on = running()
        for slug in on:
            power(client, slug, "off")
    assert on == ["today"]


def test_a_launch_with_resume_off_drops_what_the_last_run_left(fresh, monkeypatch, no_local_providers):
    save(fresh, "digest", MANUAL_LOG)
    with local_client() as client:
        power(client, "digest", "on", MANUAL_LOG)
    restart_server(monkeypatch)
    with local_client() as client:
        # a graph turned On before the launch steps run is this run's own
        power(client, "chat", "on", MANUAL_LOG)
        client.portal.call(server.launch_sequence)
        assert resume.slugs() == ["chat"]  # digest was Off in this run
        power(client, "chat", "off")


def test_a_restart_that_leaves_the_graph_off_forgets_it(fresh, monkeypatch, no_local_providers):
    save(fresh, "chat", MANUAL_LOG)
    settings.update({"resume_workflows": True})
    real_build = server.Runtime.build

    def build(self, graph):
        if any(n["id"] == "lg2" for n in graph["nodes"]):
            raise RuntimeError("the store is locked")
        return real_build(self, graph)

    monkeypatch.setattr(server.Runtime, "build", build)
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        # the new build fails after the old runtime stopped: nothing runs now
        assert power(client, "chat", "restart", MANUAL_LOG_DRAFT)["power"] == "off"
        assert resume.slugs() == []
    restart_server(monkeypatch)
    with local_client() as client:
        client.portal.call(server.launch_sequence)
        assert running() == []


def test_a_restart_refused_by_validation_keeps_the_old_graph_on_and_recorded(fresh):
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        assert power(client, "chat", "restart", NO_TRIGGER)["problems"]
        assert running() == ["chat"]
        assert dict(resume.recorded())["chat"] == migrate(copy.deepcopy(MANUAL_LOG))
        power(client, "chat", "off")


def test_no_resume_keeps_the_graphs_for_the_next_launch(fresh, monkeypatch, no_local_providers):
    save(fresh, "chat", MANUAL_LOG)
    settings.update({"resume_workflows": True})
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
    restart_server(monkeypatch)
    with local_client() as client:
        client.portal.call(server.launch_sequence, False)  # --no-resume
        assert running() == []
    restart_server(monkeypatch)
    with local_client() as client:
        client.portal.call(server.launch_sequence)
        assert running() == ["chat"]
        power(client, "chat", "off")


def test_a_graph_that_failed_to_resume_is_tried_again_at_the_next_launch(fresh, monkeypatch,
                                                                        no_local_providers):
    save(fresh, "chat", MANUAL_LOG)
    settings.update({"resume_workflows": True})
    left_on(monkeypatch, ("chat", MANUAL_LOG))
    real_build = server.Runtime.build

    def refuses(self, graph):
        raise RuntimeError("the store is locked")

    with local_client() as client:
        monkeypatch.setattr(server.Runtime, "build", refuses)
        client.portal.call(server.launch_sequence)
        assert running() == []
    restart_server(monkeypatch)
    monkeypatch.setattr(server.Runtime, "build", real_build)
    with local_client() as client:
        client.portal.call(server.launch_sequence)
        assert running() == ["chat"]
        power(client, "chat", "off")


# ---------------------------------------------------------------- saved or not
def test_a_never_saved_graph_turned_on_in_the_editor_comes_back(fresh, monkeypatch, no_local_providers):
    settings.update({"resume_workflows": True})
    with local_client() as client:
        # a New workflow tab, never saved, turned On over its socket
        with client.websocket_connect("/ws?slug=untitled-2") as ws:
            assert ws.receive_json()["kind"] == "status"
            ws.send_json({"action": "on", "graph": MANUAL_LOG_DRAFT})
            while (event := ws.receive_json()).get("kind") != "status" or event["power"] != "on":
                pass
    restart_server(monkeypatch)
    with local_client() as client:
        client.portal.call(server.launch_sequence)
        assert running() == ["untitled-2"]
        assert [n["id"] for n in server.HUBS["untitled-2"].graph["nodes"]] == ["m", "lg", "lg2"]
        power(client, "untitled-2", "off")


def test_deleting_a_graph_drops_it(fresh):
    save(fresh, "chat", MANUAL_LOG)
    with local_client() as client:
        power(client, "chat", "on", MANUAL_LOG)
        assert client.delete("/api/graphs/chat").json() == {"ok": True}
        assert resume.slugs() == []  # still running now, not at the next launch
        power(client, "chat", "off")


def test_a_graph_saved_while_on_and_then_removed_by_hand_is_dropped(fresh, monkeypatch,
                                                                    no_local_providers, caplog):
    settings.update({"resume_workflows": True})
    with local_client() as client:
        power(client, "draft", "on", MANUAL_LOG)  # never saved when it turned On
        assert client.put("/api/graphs/draft", json=MANUAL_LOG).json() == {"ok": True}
    (fresh / "draft.json").unlink()  # removed outside Boltjar
    restart_server(monkeypatch)
    with caplog.at_level(logging.INFO, logger="boltjar.graph"):
        with local_client() as client:
            client.portal.call(server.launch_sequence)
            assert running() == []
    assert resume.slugs() == []
    assert any(getattr(r, "tag", None) == "draft" and "gone" in r.detail for r in caplog.records)


def test_the_summary_never_says_nothing_was_on_when_a_graph_was_dropped(fresh, monkeypatch, caplog):
    left_on(monkeypatch, ("gone", MANUAL_LOG))  # saved then, deleted by hand since
    with caplog.at_level(logging.INFO, logger="boltjar.graph"):
        with local_client() as client:
            client.portal.call(server.resume_graphs)
    shown = [r.getMessage() for r in caplog.records if r.name == "boltjar.graph"]
    assert shown[-1] == "nothing left to resume"
    assert not any("no graph was On" in line for line in shown)


# ---------------------------------------------------------------- a person first
def _hold_the_first_validation(monkeypatch):
    """The first validation (the resume's) waits until `gate` is set; every
    later one passes at once. Returns (gate, entered), thread events."""
    import threading

    gate, entered = threading.Event(), threading.Event()
    calls = []

    async def confirm(refs):
        calls.append(refs)
        if len(calls) == 1:
            entered.set()
            while not gate.is_set():
                await asyncio.sleep(0.01)

    monkeypatch.setattr(model_discovery, "confirm_missing", confirm)
    return gate, entered


def test_a_persons_off_while_the_launch_resumes_the_graph_wins(fresh, monkeypatch):
    save(fresh, "chat", MANUAL_LOG)
    left_on(monkeypatch, ("chat", MANUAL_LOG))
    gate, entered = _hold_the_first_validation(monkeypatch)
    with local_client() as client:
        pending = client.portal.start_task_soon(server.resume_graphs)
        assert entered.wait(5)
        assert power(client, "chat", "off")["power"] == "off"  # the REST API, the MCP server
        gate.set()
        assert pending.result(10) == ([], [])
        assert client.get("/api/runtime/chat/state").json()["power"] == "off"
    assert resume.slugs() == []


def test_a_persons_on_while_the_launch_resumes_the_graph_wins(fresh, monkeypatch):
    save(fresh, "chat", MANUAL_LOG)
    left_on(monkeypatch, ("chat", MANUAL_LOG))
    gate, entered = _hold_the_first_validation(monkeypatch)
    with local_client() as client:
        pending = client.portal.start_task_soon(server.resume_graphs)
        assert entered.wait(5)
        assert power(client, "chat", "on", MANUAL_LOG_DRAFT)["power"] == "on"  # their draft
        gate.set()
        assert pending.result(10) == ([], [])
        assert [n["id"] for n in server.HUBS["chat"].graph["nodes"]] == ["m", "lg", "lg2"]
        assert dict(resume.recorded())["chat"] == migrate(copy.deepcopy(MANUAL_LOG_DRAFT))
        power(client, "chat", "off")


def test_a_persons_refused_on_before_the_launch_leaves_the_graph_off(fresh, monkeypatch,
                                                                     no_local_providers):
    save(fresh, "chat", MANUAL_LOG)
    settings.update({"resume_workflows": True})
    left_on(monkeypatch, ("chat", MANUAL_LOG))
    with local_client() as client:
        assert power(client, "chat", "on", NO_TRIGGER)["problems"]  # a draft that cannot run
        client.portal.call(server.launch_sequence)
        assert running() == []  # not the older JSON behind the person's back
    assert resume.slugs() == []


def _wait_until_unsubscribed(hub) -> None:
    import time

    for _ in range(500):
        if not hub.subscribers:
            return
        time.sleep(0.01)
    raise AssertionError("the editor socket never closed")


def test_an_editor_closing_while_the_launch_resumes_leaves_the_graph_reachable(fresh, monkeypatch):
    save(fresh, "chat", MANUAL_LOG)
    left_on(monkeypatch, ("chat", MANUAL_LOG))
    gate, entered = _hold_the_first_validation(monkeypatch)
    with local_client() as client:
        pending = client.portal.start_task_soon(server.resume_graphs)
        assert entered.wait(5)
        hub = server.HUBS["chat"]
        # an editor tab for chat opens and goes away (a reload, a closed tab)
        with client.websocket_connect("/ws?slug=chat") as ws:
            assert ws.receive_json()["kind"] == "status"
        _wait_until_unsubscribed(hub)
        gate.set()
        assert pending.result(10) == (["chat"], [])
        assert server.HUBS.get("chat") is hub
        assert client.get("/api/runtime/chat/state").json()["power"] == "on"
        power(client, "chat", "off")
        assert hub.runtime is None  # the Off reached the graph that runs


def test_an_editor_closing_while_the_api_turns_a_graph_on_leaves_it_reachable(fresh, monkeypatch):
    gate, entered = _hold_the_first_validation(monkeypatch)
    with local_client() as client:
        pending = client.portal.start_task_soon(
            server.runtime_power, "chat", {"action": "on", "graph": MANUAL_LOG})
        assert entered.wait(5)
        hub = server.HUBS["chat"]
        with client.websocket_connect("/ws?slug=chat") as ws:
            assert ws.receive_json()["kind"] == "status"
        _wait_until_unsubscribed(hub)
        gate.set()
        assert pending.result(10)["power"] == "on"
        assert server.HUBS.get("chat") is hub
        assert client.portal.call(server.shutdown_all) == 1  # the exit reaches it
