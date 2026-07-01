"""Companion-monitor selection — where an opened window goes.

Eve strongly recommends 2+ monitors so opened windows land on whichever screen
the game ISN'T on (never behind it). `monitor._select_target_monitor` is the pure
policy; tested here with no Win32.

Run either way:
    pytest tests/
    python tests/test_monitor_target.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import monitor

M0 = {'rect': (0, 0, 1920, 1040),      'is_primary': True}    # primary
M1 = {'rect': (1920, 0, 3840, 1040),   'is_primary': False}   # secondary
M2 = {'rect': (3840, 0, 5760, 1040),   'is_primary': False}   # tertiary


def test_single_monitor_returns_none():
    # Nowhere to put it but behind the game → caller keeps it backgrounded.
    assert monitor._select_target_monitor([M0], avoid_index=0) is None


def test_two_monitors_game_on_primary_uses_secondary():
    assert monitor._select_target_monitor([M0, M1], avoid_index=0) == M1['rect']


def test_two_monitors_game_on_secondary_uses_primary():
    # Adapts to whichever screen the game is on — the win over a fixed "monitor 2".
    assert monitor._select_target_monitor([M0, M1], avoid_index=1) == M0['rect']


def test_three_monitors_prefers_primary_companion():
    # Game on the tertiary → prefer the primary as the companion (main screen).
    assert monitor._select_target_monitor([M0, M1, M2], avoid_index=2) == M0['rect']


def test_three_monitors_game_on_primary_falls_to_first_other():
    # Game on primary → no primary candidate → first other monitor.
    assert monitor._select_target_monitor([M0, M1, M2], avoid_index=0) == M1['rect']


def test_no_foreground_match_still_picks_a_companion():
    # avoid_index None (foreground monitor not identified) → still returns a screen.
    assert monitor._select_target_monitor([M0, M1], avoid_index=None) == M0['rect']


# ── designated "Eve monitor" (companion_rect) ─────────────────────────────────

# companion rects are (x, y, w, h); M1 is at x=1920, M2 at x=3840.
COMP_M1 = (1920, 0, 1920, 1040)
COMP_M2 = (3840, 0, 1920, 1040)


def test_designated_eve_monitor_wins_over_primary():
    # Game on primary (M0); Eve monitor designated as M2 → windows go to M2,
    # NOT the primary the auto policy would otherwise prefer.
    got = monitor._select_target_monitor([M0, M1, M2], avoid_index=0, companion_rect=COMP_M2)
    assert got == M2['rect']


def test_designated_eve_monitor_falls_back_when_game_is_on_it():
    # Eve monitor designated as M1, but the game is running on M1 → don't place
    # behind the game; fall back to auto (primary M0).
    got = monitor._select_target_monitor([M0, M1, M2], avoid_index=1, companion_rect=COMP_M1)
    assert got == M0['rect']


def test_designated_eve_monitor_ignored_on_single_monitor():
    assert monitor._select_target_monitor([M0], avoid_index=0, companion_rect=COMP_M2) is None


# ── zero-dependency runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    failed = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            try:
                v()
                print(f"  PASS  {v.__name__}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {v.__name__}: {e}")
            except Exception as e:
                failed += 1
                print(f"  ERROR {v.__name__}: {type(e).__name__}: {e}")
    total = sum(1 for k in globals() if k.startswith("test_"))
    print(f"\n{total - failed} passed, {failed} failed")
