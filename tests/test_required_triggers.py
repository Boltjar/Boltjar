"""Every trigger input must be wired: a trigger fires its node.

Validation names each unwired trigger ("required trigger 'x' is not connected",
the missing-input kind every unwired required input has), so the graph cannot
turn On. A growable trigger (Sync's `in`, a Queue's `in`) needs a wire on at
least one of its sockets. A bypassed node is skipped, and a graph with no
trigger node is still refused. The SDK refuses a trigger declared optional, so
no node can offer one."""
import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.sdk import NODE_REGISTRY, Kind, Port, node
from boltjar.server import validate_graph

# the triggers that used to be declared optional: each is required like any other.
ONCE_OPTIONAL = {
    ("core.data.template", "trigger"), ("core.output.preview", "trigger"),
    ("core.state.meter", "nudge"), ("core.state.meter", "set"),
    ("core.flow.for_each", "loop"), ("core.flow.sync", "in"),
    ("core.flow.queue", "in"), ("core.flow.queue", "ack"),
    ("core.output.chat", "user_trigger"), ("core.output.chat", "reply_trigger"),
}

TRIGGERS = sorted((spec.id, p.name) for spec in NODE_REGISTRY.values()
                  if spec.id.startswith("core.") for p in spec.inputs if p.trigger)


def _socket(node_type: str, port: str) -> str:
    """The dst_port a wire into `port` lands on: a growable trigger mints a
    numbered socket (in0), any other trigger is its own port."""
    spec = NODE_REGISTRY[node_type]
    return f"{port}0" if next(p for p in spec.inputs if p.name == port).growable else port


def _graph(node_type: str, sockets: list[str], **node) -> dict:
    return {"nodes": [{"id": "go", "type": "core.trigger.manual"},
                      {"id": "n", "type": node_type, "config": {}, **node}],
            "edges": [{"src": "go", "src_port": "trigger", "dst": "n", "dst_port": s}
                      for s in sockets]}


def _trigger_problems(graph: dict) -> list[dict]:
    return [p for p in validate_graph(graph) if p["message"].startswith("required trigger")]


def test_every_once_optional_trigger_is_checked():
    assert ONCE_OPTIONAL <= set(TRIGGERS)


def test_the_sdk_refuses_an_optional_trigger_naming_the_node():
    for node_id, port in (("test.lazy", Port("go", "event", trigger=True, optional=True)),
                          ("test.lazy_sync", Port("in", "event", growable=True, trigger=True,
                                                  optional=True))):
        with pytest.raises(ValueError) as err:
            node(id=node_id, name="Probe", kind=Kind.TRANSFORM, category="Test")(
                type("Probe", (), {"inputs": [port]}))
        assert str(err.value) == (f"node {node_id!r}: trigger input {port.name!r} cannot be "
                                  f"optional: a trigger fires the node and must be wired "
                                  f"(drop optional=True)")
        assert node_id not in NODE_REGISTRY
    assert Port("name", "text", optional=True).optional, "a data input may be optional"


def test_no_registered_node_serves_an_optional_trigger():
    served = [(spec.id, p.name) for spec in NODE_REGISTRY.values()
              for p in spec.inputs if p.trigger and p.optional]
    assert served == []


@pytest.mark.parametrize("node_type,port", TRIGGERS, ids=[f"{t}.{p}" for t, p in TRIGGERS])
def test_an_unwired_trigger_keeps_the_graph_off(node_type, port):
    spec = NODE_REGISTRY[node_type]
    others = [_socket(node_type, p.name) for p in spec.inputs if p.trigger and p.name != port]
    assert _trigger_problems(_graph(node_type, others)) == [
        {"node": "n", "kind": "missing-input",
         "message": f"required trigger '{port}' is not connected"}]
    assert _trigger_problems(_graph(node_type, others + [_socket(node_type, port)])) == []


def test_a_node_with_two_unwired_triggers_names_both():
    assert [p["message"] for p in _trigger_problems(_graph("core.state.meter", []))] == [
        "required trigger 'nudge' is not connected", "required trigger 'set' is not connected"]


def test_a_queue_socket_named_after_its_source_wires_in_but_a_promoted_knob_does_not():
    graph = _graph("core.flow.queue", ["ack", "timeout"], config={"promoted": ["timeout"]})
    assert [p["message"] for p in _trigger_problems(graph)] == [
        "required trigger 'in' is not connected"]
    graph["edges"].append({"src": "go", "src_port": "trigger", "dst": "n", "dst_port": "go"})
    assert _trigger_problems(graph) == []


def test_a_sync_needs_one_wire_on_any_socket():
    assert [p["message"] for p in _trigger_problems(_graph("core.flow.sync", []))] == [
        "required trigger 'in' is not connected"]
    assert _trigger_problems(_graph("core.flow.sync", ["in_3"])) == []
    assert _trigger_problems(_graph("core.flow.sync", ["in0", "in1"])) == []


def test_a_bypassed_node_needs_no_trigger_wire():
    for node_type in {t for t, _ in ONCE_OPTIONAL}:
        assert validate_graph(_graph(node_type, [], disabled=True)) == [], node_type


def test_a_graph_with_no_trigger_node_is_still_refused():
    graph = {"nodes": [{"id": "log", "type": "core.output.log", "config": {}}], "edges": []}
    assert validate_graph(graph) == [
        {"node": "log", "kind": "missing-input", "message": "required trigger 'in' is not connected"},
        {"node": None, "kind": "no-trigger", "message": "no trigger node: nothing can fire"}]


def _manual_to_log_through(mid: dict, into: str, out: str, **wire) -> dict:
    """Manual -> `mid`.`into`, `mid`.`out` -> Log.in (the Log's only trigger)."""
    return {"nodes": [{"id": "go", "type": "core.trigger.manual"}, mid,
                      {"id": "lg", "type": "core.output.log", "config": {}}],
            "edges": [{"src": "go", "src_port": "trigger", "dst": mid["id"], "dst_port": into},
                      {"src": mid["id"], "src_port": out, "dst": "lg", "dst_port": "in"}]}


def test_a_trigger_wired_through_a_bypassed_node_that_passes_nothing_is_not_connected():
    # the bypassed Preview passes its `trigger` on only from its own trigger, which
    # nothing feeds, so the runtime drops the wire and the Log never fires.
    pv = {"id": "pv", "type": "core.output.preview", "config": {}, "disabled": True}
    graph = _manual_to_log_through(pv, "in", "trigger")
    assert validate_graph(graph) == [
        {"node": "lg", "kind": "missing-input",
         "message": "required trigger 'in' is wired through bypassed 'pv', which passes nothing on"}]
    # a bypassed node with no passthrough at all (an LLM) reaches nothing either.
    llm = {"id": "llm", "type": "core.ai.llm", "config": {}, "disabled": True}
    assert [p["message"] for p in _trigger_problems(_manual_to_log_through(llm, "trigger", "trigger"))] == [
        "required trigger 'in' is wired through bypassed 'llm', which passes nothing on"]


def test_a_trigger_wired_through_a_bypassed_passthrough_reaches_its_source():
    for pv_in, pv_out in (("in", "out"), ("trigger", "trigger")):
        pv = {"id": "pv", "type": "core.output.preview", "config": {}, "disabled": True}
        assert validate_graph(_manual_to_log_through(pv, pv_in, pv_out)) == [], pv_in
    tpl = {"id": "tpl", "type": "core.data.template", "config": {}, "disabled": True}
    assert validate_graph(_manual_to_log_through(tpl, "trigger", "trigger")) == []


def test_a_trigger_wired_from_a_wireless_out_needs_a_wireless_in_on_its_channel():
    graph = {"nodes": [{"id": "go", "type": "core.trigger.manual"},
                       {"id": "wo", "type": "core.flow.wireless_out", "config": {"channel": "7"}},
                       {"id": "lg", "type": "core.output.log", "config": {}}],
             "edges": [{"src": "wo", "src_port": "go.trigger", "dst": "lg", "dst_port": "in"}]}
    assert _trigger_problems(graph) == [
        {"node": "lg", "kind": "missing-input",
         "message": "required trigger 'in' is wired through 'wo', which passes nothing on"}]
    graph["nodes"].append({"id": "wi", "type": "core.flow.wireless_in", "config": {"channel": "7"}})
    graph["edges"].append({"src": "go", "src_port": "trigger", "dst": "wi", "dst_port": "go.trigger"})
    assert _trigger_problems(graph) == []
