"""Reminder re-arm logic — the recurring re-fire math behind the checker.

`reminders._advance(entry, now)` is the whole re-arm decision: the background
checker only fires a due reminder then either re-arms it (recurring → next
trigger strictly after `now`) or marks it done (one-shot → None). Because it
takes `now` as an argument, this is testable deterministically with a fixed
clock — no thread, no sleep, no fake mocks, and no touching the on-disk store.

Run either way:
    pytest tests/
    python tests/test_reminders.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands import reminders as R


def _entry(trigger: datetime, recurrence=None):
    return {"trigger": trigger.isoformat(timespec="seconds"),
            "fired": False, "recurrence": recurrence}


# ── One-shot: never re-arms ──────────────────────────────────────────────────

def test_oneshot_returns_none():
    now = datetime(2026, 6, 25, 12, 0)
    assert R._advance(_entry(now), None) is None
    assert R._advance(_entry(now, recurrence=None), now) is None


# ── Interval: steps forward past `now`, not just one hop ─────────────────────

def test_interval_advances_strictly_past_now():
    now = datetime(2026, 6, 25, 12, 0, 0)
    # trigger 65 min ago, 30-min interval → next must be > now (12:00),
    # which is 12:25 (10:55→11:25→11:55→12:25), the first slot after now.
    trig = datetime(2026, 6, 25, 10, 55, 0)
    nxt = R._advance(_entry(trig, {"kind": "interval", "seconds": 1800}), now)
    assert nxt > now
    assert nxt == datetime(2026, 6, 25, 12, 25, 0), nxt


# ── Daily: same time tomorrow once today's slot has passed ───────────────────

def test_daily_rolls_to_tomorrow_when_past():
    now = datetime(2026, 6, 25, 9, 0)            # 09:00, target 08:00
    nxt = R._advance(_entry(now, {"kind": "daily", "hour": 8, "minute": 0}), now)
    assert nxt == datetime(2026, 6, 26, 8, 0), nxt


def test_daily_stays_today_when_future():
    now = datetime(2026, 6, 25, 7, 0)            # 07:00, target 08:00 today
    nxt = R._advance(_entry(now, {"kind": "daily", "hour": 8, "minute": 0}), now)
    assert nxt == datetime(2026, 6, 25, 8, 0), nxt


# ── Weekly: next matching weekday, never <= now ──────────────────────────────

def test_weekly_picks_next_matching_weekday():
    # 2026-06-25 is a Thursday (weekday 3). Reminder fires Mon(0)+Fri(4) at 09:00.
    now = datetime(2026, 6, 25, 10, 0)
    rec = {"kind": "weekly", "hour": 9, "minute": 0, "weekdays": [0, 4]}
    nxt = R._advance(_entry(now, rec), now)
    # Next Friday is 2026-06-26 (tomorrow), before next Monday.
    assert nxt == datetime(2026, 6, 26, 9, 0), nxt
    assert nxt.weekday() == 4


def test_weekly_same_day_but_time_passed_rolls_a_week():
    # Thursday-only reminder at 08:00, but it's already 10:00 Thursday.
    now = datetime(2026, 6, 25, 10, 0)
    rec = {"kind": "weekly", "hour": 8, "minute": 0, "weekdays": [3]}
    nxt = R._advance(_entry(now, rec), now)
    assert nxt == datetime(2026, 7, 2, 8, 0), nxt   # next Thursday
    assert nxt > now


# ── Labels round-trip (speech + UI strings) ─────────────────────────────────

def test_recurrence_labels():
    assert R._recurrence_label({"kind": "interval", "seconds": 1800}) == "every 30 minutes"
    assert R._recurrence_label({"kind": "interval", "seconds": 3600}) == "every hour"
    assert "every day at" in R._recurrence_label({"kind": "daily", "hour": 7, "minute": 0})
    assert "weekday" in R._recurrence_label(
        {"kind": "weekly", "hour": 7, "minute": 0, "weekdays": [0, 1, 2, 3, 4]})


# ── Zero-dependency runner ───────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    total = len(tests)
    print(f"\n{total - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
