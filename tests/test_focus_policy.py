"""Focus invariant — the app-launch focus guard.

Eve must never take focus from a game / protected task unless explicitly asked.
`apps._capture_focus_guard()` decides whether a launch needs a focus-restore, and
`apps._restore_foreground()` hands focus back to the task if the launched app stole
it. Both are tested against fakes — no real windows, no real launch, no real sleep
delay (attempts=(0,)).

Run either way:
    pytest tests/
    python tests/test_focus_policy.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands import apps
from core import essential, window_ops, key_ops


# ── _capture_focus_guard: capture the task hwnd only when protected ───────────

def test_guard_captures_foreground_when_protected(monkeypatch):
    monkeypatch.setattr(essential, "should_defer", lambda: True)
    monkeypatch.setattr(window_ops, "foreground_hwnd", lambda: 4242)
    assert apps._capture_focus_guard() == 4242


def test_guard_is_zero_when_focus_is_free(monkeypatch):
    # No game / protected app in front → focus may move freely → no restore.
    monkeypatch.setattr(essential, "should_defer", lambda: False)
    monkeypatch.setattr(window_ops, "foreground_hwnd", lambda: 4242)
    assert apps._capture_focus_guard() == 0


# ── _restore_foreground: re-assert the task's focus if the app stole it ───────

def test_restore_refocuses_task_when_app_stole_focus(monkeypatch):
    calls = []
    monkeypatch.setattr(window_ops, "exists", lambda h: True)
    monkeypatch.setattr(window_ops, "foreground_hwnd", lambda: 999)   # app grabbed it
    monkeypatch.setattr(key_ops, "focus_window", lambda h: calls.append(h))
    apps._restore_foreground(4242, attempts=(0,))
    assert calls == [4242]                                            # game refocused


def test_restore_noop_when_task_already_foreground(monkeypatch):
    calls = []
    monkeypatch.setattr(window_ops, "exists", lambda h: True)
    monkeypatch.setattr(window_ops, "foreground_hwnd", lambda: 4242)  # never lost focus
    monkeypatch.setattr(key_ops, "focus_window", lambda h: calls.append(h))
    apps._restore_foreground(4242, attempts=(0,))
    assert calls == []                                               # nothing to fight


def test_restore_noop_when_task_window_gone(monkeypatch):
    calls = []
    monkeypatch.setattr(window_ops, "exists", lambda h: False)       # task closed
    monkeypatch.setattr(key_ops, "focus_window", lambda h: calls.append(h))
    apps._restore_foreground(4242, attempts=(0, 0))
    assert calls == []


# ── invariant wiring: launch guard is gated by game_protection ────────────────

def test_guard_off_when_game_protection_disabled(monkeypatch):
    # essential.should_defer() already returns False when game_protection is off;
    # prove the guard follows that (defense against a future regression).
    from core import features
    monkeypatch.setattr(features, "get", lambda k: False if k == "game_protection" else True)
    # active() -> None when protection off -> should_defer() False -> guard 0
    assert apps._capture_focus_guard() == 0


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
