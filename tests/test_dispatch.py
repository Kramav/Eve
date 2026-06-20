"""Routing tests for the dispatcher's INTENTS table + mode routing.

Characterizes which handler each spoken phrase maps to, WITHOUT executing the
handler (no windows open, no mic, no Electron). Guards the 30+ regex patterns
against silent breakage when intents are added or reordered.

Run either way:
    pytest tests/                # if pytest is installed
    python tests/test_dispatch.py   # zero-dependency fallback runner
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dispatcher as d
from core import features, session as S
from commands import (apps, search, system, youtube, windows as windows_cmd)


def route(text: str):
    """The handler dispatcher.INTENTS would fire for `text`, NOT executed.

    Mirrors dispatch()'s normalization (lowercase, strip trailing punctuation,
    strip wake prefix) and the _try_intents loop incl. feature gating, but
    returns the handler object instead of calling it."""
    text = text.strip().lower()
    text = re.sub(r"[.,!?]+$", "", text).strip()
    for prefix in d._WAKE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip(",. ")
            break
    for pattern, handler in d.INTENTS:
        if re.search(pattern, text):
            feat = d._HANDLER_FEATURE.get(handler)
            if feat and not features.get(feat):
                continue
            return handler
    return None


# ── System / media / panels ───────────────────────────────────────────────────

def test_system_and_media():
    assert route("mute") is system.toggle_mute
    assert route("volume up") is system.volume_up
    assert route("volume down") is system.volume_down
    assert route("what time is it") is system.get_time
    assert route("what is the date") is system.get_date
    assert route("take a screenshot") is system.screenshot


def test_panels_open_close():
    assert route("open app manager") is system.open_app_manager
    assert route("close app manager") is system.close_app_manager
    assert route("open window manager") is system.open_window_manager
    assert route("open voice settings") is system.open_voice_settings
    assert route("open command editor") is system.open_editor
    assert route("open api keys") is system.open_integrations


def test_directory_and_identify():
    assert route("show overlay") is system.show_directory
    assert route("open hud") is system.show_directory
    assert route("hide hud") is system.hide_directory
    assert route("identify monitors") is system.identify_monitors
    assert route("identify zones") is system.identify_zones
    assert route("what's open") is windows_cmd.identify_windows


# ── Apps ──────────────────────────────────────────────────────────────────────

def test_apps():
    assert route("open firefox") is apps.open_app
    assert route("close firefox") is apps.close_app


# ── YouTube: HUD default + search ────────────────────────────────────────────

def test_youtube_routing():
    assert route("browse youtube") is youtube.browse_feed_intent
    assert route("open the youtube feed") is youtube.browse_feed_intent
    assert route("play lofi beats") is youtube.play_or_search
    assert route("search youtube for jazz") is youtube.play_or_search


# ── Web search ────────────────────────────────────────────────────────────────

def test_web_search():
    assert route("search for puppies") is search.web_search_list
    assert route("go to github.com") is search.go_to_site


# ── Apps feature gate: disabling 'apps' skips open_app ───────────────────────

def test_feature_gate_skips_handler():
    prev = features.get("apps")
    try:
        features.set_feature("apps", False)
        # With apps off, "open firefox" must NOT route to apps.open_app.
        assert route("open firefox") is not apps.open_app
    finally:
        features.set_feature("apps", bool(prev))


# ── Mishear substitutions ─────────────────────────────────────────────────────

def test_mishear_subs():
    assert "hud" in d._apply_mishear_subs("show me the hood")
    assert "youtube" in d._apply_mishear_subs("open you tube")
    assert "app manager" in d._apply_mishear_subs("open at manager")
    # filler stripped
    assert d._apply_mishear_subs("please open firefox").strip() == "open firefox"


# ── BROWSING mode routing (YouTube HUD) ──────────────────────────────────────
# _dispatch_browsing executes feed handlers, but with no Display wired they are
# safe no-ops that return their spoken-confirmation strings.

def test_browsing_mode_commands():
    prev_mode = S.get().mode
    try:
        S.get().mode = S.Mode.BROWSING
        assert d._dispatch_browsing("scroll down") is not None
        assert d._dispatch_browsing("scroll up") is not None
        assert d._dispatch_browsing("show numbers") is not None
        assert d._dispatch_browsing("open video 3") is not None
        assert d._dispatch_browsing("search for synthwave") is not None
        # Unrelated text falls through (returns None) so normal dispatch can run.
        assert d._dispatch_browsing("what time is it") is None
    finally:
        S.get().mode = prev_mode


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
    total = len(tests)
    print(f"\n{total - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
