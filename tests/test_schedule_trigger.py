"""The Schedule trigger's tiny cron matcher (minute granularity, no dep)."""
from __future__ import annotations

import datetime

import boltjar.nodes.core.builtin as b  # noqa: F401  registers the nodes
from boltjar.sdk import NODE_REGISTRY


def dt(h=9, mi=0, d=17):
    # 2026-06-17 is a Wednesday (python weekday=2 -> cron dow=3).
    return datetime.datetime(2026, 6, d, h, mi)


def test_cron_match_minute_hour_and_steps():
    assert b._cron_match("* * * * *", dt())
    assert b._cron_match("0 9 * * *", dt(h=9, mi=0))      # daily 9:00
    assert not b._cron_match("0 9 * * *", dt(h=9, mi=1))
    assert not b._cron_match("0 9 * * *", dt(h=10, mi=0))
    assert b._cron_match("*/15 * * * *", dt(mi=0))         # every 15 min
    assert b._cron_match("*/15 * * * *", dt(mi=30))
    assert not b._cron_match("*/15 * * * *", dt(mi=7))


def test_cron_match_ranges_lists_and_weekday():
    assert b._cron_match("0 9-17 * * *", dt(h=13))         # business hours range
    assert not b._cron_match("0 9-17 * * *", dt(h=8))
    assert b._cron_match("0 9 * * 3", dt())                # Wednesday (cron dow 3)
    assert not b._cron_match("0 9 * * 1", dt())            # Monday
    assert b._cron_match("0 0 * * 1,3,5", dt(h=0, mi=0))   # Mon/Wed/Fri list incl. Wed


def test_cron_match_invalid_is_no_match():
    assert not b._cron_match("bad", dt())
    assert not b._cron_match("0 9 * *", dt())              # 4 fields
    assert not b._cron_match("", dt())


def test_cron_match_step_on_range_and_sunday_seven():
    # step over a range: '1-9/2' = odd minutes 1,3,5,7,9
    assert b._cron_match("1-9/2 * * * *", dt(mi=3))
    assert b._cron_match("1-9/2 * * * *", dt(mi=9))
    assert not b._cron_match("1-9/2 * * * *", dt(mi=4))
    assert not b._cron_match("1-9/2 * * * *", dt(mi=11))
    # step on '*': '*/3' hour fires at 0,3,6,...
    assert b._cron_match("0 */3 * * *", dt(h=0, mi=0))
    assert b._cron_match("0 */3 * * *", dt(h=6, mi=0))
    assert not b._cron_match("0 */3 * * *", dt(h=7, mi=0))
    # day-of-week 7 is Sunday (== 0). 2026-06-21 is a Sunday.
    assert b._cron_match("0 9 * * 7", dt(d=21))
    assert b._cron_match("0 9 * * 0", dt(d=21))
    assert not b._cron_match("0 9 * * 7", dt(d=17))  # Wednesday


def test_cron_dow_range_with_seven():
    # 5-7 = Fri(5), Sat(6), Sun(0 via 7). June 2026: 19 Fri, 20 Sat, 21 Sun, 17 Wed.
    assert b._cron_match("0 9 * * 5-7", dt(d=19))     # Friday
    assert b._cron_match("0 9 * * 5-7", dt(d=20))     # Saturday
    assert b._cron_match("0 9 * * 5-7", dt(d=21))     # Sunday (the 7 in the range)
    assert not b._cron_match("0 9 * * 5-7", dt(d=17))  # Wednesday


def test_cron_dom_dow_or_semantics():
    # standard cron: when BOTH day fields are restricted, match if EITHER matches.
    # '0 0 1 * 1' = midnight on the 1st OR any Monday. June 2026: 1/8/15 are Mondays.
    assert b._cron_match("0 0 1 * 1", dt(d=1, h=0, mi=0))    # the 1st (and a Monday)
    assert b._cron_match("0 0 1 * 1", dt(d=8, h=0, mi=0))    # a Monday, not the 1st -> OR
    assert b._cron_match("0 0 1 * 1", dt(d=15, h=0, mi=0))   # another Monday
    assert not b._cron_match("0 0 1 * 1", dt(d=3, h=0, mi=0))  # Wed, not the 1st -> neither
    # one field '*' keeps AND: '0 0 5 * *' is only the 5th regardless of weekday.
    assert b._cron_match("0 0 5 * *", dt(d=5, h=0, mi=0))
    assert not b._cron_match("0 0 5 * *", dt(d=8, h=0, mi=0))


def test_cron_step_anchors_at_field_minimum():
    # */2 on the 1-based day-of-month field = 1,3,5,7 (anchored at 1), not 2,4,6.
    assert b._cron_match("0 0 */2 * *", dt(d=1, h=0, mi=0))
    assert b._cron_match("0 0 */2 * *", dt(d=3, h=0, mi=0))
    assert not b._cron_match("0 0 */2 * *", dt(d=2, h=0, mi=0))
    # */15 on the 0-based minute field stays 0,15,30,45
    assert b._cron_match("*/15 * * * *", dt(mi=0))
    assert not b._cron_match("*/15 * * * *", dt(mi=2))


def test_schedule_tick_fires_once_per_minute():
    cron = "* * * * *"  # matches every minute
    fire, mk = b._schedule_tick(dt(h=9, mi=0), None, cron)
    assert fire is True
    # a second poll in the SAME minute does not re-fire (last_minute == mk)
    fire2, mk2 = b._schedule_tick(dt(h=9, mi=0), mk, cron)
    assert fire2 is False and mk2 == mk
    # crossing into the next minute fires again
    fire3, mk3 = b._schedule_tick(dt(h=9, mi=1), mk, cron)
    assert fire3 is True and mk3 != mk
    # a non-matching cron never fires, even on a fresh minute
    fire4, _ = b._schedule_tick(dt(h=9, mi=0), None, "0 8 * * *")
    assert fire4 is False


def test_schedule_node_registered():
    spec = NODE_REGISTRY["core.trigger.schedule"]
    assert spec.kind.value == "trigger"
    assert [(p.name, p.type) for p in spec.outputs] == [("trigger", "event"), ("time", "text")]
