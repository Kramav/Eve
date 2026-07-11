"""Tests for the LLM fallback (commands/fallback.py, OpenAI protocol) and the
learned-intents loop (core/intent_learning.py LearnedStore).

Covers: phrase normalization, arg→pattern templating, capture/dedupe, the
exact-vs-trusted-pattern match ladder, the class-based destructive gate,
OpenAI response parsing (tool_calls / content / garbage), the capture hook on
verified tool success, and the learned tier's execute+record path. No live
LLM server anywhere — HTTP is monkeypatched.

Run either way:
    pytest tests/
    python tests/test_fallback.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core import intent_learning
from core.intent_learning import (LearnedStore, make_template, normalize,
                                  DESTRUCTIVE_TOOLS, TRUST_CONFIDENCE)
from commands import fallback


def _tmp():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)          # start absent; the store must tolerate a missing file
    return path


def _store():
    return LearnedStore(_tmp())


# ── normalize ────────────────────────────────────────────────────────────────

def test_normalize():
    assert normalize("  Throw  FIREFOX on my left screen!  ") == "throw firefox on my left screen"
    assert normalize("") == ""
    assert normalize(None) == ""


# ── make_template ────────────────────────────────────────────────────────────

def test_template_round_trips():
    import re
    phrase = normalize("throw firefox on my left screen")
    pat = make_template(phrase, {"app": "firefox", "zone": "left"})
    assert pat is not None
    m = re.fullmatch(pat, "throw chrome on my right screen")
    assert m and m.group("app") == "chrome" and m.group("zone") == "right"


def test_template_none_when_value_absent_or_ambiguous():
    # value not in the phrase verbatim
    assert make_template("open the browser", {"name": "firefox"}) is None
    # value appears twice → ambiguous span
    assert make_template("move left to the left", {"zone": "left"}) is None
    # nothing to generalize
    assert make_template("do the thing", {}) is None


def test_template_optional_none_arg_ok():
    pat = make_template(normalize("throw firefox on my left screen"),
                        {"app": "firefox", "zone": "left", "monitor": None})
    assert pat is not None


# ── LearnedStore: capture / match / trust ladder ─────────────────────────────

def test_capture_creates_and_dedupes():
    st = _store()
    e1 = st.capture("Throw Firefox on my LEFT screen", "snap_window",
                    {"app": "firefox", "zone": "left"})
    assert e1["s"] == 1 and e1["pattern"] is not None
    e2 = st.capture("throw firefox on my left screen", "snap_window",
                    {"app": "firefox", "zone": "left"})
    assert e2 is e1 and e1["s"] == 2 and len(st.entries) == 1
    # persists across reload
    st2 = LearnedStore(st.path)
    assert len(st2.entries) == 1 and st2.entries[0]["s"] == 2


def test_exact_match_serves_immediately_pattern_waits_for_trust():
    st = _store()
    st.capture("throw firefox on my left screen", "snap_window",
               {"app": "firefox", "zone": "left"})
    # exact phrase: served at one success
    hit = st.match("throw firefox on my left screen")
    assert hit and hit[1] == {"app": "firefox", "zone": "left"}
    # generalized phrase: NOT served yet (confidence below trust bar)
    assert st.match("throw chrome on my right screen") is None
    # two more verified successes → trusted → pattern serves with fresh args
    entry = st.entries[0]
    st.record(entry, True); st.record(entry, True)
    assert st.confidence(entry) >= TRUST_CONFIDENCE
    hit = st.match("throw chrome on my right screen")
    assert hit and hit[1]["app"] == "chrome" and hit[1]["zone"] == "right"


def test_destructive_never_served_but_still_captured():
    st = _store()
    e = st.capture("nuke chrome", "close_app", {"name": "chrome"})
    for _ in range(10):
        st.record(e, True)                      # evidence piles up…
    assert st.match("nuke chrome") is None      # …but class safety wins
    assert "close_app" in DESTRUCTIVE_TOOLS


def test_failures_drop_confidence_and_stamp():
    st = _store()
    e = st.capture("throw firefox on my left screen", "snap_window",
                   {"app": "firefox", "zone": "left"})
    st.record(e, True); st.record(e, True)      # trusted (3s/0f)
    assert st.match("throw chrome on my right screen") is not None
    st.record(e, False); st.record(e, False); st.record(e, False)
    assert e["f"] == 3 and e.get("last_failure")
    assert st.confidence(e) < TRUST_CONFIDENCE  # pattern serving revoked
    assert st.match("throw chrome on my right screen") is None
    # exact phrase still serves (same phrase = same meaning; failures are
    # execution trouble, not interpretation doubt)
    assert st.match("throw firefox on my left screen") is not None


# ── fallback: OpenAI response parsing + capture hook ─────────────────────────

def _with(monkey_post, learned_store, fn):
    """Run fn() with fallback._post and the learned singleton swapped."""
    old_post, old_learned = fallback._post, intent_learning._learned
    old_mode = config.FALLBACK_LLM
    fallback._post = monkey_post
    intent_learning._learned = learned_store
    config.FALLBACK_LLM = "local"
    try:
        return fn()
    finally:
        fallback._post = old_post
        intent_learning._learned = old_learned
        config.FALLBACK_LLM = old_mode


def test_off_switch_short_circuits():
    old = config.FALLBACK_LLM
    config.FALLBACK_LLM = "none"
    try:
        assert fallback.answer("anything") is None
    finally:
        config.FALLBACK_LLM = old


def test_tool_call_executes_and_captures():
    st = _store()
    calls = []
    old = fallback._TOOL_HANDLERS.get("snap_window")
    fallback._TOOL_HANDLERS["snap_window"] = (
        lambda a: calls.append(a) or "Snapped firefox to left.", None)
    resp = {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "snap_window",
        "arguments": '{"app": "firefox", "zone": "left"}'}}]}}]}
    try:
        out = _with(lambda body: resp, st, lambda: fallback.answer(
            "throw firefox on my left screen"))
    finally:
        fallback._TOOL_HANDLERS["snap_window"] = old
    assert out == "Snapped firefox to left."
    assert calls == [{"app": "firefox", "zone": "left"}]
    # verified success was captured as a learned candidate
    assert len(st.entries) == 1
    assert st.entries[0]["phrase"] == "throw firefox on my left screen"
    assert st.entries[0]["tool"] == "snap_window"


def test_content_answer_and_garbage():
    st = _store()
    resp = {"choices": [{"message": {"content": "Paris is the capital of France."}}]}
    assert _with(lambda b: resp, st, lambda: fallback.answer("capital of france")) \
        == "Paris is the capital of France."
    assert len(st.entries) == 0                 # plain answers never captured
    # server down / garbage → None, never raises
    assert _with(lambda b: None, st, lambda: fallback.answer("hm")) is None
    assert _with(lambda b: {"weird": 1}, st, lambda: fallback.answer("hm")) is None


def test_learned_answer_executes_and_records():
    st = _store()
    st.capture("throw firefox on my left screen", "snap_window",
               {"app": "firefox", "zone": "left"})
    calls = []
    old = fallback._TOOL_HANDLERS.get("snap_window")
    fallback._TOOL_HANDLERS["snap_window"] = (
        lambda a: calls.append(a) or "Snapped.", None)
    old_learned = intent_learning._learned
    intent_learning._learned = st
    try:
        out = fallback.learned_answer("throw firefox on my left screen")
    finally:
        fallback._TOOL_HANDLERS["snap_window"] = old
        intent_learning._learned = old_learned
    assert out == "Snapped."
    assert calls and st.entries[0]["s"] == 2    # capture(1) + verified exec(1)
    # no learned match → None (falls through to the LLM tier)
    assert fallback.learned_answer("completely unknown gibberish") is None


def test_tool_registry_consistent():
    assert set(fallback._TOOL_HANDLERS) == {t["function"]["name"] for t in fallback._TOOLS}


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
