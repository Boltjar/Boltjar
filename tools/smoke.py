"""Smoke test: node definitions, validation, and a websocket On run of the demo."""
import json
import pathlib

from fastapi.testclient import TestClient

from boltjar.server import app

client = TestClient(app)

info = client.get("/api/object_info").json()
print("nodes:", len(info["nodes"]), "| types:", len(info["types"]))
print("ids:", ", ".join(sorted(n["id"] for n in info["nodes"])))

graph = json.loads(pathlib.Path("examples/demo.json").read_text(encoding="utf-8"))
print("validate:", client.post("/api/validate", json=graph).json()["problems"] or "ok")

events = []
with client.websocket_connect("/ws") as ws:
    ws.send_json({"action": "on", "graph": graph})
    for _ in range(12):
        e = ws.receive_json()
        events.append(e)
        if e.get("kind") == "log":
            break
    ws.send_json({"action": "off"})

for e in events:
    print("  ", e.get("kind"), e.get("node", ""), e.get("port") or e.get("message") or e.get("power") or "")
print("OK" if any(e.get("kind") == "log" for e in events) else "FAIL")
