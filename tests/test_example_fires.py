"""One send through a shipped example runs each node once per turn.

Each example is turned On the way the editor does it (a Hub validates the
graph, then runs it), driven by its trigger, and every live event is counted.
One chat send means one LLM run, one reply, one row per side of the chat
history and one TTS call; one Manual fire means one LLM run and one log line.
A Preview is fired by its value and by its trigger, so it runs twice a turn and
still passes on exactly one value and one trigger: the chat example once spoke
every reply twice because a Preview relayed both.

Offline: the LLM runs the mock, the TTS vendor is faked at the HTTP boundary
and the chat history lives in a temp SQLite store."""
import asyncio
import collections
import json
import pathlib

import httpx
import pytest

import boltjar.server as server
from boltjar.sqlite_store import SqliteStore

SHIPPED = pathlib.Path(__file__).resolve().parent.parent / "examples"

# what one chat send runs in examples/chat.json: node -> fires. A Preview fires
# on its value and on its trigger, every other node once.
CHAT_FIRES = {
    "Chat History": 1, "Chat Append (User)": 1, "Prompt Template": 1, "Context": 2,
    "LLM": 1, "Response Preview": 2, "TTS": 1, "Audio Preview": 2,
    "Chat Append (Assistant)": 1,
}
# the console lines one chat send writes: each Preview shows its value once.
CHAT_LOGS = {"Context": 1, "Response Preview": 1, "Audio Preview": 1}
CHAT_PREVIEWS = sorted(CHAT_LOGS)


class Watch:
    """A Hub subscriber that keeps every event the running graph broadcasts."""

    def __init__(self, hub: server.Hub) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.events: list[dict] = []
        hub.subscribers.add(self.queue)

    def drain(self) -> list[dict]:
        while not self.queue.empty():
            self.events.append(self.queue.get_nowait())
        return self.events

    async def until(self, done, timeout: float = 5.0, settle: float = 0.3) -> None:
        """Wait until `done(events)` holds, then a little longer, so a second
        fire that would arrive late is counted too."""
        for _ in range(int(timeout / 0.02)):
            if done(self.drain()):
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(settle)
        self.drain()

    def fires(self) -> dict:
        return dict(collections.Counter(
            e["node"] for e in self.events
            if e["kind"] == "node_status" and e["status"] == "running"))

    def emits(self, node: str, port: str) -> list:
        return [e["value"] for e in self.events
                if e["kind"] == "value" and e["node"] == node and e["port"] == port]

    def logs(self) -> dict:
        return dict(collections.Counter(e["node"] for e in self.events if e["kind"] == "log"))

    def log_lines(self, node: str) -> list[str]:
        return [e["message"] for e in self.events if e["kind"] == "log" and e["node"] == node]

    def trouble(self) -> list[dict]:
        return [e for e in self.events if e["kind"] in ("node_error", "warning", "error")]


def _example(name: str) -> dict:
    return json.loads((SHIPPED / f"{name}.json").read_text(encoding="utf-8"))


def _times(counts: dict, n: int) -> dict:
    return {k: v * n for k, v in counts.items()}


def test_every_example_has_a_count_test():
    assert sorted(p.stem for p in SHIPPED.glob("*.json")) == ["chat", "demo"], \
        "a new example needs its own fire counts here"


def test_chat_one_send_runs_each_node_once(tmp_path, monkeypatch, vendor_http):
    store = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", store)
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")  # the TTS call is faked below
    vendor_http.reply = lambda r: httpx.Response(200, content=b"ID3mp3")
    graph = _example("chat")
    for n in graph["nodes"]:
        if n["type"] == "core.ai.llm":
            n["config"]["model"] = "mock/echo"  # offline: no real model is called
    db_key = next(n["config"]["db_key"] for n in graph["nodes"]
                  if n["type"] == "core.store.database")
    triggers = sorted({(e["src"], e["src_port"]) for e in graph["edges"]
                       if e["src_port"] == "trigger" and e["src"] != "chat"})
    hub = server.Hub()
    watch = Watch(hub)
    turns: list[dict] = []

    async def drive() -> None:
        assert await hub.power_on(graph) is None, "the chat example turns On"
        for n, text in enumerate(("hi", "again"), start=1):
            hub.send_chat("chat", text)
            await watch.until(lambda ev, n=n: (
                len([e for e in ev if e["kind"] == "value" and e["node"] == "Audio Preview"
                     and e["port"] == "trigger"]) >= n
                and len([e for e in ev if e["kind"] == "value"
                         and e["node"] == "Chat Append (Assistant)" and e["port"] == "trigger"]) >= n))
            turns.append({
                "fires": watch.fires(),
                "logs": watch.logs(),
                "replies": len(watch.emits("LLM", "response")),
                "passed": {pv: len(watch.emits(pv, "out")) for pv in CHAT_PREVIEWS},
                "relayed": {f"{src}.{port}": len(watch.emits(src, port)) for src, port in triggers},
                "rows": [r["sender"] for r in store.query(
                    db_key, "SELECT sender FROM chat_history ORDER BY id")],
                "tts": len(vendor_http.requests),
            })
        await hub.power_off()

    asyncio.run(drive())
    assert not watch.trouble(), watch.trouble()
    for n, turn in enumerate(turns, start=1):
        assert turn["fires"] == _times(CHAT_FIRES, n), f"after send {n}"
        assert turn["logs"] == _times(CHAT_LOGS, n), f"after send {n}"
        assert turn["replies"] == n, "one LLM reply per send"
        assert turn["passed"] == {pv: n for pv in CHAT_PREVIEWS}, "each Preview passes one value per send"
        assert turn["relayed"] == {k: n for k in turn["relayed"]}, \
            f"every trigger in the graph fires once per send, after send {n}"
        assert turn["rows"] == ["User", "Assistant"] * n, "one row per side per send"
        assert turn["tts"] == n, "one TTS call per send"
    assert [str(r.url) for r in vendor_http.requests] == ["https://api.x.ai/v1/tts"] * 2


@pytest.mark.parametrize("fires", [1, 3])
def test_demo_one_fire_runs_the_llm_once(fires):
    graph = _example("demo")
    hub = server.Hub()
    watch = Watch(hub)

    async def drive() -> None:
        assert await hub.power_on(graph) is None, "the demo turns On"
        # the Manual fires once when the graph turns On, then once per press.
        await watch.until(lambda ev: len(watch.log_lines("Log")) >= 1)
        for n in range(2, fires + 1):
            hub.fire_manual("Fire")
            await watch.until(lambda ev, n=n: len(watch.log_lines("Log")) >= n)
        await hub.power_off()

    asyncio.run(drive())
    assert not watch.trouble(), watch.trouble()
    assert watch.fires() == {"Prompt": fires, "LLM": fires, "Log": fires}, \
        "the Template, then the LLM, then the Log: each once per fire"
    assert len(watch.emits("LLM", "response")) == fires, "one reply per fire"
    lines = watch.log_lines("Log")
    assert len(lines) == fires and len(set(lines)) == 1, "one log line per fire"
    assert lines[0].startswith("assistant: ") and "Hi there, how are you today?" in lines[0]
    assert watch.emits("Prompt", "out") == [
        "You are a friendly, concise assistant.\n\nUser: Hi there, how are you today?"] * fires, \
        "the LLM reads the prompt the Template assembled, once per fire"


def test_demo_with_its_template_bypassed_still_fires_the_llm():
    # the Template sits on the trigger path (Manual -> Template -> LLM), so a
    # disabled one passes its trigger through: the LLM runs once per fire, with
    # no prompt, instead of the graph turning On with nothing ever running.
    graph = _example("demo")
    next(n for n in graph["nodes"] if n["id"] == "Prompt")["disabled"] = True
    hub = server.Hub()
    watch = Watch(hub)

    async def drive() -> None:
        assert await hub.power_on(graph) is None, "a bypassed Template keeps the demo powerable"
        await watch.until(lambda ev: len(watch.log_lines("Log")) >= 1)
        hub.fire_manual("Fire")
        await watch.until(lambda ev: len(watch.log_lines("Log")) >= 2)
        await hub.power_off()

    asyncio.run(drive())
    assert not watch.trouble(), watch.trouble()
    assert watch.fires() == {"LLM": 2, "Log": 2}, "the LLM and the Log, once per fire"
    assert watch.log_lines("Log") == ["assistant: [mock] "] * 2, "an empty prompt"
