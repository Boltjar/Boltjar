"""Shipped examples are read-only graph sources behind the user's saved graphs.

GET falls back to examples/<slug>.json when there is no saved copy, the list is
the union of both folders, a PUT always writes a user copy (which then overrides
the example), and DELETE only ever removes user copies.

Isolation: the `dirs` fixture repoints GRAPHS_DIR, EXAMPLES_DIR and AUTOSAVE_DIR
at a tmp dir, so nothing here touches the project's real user/ or examples/. The
shipped examples themselves are only read, and the chat example runs against a
temp SQLite store with an offline LLM and a faked TTS vendor."""
import asyncio
import json
import pathlib

import httpx
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
    assert client.get("/api/graphs").json() == {"graphs": ["starter"]}
    assert not user.exists()
    client.put("/api/graphs/mine", json=_graph("mine"))
    client.put("/api/graphs/starter", json=_graph("starter", 3))
    assert client.get("/api/graphs").json() == {"graphs": ["mine", "starter"]}


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
    graph = json.loads((SHIPPED / "chat.json").read_text(encoding="utf-8"))
    for n in graph["nodes"]:
        if n["type"] == "core.ai.llm":
            n["config"]["model"] = "mock/echo"  # offline: no real model is called
    return graph


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
    """A fresh install has an empty database: running the chat example creates
    the chat_history table it declares, so the very first message runs without
    a node error and stores both sides of the turn. It runs on a bare Runtime,
    as `python -m boltjar` and tools/verify_chat.py do, not through the server."""
    store = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", store)
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")  # the TTS call is faked below
    vendor_http.reply = lambda r: httpx.Response(200, content=b"ID3mp3")
    graph = _chat_example()
    db_key = _chat_db_key(graph)

    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        rt.send_chat("chat", "hi")
        for _ in range(60):  # until the spoken reply reaches the audio preview
            await asyncio.sleep(0.05)
            if any(e["kind"] == "value" and e["node"] == "Audio Preview" for e in events):
                break
        await rt.stop()

    asyncio.run(drive())
    assert not [e for e in events if e["kind"] in ("node_error", "warning")], events
    rows = store.query(db_key, "SELECT sender, message FROM chat_history ORDER BY id")
    assert [r["sender"] for r in rows] == ["User", "Assistant"]
    assert rows[0]["message"] == "hi"
    assert str(vendor_http.last.url) == "https://api.x.ai/v1/tts"
