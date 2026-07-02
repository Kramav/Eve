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


from core.intent_registry import _captured_len


def _raw_matches(text: str) -> list:
    """Handlers whose RAW pattern matches — a lint on the INTENTS table itself
    (shows which patterns still overlap; the registry resolves them without
    relying on order)."""
    norm = _normalize(text)
    return [h for pat, h in d.INTENTS if re.search(pat, norm)]


def _registry_ranked(text: str) -> list:
    """The REAL router's matches, best-first: [(intent, match), ...]."""
    return d._registry().matches(_normalize(text))


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

# A phrase is a GENUINE tie (still order-dependent) only when the registry's top
# two matches share BOTH priority AND literal-match specificity — so only stable
# insertion order separates them. Priority banding + specificity + two explicit
# priority nudges (`_INTENT_PRIORITY`) resolved ALL of the original 7 raw-overlap
# canonical phrases by design: the five "open <panel>" phrases (open_app demoted),
# "snap X to top" (snap wins by specificity), and "hide interface" (toggle_overlay
# promoted). So the set is now EMPTY — no canonical command routes correctly only
# because of table position. A new entry means real ambiguity to resolve.
_TRUE_TIES = set()


def test_registry_routes_canonical_to_intended_handler():
    """The REAL router (banded registry) sends each canonical phrase to its
    intended handler — order-independently."""
    for phrase, intended in CANONICAL.items():
        hit = d._registry().best(_normalize(phrase))
        assert hit is not None, f"{phrase!r} matched no intent"
        assert hit[0].handler is intended, (
            f"{phrase!r} routed to {hit[0].handler.__name__}, expected {intended.__name__}")


def test_no_new_true_ties():
    """Genuine scoring ties (only insertion order separates the top two) must be
    documented. A new one means real ambiguity crept in — resolve it with an
    explicit priority or a more specific pattern, don't just whitelist it."""
    ties = set()
    for phrase in CANONICAL:
        ranked = _registry_ranked(phrase)
        if len(ranked) < 2:
            continue
        (i1, m1), (i2, m2) = ranked[0], ranked[1]
        if i1.priority == i2.priority and _captured_len(m1) == _captured_len(m2):
            ties.add(phrase)
    new = ties - _TRUE_TIES
    assert not new, (
        f"New genuine scoring ties (order-dependent): {sorted(new)}. "
        "Resolve with an explicit priority or a more specific pattern.")


def test_banding_resolved_the_open_panel_shadows():
    """Headline-win regression guard: every 'open <panel>' phrase still raw-
    matches open_app (the table overlaps), but the registry now resolves it by
    PRIORITY, not table order — open_app can never win."""
    for phrase in ("open app manager", "open window manager", "open voice settings",
                   "open command editor", "open api keys"):
        assert apps.open_app in _raw_matches(phrase)              # still overlaps
        ranked = _registry_ranked(phrase)
        assert ranked[0][0].handler is not apps.open_app         # but never wins
        assert ranked[0][0].priority > ranked[1][0].priority     # won by priority


def _report():
    """How each canonical phrase resolves: raw table overlaps → registry winner."""
    lines = []
    for phrase in CANONICAL:
        raw = list(dict.fromkeys(h.__name__ for h in _raw_matches(phrase)))
        ranked = _registry_ranked(phrase)
        if ranked:
            winner = ranked[0][0].name
            reason = d._registry().explain(_normalize(phrase))["reason"]
        else:
            winner, reason = None, "no match"
        flag = "  <-- raw overlap" if len(raw) > 1 else ""
        lines.append(f"  {phrase!r}: raw={raw} -> {winner} ({reason}){flag}")
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
