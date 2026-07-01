"""Window quick-actions skill — routing + handler behavior.

Deterministic: routing tests just match regexes; handler tests run against a
fake desktop injected over core.window_ops / core.key_ops, so nothing touches a
real window or sends a real hotkey.

Run either way:
    pytest tests/
    python tests/test_window_actions.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills import window_actions as wa
from core import window_ops, key_ops
from core.response import Verified


# ── routing helper (mirrors skills._run first-match order) ────────────────────

def _route(text: str):
    for pat, handler in wa.INTENTS:
        if re.search(pat, text):
            return handler
    return None


# ── fake desktop injected over the OS helpers ─────────────────────────────────

class _Desktop:
    def __init__(self):
        self.win = {1: {"title": "My Doc — Editor", "min": False,
                        "max": False, "top": False, "alive": True}}
        self.fg = 1
        self.keys = []

    def install(self, mp):
        mp.setattr(window_ops, "foreground_hwnd", lambda: self.fg)
        mp.setattr(window_ops, "exists", lambda h: self.win.get(h, {}).get("alive", False))
        mp.setattr(window_ops, "window_title", lambda h: self.win.get(h, {}).get("title", ""))
        mp.setattr(window_ops, "is_minimized", lambda h: self.win.get(h, {}).get("min", False))
        mp.setattr(window_ops, "is_maximized", lambda h: self.win.get(h, {}).get("max", False))
        mp.setattr(window_ops, "is_topmost",   lambda h: self.win.get(h, {}).get("top", False))
        mp.setattr(window_ops, "minimize", lambda h: self.win[h].update(min=True, max=False) or True)
        mp.setattr(window_ops, "maximize", lambda h: self.win[h].update(max=True, min=False) or True)
        mp.setattr(window_ops, "restore",  lambda h: self.win[h].update(max=False, min=False) or True)
        mp.setattr(window_ops, "set_topmost", lambda h, on: self.win[h].update(top=bool(on)) or True)
        mp.setattr(window_ops, "close_window", lambda h: self.win[h].update(alive=False) or True)
        mp.setattr(key_ops, "press_global", lambda combo: self.keys.append(combo))


# ── routing ───────────────────────────────────────────────────────────────────

def test_this_window_routing():
    assert _route("minimize this") is wa._minimize_this
    assert _route("minimize this window") is wa._minimize_this
    assert _route("maximize this window") is wa._maximize_this
    assert _route("restore this") is wa._restore_this
    assert _route("unmaximize this window") is wa._restore_this
    assert _route("close this window") is wa._close_this
    assert _route("close this") is wa._close_this


def test_desktop_wide_routing():
    assert _route("minimize all") is wa._minimize_all
    assert _route("minimize everything") is wa._minimize_all
    assert _route("show desktop") is wa._show_desktop
    assert _route("bring my windows back") is wa._restore_all
    assert _route("restore all windows") is wa._restore_all
    # "minimize all" must NOT be read as the this-window minimize
    assert _route("minimize all") is not wa._minimize_this


def test_always_on_top_routing():
    assert _route("always on top") is wa._pin_on_top
    assert _route("pin this on top") is wa._pin_on_top
    assert _route("keep this on top") is wa._pin_on_top
    assert _route("unpin this") is wa._unpin_on_top
    assert _route("stop always on top") is wa._unpin_on_top


def test_does_not_shadow_core_or_apps():
    # Pronoun follow-ups stay with the core handler (session.last_action).
    assert _route("close that window") is None
    assert _route("close it") is None
    # The generic app launcher/closer keeps "close <app>".
    assert _route("close firefox") is None
    assert _route("open firefox") is None
    assert _route("minimize firefox") is None


# ── handler behavior (against the fake desktop) ───────────────────────────────

def test_minimize_returns_verified_and_confirms(monkeypatch):
    d = _Desktop(); d.install(monkeypatch)
    r = wa._minimize_this()
    assert isinstance(r, Verified)
    assert "Minimized" in r and "My Doc" in r     # short name from the title
    assert r.check() is True                       # state now minimized
    assert d.win[1]["min"] is True


def test_maximize_and_restore(monkeypatch):
    d = _Desktop(); d.install(monkeypatch)
    assert wa._maximize_this().check() is True
    assert d.win[1]["max"] is True
    # restore clears both min and max
    assert wa._restore_this().check() is True
    assert d.win[1]["max"] is False and d.win[1]["min"] is False


def test_pin_toggles_and_unpin_clears(monkeypatch):
    d = _Desktop(); d.install(monkeypatch)
    r1 = wa._pin_on_top()
    assert d.win[1]["top"] is True and r1.check() is True
    assert "pinned on top" in r1
    # toggling again turns it back off
    r2 = wa._pin_on_top()
    assert d.win[1]["top"] is False and r2.check() is True
    # explicit unpin is idempotent-off
    d.win[1]["top"] = True
    wa._unpin_on_top()
    assert d.win[1]["top"] is False


def test_close_returns_verified(monkeypatch):
    d = _Desktop(); d.install(monkeypatch)
    r = wa._close_this()
    assert isinstance(r, Verified)
    assert r.check() is True                        # window gone
    assert d.win[1]["alive"] is False


def test_no_active_window_is_graceful(monkeypatch):
    d = _Desktop(); d.fg = 0; d.install(monkeypatch)
    for fn in (wa._minimize_this, wa._maximize_this, wa._restore_this,
               wa._pin_on_top, wa._unpin_on_top, wa._close_this):
        msg = fn()
        assert "don't see an active window" in msg
        assert not isinstance(msg, Verified)


def test_desktop_actions_send_hotkeys(monkeypatch):
    d = _Desktop(); d.install(monkeypatch)
    wa._minimize_all(); wa._restore_all(); wa._show_desktop()
    assert d.keys == ["win+m", "win+shift+m", "win+d"]


# ── wiring ─────────────────────────────────────────────────────────────────────

def test_skill_is_preempt_and_loads():
    assert wa.PREEMPT is True
    from core import skills
    assert "window_actions" in skills.load(display=None)


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
