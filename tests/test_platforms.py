"""Tests for the streaming-platform search skill (skills/platforms.py).

No network / no browser: core.browser.open_url is monkeypatched to record the
URL. Covers query/platform extraction, the focus-safe open, leading-verb
stripping, unknown-platform decline, end-anchored false-positive rejection, and
custom-file overrides.

Run either way:  pytest tests/  |  python tests/test_platforms.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import browser
from skills import platforms


def _route(text):
    """(handler, query, platform) for the first matching intent, or (None,…)."""
    for pat, handler in platforms.INTENTS:
        m = re.search(pat, text)
        if m:
            return handler, m.group(1), m.group(2)
    return None, None, None


def _capture(fn):
    """Run fn with browser.open_url recording instead of opening a browser."""
    opened = []
    old = browser.open_url
    browser.open_url = lambda url: opened.append(url)
    try:
        fn(opened)
    finally:
        browser.open_url = old


def test_routes_and_opens_focus_safe_url():
    def body(opened):
        h, q, p = _route("look up stranger things on netflix")
        assert p == "netflix" and q == "look up stranger things"
        msg = h(q, p)
        assert opened == ["https://www.netflix.com/search?q=stranger+things"]
        assert msg == "Searching netflix for stranger things"
    _capture(body)


def test_multiword_platform_and_lead_strip():
    def body(opened):
        h, q, p = _route("the office on hbo max")
        assert p == "hbo max" and q == "the office"
        h(q, p)
        assert opened[0].startswith("https://play.max.com/search?q=the+office")
    _capture(body)


def test_unknown_platform_declines():
    # shape matches but platform unknown → None (falls through to dispatch)
    assert platforms.search_on_platform("stuff", "myspace") is None


def test_end_anchor_rejects_false_positives():
    for text in ("turn on max volume", "put the window on the left",
                 "play despacito on youtube", "snap firefox to the left",
                 "what's the weather"):
        h, _, _ = _route(text)
        assert h is None, text


def test_custom_file_overrides_and_adds():
    d = tempfile.mkdtemp()
    old_file = platforms._CUSTOM_FILE
    from pathlib import Path
    platforms._CUSTOM_FILE = Path(d) / "platform_searches.json"
    platforms._CUSTOM_FILE.write_text(json.dumps({
        "crunchyroll": "https://www.crunchyroll.com/search?q={q}",
        "netflix": "https://example.com/nf?q={q}",          # override default
    }))
    try:
        p = platforms._platforms()
        assert p["crunchyroll"].startswith("https://www.crunchyroll.com")
        assert p["netflix"] == "https://example.com/nf?q={q}"
        # bad entries (no {q}, non-str) are dropped
        platforms._CUSTOM_FILE.write_text(json.dumps({"x": "no-placeholder", "y": 5}))
        assert "x" not in platforms._custom() and "y" not in platforms._custom()
    finally:
        platforms._CUSTOM_FILE = old_file


def test_skill_loads_via_registry():
    from core import skills
    skills.load(display=None)
    assert "platforms" in skills.loaded_names()


# ── Zero-dependency runner ────────────────────────────────────────────────────

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
