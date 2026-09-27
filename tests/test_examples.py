"""Shipped examples are read-only graph sources behind the user's saved graphs.

GET falls back to examples/<slug>.json when there is no saved copy, the list is
the union of both folders, a PUT always writes a user copy (which then overrides
the example), and DELETE only ever removes user copies.

Isolation: the `dirs` fixture repoints GRAPHS_DIR, EXAMPLES_DIR and AUTOSAVE_DIR
at a tmp dir, so nothing here touches the project's real user/ or examples/. The
shipped examples themselves are only read, and the chat example runs against a
temp SQLite store. The chat example picks no model: its LLM runs the offline
mock and its TTS ships bypassed, so it turns On with nothing connected."""
import asyncio
import json
import pathlib
import time

import pytest
from local_client import local_client

import boltjar.server as server
from boltjar.runtime import Runtime
from boltjar.sqlite_store import SqliteStore

client = local_client()

SHIPPED = pathlib.Path(__file__).resolve().parent.parent / "examples"


def _graph(name: str, triggers: int = 1) -> dict:
    nodes = [{"id": f"m{i}", "type": "core.trigger.manual"} for i in range(triggers)]
    return {"name": name, "nodes": nodes, "edges": []}


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    user = tmp_path / "user" / "graphs"
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "starter.json").write_text(json.dumps(_graph("starter")), encoding="utf-8")
    monkeypatch.setattr(server, "GRAPHS_DIR", user)
    monkeypatch.setattr(server, "EXAMPLES_DIR", examples)
    monkeypatch.setattr(server, "AUTOSAVE_DIR", tmp_path / "user" / "autosave")
    return user, examples


def test_example_loads_when_there_is_no_saved_copy(dirs):
    assert client.get("/api/graphs/starter").json()["name"] == "starter"
    assert client.get("/api/graphs/nope").status_code == 404


def test_list_is_the_union_of_saved_graphs_and_examples(dirs):
    user, _ = dirs
    # a fresh install has no user/graphs yet: listing works and creates nothing.
    assert client.get("/api/graphs").json() == {"graphs": ["starter"], "files": []}
    assert not user.exists()
    client.put("/api/graphs/mine", json=_graph("mine"))
    client.put("/api/graphs/starter", json=_graph("starter", 3))
    assert client.get("/api/graphs").json() == {"graphs": ["mine", "starter"], "files": []}


def test_save_writes_a_user_copy_that_overrides_the_example(dirs):
    user, examples = dirs
    assert client.put("/api/graphs/starter", json=_graph("starter", 3)).json()["ok"]
    assert (user / "starter.json").exists()
    example = json.loads((examples / "starter.json").read_text(encoding="utf-8"))
    assert len(example["nodes"]) == 1, "the example itself is never written"
    assert len(client.get("/api/graphs/starter").json()["nodes"]) == 3


def test_deleting_the_user_copy_brings_the_example_back(dirs):
    user, examples = dirs
    client.put("/api/graphs/starter", json=_graph("starter", 3))
    assert client.delete("/api/graphs/starter").json() == {"ok": True}
    assert not (user / "starter.json").exists()
    assert (examples / "starter.json").exists()
    assert len(client.get("/api/graphs/starter").json()["nodes"]) == 1


def test_deleting_an_example_only_slug_is_refused(dirs):
    _, examples = dirs
    r = client.delete("/api/graphs/starter")
    assert r.status_code == 409 and "example" in r.json()["error"]
    assert (examples / "starter.json").exists()
    # a slug that exists nowhere is still a no-op success (already gone).
    assert client.delete("/api/graphs/nope").json() == {"ok": True}


def test_power_on_by_slug_loads_the_example(dirs):
    with local_client() as c:
        try:
            r = c.post("/api/runtime/starter/power", json={"action": "on"})
            assert r.status_code == 200 and r.json()["power"] == "on"
        finally:
            c.post("/api/runtime/starter/power", json={"action": "off"})
            server.HUBS.pop("starter", None)


# ---------------------------------------------------------------- the shipped files
@pytest.mark.parametrize("path", sorted(SHIPPED.glob("*.json")), ids=lambda p: p.stem)
def test_shipped_example_validates_under_its_own_slug(path):
    graph = json.loads(path.read_text(encoding="utf-8"))
    assert graph["name"] == path.stem
    assert server.validate_graph(graph) == []


def _chat_example() -> dict:
    return json.loads((SHIPPED / "chat.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(SHIPPED.glob("*.json")), ids=lambda p: p.stem)
def test_no_shipped_example_picks_a_model(path):
    # a model can cost money: an example never picks one for the person.
    graph = json.loads(path.read_text(encoding="utf-8"))
    picked = {n["id"]: w.name for n in graph["nodes"]
              for w in server.NODE_REGISTRY[n["type"]].widgets
              if w.kind == "model" and (n.get("config") or {}).get(w.name)}
    assert picked == {}


def test_the_chat_example_ships_its_voice_bypassed():
    # its TTS needs a voice model somebody picks, so it ships disabled with the
    # Audio Preview after it, and the chat runs text-only until both are enabled.
    graph = _chat_example()
    by_type = {n["id"]: n for n in graph["nodes"]}
    assert by_type["TTS"]["type"] == "core.ai.tts" and by_type["TTS"].get("disabled") is True
    assert by_type["Audio Preview"].get("disabled") is True
    assert [n["id"] for n in graph["nodes"] if n.get("disabled")] == ["TTS", "Audio Preview"]


def _chat_db_key(graph: dict) -> str:
    return next(n["config"]["db_key"] for n in graph["nodes"]
                if n["type"] == "core.store.database")


def test_chat_example_declares_its_table_instead_of_creating_it():
    """The chat table is part of the graph (the Database node's schema), not a
    CREATE TABLE node that has to fire before Chat Append can see the table."""
    graph = json.loads((SHIPPED / "chat.json").read_text(encoding="utf-8"))
    assert not [n for n in graph["nodes"] if "CREATE TABLE" in str(n.get("config", {}).get("sql", ""))]
    assert "Chat Setup" not in {n["id"] for n in graph["nodes"]}
    db = next(n for n in graph["nodes"] if n["type"] == "core.store.database")
    tables = {t["name"]: [c["name"] for c in t["columns"]] for t in db["config"]["schema"]}
    assert tables == {"chat_history": ["id", "time", "sender", "message"]}
    # the chat trigger still reaches Chat History first, as it did through setup.
    assert {"src": "chat", "src_port": "trigger", "dst": "Chat History",
            "dst_port": "trigger"} in graph["edges"]


def test_opening_the_chat_example_gives_chat_append_its_table(tmp_path, monkeypatch):
    """Opening the example in the editor makes chat_history exist, so Chat
    Append's table list has it before any node fires."""
    store = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", store)
    graph = _chat_example()
    key = _chat_db_key(graph)
    assert client.get(f"/api/db/{key}/schema").json()["schema"] == []
    r = client.post("/api/stores/ensure", json=graph)
    assert r.status_code == 200
    assert r.json() == {"stores": [{"node": "database", "key": key, "created": ["chat_history"],
                                    "added": []}], "warnings": []}
    tables = client.get(f"/api/db/{key}/schema").json()["schema"]
    assert [t["name"] for t in tables] == ["chat_history"]


def test_chat_example_first_turn_on_a_fresh_install(tmp_path, monkeypatch, vendor_http):
    """A fresh install has an empty database and no provider: running the chat
    example creates the chat_history table it declares, so the very first
    message runs without a node error, stores both sides of the turn and calls
    no provider. It runs on a bare Runtime, as `python -m boltjar` and
    tools/verify_chat.py do, not through the server."""
    store = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", store)
    graph = _chat_example()
    db_key = _chat_db_key(graph)

    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        rt.send_chat("chat", "hi")
        for _ in range(60):  # until the reply is stored
            await asyncio.sleep(0.05)
            if any(e["kind"] == "value" and e["node"] == "Chat Append (Assistant)"
                   and e["port"] == "trigger" for e in events):
                break
        await rt.stop()

    asyncio.run(drive())
    assert not [e for e in events if e["kind"] in ("node_error", "warning")], events
    rows = store.query(db_key, "SELECT sender, message FROM chat_history ORDER BY id")
    assert [r["sender"] for r in rows] == ["User", "Assistant"]
    assert rows[0]["message"] == "hi"
    assert rows[1]["message"].startswith("[mock]"), "the LLM with no model runs the mock"
    assert vendor_http.requests == [], "nothing picked, so no provider is called"


def test_chat_example_stores_the_message_before_the_reply(tmp_path, monkeypatch, vendor_http):
    """The reply is written after the message it answers, however slow the
    message's own write is, so the history the next turn reads is in order."""

    class SlowUserWrites(SqliteStore):
        def execute(self, key, sql, params=None):
            if (params or {}).get("sender") == "User":
                time.sleep(0.3)  # the message's write is slow; the reply's is not
            return super().execute(key, sql, params)

    store = SlowUserWrites(root=tmp_path)
    monkeypatch.setattr(server, "STORE", store)
    graph = _chat_example()
    db_key = next(n["config"]["db_key"] for n in graph["nodes"]
                  if n["type"] == "core.store.database")

    hub = server.Hub()
    events: asyncio.Queue = asyncio.Queue()
    hub.subscribers.add(events)
    seen: list[dict] = []

    async def drive() -> None:
        assert await hub.power_on(graph) is None
        hub.send_chat("chat", "hi")
        for _ in range(60):  # until the reply is stored
            await asyncio.sleep(0.05)
            while not events.empty():
                seen.append(events.get_nowait())
            if any(e["kind"] == "value" and e["node"] == "Chat Append (Assistant)"
                   and e["port"] == "trigger" for e in seen):
                break
        await hub.power_off()

    asyncio.run(drive())
    assert not [e for e in seen if e["kind"] in ("node_error", "error")], seen
    rows = store.query(db_key, "SELECT sender, message FROM chat_history ORDER BY id")
    assert [r["sender"] for r in rows] == ["User", "Assistant"]
    assert rows[0]["message"] == "hi"
