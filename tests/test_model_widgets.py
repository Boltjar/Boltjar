"""A model picker is declared on its widget, never keyed off a node id: the
family of models it lists (`model_kind`) and the special values it offers above
the list (`options`), so any pack node can carry one."""
from __future__ import annotations

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.sdk import Widget, model
from local_client import local_client

client = local_client()


def _model_widgets() -> dict[str, dict]:
    nodes = client.get("/api/object_info").json()["nodes"]
    return {n["id"]: w for n in nodes for w in n["widgets"] if w["kind"] == "model"}


def test_every_core_model_widget_declares_its_family():
    assert {nid: w["model_kind"] for nid, w in _model_widgets().items()} == {
        "core.ai.llm": "llm", "core.ai.tts": "tts", "core.ai.stt": "stt",
        "core.ai.embed": "embed", "core.ai.rerank": "rerank"}


def test_only_the_llm_picker_offers_auto():
    widgets = _model_widgets()
    assert widgets["core.ai.llm"]["options"] == ["auto"]
    assert all(w["options"] == [] for nid, w in widgets.items() if nid != "core.ai.llm")


def test_a_family_is_served_on_model_widgets_only():
    nodes = client.get("/api/object_info").json()["nodes"]
    assert all(w["model_kind"] is None for n in nodes for w in n["widgets"] if w["kind"] != "model")
    # a pack's model widget that names no family lists LLMs, the manifest default.
    assert Widget(kind="model").as_dict()["model_kind"] == "llm"
    assert model("tts", "acme/voice").as_dict()["default"] == "acme/voice"
