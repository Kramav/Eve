"""Phrase-similarity matching for misheard commands.

Builds a flat catalog of canonical command phrases (intent labels, app spoken
names, UI panel aliases, custom aliases).  When the dispatcher's regex pass
fails, we ask this module for the closest entry and let dispatch decide what
to do based on confidence tier:

    score >= HIGH_THRESHOLD  →  execute immediately (silent guess)
    MED_THRESHOLD <= score   →  ask "did you mean X?" via Session.pending_confirm
    score < MED_THRESHOLD    →  treat as no match

Catalog entries are tuples of (canonical_phrase, callable, args).  The callable
runs the original command path so behavior matches what the user would have
gotten from the matching regex.
"""
from __future__ import annotations

from typing import Callable, Iterable
from rapidfuzz import fuzz, process

# Confidence tiers (0-100 scale from rapidfuzz scorers)
HIGH_THRESHOLD = 88   # near-certain: execute silently
MED_THRESHOLD  = 68   # close enough to ask "did you mean X?"


def _ws(scorer):
    """Wrap a scorer so it operates on whitespace-normalized strings."""
    def s(a, b, **kw):
        return scorer(' '.join(a.split()), ' '.join(b.split()), **kw)
    return s


# token_set_ratio is robust to:
#   - extra filler words ("show me the overlay" vs "show overlay")
#   - reordering ("manager app" vs "app manager")
#   - duplicates / missing words
# It does drop word ORDER though — that's why we *also* prefer regex first.
_SCORER = _ws(fuzz.token_set_ratio)


def build_catalog() -> list[tuple[str, Callable, tuple]]:
    """Assemble the catalog. Called fresh each dispatch — apps/aliases live in
    files that the user can edit, so we want to pick up changes immediately."""
    from commands import apps, system, reminders, youtube, tiling, search
    import json
    from pathlib import Path

    cat: list[tuple[str, Callable, tuple]] = []

    # ─── UI panels (open) ────────────────────────────────────────────────
    cat += [
        ('open app manager',         system.open_app_manager,    ()),
        ('app manager',              system.open_app_manager,    ()),
        ('open window manager',      system.open_window_manager, ()),
        ('window manager',           system.open_window_manager, ()),
        ('open voice manager',       system.open_voice_settings, ()),
        ('open voice settings',      system.open_voice_settings, ()),
        ('voice manager',            system.open_voice_settings, ()),
        ('voice settings',           system.open_voice_settings, ()),
        ('open command editor',      system.open_editor,         ()),
        ('command editor',           system.open_editor,         ()),
        ('commands',                 system.open_editor,         ()),
        ('show routing directory',   system.show_directory,      ()),
        ('routing directory',        system.show_directory,      ()),
        ('show overlay',             system.show_directory,      ()),
        ('show hud',                 system.show_directory,      ()),
        ('hud',                      system.show_directory,      ()),
        ('overlay',                  system.show_directory,      ()),
    ]

    # ─── UI panels (close) ───────────────────────────────────────────────
    cat += [
        ('close app manager',        system.close_app_manager,    ()),
        ('close window manager',     system.close_window_manager, ()),
        ('close command editor',     system.close_editor,         ()),
        ('close routing directory',  system.hide_directory,       ()),
        ('hide overlay',             system.hide_directory,       ()),
        ('hide hud',                 system.hide_directory,       ()),
    ]

    # ─── System actions ──────────────────────────────────────────────────
    cat += [
        ('identify monitors',       system.identify_monitors, ()),
        ('show monitor numbers',    system.identify_monitors, ()),
        ('which monitor is which',  system.identify_monitors, ()),
        ('label displays',          system.identify_monitors, ()),
        ('volume up',               system.volume_up,         ()),
        ('volume down',             system.volume_down,       ()),
        ('mute',                    system.toggle_mute,       ()),
        ('unmute',                  system.toggle_mute,       ()),
        ('pause',                   system.media_play_pause,  ()),
        ('play',                    system.media_play_pause,  ()),
        ('resume',                  system.media_play_pause,  ()),
        ('next track',              system.media_next,        ()),
        ('previous track',          system.media_prev,        ()),
        ('take screenshot',         system.screenshot,        ()),
        ('what time is it',         system.get_time,          ()),
        ('what is the time',        system.get_time,          ()),
        ('what is the date',        system.get_date,          ()),
        ('go to sleep',             system.sleep_pc,          ()),
        ('shut down',               system.shutdown,          ()),
        ('cancel shutdown',         system.cancel_shutdown,   ()),
        ('silence',                 system.silence_voice,     ()),
        ('be quiet',                system.silence_voice,     ()),
        ('shut up',                 system.silence_voice,     ()),
        ('enable voice',            system.enable_voice,      ()),
        ('toggle voice',            system.toggle_voice,      ()),
        ('list reminders',          reminders.list_reminders, ()),
        ('cancel reminders',        reminders.cancel_all,     ()),
        ('help',                    lambda: _HELP_TEXT,       ()),
    ]

    # ─── YouTube ─────────────────────────────────────────────────────────
    cat += [
        ('open youtube',            youtube.browse_home_intent, ()),
        ('youtube',                 youtube.browse_home_intent, ()),
    ]

    # ─── Apps from apps.json (open + close, both via spoken name) ────────
    for spoken in apps._load_apps().keys():
        cat.append((f'open {spoken}', apps.open_app,  (spoken,)))
        cat.append((spoken,           apps.open_app,  (spoken,)))
        cat.append((f'close {spoken}', apps.close_app, (spoken,)))

    # ─── User aliases (custom keywords → built-in functions) ─────────────
    cat += _alias_entries()

    return cat


_HELP_TEXT = (
    "I can search and play YouTube, open and close apps, search the web, "
    "go to websites, set reminders and timers, control volume and media, "
    "take screenshots, and control your PC."
)


def _alias_entries() -> list[tuple[str, Callable, tuple]]:
    """Pull custom alias phrases from aliases.json. Returns empty list on
    error so missing/bad files don't break dispatch."""
    import json
    from pathlib import Path
    from core import dispatcher as _disp
    path = Path(__file__).parent.parent / 'aliases.json'
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    out = []
    for phrase, key in data:
        handler = _disp.BUILTIN_MAP.get(key)
        if handler:
            out.append((phrase.lower(), handler, ()))
    return out


def best_match(text: str, catalog: list[tuple[str, Callable, tuple]]):
    """Return (canonical, callable, args, score) for the best match, or None."""
    if not catalog or not text:
        return None
    phrases = [c[0] for c in catalog]
    # process.extractOne uses scorer to rank — returns (match, score, index)
    result = process.extractOne(text, phrases, scorer=_SCORER, score_cutoff=MED_THRESHOLD)
    if result is None:
        return None
    _, score, idx = result
    canonical, fn, args = catalog[idx]
    return canonical, fn, args, score
