"""The `carry` event: which drawn wires a value actually travelled.

The editor lights a wire only when it carried something. A push carries along
the wires it fires (a trigger input); a data wire carries when the node on its
far end reads it. So when one DB node pulls a Database's `db`, only the wire
into that DB node carries, never the Database's wires into DB nodes that did
not run. Each wire is [src, src_port, dst, dst_port] as drawn, so a value that
crosses a Router or a Wireless pair names every drawn wire on its way.
"""
import asyncio

from boltjar.runtime import Runtime, flatten_graph
from boltjar.sqlite_store import SqliteStore
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)


def _carried(events: list[dict]) -> list[list[str]]:
    return [w for e in events if e["kind"] == "carry" for w in e["wires"]]


def _fire_and_collect(graph: dict, node: str, settle: float = 0.2) -> list[dict]:
    """Build and run `graph`, drop what its start produced, fire the Manual
    trigger `node` once and return the events that fire caused."""
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build(graph)

    async def drive() -> None:
        await rt.run()
        await asyncio.sleep(settle)
        events.clear()
        rt.fire_manual(node)
        await asyncio.sleep(settle)
        await rt.stop()

    asyncio.run(drive())
    return events


def _database_graph() -> dict:
    """A Database feeding three DB nodes; each DB node has its own Manual, so
    firing one runs one DB node and leaves the other two idle."""
    nodes = [{"id": "database", "type": "core.store.database", "config": {}}]
    edges = []
    for name in ("reader", "idle_a", "idle_b"):
        nodes.append({"id": name, "type": "core.db",
                      "config": {"operation": "query", "sql": "SELECT 1 AS one"}})
        nodes.append({"id": f"go_{name}", "type": "core.trigger.manual", "config": {}})
        edges.append({"src": "database", "src_port": "db", "dst": name, "dst_port": "db"})
        edges.append({"src": f"go_{name}", "src_port": "trigger", "dst": name, "dst_port": "trigger"})
    return {"nodes": nodes, "edges": edges}


def test_a_pull_carries_only_to_the_node_that_pulled(tmp_path, monkeypatch) -> None:
    from boltjar import server
    monkeypatch.setattr(server, "STORE", SqliteStore(root=tmp_path))
    events = _fire_and_collect(_database_graph(), "go_reader")
    carried = _carried(events)
    assert ["go_reader", "trigger", "reader", "trigger"] in carried, "the push fires the reader"
    assert ["database", "db", "reader", "db"] in carried, "the reader's read carries its db wire"
    assert not [w for w in carried if w[2] in ("idle_a", "idle_b")], \
        "the Database's wires into DB nodes that did not run carry nothing"
    # the Database still reports its value once, as before
    assert any(e["kind"] == "value" and e["node"] == "database" for e in events)


def test_a_push_carries_only_along_the_wires_it_fires() -> None:
    # Manual pushes its trigger into a Template's trigger input (it fires) and
    # into a Log (it fires). A Text node's out feeds two Templates, but only the
    # fired one reads it.
    graph = {
        "nodes": [
            {"id": "go", "type": "core.trigger.manual", "config": {}},
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "{x}"}},
            {"id": "other", "type": "core.data.template", "config": {"template": "{x}"}},
            {"id": "log", "type": "core.output.log", "config": {}},
        ],
        "edges": [
            {"src": "go", "src_port": "trigger", "dst": "tpl", "dst_port": "trigger"},
            {"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "x"},
            {"src": "txt", "src_port": "out", "dst": "other", "dst_port": "x"},
            {"src": "tpl", "src_port": "out", "dst": "log", "dst_port": "in"},
        ],
    }
    carried = _carried(_fire_and_collect(graph, "go"))
    assert ["go", "trigger", "tpl", "trigger"] in carried
    assert ["txt", "out", "tpl", "x"] in carried, "the fired Template read its tag"
    assert ["tpl", "out", "log", "in"] in carried, "the Template's result fires the Log"
    assert ["txt", "out", "other", "x"] not in carried, "a Template nobody fired read nothing"


def test_a_carry_through_a_router_names_every_drawn_wire() -> None:
    graph = {
        "nodes": [
            {"id": "go", "type": "core.trigger.manual", "config": {}},
            {"id": "r", "type": "core.flow.router", "config": {}},
            {"id": "log", "type": "core.output.log", "config": {}},
        ],
        "edges": [
            {"src": "go", "src_port": "trigger", "dst": "r", "dst_port": "in"},
            {"src": "r", "src_port": "out", "dst": "log", "dst_port": "in"},
        ],
    }
    events = _fire_and_collect(graph, "go")
    carry = [e["wires"] for e in events if e["kind"] == "carry"]
    assert carry == [[["go", "trigger", "r", "in"], ["r", "out", "log", "in"]]], \
        "one carry, the source's wire into the Router first"


def test_flatten_paths_follow_a_wireless_pair() -> None:
    flat = flatten_graph({
        "nodes": [
            {"id": "iv", "type": "core.trigger.interval", "config": {"seconds": 1}},
            {"id": "win", "type": "core.flow.wireless_in", "config": {"channel": "3"}},
            {"id": "wout", "type": "core.flow.wireless_out", "config": {"channel": "3"}},
            {"id": "log", "type": "core.output.log", "config": {}},
        ],
        "edges": [
            {"src": "iv", "src_port": "trigger", "dst": "win", "dst_port": "iv"},
            {"src": "wout", "src_port": "iv", "dst": "log", "dst_port": "in"},
        ],
    })
    assert flat.live == [("iv", "trigger", "log", "in")]
    assert flat.paths[("log", "in")] == [("iv", "trigger", "win", "iv"), ("wout", "iv", "log", "in")]


def test_no_carry_for_a_graph_that_does_nothing() -> None:
    # a Text wired into a Template with nothing firing: no wire carries
    events: list[dict] = []
    rt = Runtime(observer=events.append)
    rt.build({
        "nodes": [
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": "{x}"}},
        ],
        "edges": [{"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "x"}],
    })

    async def drive() -> None:
        await rt.run()
        await asyncio.sleep(0.1)
        await rt.stop()

    asyncio.run(drive())
    assert _carried(events) == []
