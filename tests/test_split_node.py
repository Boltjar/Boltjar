"""Split JSON: the reverse of Build JSON. A json object in, one output port per
configured key, each routing that key's value. Pulled, lean: a non-object or a
parse error resolves every key to null, never raised.
"""
from boltjar.runtime import Runtime
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)


def _node(rt, node_id):
    return rt.nodes[node_id].obj


def test_split_dict_into_per_key_values():
    rt = Runtime()
    rt.build({"nodes": [{"id": "s", "type": "core.data.split", "config": {}}], "edges": []})
    s = _node(rt, "s")
    s._node_cfg = {"keys": "name, age"}
    out = s.run(json={"name": "ada", "age": 42, "extra": "x"})
    # one entry per configured key; an unconfigured property is dropped.
    assert out == {"name": "ada", "age": 42}


def test_split_missing_key_is_null():
    rt = Runtime()
    rt.build({"nodes": [{"id": "s", "type": "core.data.split", "config": {}}], "edges": []})
    s = _node(rt, "s")
    s._node_cfg = {"keys": "name age city"}
    out = s.run(json={"name": "ada", "age": 42})
    # a configured key absent from the object resolves to None (not omitted).
    assert out == {"name": "ada", "age": 42, "city": None}


def test_split_parses_json_string_input():
    rt = Runtime()
    rt.build({"nodes": [{"id": "s", "type": "core.data.split", "config": {}}], "edges": []})
    s = _node(rt, "s")
    s._node_cfg = {"keys": "name, age"}
    # a json STRING input is parsed before the keys are read.
    out = s.run(json='{"name": "ada", "age": 42}')
    assert out == {"name": "ada", "age": 42}


def test_split_non_object_and_parse_error_resolve_to_null():
    rt = Runtime()
    rt.build({"nodes": [{"id": "s", "type": "core.data.split", "config": {}}], "edges": []})
    s = _node(rt, "s")
    s._node_cfg = {"keys": "name age"}
    # a non-object value (list / scalar / None) -> every key null.
    assert s.run(json=[1, 2, 3]) == {"name": None, "age": None}
    assert s.run(json=7) == {"name": None, "age": None}
    assert s.run(json=None) == {"name": None, "age": None}
    # an invalid json string is a parse error -> every key null (never raised).
    assert s.run(json="{not valid json") == {"name": None, "age": None}


def test_split_keys_config_drives_the_ports():
    rt = Runtime()
    rt.build({"nodes": [{"id": "s", "type": "core.data.split", "config": {}}], "edges": []})
    s = _node(rt, "s")
    data = {"name": "ada", "age": 42, "city": "lon"}
    # commas, spaces and newlines all separate; blanks and dupes collapse.
    s._node_cfg = {"keys": "name,  age\nname"}
    out = s.run(json=data)
    assert out == {"name": "ada", "age": 42}
    # an empty keys config exposes no ports (empty result).
    s._node_cfg = {"keys": ""}
    assert s.run(json=data) == {}


def test_split_through_runtime_pull_per_port():
    """Feed a json object in and pull each configured key port independently."""
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "p", "type": "core.data.parse", "config": {}},
            {"id": "s", "type": "core.data.split", "config": {"keys": "name, age"}},
        ],
        "edges": [
            {"src": "p", "src_port": "json", "dst": "s", "dst_port": "json"},
        ],
    })
    # latch a json object onto the Parse source via its text input.
    rt.nodes["p"].latch["text"] = '{"name": "ada", "age": 42}'
    turn = rt.new_turn()
    # each key port pulls independently and resolves its own value.
    assert rt._pull_output("s", "name", turn) == "ada"
    assert rt._pull_output("s", "age", turn) == 42
    # a key that is not present in the object pulls null.
    assert rt._pull_output("s", "city", turn) is None
