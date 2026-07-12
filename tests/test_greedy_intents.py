"""Greedy-verb guard corpus — the enforcement that keeps the class dead.

Eve hit the same bug three times: a command verb ("play", "mute", "sleep")
matched *anywhere* inside a longer utterance, so "look up a guide on how to
PLAY spiderman" fired media-play instead of a web search. The architectural fix
is whole-utterance matching (core.intent_registry.Intent.match uses `fullmatch`
on a normalized utterance), which makes fragment matches structurally
impossible. This file is the behavioural net: each trap must route past the
greedy verb, and each bare command must still route to it.

Run either way:
    pytest tests/
    python tests/test_greedy_intents.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Hermetic: never reach a live LLM host or the user's real learned stores (mirror
# of test_dispatch's setup — route() only touches the registry, but importing
# dispatcher pulls the fallback wiring in).
import config
import tempfile
config.FALLBACK_LLM = "none"
from core import intent_learning, llm_host
llm_host.settings = lambda: {**llm_host.DEFAULTS, "enabled": False,
                             "base_url": "http://127.0.0.1:1", "model": "x"}
intent_learning._learned = intent_learning.LearnedStore(
    os.path.join(tempfile.mkdtemp(), "learned_intents.json"))
intent_learning._imported = intent_learning.LearnedStore(
    os.path.join(tempfile.mkdtemp(), "imported_intents.json"))

from core import dispatcher as d
from core import features
from commands import search, system, discord as discord_cmd, context as ctx_cmd


def route(text: str):
    """The handler the scored registry would fire for `text`, NOT executed."""
    hit = d._registry().best(d.normalize(text), feature_get=features.get)
    return hit[0].handler if hit is not None else None


# ── Traps: a command verb buried mid-utterance must NOT fire; these are all web
# searches (a harmless, correct destination) because they lead with a search verb.
def test_buried_verbs_do_not_fire():
    assert route("look up a guide on how to play spiderman") is search.web_search_list
    assert route("do a search for a guide on how to play spiderman") is search.web_search_list
    assert route("look up how to mute someone on discord") is search.web_search_list
    assert route("search for how to shut down a business") is search.web_search_list
    assert route("google how to undo a commit") is search.web_search_list


def test_buried_verb_with_no_search_verb_does_not_misroute():
    # No leading search verb, so it shouldn't route anywhere in the registry —
    # and critically must NOT hit sleep_pc off the bare word "sleep".
    assert route("how many hours of sleep did i get") is not system.sleep_pc
    assert route("how many hours of sleep did i get") is None


# ── Positive controls: the bare commands MUST still route to their verb. ───────
def test_bare_commands_still_route():
    assert route("mute") is system.toggle_mute
    assert route("unmute") is system.toggle_mute
    assert route("play") is system.media_play_pause
    assert route("pause") is system.media_play_pause
    assert route("play music") is system.media_play_pause
    assert route("shut down") is system.shutdown
    assert route("turn off the computer") is system.shutdown
    assert route("go to sleep") is system.sleep_pc     # beats go_to_site by priority
    assert route("undo") is ctx_cmd.undo
    assert route("go back") is ctx_cmd.undo
    assert route("help") is d._help
    assert route("deafen me") is discord_cmd.deafen
    assert route("disconnect") is discord_cmd.disconnect


def test_specificity_still_beats_catchall():
    # The panel intent must win over the demoted open_app catch-all even though
    # both now whole-utterance match "open app manager".
    assert route("open app manager") is system.open_app_manager
    from commands import apps
    assert route("open firefox") is apps.open_app          # catch-all still works bare


def test_leading_filler_is_stripped():
    # normalize() peels leading filler so anchored patterns match without each
    # re-encoding it.
    from commands import apps
    assert route("please play music") is system.media_play_pause
    assert route("hey can you mute") is system.toggle_mute
    assert route("just open firefox") is apps.open_app


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
