"""Intent-conflict audit for the hand-ordered dispatcher `INTENTS` wall.

The wall is ordered so specific intents beat general ones ("protect X" above
"snap X" above "open X"). That ordering is invisible and fragile: reordering or
adding a pattern can silently let the wrong handler win. This test is the guard
the roadmap (P0 #3) asks for — "no two patterns both match canonical phrase X"
— made concrete, and it doubles as groundwork for the Tier-A registry refactor
(you must know the real overlaps before you restructure).

Two things it enforces:
  1. For a curated set of canonical command phrases, the INTENDED handler is the
     FIRST match (the correctness property the ordering is supposed to provide).
  2. Every phrase whose text matches MORE THAN ONE distinct handler (i.e. only
     routes correctly because of ordering) is documented in _ORDER_DEPENDENT.
     A NEW order-dependent phrase fails the test → forces you to look at it.

Run either way:
    pytest tests/
    python tests/test_intent_audit.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dispatcher as d
from commands import (apps, search, system, tiling, context as ctx_cmd,
                      window_manager as wm, windows as windows_cmd,
                      reminders, discord as discord_cmd)


def _normalize(text: str) -> str:
    """Mirror dispatch()'s pre-match normalization (no mishear subs)."""
    text = text.strip().lower()
    text = re.sub(r"[.,!?]+$", "", text).strip()
    for prefix in d._WAKE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip(",. ")
            break
    return text


def _matching_handlers(text: str) -> list:
    """Every built-in handler whose pattern matches `text`, in table order
    (feature gating ignored — this audits the regex overlaps themselves)."""
    norm = _normalize(text)
    return [h for pat, h in d.INTENTS if re.search(pat, norm)]


# Canonical phrase → the handler it MUST route to. One representative per major
# intent (kept in sync with the behaviours asserted in test_dispatch.py).
CANONICAL = {
    "open app manager":            system.open_app_manager,
    "open window manager":         system.open_window_manager,
    "open voice settings":         system.open_voice_settings,
    "open command editor":         system.open_editor,
    "open api keys":               system.open_integrations,
    "hide interface":              system.toggle_overlay,
    "show directory":              system.show_directory,
    "what time is it":             system.get_time,
    "take a screenshot":           system.screenshot,
    "open firefox":                apps.open_app,
    "close firefox":               apps.close_app,
    "search for puppies":          search.web_search_list,
    "go to github.com":            search.go_to_site,
    "protect discord":             ctx_cmd.protect_program,
    "stop protecting elden ring":  ctx_cmd.unprotect_program,
    "save layout as work":         tiling.save_workspace,
    "restore work layout":         tiling.restore_workspace,
    "snap firefox to top":         tiling.snap_app,
    "next channel":                discord_cmd.next_channel,
    "identify windows":            windows_cmd.identify_windows,
    "name monitor 2 gaming":       wm.name_monitor,
    "remind me to call mom at 3pm": ctx_cmd.remind,
}

# Phrases whose text matches >1 distinct handler and therefore route correctly
# ONLY because of table ordering. Documented on purpose; a NEW entry means a
# fresh ordering dependency crept in — investigate before whitelisting it.
#
# What the audit revealed about the current wall (the fragility Tier-A fixes):
#   * Every "open <panel>" command ALSO matches apps.open_app ("open <X>"). They
#     route right only because the panel intents sit ABOVE open_app; reorder them
#     below and "open app manager" tries to launch an app called "app manager".
#   * "hide interface" matches both the overlay toggle and hide_directory
#     (the `interface` synonym lives in both); toggle wins by position.
#   * "snap X to top" matches snap_app AND bring_to_front ("to top" z-order).
_ORDER_DEPENDENT = {
    "open app manager",      # + apps.open_app
    "open window manager",   # + apps.open_app
    "open voice settings",   # + apps.open_app
    "open command editor",   # + apps.open_app
    "open api keys",         # + apps.open_app
    "hide interface",        # toggle_overlay + hide_directory
    "snap firefox to top",   # snap_app + bring_to_front
}


def test_canonical_phrase_routes_to_intended_handler():
    """The ordering must make the intended handler win (first match)."""
    for phrase, intended in CANONICAL.items():
        matches = _matching_handlers(phrase)
        assert matches, f"{phrase!r} matched no intent at all"
        assert matches[0] is intended, (
            f"{phrase!r} routed to {matches[0].__name__}, expected {intended.__name__}")


def test_no_new_order_dependent_phrases():
    """Every phrase that only routes correctly because of ordering must be
    documented. A new one failing here is the point — it flags fresh fragility."""
    order_dependent = set()
    for phrase in CANONICAL:
        distinct = {h for h in _matching_handlers(phrase)}
        if len(distinct) > 1:
            order_dependent.add(phrase)
    new = order_dependent - _ORDER_DEPENDENT
    assert not new, (
        "New order-dependent phrases (multiple handlers match; only ordering "
        f"saves them): {sorted(new)}. Investigate, then add to _ORDER_DEPENDENT.")


def _report():
    """Human-readable overlap map — how many distinct handlers each canonical
    phrase matches. Printed by the standalone runner for eyeballing the wall."""
    lines = []
    for phrase in CANONICAL:
        hs = _matching_handlers(phrase)
        distinct = list(dict.fromkeys(h.__name__ for h in hs))
        flag = "  <-- order-dependent" if len(distinct) > 1 else ""
        lines.append(f"  {phrase!r}: {distinct}{flag}")
    return "\n".join(lines)


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
    print("\n--- overlap map ---")
    print(_report())
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
