"""The Meter state node (core.state.meter): a float that persists across fires,
drifts toward `rest` at `rate`/sec (lazy, from elapsed time, no timer), clamps to
[min,max], and emits a `crossed` event (with direction) when a fire moves it across
`threshold`. Persist survives a simulated Restart via the shared KV store.

Shape (settled idiom): `nudge` / `set` are EVENT triggers; the operands ride the
promotable `amount` / `to` KNOBS (pulled when the trigger fires: a wired/promoted
knob is folded into _node_cfg by the runtime before run()). This is the only shape
a user graph can drive: the palette has no pushed-number producer, so a nudge value
cannot arrive on a data port; it is pulled from a knob (Float/Integer/Compute).

Drift is tested with a FAKE clock (patch builtin._meter_now) so the elapsed-time
math is deterministic. Pure helpers (_meter_drift, _crossed_dir) are tested directly.
"""
import asyncio

from boltjar.kv_store import KvStore
from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
import boltjar.nodes.core.builtin as builtin


def _meter(cfg=None, node_id="m"):
    obj = NODE_REGISTRY["core.state.meter"].cls()
    obj._node_cfg = dict(cfg or {})
    obj._node_id = node_id
    return obj


def _fire(obj, port, **knobs):
    """Fire a trigger. The operands ride the knobs: `nudge` adds `amount`, `set`
    overwrites with `to`. We set them straight on _node_cfg, which is the same
    surface run() reads (and what _apply_promoted folds a wired value into). The
    event payload is a bare truthy value; run() ignores it, reading the knobs."""
    if knobs:
        obj._node_cfg.update(knobs)
    obj._fired_port = port
    return obj.run(**{port: True})


class _Clock:
    """A patchable monotone-ish wall clock the fake _meter_now reads."""
    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t


# --------------------------------------------------------------- pure helpers

def test_drift_relaxes_toward_rest_without_overshoot():
    # rate 10/s toward rest 0: 100 -> 70 after 3s.
    assert builtin._meter_drift(100.0, 0.0, 10.0, 3.0) == 70.0
    # never overshoots rest (10s * 10/s = 100 > 100, clamps at rest 0).
    assert builtin._meter_drift(100.0, 0.0, 10.0, 20.0) == 0.0
    # drift is symmetric: rises toward a higher rest too.
    assert builtin._meter_drift(0.0, 50.0, 5.0, 4.0) == 20.0
    # rate 0 or no elapsed: unchanged.
    assert builtin._meter_drift(42.0, 0.0, 0.0, 100.0) == 42.0
    assert builtin._meter_drift(42.0, 0.0, 10.0, 0.0) == 42.0


def test_crossed_direction_both_ways():
    assert builtin._crossed_dir(40.0, 60.0, 50.0) == "up"     # below -> above
    assert builtin._crossed_dir(60.0, 40.0, 50.0) == "down"   # above -> below
    assert builtin._crossed_dir(51.0, 60.0, 50.0) is None     # both above
    assert builtin._crossed_dir(10.0, 40.0, 50.0) is None     # both below
    assert builtin._crossed_dir(40.0, 50.0, 50.0) == "up"     # landing on it = up


# --------------------------------------------------------------- nudge / set / clamp

def test_nudge_adds_and_clamps(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(builtin, "_meter_now", clock)
    m = _meter({"start": 0, "min": 0, "max": 100, "rate": 0, "threshold": 50})
    assert _fire(m, "nudge", amount=30)["value"] == 30
    assert _fire(m, "nudge", amount=30)["value"] == 60
    # clamp at max: 60 + 80 = 140 -> 100.
    assert _fire(m, "nudge", amount=80)["value"] == 100
    # clamp at min: 100 - 500 = -400 -> 0.
    assert _fire(m, "nudge", amount=-500)["value"] == 0


def test_nudge_amount_defaults_to_one(monkeypatch):
    # an unset `amount` knob defaults to 1.0 (fire nudge with no operand = +1).
    monkeypatch.setattr(builtin, "_meter_now", _Clock())
    m = _meter({"start": 0, "min": 0, "max": 100, "rate": 0})
    assert _fire(m, "nudge")["value"] == 1
    assert _fire(m, "nudge")["value"] == 2


def test_set_overwrites_and_clamps(monkeypatch):
    monkeypatch.setattr(builtin, "_meter_now", _Clock())
    m = _meter({"start": 0, "min": 0, "max": 100, "rate": 0})
    assert _fire(m, "set", to=77)["value"] == 77
    assert _fire(m, "set", to=999)["value"] == 100   # clamped to max
    assert _fire(m, "set", to=-5)["value"] == 0       # clamped to min


def test_crossed_fires_up_and_down_on_nudge(monkeypatch):
    monkeypatch.setattr(builtin, "_meter_now", _Clock())
    m = _meter({"start": 40, "min": 0, "max": 100, "rate": 0, "threshold": 50})
    up = _fire(m, "nudge", amount=20)         # 40 -> 60 crosses up
    assert up["value"] == 60
    assert up["crossed"] == {"direction": "up", "value": 60.0, "threshold": 50.0, "from": 40.0}
    # no crossing while staying above.
    assert "crossed" not in _fire(m, "nudge", amount=5)
    down = _fire(m, "nudge", amount=-30)      # 65 -> 35 crosses down
    assert down["crossed"]["direction"] == "down"


def test_drift_alone_can_cross_threshold(monkeypatch):
    clock = _Clock(1000.0)
    monkeypatch.setattr(builtin, "_meter_now", clock)
    # start above threshold, drifting down toward rest 0 at 10/s.
    m = _meter({"start": 60, "min": 0, "max": 100, "rate": 10, "threshold": 50})
    assert _fire(m, "nudge", amount=0)["value"] == 60      # t=1000, no elapsed yet
    clock.t = 1003.0                                        # 3s later
    out = _fire(m, "nudge", amount=0)                       # a bare read: drift 60 -> 30
    assert out["value"] == 30
    assert out["crossed"]["direction"] == "down"     # the DRIFT carried it across


def test_lazy_drift_applied_on_read_from_elapsed(monkeypatch):
    clock = _Clock(500.0)
    monkeypatch.setattr(builtin, "_meter_now", clock)
    m = _meter({"start": 100, "min": 0, "max": 100, "rate": 2, "threshold": -1})
    _fire(m, "nudge", amount=0)                       # seed at t=500, value 100
    clock.t = 510.0                                   # 10s -> drift 2*10 = 20
    assert _fire(m, "nudge", amount=0)["value"] == 80
    clock.t = 560.0                                   # +50s -> drift 100, clamps at 0
    assert _fire(m, "nudge", amount=0)["value"] == 0


# --------------------------------------------------------------- persist across restart

def _kv(tmp_path, monkeypatch):
    from boltjar import server
    store = KvStore(root=tmp_path)
    monkeypatch.setattr(server, "KV_STORE", store)
    return store


def test_persist_true_survives_a_simulated_restart(tmp_path, monkeypatch):
    _kv(tmp_path, monkeypatch)
    clock = _Clock(2000.0)
    monkeypatch.setattr(builtin, "_meter_now", clock)
    cfg = {"start": 0, "min": 0, "max": 100, "rate": 0, "persist": True}

    # session 1: same node id (stable across restart), nudge to 70.
    m1 = _meter(cfg, node_id="mood")
    assert _fire(m1, "nudge", amount=70)["value"] == 70

    # session 2: a BRAND NEW instance (the Restart) with the same id + persist -> it
    # loads 70 from the KV store, not `start` (0). No drift (rate 0), same clock.
    m2 = _meter(cfg, node_id="mood")
    assert _fire(m2, "nudge", amount=0)["value"] == 70


def test_persist_false_resets_to_start_on_restart(tmp_path, monkeypatch):
    _kv(tmp_path, monkeypatch)
    monkeypatch.setattr(builtin, "_meter_now", _Clock())
    cfg = {"start": 10, "min": 0, "max": 100, "rate": 0, "persist": False}
    m1 = _meter(cfg, node_id="vol")
    assert _fire(m1, "nudge", amount=50)["value"] == 60
    # a fresh instance with persist OFF ignores any prior value: back to start.
    m2 = _meter(cfg, node_id="vol")
    assert _fire(m2, "nudge", amount=0)["value"] == 10


# --------------------------------------------------------------- end-to-end firing

def test_meter_fires_through_the_runtime(monkeypatch):
    # a nudge EVENT lands via the mailbox/_fire path; the operand rides the `amount`
    # knob (config). `value` is emitted (readable by a pull).
    monkeypatch.setattr(builtin, "_meter_now", _Clock())
    rt = Runtime()
    rt.build({"nodes": [{"id": "mt", "type": "core.state.meter",
                         "config": {"start": 0, "min": 0, "max": 100,
                                    "rate": 0, "threshold": 50, "amount": 80}}], "edges": []})
    asyncio.run(rt._fire(rt.nodes["mt"], "nudge", True, rt.new_turn()))
    assert rt.nodes["mt"].out_latch.get("value") == 80
    # crossing 0 -> 80 fired the `crossed` event too.
    assert rt.nodes["mt"].out_latch.get("crossed")["direction"] == "up"


def test_meter_pulls_amount_from_a_wired_source(monkeypatch):
    # the reshape's whole point: a PULL-only source (Float) promoted onto `amount`
    # drives the nudge. `nudge` fires (event); the runtime pulls the wired Float
    # into the promoted `amount` knob before run(), so the meter adds 25.
    monkeypatch.setattr(builtin, "_meter_now", _Clock())
    rt = Runtime()
    rt.build({
        "nodes": [
            {"id": "amt", "type": "core.value.float", "config": {"number": 25}},
            {"id": "mt", "type": "core.state.meter",
             "config": {"start": 0, "min": 0, "max": 100, "rate": 0,
                        "threshold": 50, "promoted": ["amount"]}},
        ],
        "edges": [{"src": "amt", "src_port": "out", "dst": "mt", "dst_port": "amount"}],
    })
    asyncio.run(rt._fire(rt.nodes["mt"], "nudge", True, rt.new_turn()))
    assert rt.nodes["mt"].out_latch.get("value") == 25
