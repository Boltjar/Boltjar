"""The Changed dedup gate: forward only when the value differs from the last seen."""
from __future__ import annotations

import boltjar.nodes.core  # noqa: F401  registers the nodes
from boltjar.sdk import NODE_REGISTRY


def test_changed_forwards_only_on_change():
    c = NODE_REGISTRY["core.flow.changed"].cls()
    assert c.run(value="a") == {"changed": True, "value": "a"}   # first always counts
    assert c.run(value="a") == {"same": True}                    # unchanged suppressed
    assert c.run(value="b") == {"changed": True, "value": "b"}   # a real change forwards


def test_changed_uses_content_equality_for_dicts():
    c = NODE_REGISTRY["core.flow.changed"].cls()
    assert c.run(value={"x": 1, "y": 2}) == {"changed": True, "value": {"x": 1, "y": 2}}
    # an equal-by-content dict (different object, different key order) is the SAME
    assert c.run(value={"y": 2, "x": 1}) == {"same": True}
    assert c.run(value={"x": 1, "y": 3}) == {"changed": True, "value": {"x": 1, "y": 3}}
