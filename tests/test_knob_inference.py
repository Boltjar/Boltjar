"""A bare annotated knob (`on: bool = False`) gets the widget its annotation
names, even in a module with `from __future__ import annotations` (this one),
where every annotation is a string. boltjar.nodes.core.builtin is such a module: before the
hints were resolved, Boolean, Integer, Float and Interval all rendered as text
boxes, and a Boolean saved as "false" ran as True. Saved strings from those
graphs are read as the widget's kind, so they keep working. A text wire into
one of those knobs, promoted, does not: the port takes the knob's own type.
"""
from __future__ import annotations

from typing import Optional

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.runtime import Runtime, node_config
from boltjar.sdk import NODE_REGISTRY, Kind, Port, Widget, node

# Every core node's widget kinds. A change here is a change to what the editor
# renders for that node, so it must be deliberate.
CORE_WIDGET_KINDS = {
    "core.ai.embed": {"model": "model"},
    "core.ai.llm": {"model": "model"},
    "core.ai.rerank": {"model": "model"},
    "core.ai.stt": {"model": "model"},
    "core.ai.tool": {"name": "text", "description": "code", "schema_mode": "select", "schema": "code"},
    "core.ai.tool_args": {"fields": "text"},
    "core.ai.tts": {"model": "model"},
    "core.data.build": {},
    "core.data.chunk": {"size": "number", "overlap": "number", "by": "select"},
    "core.data.compute": {"expression": "code"},
    "core.data.format_list": {"item": "code", "separator": "select"},
    "core.data.get": {"path": "text"},
    "core.data.list": {},
    "core.data.parse": {},
    "core.data.sentences": {},
    "core.data.split": {"keys": "text"},
    "core.data.stringify": {},
    "core.data.template": {"template": "code"},
    "core.db": {"operation": "select", "sql": "code", "table": "text", "fields": "code", "where": "code"},
    "core.file.append": {"path": "text"},
    "core.file.delete": {"path": "text"},
    "core.file.list": {"path": "text"},
    "core.file.read": {"path": "text"},
    "core.file.write": {"path": "text"},
    "core.flow.changed": {},
    "core.flow.for_each": {},
    "core.flow.queue": {"timeout": "number"},
    "core.flow.router": {"label": "text"},
    "core.flow.sync": {},
    "core.flow.wait": {"amount": "number", "unit": "select"},
    "core.flow.wireless_in": {"channel": "select"},
    "core.flow.wireless_out": {"channel": "select"},
    "core.kv": {"operation": "select", "key": "text", "value": "code", "prefix": "text"},
    "core.logic.condition": {"expression": "code"},
    "core.net.http": {"method": "select", "url": "text", "headers": "code", "query": "code",
                      "body": "code", "response_type": "select"},
    "core.output.chat": {},
    "core.output.log": {"label": "text"},
    "core.output.preview": {},
    "core.output.respond_webhook": {"status": "number", "content_type": "select",
                                    "headers": "code", "last": "bool"},
    "core.parse.tags": {"moods": "code", "actions": "code", "priority": "code", "default": "text",
                        "max_emoji": "number", "emoji": "bool"},
    "core.sensor.clock": {"format": "select", "timezone": "select"},
    "core.sensor.foreground": {"list_all": "bool"},
    "core.sensor.screen": {"monitor": "select", "max_dim": "number", "format": "select", "quality": "number"},
    "core.sensor.window": {"title": "text", "max_dim": "number", "format": "select", "quality": "number"},
    "core.state.meter": {"amount": "number", "to": "number", "rest": "number", "rate": "number",
                         "min": "number", "max": "number", "threshold": "number", "start": "number",
                         "persist": "bool"},
    "core.store.database": {"schema": "schema"},
    "core.store.kv": {},
    "core.store.vectors": {},
    "core.text.strip": {"emoji": "bool", "markdown": "bool", "tags": "bool", "actions": "bool", "urls": "bool"},
    "core.trigger.agenda": {"poll": "number", "table": "text"},
    "core.trigger.audio_in": {},
    "core.trigger.chat": {},
    "core.trigger.interval": {"seconds": "number"},
    "core.trigger.manual": {},
    "core.trigger.schedule": {"cron": "text", "timezone": "select"},
    "core.trigger.webhook": {"path": "text", "method": "select", "secret": "text",
                             "reply": "select", "timeout": "number"},
    "core.value.audio": {"src": "text"},
    "core.value.boolean": {"on": "bool"},
    "core.value.float": {"number": "number"},
    "core.value.image": {"src": "text"},
    "core.value.integer": {"number": "number"},
    "core.value.text": {"text": "code"},
    "core.vectors": {"operation": "select", "namespace": "text", "top_k": "number", "min_score": "number",
                     "metadata": "code", "ref": "text"},
}


@pytest.fixture
def registry():
    """Registers test nodes and removes them again, so the shared registry is
    left exactly as the core nodes made it."""
    before = dict(NODE_REGISTRY)
    yield NODE_REGISTRY
    NODE_REGISTRY.clear()
    NODE_REGISTRY.update(before)


def _kinds(node_id: str) -> dict[str, str]:
    return {w.name: w.kind for w in NODE_REGISTRY[node_id].widgets}


def _built(node_type: str, config: dict) -> object:
    rt = Runtime()
    rt.build({"nodes": [{"id": "n", "type": node_type, "config": config}], "edges": []})
    return rt.nodes["n"].obj


def _pulled(node_type: str, config: dict):
    rt = Runtime()
    rt.build({"nodes": [{"id": "n", "type": node_type, "config": config}], "edges": []})
    return rt._pull_output("n", "out", rt.new_turn())


# --------------------------------------------------------------- inference

def test_every_core_node_has_the_widget_kinds_it_declares():
    core = {nid: _kinds(nid) for nid in NODE_REGISTRY if nid.startswith("core.")}
    assert core == CORE_WIDGET_KINDS


def test_string_annotations_resolve_to_their_widget_kinds(registry):
    @node(id="test.inference.bare", name="Bare", kind=Kind.VALUE, category="Test", pulled=True)
    class Bare:
        flag: bool = False
        count: int = 3
        ratio: float = 0.5
        label: str = "x"
        extra: dict = {}
        maybe: Optional[int] = None
        either: float | None = None
        outputs = [Port("out", "any")]

    assert _kinds("test.inference.bare") == {
        "flag": "bool", "count": "number", "ratio": "number", "label": "text",
        "extra": "code", "maybe": "number", "either": "number",
    }


def test_an_unresolvable_annotation_falls_back_to_the_type_names(registry):
    # `Missing` is not defined anywhere, so resolving the hints fails as a whole;
    # the bare names still map, and the unknown one is a text box.
    @node(id="test.inference.broken", name="Broken", kind=Kind.VALUE, category="Test", pulled=True)
    class Broken:
        flag: bool = True
        count: int = 1
        other: Missing = None  # noqa: F821
        outputs = [Port("out", "any")]

    assert _kinds("test.inference.broken") == {"flag": "bool", "count": "number", "other": "text"}


def test_an_int_knob_steps_in_whole_numbers(registry):
    steps = {w.name: w.step for w in NODE_REGISTRY["core.value.integer"].widgets}
    assert steps == {"number": 1}
    steps = {w.name: w.step for w in NODE_REGISTRY["core.value.float"].widgets}
    assert steps == {"number": None}


def test_an_explicit_widget_keeps_its_declared_kind(registry):
    @node(id="test.inference.explicit", name="Explicit", kind=Kind.VALUE, category="Test", pulled=True)
    class Explicit:
        mode: str = Widget(kind="select", options=["a", "b"], default="a")
        outputs = [Port("out", "any")]

    assert _kinds("test.inference.explicit") == {"mode": "select"}


# --------------------------------------------------------------- saved strings

@pytest.mark.parametrize("saved, on", [
    ("false", False), ("False", False), ("0", False), ("", False), ("off", False), ("no", False),
    ("true", True), ("True", True), (" 1 ", True), ("on", True), ("yes", True),
    (False, False), (True, True), (0, False), (1, True),
])
def test_a_bool_knob_reads_saved_strings_as_the_toggle_shows(saved, on):
    assert Widget(kind="bool").coerce(saved) is on


@pytest.mark.parametrize("saved, value", [
    ("3", 3), (" 42 ", 42), ("2.5", 2.5), ("1e3", 1000.0), ("", 0), (7, 7), (1.5, 1.5),
])
def test_a_number_knob_reads_saved_strings_as_numbers(saved, value):
    got = Widget(kind="number").coerce(saved)
    assert got == value and type(got) is type(value)


def test_an_unparsable_number_and_other_kinds_pass_through():
    assert Widget(kind="number").coerce("abc") == "abc"
    assert Widget(kind="text").coerce("false") == "false"
    assert Widget(kind="select", options=[1, 2]).coerce("2") == "2"


def test_a_boolean_saved_as_the_string_false_runs_as_false():
    assert _pulled("core.value.boolean", {"on": "false"}) is False
    assert _pulled("core.value.boolean", {"on": "true"}) is True
    assert _pulled("core.value.boolean", {}) is False


def test_numbers_saved_as_strings_run_as_numbers():
    assert _pulled("core.value.integer", {"number": "3"}) == 3
    assert _pulled("core.value.float", {"number": "2.5"}) == 2.5
    assert _built("core.trigger.interval", {"seconds": "5"}).seconds == 5


def test_validation_and_the_runtime_read_the_same_coerced_config():
    spec = NODE_REGISTRY["core.value.boolean"]
    assert node_config(spec, {"on": "false"})["on"] is False
    assert _built("core.value.boolean", {"on": "false"})._node_cfg["on"] is False


# --------------------------------------------------------------- promoted ports

def _wired_into(source: str, node_type: str, knob: str) -> list[str]:
    """The edge problems of a graph that wires `source`'s output into `knob` of a
    `node_type` node, the knob promoted to an input."""
    import boltjar.server as server

    graph = {
        "nodes": [{"id": "src", "type": source, "config": {}},
                  {"id": "n", "type": node_type, "config": {"promoted": [knob]}}],
        "edges": [{"src": "src", "src_port": "out", "dst": "n", "dst_port": knob}],
    }
    return [p["message"] for p in server.validate_graph(graph) if p["message"].startswith("edge ")]


@pytest.mark.parametrize("node_type, knob, own", [
    ("core.trigger.interval", "seconds", ("core.value.float", "float")),
    ("core.value.integer", "number", ("core.value.float", "float")),
    ("core.value.float", "number", ("core.value.float", "float")),
    ("core.value.boolean", "on", ("core.value.boolean", "bool")),
])
def test_a_promoted_number_or_bool_knob_takes_its_type_not_text(node_type, knob, own):
    # while these knobs were text boxes their promoted port was `text`, so a text
    # wire passed. A wired value reaches the node as sent (a wired "false" would
    # run a Boolean as on), so the port takes what every number and bool knob takes.
    source, port_type = own
    assert _wired_into("core.value.text", node_type, knob) == [
        f"edge src.out -> n.{knob}: text output cannot feed {port_type} input"]
    assert _wired_into(source, node_type, knob) == []
