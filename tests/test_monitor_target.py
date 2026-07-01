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


# ── ref resolution for designation ("set X as Eve's monitor") ─────────────────

# enumerate_work_areas() shape: {'x','y','w','h','is_primary'}
WA0 = {'x': 0,    'y': 0, 'w': 1920, 'h': 1040, 'is_primary': True}
WA1 = {'x': 1920, 'y': 0, 'w': 1920, 'h': 1040, 'is_primary': False}
WA2 = {'x': 3840, 'y': 0, 'w': 2560, 'h': 1440, 'is_primary': False}
MONS = [WA0, WA1, WA2]


def test_resolve_by_index():
    assert monitor._resolve_ref_to_monitor('2', MONS) is WA1


def test_resolve_by_position():
    assert monitor._resolve_ref_to_monitor('left', MONS) is WA0
    assert monitor._resolve_ref_to_monitor('right', MONS) is WA2
    assert monitor._resolve_ref_to_monitor('middle', MONS) is WA1


def test_resolve_primary():
    assert monitor._resolve_ref_to_monitor('primary', MONS) is WA0


def test_resolve_out_of_range_is_none():
    assert monitor._resolve_ref_to_monitor('9', MONS) is None


def test_describe_is_positional_and_sized():
    assert monitor._describe_monitor(WA2, MONS) == 'right monitor (2560×1440)'
    assert monitor._describe_monitor(WA0, MONS) == 'primary monitor (1920×1040)'


def test_set_eve_monitor_single_monitor_declines():
    ok, msg = monitor.set_eve_monitor('right', monitors=[WA0])
    assert ok is False and 'one monitor' in msg


# ── startup prompt: fire only on 3+ monitors when nothing is designated ───────

def test_companion_prompt_fires_on_3plus_undesignated(monkeypatch):
    monkeypatch.setattr(monitor, 'count', lambda: 3)
    monkeypatch.setattr(monitor, 'eve_monitor_designated', lambda: False)
    assert monitor.companion_prompt() is not None


def test_companion_prompt_silent_when_designated(monkeypatch):
    monkeypatch.setattr(monitor, 'count', lambda: 3)
    monkeypatch.setattr(monitor, 'eve_monitor_designated', lambda: True)
    assert monitor.companion_prompt() is None


def test_companion_prompt_silent_on_two_monitors(monkeypatch):
    # Two monitors is unambiguous (the non-game screen) — no designation needed.
    monkeypatch.setattr(monitor, 'count', lambda: 2)
    monkeypatch.setattr(monitor, 'eve_monitor_designated', lambda: False)
    assert monitor.companion_prompt() is None


# ── zero-dependency runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    class _MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._undo):
                setattr(obj, name, val)

    import inspect
    failed = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            mp = _MP()
            try:
                if "monkeypatch" in inspect.signature(v).parameters:
                    v(mp)
                else:
                    v()
                print(f"  PASS  {v.__name__}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {v.__name__}: {e}")
            except Exception as e:
                failed += 1
                print(f"  ERROR {v.__name__}: {type(e).__name__}: {e}")
            finally:
                mp.undo()
    total = sum(1 for k in globals() if k.startswith("test_"))
    print(f"\n{total - failed} passed, {failed} failed")
