"""Tests for the Conversation Engine (core/conversation.py), Phase 1.

The engine is pure logic — a fake router + fake engaged_signal drive it with no
audio. Covers: the grace window after a reply, the legacy pending_confirm/
converse bridge (answer without a wake word), extension phrases ("hold on")
preserving the pending turn, cancel, silence timeout, the structured Outcome
types, and multi-turn continuation.

Run either way:  pytest tests/  |  python tests/test_conversation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.conversation import (ConversationEngine, State, UserTurn, SilenceTimeout,
                               Done, NeedConfirm, NeedClarify, NeedSlot, Failed)
from core.response import Panel


class _Router:
    """Records (text, followup) calls and returns a scripted result."""
    def __init__(self, result="Done"):
        self.calls = []          # text only (back-compat with existing asserts)
        self.followups = []      # the followup flag per call
        self.result = result

    def __call__(self, text, followup=False):
        self.calls.append(text)
        self.followups.append(followup)
        return self.result


def _engine(result="Done", engaged=False, **kw):
    r = _Router(result)
    opts = {"followup_ttl": 6, "awaiting_ttl": 12, "extend_by": 20, **kw}
    eng = ConversationEngine(router=r, engaged_signal=lambda: engaged, **opts)
    return eng, r


# ── grace window after a plain reply ─────────────────────────────────────────

def test_plain_reply_opens_grace_window():
    eng, r = _engine("It's 3 PM.")
    step = eng.handle(UserTurn("what time is it"))
    assert step.response == "It's 3 PM." and step.listen and step.ttl == 6
    assert eng.state is State.FOLLOWUP_ACTIVE and eng.engaged()
    assert r.calls == ["what time is it"]


def test_grace_window_disabled_when_ttl_zero():
    eng, r = _engine("Done", followup_ttl=0)
    step = eng.handle(UserTurn("volume up"))
    assert step.response == "Done" and step.listen is False
    assert not eng.engaged()


def test_multi_turn_continuation_no_wake_word():
    eng, r = _engine("Turned off the TV.")
    eng.handle(UserTurn("turn off the tv"))
    # a continuation routes straight through — engine stays the turn loop
    r.result = "Turned the TV back on."
    step = eng.handle(UserTurn("actually turn it back on"))
    assert step.response == "Turned the TV back on."
    assert r.calls == ["turn off the tv", "actually turn it back on"]


def test_wake_turn_allows_llm_followup_gates_it():
    # The wake turn routes with followup=False (LLM allowed); the grace-window
    # continuation routes with followup=True (LLM gated off).
    eng, r = _engine("It's 3 PM.")
    eng.handle(UserTurn("what time is it"))
    r.result = "And the date."
    eng.handle(UserTurn("what about the date"))
    assert r.followups == [False, True]


def test_unmatched_followup_ends_conversation():
    # A follow-up that routes to nothing (router → None, mirroring a gated LLM)
    # ends the conversation instead of re-opening the mic on noise.
    eng, r = _engine("It's 3 PM.")
    eng.handle(UserTurn("what time is it"))
    assert eng.engaged()
    r.result = None                       # gated follow-up, no match
    step = eng.handle(UserTurn("some ambient noise"))
    assert step.listen is False and eng.state is State.IDLE


# ── legacy bridge: pending_confirm / converse answered without a wake word ───

def test_pending_confirm_bridge_keeps_listening():
    eng, r = _engine("Did you mean chrome?", engaged=True)   # dispatch set pending_confirm
    step = eng.handle(UserTurn("close chrom"))
    assert step.listen and step.ttl == 12
    assert eng.state is State.AWAITING_CONFIRMATION
    # the "yes" answer routes to dispatch (which resolves pending_confirm)
    step = eng.handle(UserTurn("yes"))
    assert r.calls[-1] == "yes"


# ── extension phrases (honored only while engaged, before routing) ───────────

def test_extension_phrase_extends_and_does_not_route():
    eng, r = _engine("Which one — upstairs or downstairs?", engaged=True)
    eng.handle(UserTurn("turn on the bedroom lights"))
    assert eng.engaged()
    before = eng._deadline
    for phrase in ("hold on", "one moment", "give me a second", "i'm thinking",
                   "let me check", "standby", "wait"):
        r.calls.clear()
        step = eng.handle(UserTurn(phrase))
        assert step.say and step.listen and step.ttl == 12, phrase
        assert r.calls == [], f"{phrase!r} must not route"   # pending turn preserved
        assert eng.engaged()
    assert eng._deadline >= before                            # deadline pushed out


def test_extension_phrase_ignored_when_not_engaged():
    eng, r = _engine("Done")
    step = eng.handle(UserTurn("hold on"))          # cold — no active conversation
    assert r.calls == ["hold on"]                   # routed normally, not intercepted


# ── cancel ───────────────────────────────────────────────────────────────────

def test_cancel_ends_conversation():
    eng, r = _engine("Which one?", engaged=True)
    eng.handle(UserTurn("turn on the lights"))
    step = eng.handle(UserTurn("never mind"))
    assert step.say and step.listen is False
    assert eng.state is State.IDLE and not eng.engaged()


# ── silence timeout ──────────────────────────────────────────────────────────

def test_silence_timeout_returns_to_idle():
    eng, r = _engine("Which one?", engaged=True)
    eng.handle(UserTurn("turn on the lights"))
    assert eng.engaged()
    step = eng.handle(SilenceTimeout())
    assert step.response is None and step.say is None and step.listen is False
    assert eng.state is State.IDLE


# ── structured Outcomes (the forward contract) ───────────────────────────────

def test_outcomes_speak_prompt_and_set_awaiting_states():
    for outcome, want, prompt in [
        (NeedConfirm(action=lambda: "x", prompt="sure?"), State.AWAITING_CONFIRMATION, "sure?"),
        (NeedClarify("which?", [("a", None)]),  State.AWAITING_CLARIFICATION, "which?"),
        (NeedSlot("minutes", "how long?"),      State.AWAITING_SLOT,          "how long?"),
        (Failed("device unreachable", [("retry", None)]), State.RETRY_PENDING, "device unreachable"),
    ]:
        eng, r = _engine(outcome)
        step = eng.handle(UserTurn("do the thing"))
        assert step.say == prompt and step.listen and step.ttl == 12   # prompt SPOKEN
        assert step.response is None and eng.state is want


def test_done_outcome_grace_window():
    eng, r = _engine(Done("okay"))
    step = eng.handle(UserTurn("do it"))
    assert step.listen and eng.state is State.FOLLOWUP_ACTIVE


# ── engine owns confirmation / clarification resolution (Phase 2) ────────────

def test_confirm_yes_runs_action():
    ran = []
    eng, r = _engine(NeedConfirm(action=lambda: ran.append(1) or "Closed chrome.",
                                 prompt="Did you mean chrome?"))
    step = eng.handle(UserTurn("close chrom"))
    assert step.say == "Did you mean chrome?" and eng.state is State.AWAITING_CONFIRMATION
    step = eng.handle(UserTurn("yes"))
    assert ran == [1] and step.response == "Closed chrome."
    assert eng.state is State.FOLLOWUP_ACTIVE           # action's reply → grace window


def test_confirm_no_without_and_with_on_no():
    ran = []
    eng, r = _engine(NeedConfirm(action=lambda: ran.append(1), prompt="sure?"))
    eng.handle(UserTurn("do it"))
    step = eng.handle(UserTurn("no"))
    assert ran == [] and step.listen is False and eng.state is State.IDLE
    eng2, r2 = _engine(NeedConfirm(action=lambda: "yes-branch", prompt="sure?",
                                   on_no=lambda: "no-branch"))
    eng2.handle(UserTurn("do it"))
    step2 = eng2.handle(UserTurn("nope"))
    assert step2.response == "no-branch"


def test_confirm_unrelated_utterance_routes_as_new_command():
    eng, r = _engine(NeedConfirm(action=lambda: "confirmed", prompt="sure?"))
    eng.handle(UserTurn("do it"))
    r.result = "New command ran."
    step = eng.handle(UserTurn("what time is it"))
    assert step.response == "New command ran." and r.calls[-1] == "what time is it"


def test_clarify_option_match_and_no_match():
    picked = []
    opts = [("upstairs",   lambda: picked.append("up") or "Upstairs on."),
            ("downstairs", lambda: picked.append("down") or "Downstairs on.")]
    eng, r = _engine(NeedClarify("Upstairs or downstairs?", opts))
    eng.handle(UserTurn("turn on the bedroom lights"))
    assert eng.state is State.AWAITING_CLARIFICATION
    step = eng.handle(UserTurn("the upstairs one"))          # fuzzy/substring match
    assert picked == ["up"] and step.response == "Upstairs on."
    # a non-matching utterance clears the options and routes as a new command
    eng2, r2 = _engine(NeedClarify("which?", opts))
    eng2.handle(UserTurn("lights"))
    r2.result = "It's sunny."
    step2 = eng2.handle(UserTurn("what's the weather"))
    assert step2.response == "It's sunny."


# ── panels / no-ops end the turn ─────────────────────────────────────────────

def test_panel_and_none_end_turn():
    eng, r = _engine(Panel("opened editor"))
    step = eng.handle(UserTurn("open command editor"))
    assert step.listen is False and eng.state is State.IDLE
    eng2, r2 = _engine(None)
    step2 = eng2.handle(UserTurn("nonsense"))
    assert step2.listen is False and eng2.state is State.IDLE


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
