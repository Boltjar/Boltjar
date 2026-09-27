"""Tests for the FastAPI bridge: node definitions, websocket run, graph save/load."""
import asyncio

from local_client import local_client

import boltjar.server as server
from boltjar.server import Hub, HUBS, validate_graph

client = local_client()


def test_object_info_lists_core_nodes() -> None:
    info = client.get("/api/object_info").json()
    ids = {n["id"] for n in info["nodes"]}
    assert {"core.ai.llm", "core.trigger.interval", "core.logic.condition"} <= ids
    assert info["types"]["text"], "types should carry a color per pipe type"


def test_websocket_runs_a_graph() -> None:
    graph = {
        "nodes": [
            {"id": "m", "type": "core.trigger.manual"},
            {"id": "log", "type": "core.output.log", "config": {"label": "t"}},
        ],
        "edges": [{"src": "m", "src_port": "trigger", "dst": "log", "dst_port": "in"}],
    }
    with client.websocket_connect("/ws") as ws:
        # On connect the hub sends the current status (off) + the (empty) live_graph
        # set; then `on` streams the runtime's value/log events, a status:on and the
        # live_graph set. Drain a bounded number of frames until we've seen the run
        # events (frame-count-agnostic so protocol additions don't break the test).
        ws.send_json({"action": "on", "graph": graph})
        events = [ws.receive_json() for _ in range(8)]
        ws.send_json({"action": "off"})
    assert any(e.get("kind") == "value" for e in events)
    assert any(e.get("kind") == "log" for e in events)


def test_validate_flags_missing_trigger() -> None:
    graph = {"nodes": [{"id": "t", "type": "core.value.text", "config": {"text": "x"}}], "edges": []}
    problems = client.post("/api/validate", json=graph).json()["problems"]
    assert any(p["kind"] == "no-trigger" for p in problems)


def test_validate_skips_disabled_nodes() -> None:
    # a disabled node never runs, so its missing inputs must NOT block power-on.
    graph = {"nodes": [
        {"id": "iv", "type": "core.trigger.interval", "config": {}},
        {"id": "kv", "type": "core.kv", "config": {"operation": "get"}, "disabled": True},
    ], "edges": []}
    problems = client.post("/api/validate", json=graph).json()["problems"]
    assert problems == [], f"disabled node should not produce problems, got {problems}"


def _missing_secrets(graph: dict) -> list[dict]:
    return [p for p in validate_graph(graph) if p["kind"] == "missing-secret"]


def test_validate_names_a_secret_nobody_defined(monkeypatch) -> None:
    # a .env name other than a provider key does not resolve: the Webhook would
    # refuse every call, and HTTP Request would send the literal token.
    monkeypatch.setenv("HOOK_ONLY_IN_ENV", "from-env")
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    graph = {"nodes": [
        {"id": "hook", "type": "core.trigger.webhook",
         "config": {"secret": "{{secret.HOOK_ONLY_IN_ENV}}"}},
        {"id": "http", "type": "core.net.http", "config": {
            "headers": "Authorization: Bearer {{secret.XAI_API_KEY}}\nX-Other: {{secret.NO_SUCH_KEY}}",
            "body": "{{secret.NO_SUCH_KEY}}"}},
    ], "edges": [{"src": "hook", "src_port": "trigger", "dst": "http", "dst_port": "trigger"}]}
    assert _missing_secrets(graph) == [
        {"node": "hook", "kind": "missing-secret",
         "message": "secret HOOK_ONLY_IN_ENV is not defined: add it in Settings, Secrets"},
        {"node": "http", "kind": "missing-secret",
         "message": "secret NO_SUCH_KEY is not defined: add it in Settings, Secrets"},
    ]


def test_validate_ignores_a_secret_the_node_never_reads() -> None:
    graph = {"nodes": [
        {"id": "iv", "type": "core.trigger.interval", "config": {}},
        # the DB's sql field only runs for query and exec.
        {"id": "db", "type": "core.db", "config": {"operation": "insert", "sql": "{{secret.NO_SUCH_KEY}}"}},
        # promoted to an input that a wire feeds.
        {"id": "src", "type": "core.value.text", "config": {"text": "https://example.com"}},
        {"id": "http", "type": "core.net.http",
         "config": {"url": "{{secret.NO_SUCH_KEY}}", "promoted": ["url"]}},
        # a bypassed node never runs.
        {"id": "off", "type": "core.net.http", "config": {"body": "{{secret.NO_SUCH_KEY}}"},
         "disabled": True},
        # a field that takes no secrets keeps the token as plain text.
        {"id": "txt", "type": "core.value.text", "config": {"text": "{{secret.NO_SUCH_KEY}}"}},
    ], "edges": [{"src": "src", "src_port": "out", "dst": "http", "dst_port": "url"}]}
    assert _missing_secrets(graph) == []
    graph["nodes"][1]["config"]["operation"] = "query"
    assert [p["node"] for p in _missing_secrets(graph)] == ["db"]


def test_validate_flags_duplicate_wireless_channel() -> None:
    # a wireless channel may have only one Wireless In (the broadcast source).
    graph = {"nodes": [
        {"id": "iv", "type": "core.trigger.interval", "config": {}},
        {"id": "wa", "type": "core.flow.wireless_in", "config": {"channel": "1"}},
        {"id": "wb", "type": "core.flow.wireless_in", "config": {"channel": "1"}},
        {"id": "wc", "type": "core.flow.wireless_in", "config": {"channel": "2"}},
    ], "edges": []}
    problems = client.post("/api/validate", json=graph).json()["problems"]
    dups = [p for p in problems if p["kind"] == "duplicate-channel"]
    assert len(dups) == 1 and dups[0]["node"] == "wb", f"only the 2nd In on ch1 is flagged, got {problems}"


def test_graph_save_and_load(monkeypatch, tmp_path) -> None:
    # isolated: the Save (and its Auto-save snapshot) never touches the real user/.
    monkeypatch.setattr(server, "GRAPHS_DIR", tmp_path / "graphs")
    monkeypatch.setattr(server, "AUTOSAVE_DIR", tmp_path / "autosave")
    graph = {"name": "rt_probe", "nodes": [], "edges": []}
    assert client.put("/api/graphs/rt_probe", json=graph).json()["ok"]
    assert client.get("/api/graphs/rt_probe").json()["name"] == "rt_probe"
    assert (tmp_path / "graphs" / "rt_probe.json").exists()


def test_db_endpoints_reject_empty_key() -> None:
    # An empty/whitespace db key must be refused at the API boundary with a clean
    # 400 (via _need inside _db), never reaching SQL. %20 decodes to a space.
    assert client.get("/api/db/%20/schema").status_code == 400
    assert client.post("/api/db/%20/table", json={"table": "t"}).status_code == 400
    assert client.patch("/api/db/%20/table", json={"old": "a", "new": "b"}).status_code == 400
    assert client.request("DELETE", "/api/db/%20/table", json={"table": "t"}).status_code == 400
    assert client.post("/api/db/%20/column", json={"table": "t", "name": "c"}).status_code == 400
    assert client.patch("/api/db/%20/column", json={"table": "t", "old": "a", "new": "b"}).status_code == 400
    assert client.request("DELETE", "/api/db/%20/column", json={"table": "t", "name": "c"}).status_code == 400


def test_runtime_survives_subscriber_disconnect() -> None:
    """Power persistence: a subscriber leaving must NOT stop the live runtime,
    and a freshly-registered subscriber must receive the status + replayed values."""

    async def drive() -> None:
        hub = Hub()
        graph = {
            "nodes": [
                {"id": "m", "type": "core.trigger.manual"},
                {"id": "log", "type": "core.output.log", "config": {"label": "t"}},
            ],
            "edges": [{"src": "m", "src_port": "trigger", "dst": "log", "dst_port": "in"}],
        }

        # First subscriber turns the graph on.
        sub_a: asyncio.Queue = asyncio.Queue()
        hub.subscribers.add(sub_a)
        problems = await hub.power_on(graph)
        assert problems is None
        assert hub.runtime is not None and hub.runtime.alive

        # Let the manual trigger fire so at least one `value` lands in `latest`.
        await asyncio.sleep(0.05)
        assert hub.latest, "the runtime should have cached at least one live value"

        # The subscriber disconnects (browser refresh / tab close).
        hub.subscribers.discard(sub_a)

        # The runtime is untouched: still the same object, still alive.
        assert hub.runtime is not None and hub.runtime.alive

        # A fresh subscriber connects and gets status + a replay of every cached value,
        # exactly as the /ws handler does on connect.
        sub_b: asyncio.Queue = asyncio.Queue()
        hub.subscribers.add(sub_b)
        sub_b.put_nowait({"kind": "status", "power": "on" if hub.runtime else "off"})
        for event in list(hub.latest.values()):
            sub_b.put_nowait(event)

        drained = []
        while not sub_b.empty():
            drained.append(sub_b.get_nowait())

        assert drained[0] == {"kind": "status", "power": "on"}
        assert any(e.get("kind") == "value" for e in drained[1:]), "replayed values expected"

        # power_off stops the runtime and clears the replay cache.
        await hub.power_off()
        assert hub.runtime is None
        assert hub.latest == {}

    asyncio.run(drive())


# ============================================================ edge validation
# Every edge is checked: the src_port is a real output, the dst_port a real input
# (declared OR a legal dynamic port), and the wire's types are compatible. These
# guard the two proven gaps: an edge to a non-existent dst_port, and a stale
# src_port left after a port rename. The permissive-dynamic-port logic must never
# reject a legal growable / promoted / model-reshaped wire.


def test_validate_edges_accepts_valid_graph() -> None:
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
        {"id": "tpl", "type": "core.data.template", "config": {"template": "{msg}"}},
    ], "edges": [
        {"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "msg"},
    ]}
    assert validate_graph(graph) == []


def test_validate_edges_flags_unknown_target_port() -> None:
    # an edge to a port a node without any dynamic input surface does not have
    # (the rename-bug ghost `preview.out -> tts.Username` is exactly this shape).
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "tts", "type": "core.ai.tts", "config": {}},
    ], "edges": [
        {"src": "m", "src_port": "trigger", "dst": "tts", "dst_port": "nope"},
    ]}
    bad = [p for p in validate_graph(graph) if p["kind"] == "bad-target-port"]
    assert len(bad) == 1 and bad[0]["node"] == "tts"
    assert "m.trigger -> tts.nope" in bad[0]["message"]


def test_validate_accepts_a_wire_into_a_template_tag_the_text_no_longer_uses() -> None:
    # the editor keeps drawing a wired tag socket after its `{tag}` is deleted from
    # the text, and the value is simply unused, so it must not block power-on.
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
        {"id": "tpl", "type": "core.data.template", "config": {"template": "{msg}"}},
    ], "edges": [
        {"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "gone"},
    ]}
    assert not [p for p in validate_graph(graph) if p["kind"] == "bad-target-port"]


def test_validate_edges_flags_stale_source_port() -> None:
    # a draft saved before a port rename keeps an edge whose src_port no longer
    # exists (core.value.text emits 'out', never 'value').
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
        {"id": "log", "type": "core.output.log", "config": {"label": "l"}},
    ], "edges": [
        {"src": "txt", "src_port": "value", "dst": "log", "dst_port": "in"},
    ]}
    bad = [p for p in validate_graph(graph) if p["kind"] == "bad-source-port"]
    assert len(bad) == 1 and bad[0]["node"] == "txt"


def test_validate_edges_flags_type_mismatch() -> None:
    # an int output cannot feed a text input (int subtypes number, never text).
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "n", "type": "core.value.integer", "config": {"value": 3}},
        {"id": "tts", "type": "core.ai.tts", "config": {}},
    ], "edges": [
        {"src": "n", "src_port": "out", "dst": "tts", "dst_port": "text"},
    ]}
    mm = [p for p in validate_graph(graph) if p["kind"] == "type-mismatch"]
    assert len(mm) == 1 and mm[0]["node"] == "tts"


def test_validate_edges_accepts_growable_socket() -> None:
    # a List node's growable `item` base mints a socket per wire (item_0, item1…);
    # any non-declared name is a legal minted socket, never flagged.
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "a", "type": "core.value.text", "config": {"text": "x"}},
        {"id": "lst", "type": "core.data.list"},
    ], "edges": [
        {"src": "a", "src_port": "out", "dst": "lst", "dst_port": "item_0"},
    ]}
    probs = validate_graph(graph)
    assert not [p for p in probs if p["kind"] in ("bad-target-port", "type-mismatch")]


def test_validate_edges_accepts_promoted_widget_port() -> None:
    # a widget promoted to a typed input (config.promoted=['amount']) becomes a
    # real input port; without the promote the same edge is a dead wire.
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "f", "type": "core.value.float", "config": {"value": 2.0}},
        {"id": "w", "type": "core.flow.wait", "config": {"promoted": ["amount"]}},
    ], "edges": [
        {"src": "m", "src_port": "trigger", "dst": "w", "dst_port": "trigger"},
        {"src": "f", "src_port": "out", "dst": "w", "dst_port": "amount"},
    ]}
    assert not [p for p in validate_graph(graph)
                if p["kind"] in ("bad-target-port", "type-mismatch")]
    # the promote is load-bearing: drop it and the wire becomes invalid.
    graph["nodes"][2]["config"] = {}
    assert any(p["kind"] == "bad-target-port" and p["node"] == "w"
               for p in validate_graph(graph))


def test_validate_edges_accepts_llm_reshaped_ports() -> None:
    # the LLM reshapes its ports from the selected model's manifest: a model with
    # an image input grows an `image` port, a promoted param grows a typed port,
    # and a tool-calling model grows a `tool_call` output. None may be flagged.
    model = "ollama/gemma4:12b"  # inputs text+image, tools, temperature param
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "img", "type": "core.value.image", "config": {}},
        {"id": "t", "type": "core.value.float", "config": {"value": 0.7}},
        {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
        {"id": "llm", "type": "core.ai.llm",
         "config": {"model": model, "promoted": ["temperature"]}},
        {"id": "log", "type": "core.output.log", "config": {"label": "l"}},
    ], "edges": [
        {"src": "m", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
        {"src": "txt", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
        {"src": "img", "src_port": "out", "dst": "llm", "dst_port": "image"},
        {"src": "t", "src_port": "out", "dst": "llm", "dst_port": "temperature"},
        {"src": "llm", "src_port": "tool_call", "dst": "log", "dst_port": "in"},
    ]}
    offenders = [p for p in validate_graph(graph)
                 if p["kind"] in ("bad-source-port", "bad-target-port", "type-mismatch")]
    assert offenders == [], offenders


def test_validate_edges_skip_disabled_nodes() -> None:
    # an edge to a disabled node must not raise an edge problem (the node may be
    # bypassed/flattened at runtime), matching the node-level disabled skip.
    graph = {"nodes": [
        {"id": "m", "type": "core.trigger.manual"},
        {"id": "tpl", "type": "core.data.template",
         "config": {"template": "{msg}"}, "disabled": True},
    ], "edges": [
        {"src": "m", "src_port": "trigger", "dst": "tpl", "dst_port": "trigger"},
    ]}
    assert validate_graph(graph) == []
