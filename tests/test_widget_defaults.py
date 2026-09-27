"""Nodes run the defaults the editor shows.

The editor shows a widget's saved value, or its declared default when nothing
is saved (the key is absent or null: the knobs render `value ?? default`), and a
node can come back as `config: {}` because an untouched default is not always
saved. So the runtime hands every node its config MERGED over the declared
widget defaults, and validation reads the same merged config. A node reading
`cfg.get(x) or <literal>` otherwise ran its own private literal instead of what
the editor displays: a fresh Format List printed every row as a raw dict (its
fallback was `{.}`, not the `{sender}: {message}` the editor shows).
"""
from __future__ import annotations

import asyncio

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
import boltjar.nodes.core.builtin as builtin
from boltjar import models
from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY
from boltjar.server import validate_graph

ROWS = '[{"sender": "Alice", "message": "hi"}, {"sender": "Assistant", "message": "hello"}]'

# never instantiated: the runtime flattens them into direct edges at build.
_FLATTENED = {"core.flow.wireless_in", "core.flow.wireless_out", "core.flow.router"}


def _built(node_type: str, config) -> object:
    """A node instance exactly as the runtime builds it from a saved graph."""
    rt = Runtime()
    rt.build({"nodes": [{"id": "n", "type": node_type, "config": config}], "edges": []})
    return rt.nodes["n"].obj


def _format_list_out(config, rows_json: str) -> str:
    """Pull a Format List fed a json list (Text -> Parse -> Format List)."""
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "rows", "type": "core.value.text", "config": {"text": rows_json}},
            {"id": "parse", "type": "core.data.parse", "config": {}},
            {"id": "fl", "type": "core.data.format_list", "config": config},
        ],
        "edges": [
            {"src": "rows", "src_port": "out", "dst": "parse", "dst_port": "text"},
            {"src": "parse", "src_port": "json", "dst": "fl", "dst_port": "list"},
        ],
    })
    return rt._pull_output("fl", "out", rt.new_turn())


# --------------------------------------------------------------- Format List

def test_fresh_format_list_renders_the_item_template_the_editor_shows() -> None:
    assert _format_list_out({}, ROWS) == "Alice: hi\nAssistant: hello"


def test_format_list_renders_a_none_field_blank() -> None:
    # the Template rule: a None substitutes an empty string, never the text "None".
    rows = ('[{"time": null, "sender": "Alice", "message": "hi"},'
            ' {"time": "09:00", "sender": "Assistant", "message": "hello"}]')
    out = _format_list_out({"item": "[{time}] {sender}: {message}"}, rows)
    assert out == "[] Alice: hi\n[09:00] Assistant: hello"


def test_format_list_renders_a_none_item_blank() -> None:
    assert _format_list_out({"item": "<{.}>"}, '[null, "x"]') == "<>\n<x>"


def test_format_list_runs_a_cleared_item_template_as_shown() -> None:
    # a saved "" is a real value the editor shows as an empty field: it renders
    # each row empty, never a hidden private template.
    assert _format_list_out({"item": ""}, ROWS) == "\n"


# --------------------------------------------------------------- the runtime merge

@pytest.mark.parametrize(
    "spec",
    [s for s in NODE_REGISTRY.values() if s.widgets and s.id not in _FLATTENED],
    ids=lambda s: s.id,
)
def test_a_node_built_from_an_empty_config_reads_its_declared_defaults(spec) -> None:
    cfg = _built(spec.id, {})._node_cfg
    assert {w.name: cfg.get(w.name) for w in spec.widgets} == \
        {w.name: w.default for w in spec.widgets}


def test_saved_values_win_and_non_widget_keys_pass_through() -> None:
    cfg = _built("core.flow.queue", {"timeout": 5, "promoted": ["timeout"]})._node_cfg
    assert cfg["timeout"] == 5
    assert cfg["promoted"] == ["timeout"]


def test_a_saved_null_reads_as_the_declared_default() -> None:
    assert _format_list_out({"item": None}, ROWS) == "Alice: hi\nAssistant: hello"


def test_node_attributes_carry_the_merged_value_too() -> None:
    # Template reads `self.template`, not `_node_cfg`: a saved null must reach it
    # as the declared `{in}`, the same value its config and the editor carry.
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {"template": None}},
        ],
        "edges": [{"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "in"}],
    })
    assert rt._pull_output("tpl", "out", rt.new_turn()) == "hi"


# --------------------------------------------------------------- validation

def test_validation_accepts_the_in_tag_a_fresh_template_runs() -> None:
    graph = {
        "nodes": [
            {"id": "m", "type": "core.trigger.manual"},
            {"id": "txt", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "tpl", "type": "core.data.template", "config": {}},
        ],
        "edges": [{"src": "txt", "src_port": "out", "dst": "tpl", "dst_port": "in"}],
    }
    assert validate_graph(graph) == []


def test_validation_names_a_fresh_tool_as_the_runtime_does() -> None:
    # the model is offered a fresh Tool under its declared name, so validation
    # must accept it, and two fresh Tools collide on that one name.
    def name_problems(graph):
        return [p["kind"] for p in validate_graph(graph)
                if p["kind"] in ("tool-no-name", "duplicate-tool-name")]

    graph = {"nodes": [{"id": "m", "type": "core.trigger.manual"},
                       {"id": "t1", "type": "core.ai.tool", "config": {}}], "edges": []}
    assert name_problems(graph) == []
    graph["nodes"].append({"id": "t2", "type": "core.ai.tool", "config": {}})
    assert name_problems(graph) == ["duplicate-tool-name"]


# --------------------------------------------------------------- model nodes

def _shown_model(node_type: str) -> str:
    """The model the editor's picker shows with nothing picked: the declared default."""
    return next(w.default for w in NODE_REGISTRY[node_type].widgets if w.name == "model")


@pytest.mark.parametrize("config", [{}, {"model": ""}], ids=["unsaved", "cleared"])
def test_a_fresh_embed_runs_the_model_its_picker_shows(monkeypatch, config) -> None:
    seen = {}

    async def fake_embed(manifest, text):
        seen["id"] = manifest.id if manifest else None
        return [1.0]
    monkeypatch.setattr(builtin, "_embed_model", fake_embed)
    asyncio.run(_built("core.ai.embed", config).run(text="hi"))
    shown = _shown_model("core.ai.embed")
    assert seen["id"] == shown
    assert models.get(shown).kind == "embed"


@pytest.mark.parametrize("config", [{}, {"model": ""}], ids=["unsaved", "cleared"])
def test_a_fresh_rerank_runs_the_model_its_picker_shows(monkeypatch, config) -> None:
    seen = {}

    async def fake_rerank(manifest, query, docs, endpoint):
        seen["id"] = manifest.id if manifest else None
        return [1.0 for _ in docs]
    monkeypatch.setattr(builtin, "_rerank_model", fake_rerank)
    asyncio.run(_built("core.ai.rerank", config).run(query="q", candidates=[{"text": "a"}]))
    shown = _shown_model("core.ai.rerank")
    assert seen["id"] == shown
    assert models.get(shown).kind == "rerank"
