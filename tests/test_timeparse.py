"""Execution tests for core.timeparse — the natural-language time parser behind
voice reminders. Pure + deterministic via an injected `now`, so no mocks.

Run: pytest tests/  ·  or  python tests/test_timeparse.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import timeparse as tp

# Fixed reference: Wed 2026-06-24 10:00 (weekday() == 2).
NOW = datetime(2026, 6, 24, 10, 0, 0)


def test_relative_minutes():
    w = tp.parse_when("in 5 minutes", NOW)
    assert w["recurrence"] is None
    assert (w["trigger"] - NOW).total_seconds() == 300


def test_absolute_pm():
    w = tp.parse_when("at 3pm", NOW)
    assert w["trigger"].hour == 15 and w["trigger"].minute == 0
    assert w["recurrence"] is None


def test_bare_small_hour_assumes_pm():
    # "at 3" with no am/pm → 3 PM per the daytime heuristic.
    assert tp.parse_when("at 3", NOW)["trigger"].hour == 15
    # but 8 is past the heuristic window → stays 8 AM.
    assert tp.parse_when("at 8", NOW)["trigger"].hour == 8


def test_tomorrow_defaults_to_9am():
    w = tp.parse_when("tomorrow", NOW)
    assert w["trigger"].hour == 9 and w["trigger"].day == 25


def test_recurring_interval():
    assert tp.parse_when("every 30 minutes", NOW)["recurrence"] == {
        "kind": "interval", "seconds": 1800}


def test_recurring_weekday():
    rec = tp.parse_when("every weekday at 7am", NOW)["recurrence"]
    assert rec["kind"] == "weekly"
    assert rec["weekdays"] == [0, 1, 2, 3, 4]
    assert rec["hour"] == 7 and rec["minute"] == 0


def test_recurring_every_morning():
    rec = tp.parse_when("every morning", NOW)["recurrence"]
    assert rec["kind"] == "daily" and rec["hour"] == 8


def test_weekday_one_off():
    w = tp.parse_when("on monday at 9am", NOW)
    assert w["recurrence"] is None
    assert w["trigger"].weekday() == 0 and w["trigger"].hour == 9


def test_split_separates_task_from_time():
    msg, when = tp.split("call mom at 3pm", NOW)
    assert msg == "call mom"
    assert when["trigger"].hour == 15


def test_split_no_time_phrase():
    msg, when = tp.split("check the oven", NOW)
    assert msg == "check the oven" and when is None


def test_garbage_returns_none():
    assert tp.parse_when("banana", NOW) is None
    assert tp.parse_when("", NOW) is None


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
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
