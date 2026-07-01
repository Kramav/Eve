"""Chosen-browser primitive — resolution + config, against fakes.

No real browser launch, no window raise, no Win32: resolve_browser takes an
injectable finder, and configured_browser reads settings via a monkeypatched
_read_settings.

Run either way:
    pytest tests/
    python tests/test_browser.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import browser


# ── configured_browser: default + override ────────────────────────────────────

def test_default_is_firefox(monkeypatch):
    monkeypatch.setattr(browser, "_read_settings", lambda: {})
    assert browser.configured_browser() == "firefox"


def test_configured_override(monkeypatch):
    monkeypatch.setattr(browser, "_read_settings", lambda: {"browser": "Chrome"})
    assert browser.configured_browser() == "chrome"


# ── resolve_browser: spec / default / not-found ───────────────────────────────

def test_resolve_known_installed():
    spec = browser.resolve_browser("chrome", finder=lambda k: r"C:\chrome.exe")
    assert spec == {"key": "chrome", "path": r"C:\chrome.exe", "match": "chrome"}


def test_resolve_default_is_none():
    # 'default' → None → caller uses the OS default browser (no focus-safe raise).
    assert browser.resolve_browser("default", finder=lambda k: "x") is None


def test_resolve_known_but_not_installed_is_none():
    assert browser.resolve_browser("brave", finder=lambda k: None) is None


def test_resolve_unknown_name_is_none():
    assert browser.resolve_browser("netscape", finder=lambda k: "x") is None


def test_resolve_edge_uses_msedge_match():
    spec = browser.resolve_browser("edge", finder=lambda k: r"C:\msedge.exe")
    assert spec["match"] == "edge" and spec["key"] == "edge"


# ── set_browser: validation (no real write when it should reject) ─────────────

def test_set_browser_rejects_unknown(monkeypatch):
    ok, msg = browser.set_browser("netscape")
    assert ok is False and "don't recognize" in msg


def test_set_browser_rejects_uninstalled(monkeypatch):
    monkeypatch.setattr(browser, "_find_exe", lambda k: None)
    ok, msg = browser.set_browser("chrome")
    assert ok is False and "couldn't find" in msg


# ── voice handler routes + parses ─────────────────────────────────────────────

def test_voice_handler_parses_name(monkeypatch):
    from commands import search
    captured = {}
    monkeypatch.setattr(browser, "set_browser", lambda k: captured.update(key=k) or (True, k))
    # search imports core.browser lazily inside the handler, so patch there too.
    import core.browser as _b
    monkeypatch.setattr(_b, "set_browser", lambda k: captured.update(key=k) or (True, k))
    out = search.set_web_browser("use chrome as my browser")
    assert captured.get("key") == "chrome" and "chrome" in out.lower()


def test_voice_handler_asks_when_unclear(monkeypatch):
    from commands import search
    out = search.set_web_browser("set my browser to netscape")
    assert "which browser" in out.lower() or "know" in out.lower()


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
