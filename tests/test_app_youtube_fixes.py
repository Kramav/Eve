"""Regression tests for two daily-drive bugs (2026-07-11):

  1. "close task manager" reported success while Task Manager kept running —
     the name resolved to the phantom 'task manager.exe', so the taskkill hit
     nothing and `_count_proc == 0` read as a false success. Fixed by the
     _CLOSE_MAP entry (→ Taskmgr.exe) and an honest "isn't running" pre-check.
  2. "open youtube" opened the plain browser (no HUD, no numbered list, no
     session), so "open video 2" fell through to open_app → "Unknown app".
     Fixed by routing the general youtube phrasings to the HUD feed intent.

Run either way:  pytest tests/  |  python tests/test_app_youtube_fixes.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands import apps
from skills import youtube


# ── YouTube routing — mirror skills._run: first re.search match wins ─────────

def _route(text):
    for pat, handler in youtube.INTENTS:
        if re.search(pat, text):
            return handler
    return None


def test_open_youtube_goes_to_hud_feed():
    for phrase in ("open youtube", "browse youtube", "help me browse youtube",
                   "show me youtube", "open the youtube feed", "youtube"):
        assert _route(phrase) is youtube.browse_feed_intent, phrase


def test_browser_escape_hatch():
    for phrase in ("open youtube in my browser", "open youtube homepage",
                   "show youtube in the browser"):
        assert _route(phrase) is youtube.browse_home_intent, phrase


def test_search_and_play_still_route():
    assert _route("search youtube for lofi") is youtube.play_or_search
    assert _route("play never gonna give you up") is youtube.play_or_search
    assert _route("can you play despacito") is youtube.play_or_search
    # "display" must NOT trigger play
    assert _route("name monitor 2 primary display") is None


def test_play_only_matches_at_command_start():
    # "play"/"watch" buried mid-sentence must NOT pull up YouTube — the reported
    # bug: "look up a guide to play spiderman" searched the feed for "spiderman".
    assert _route("look up a guide to play spiderman") is not youtube.play_or_search
    assert _route("how do i learn to play guitar") is not youtube.play_or_search
    assert _route("find a walkthrough to watch later") is not youtube.play_or_search


def test_on_youtube_trailing_marker():
    # Explicit trailing platform marker routes to YouTube and captures the query.
    def _q(text):
        for pat, handler in youtube.INTENTS:
            m = re.search(pat, text)
            if m:
                return handler, (m.group(1) if m.groups() else None)
        return None, None
    h, q = _q("look up a spiderman guide on youtube")
    assert h is youtube.play_or_search and q == "a spiderman guide"
    h, q = _q("despacito on youtube")
    assert h is youtube.play_or_search and q == "despacito"
    # bare "turn on youtube" opens the feed, doesn't search for "turn"
    assert _route("turn on youtube") is youtube.browse_feed_intent
    # "on netflix" is NOT youtube's job — falls through (see platform search)
    assert _route("look up stranger things on netflix") is None


def test_open_video_number_matches_feed_converse():
    # With the session armed, "open video 2" must resolve inside the feed
    # converse (→ feed_open) rather than escaping to open_app.
    youtube._state = "feed"
    try:
        m = re.search(r"(?:open|play|watch|select|pick)\s+(?:video\s+|number\s+)?(\d+)",
                      "open video 2")
        assert m and m.group(1) == "2"
    finally:
        youtube._state = None


# ── close_app / kill_app honesty ─────────────────────────────────────────────

def test_task_manager_resolves_to_real_exe():
    assert apps._resolve_close_exe("task manager") == "Taskmgr.exe"
    assert apps._resolve_close_exe("taskmgr") == "Taskmgr.exe"


def test_close_not_running_is_honest_not_false_success():
    old_count, old_names = apps._count_proc, apps._running_image_names
    apps._count_proc = lambda exe: 0            # nothing running under that name
    apps._running_image_names = lambda: ["explorer.exe"]   # no fuzzy match → plain
    try:
        assert apps.close_app("task manager") == "task manager isn't running."
        assert apps.kill_app("chrome") == "chrome isn't running."
    finally:
        apps._count_proc, apps._running_image_names = old_count, old_names


def test_close_running_app_attempts_and_verifies():
    from core.response import Verified
    calls = {"kill": 0}
    old_count, old_run = apps._count_proc, apps.subprocess.run
    apps._count_proc = lambda exe: 1            # it IS running
    apps.subprocess.run = lambda *a, **k: calls.__setitem__("kill", calls["kill"] + 1)
    try:
        r = apps.close_app("task manager")
        assert isinstance(r, Verified)          # optimistic + real side-effect check
        assert calls["kill"] == 1               # taskkill actually invoked
    finally:
        apps._count_proc, apps.subprocess.run = old_count, old_run


# ── "did you mean X?" suggestion for close/kill ──────────────────────────────

def test_suggest_running_fuzzy_matches_and_blocks_system():
    old = apps._running_image_names
    apps._running_image_names = lambda: ["chrome.exe", "Discord.exe", "explorer.exe"]
    try:
        assert apps._suggest_running("chrom") == "chrome"      # mishear → chrome
        assert apps._suggest_running("discord") == "Discord"   # casing preserved
        assert apps._suggest_running("explorer") is None       # blocklisted
        assert apps._suggest_running("wildly different xyz") is None
    finally:
        apps._running_image_names = old


def test_close_not_running_offers_did_you_mean_needconfirm():
    from core.conversation import NeedConfirm
    old_count, old_names, old_run = (apps._count_proc, apps._running_image_names,
                                     apps.subprocess.run)
    apps._count_proc = lambda exe: 0            # spoken target isn't running
    apps._running_image_names = lambda: ["chrome.exe", "explorer.exe"]
    try:
        r = apps.close_app("chrom")            # mishear → NeedConfirm did-you-mean
        assert isinstance(r, NeedConfirm)
        assert "Did you mean chrome?" in r.prompt
        # the confirmed action runs the real close on the suggested name
        calls = {"kill": 0}
        apps._count_proc = lambda exe: 1        # chrome IS running now
        apps.subprocess.run = lambda *a, **k: calls.__setitem__("kill", calls["kill"] + 1)
        from core.response import Verified
        out = r.action()
        assert isinstance(out, Verified) and calls["kill"] == 1
    finally:
        apps._count_proc, apps._running_image_names, apps.subprocess.run = (
            old_count, old_names, old_run)


def test_no_suggestion_falls_back_to_plain():
    old_count, old_names = apps._count_proc, apps._running_image_names
    apps._count_proc = lambda exe: 0
    apps._running_image_names = lambda: ["explorer.exe", "svchost.exe"]  # nothing close
    try:
        assert apps.close_app("photoshop") == "photoshop isn't running."
    finally:
        apps._count_proc, apps._running_image_names = old_count, old_names


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
