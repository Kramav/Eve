"""Registry routing corpus + banding invariants.

Originally this proved the Tier-A registry routed IDENTICALLY to the old
first-match `re.search` loop. That parity is now intentionally gone: the router
switched from substring spotting (`re.search`) to whole-utterance matching
(`fullmatch` on a normalized utterance) to kill the greedy-verb bug class — see
core.intent_registry.Intent.match and tests/test_greedy_intents.py. So this file
now guards what still holds: every canonical command phrase resolves sanely
through the real (banded) registry, and the banding/priority invariants stand.

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


# Broad phrase set spanning the tricky, overlap-prone corners of the wall:
# panels, apps, snap variants, z-order, protect, memory, discord, reminders,
# identify, autostart. All are whole-utterance commands, so each MUST still
# resolve to some handler under fullmatch (regressions surface as a None).
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
]

# These are NOT commands — they must resolve to nothing in the registry.
NON_COMMANDS = ["asdfjkl gibberish", "the weather is nice today"]


def test_canonical_phrases_still_route():
    reg = d._registry()
    dead = [p for p in PHRASES if reg.best(d.normalize(p)) is None]
    assert not dead, "canonical phrases no longer route under fullmatch:\n" + "\n".join(dead)


def test_non_commands_do_not_route():
    reg = d._registry()
    hits = [(p, reg.best(d.normalize(p))) for p in NON_COMMANDS]
    bad = [(p, h[0].name) for p, h in hits if h is not None]
    assert not bad, f"non-commands wrongly matched: {bad}"


def test_catchalls_demoted_below_specifics():
    from commands import apps as _apps, search as _search
    demoted = {_apps.open_app, _apps.close_app, _apps.kill_app,
               _search.go_to_site, _search.web_search_list}
    for it in d._registry().all():
        if it.handler in demoted:
            assert it.priority < 0, f"{it.name} not demoted (priority {it.priority})"
        else:
            assert it.priority >= 0, f"{it.name} unexpectedly demoted ({it.priority})"


def test_explain_last_after_dispatch():
    # dispatch a side-effect-free built-in, then ask why it routed that way.
    from core import session as S
    S.reset()
    d.dispatch("what time is it")          # → system.get_time (returns a string)
    explanation = d.explain_last()
    assert "get_time" in explanation, explanation
    S.reset()


def test_why_did_you_do_that_explains_previous_not_itself():
    # The voice "why did you do that" intent must explain the PRIOR command,
    # not overwrite _LAST_TEXT with the query itself.
    from core import session as S
    S.reset()
    d.dispatch("what time is it")
    out = str(d.dispatch("why did you do that"))
    assert "get_time" in out, out                  # explains the time command
    assert "why did you do that" not in out        # not the meta-query
    S.reset()


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
