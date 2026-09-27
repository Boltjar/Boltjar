"""Vision sensors: screen / window / foreground capture (lean, guarded)."""
from __future__ import annotations

import boltjar.nodes.core  # noqa: F401  registers the nodes
from boltjar.sdk import NODE_REGISTRY


def _node(nid: str, cfg: dict):
    o = NODE_REGISTRY[nid].cls()
    o._node_cfg = cfg
    o._node_id = "n"
    return o


def test_vision_sensors_register_volatile():
    for nid in ("core.sensor.screen", "core.sensor.window", "core.sensor.foreground"):
        spec = NODE_REGISTRY[nid]
        assert spec.kind.value == "sensor"
        assert spec.pulled and spec.volatile  # re-read fresh on every pull


def test_screen_capture_emits_image_or_empty():
    out = _node("core.sensor.screen", {"monitor": "Primary", "max_dim": 320,
                                       "format": "jpeg", "quality": 40}).run()["out"]
    # a data URL when a display is present, "" when headless: never a raised error.
    assert isinstance(out, str)
    if out:
        assert out.startswith("data:image/jpeg;base64,")


def test_window_capture_no_hint_is_empty():
    # no title hint -> empty (nothing to match), and a `matched` of "".
    out = _node("core.sensor.window", {"title": ""}).run(title=None)
    assert out == {"out": "", "matched": ""}


def test_foreground_window_returns_a_title_string():
    out = _node("core.sensor.foreground", {"list_all": False}).run()
    assert isinstance(out["title"], str)   # a title on Windows, "" off-Windows
    assert out["windows"] is None          # not requested
    out2 = _node("core.sensor.foreground", {"list_all": True}).run()
    assert isinstance(out2["windows"], list)
