"""The JSON family: generic data primitives for structured data.

`Parse` (text -> json), `Stringify` (json -> text), `Build JSON` (named inputs
-> object) and `Get` (path -> value). All pulled, lean error handling: a parse
error is surfaced as data, never raised.
"""
from boltjar.runtime import Runtime
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)


def _node(rt, node_id):
    return rt.nodes[node_id].obj


def test_parse_roundtrips_stringify():
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "p", "type": "core.data.parse", "config": {}},
            {"id": "s", "type": "core.data.stringify", "config": {}},
        ],
        "edges": [],
    })
    obj = _node(rt, "p").run(text='{"a": 1, "b": [1, 2, 3]}')["json"]
    assert obj == {"a": 1, "b": [1, 2, 3]}
    # stringify is pretty (2-space) and parses back to the same value.
    text = _node(rt, "s").run(json=obj)["text"]
    assert "\n  " in text  # indented
    assert _node(rt, "p").run(text=text)["json"] == obj


def test_parse_error_is_data_not_raised():
    rt = Runtime()
    rt.build({"nodes": [{"id": "p", "type": "core.data.parse", "config": {}}], "edges": []})
    out = _node(rt, "p").run(text="{not valid json")
    assert "error" in out["json"]
    assert isinstance(out["json"]["error"], str)
    # None in -> None out (no text wired, no crash).
    assert _node(rt, "p").run(text=None)["json"] is None


def test_stringify_non_serialisable_falls_back_to_str():
    rt = Runtime()
    rt.build({"nodes": [{"id": "s", "type": "core.data.stringify", "config": {}}], "edges": []})
    out = _node(rt, "s").run(json={1, 2, 3})  # a set is not JSON-serialisable
    assert isinstance(out["text"], str)
    assert out["text"]  # non-empty str() fallback


def test_build_object_from_named_inputs():
    rt = Runtime()
    rt.build({"nodes": [{"id": "b", "type": "core.data.build", "config": {}}], "edges": []})
    # each wired named input becomes a key (the dst_port name) -> value.
    out = _node(rt, "b").run(user="ada", mood="curious")
    assert out["json"] == {"user": "ada", "mood": "curious"}
    # unwired (None) inputs are dropped, not stored as null keys.
    assert _node(rt, "b").run(user="ada", mood=None)["json"] == {"user": "ada"}
    # no inputs -> empty object.
    assert _node(rt, "b").run()["json"] == {}


def test_build_through_runtime_pull():
    """Wire two Text nodes into named Build keys and pull the assembled json."""
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "u", "type": "core.value.text", "config": {"text": "ada"}},
            {"id": "m", "type": "core.value.text", "config": {"text": "curious"}},
            {"id": "b", "type": "core.data.build", "config": {}},
        ],
        "edges": [
            {"src": "u", "src_port": "out", "dst": "b", "dst_port": "user"},
            {"src": "m", "src_port": "out", "dst": "b", "dst_port": "mood"},
        ],
    })
    out = rt._pull_output("b", "json", rt.new_turn())
    assert out == {"user": "ada", "mood": "curious"}


def test_get_by_path_dot_bracket_and_missing():
    rt = Runtime()
    rt.build({"nodes": [{"id": "g", "type": "core.data.get", "config": {}}], "edges": []})
    g = _node(rt, "g")
    data = {"user": {"name": "ada"}, "items": [{"id": 10}, {"id": 20}]}

    g._node_cfg = {"path": "user.name"}
    assert g.run(json=data)["value"] == "ada"

    g._node_cfg = {"path": "items.1.id"}
    assert g.run(json=data)["value"] == 20

    # bracket form resolves the same as the dotted form.
    g._node_cfg = {"path": "items[0].id"}
    assert g.run(json=data)["value"] == 10

    # missing key, out-of-range index, and indexing a scalar all return null.
    g._node_cfg = {"path": "user.age"}
    assert g.run(json=data)["value"] is None
    g._node_cfg = {"path": "items.5.id"}
    assert g.run(json=data)["value"] is None
    g._node_cfg = {"path": "user.name.nope"}
    assert g.run(json=data)["value"] is None

    # empty path returns the whole value.
    g._node_cfg = {"path": ""}
    assert g.run(json=data)["value"] == data


def test_get_negative_index():
    rt = Runtime()
    rt.build({"nodes": [{"id": "g", "type": "core.data.get", "config": {}}], "edges": []})
    g = _node(rt, "g")
    data = {"items": [{"id": 10}, {"id": 20}, {"id": 30}]}
    # Python-style negative indices address from the end.
    g._node_cfg = {"path": "items.-1.id"}
    assert g.run(json=data)["value"] == 30
    g._node_cfg = {"path": "items[-3].id"}
    assert g.run(json=data)["value"] == 10
    # out of range (too negative) is still null.
    g._node_cfg = {"path": "items.-4.id"}
    assert g.run(json=data)["value"] is None
