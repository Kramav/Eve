"""Parity proof: the Tier-A registry, built from the live dispatcher INTENTS via
`from_intents` (position → priority), routes IDENTICALLY to the current
first-match `for … in INTENTS` loop.

This is the safety net that makes swapping `dispatch()`'s built-in loop for
`registry.resolve()` a small, de-risked change: if every phrase resolves to the
same handler through both paths, the swap is behaviour-preserving. It compares
the RAW regex ordering (no feature gating) on both sides — apples to apples with
the actual built-in loop in dispatch().

Run either way:
    pytest tests/
    python tests/test_intent_registry_parity.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dispatcher as d
from core.intent_registry import from_intents


def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[.,!?]+$", "", text).strip()
    for prefix in d._WAKE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip(",. ")
            break
    return text


def _first_match_handler(norm: str):
    """What dispatch()'s built-in loop would fire (raw first match, no gating)."""
    for pat, handler in d.INTENTS:
        if re.search(pat, norm):
            return handler
    return None


# Broad phrase set spanning the tricky, overlap-prone corners of the wall:
# panels, apps, snap variants, z-order, protect, memory, discord, reminders,
# identify, autostart, plus a few no-match phrases.
PHRASES = [
    "open app manager", "close app manager", "open window manager",
    "open voice settings", "open command editor", "open api keys",
    "show overlay", "hide hud", "toggle hud", "hud", "hide interface",
    "show directory", "hide directory",
    "open firefox", "close firefox", "kill firefox", "launch spotify",
    "search for puppies", "go to github.com",
    "protect discord", "stop protecting elden ring", "this is my game",
    "what's protected", "treat firefox as essential",
    "snap firefox to top", "snap discord to left of monitor 2",
    "move chrome to monitor 1", "bring firefox to front", "send discord to back",
    "save layout as work", "restore work layout", "what layouts do i have",
    "always open firefox in top-left", "stop auto-snapping discord",
    "next channel", "previous server", "open discord search",
    "remember my name is sam", "what is my name", "forget my name",
    "open memory", "identify windows", "identify monitors", "identify zones",
    "name monitor 2 gaming", "set monitor 1 to grid",
    "remind me to call mom at 3pm", "set a timer for 5 minutes",
    "what time is it", "what is the date", "take a screenshot",
    "add eve to startup", "don't start eve at login",
    "mute", "volume up", "help",
    "asdfjkl gibberish", "the weather is nice today",
]


def test_registry_parity_with_first_match_loop():
    reg = from_intents(d.INTENTS, d._HANDLER_FEATURE)
    mismatches = []
    for phrase in PHRASES:
        norm = _normalize(phrase)
        want = _first_match_handler(norm)
        hit = reg.best(norm)                       # default feature_get = all enabled
        got = hit[0].handler if hit else None
        if got is not want:
            mismatches.append(
                (phrase,
                 getattr(got, "__name__", None),
                 getattr(want, "__name__", None)))
    assert not mismatches, "registry != first-match for:\n" + "\n".join(
        f"  {p!r}: registry={g}  first-match={w}" for p, g, w in mismatches)


def test_bridge_preserves_priority_order():
    # Position → strictly descending priority, so the sort is exactly list order.
    reg = from_intents(d.INTENTS)
    prios = [it.priority for it in reg.all()]
    assert prios == sorted(prios, reverse=True)     # already descending
    assert len(set(prios)) == len(prios)            # unique → deterministic


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
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
