"""A model picker is declared on its widget, never keyed off a node id: the
family of models it lists (`model_kind`), so any pack node can carry one.

A model can cost money, so a picker never holds a model nobody picked: it has
no default model and no special value, and @node refuses a declaration that
brings either, naming the node. Only `optional` says what an empty pick means:
the LLM runs its offline mock, every other model node keeps its graph Off until
a model is picked."""
from __future__ import annotations

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.sdk import NODE_REGISTRY, Kind, Port, Widget, model, node
from local_client import local_client

client = local_client()


def _model_widgets() -> dict[str, dict]:
    nodes = client.get("/api/object_info").json()["nodes"]
    return {n["id"]: w for n in nodes for w in n["widgets"] if w["kind"] == "model"}


def test_every_core_model_widget_declares_its_family():
    assert {nid: w["model_kind"] for nid, w in _model_widgets().items()} == {
        "core.ai.llm": "llm", "core.ai.tts": "tts", "core.ai.stt": "stt",
        "core.ai.embed": "embed", "core.ai.rerank": "rerank"}


def test_no_core_model_picker_declares_a_model():
    served = _model_widgets()
    assert {nid: (w["default"], w["options"]) for nid, w in served.items()} == \
        {nid: ("", []) for nid in served}
    declared = [(s.id, w.name, w.default, w.options) for s in NODE_REGISTRY.values()
                for w in s.widgets if w.kind == "model" and (w.default or w.options)]
    assert declared == []


def test_only_the_llm_runs_with_no_model_picked():
    optional = {s.id: w.optional for s in NODE_REGISTRY.values() if s.id.startswith("core.")
                for w in s.widgets if w.kind == "model"}
    assert optional == {"core.ai.llm": True, "core.ai.tts": False, "core.ai.stt": False,
                        "core.ai.embed": False, "core.ai.rerank": False}


def test_a_family_is_served_on_model_widgets_only():
    nodes = client.get("/api/object_info").json()["nodes"]
    assert all(w["model_kind"] is None for n in nodes for w in n["widgets"] if w["kind"] != "model")
    # a pack's model widget that names no family lists LLMs, the manifest default.
    assert Widget(kind="model").as_dict()["model_kind"] == "llm"
    assert model("tts").as_dict()["default"] == ""


def _declare(node_id: str, picker: Widget) -> None:
    @node(id=node_id, name="Voice", kind=Kind.TRANSFORM, category="Test")
    class Voice:  # noqa: F841  (declaring it is the test)
        model: Widget = picker
        inputs = [Port("trigger", "event", trigger=True)]
        outputs = [Port("audio", "audio")]


@pytest.mark.parametrize("picker, said", [
    (lambda: model("tts", "acme/voice"), "a default model 'acme/voice'"),
    (lambda: model("tts", default="acme/voice"), "a default model 'acme/voice'"),
    (lambda: model("llm", auto=True), "the pick 'auto'"),
    (lambda: Widget(kind="model", model_kind="tts", default="acme/voice"),
     "a default model 'acme/voice'"),
    (lambda: Widget(kind="model", options=["auto"]), "the pick 'auto'"),
], ids=["positional", "keyword", "auto", "widget-default", "widget-options"])
def test_a_model_picker_that_picks_a_model_is_refused_naming_the_node(picker, said):
    with pytest.raises(ValueError) as caught:
        _declare("test.refused_voice", picker())
    message = str(caught.value)
    assert message.startswith("node 'test.refused_voice': model picker 'model' declares " + said)
    assert "never picks a model on its own" in message
    assert "test.refused_voice" not in NODE_REGISTRY


def test_an_empty_model_picker_is_accepted():
    try:
        _declare("test.empty_voice", model("tts"))
        assert NODE_REGISTRY["test.empty_voice"].widgets[0].default == ""
    finally:
        NODE_REGISTRY.pop("test.empty_voice", None)


def test_model_refuses_a_keyword_it_never_had():
    with pytest.raises(TypeError, match="unexpected keyword argument 'fallback'"):
        model("tts", fallback="acme/voice")
