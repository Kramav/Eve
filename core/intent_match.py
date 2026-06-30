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

# token_set_ratio scores 100 when the catalog phrase is a *subset* of a longer
# utterance ("make my app manager full screen" ⊃ "app manager"), which would
# silently fire the wrong panel. token_sort_ratio is length/order sensitive, so
# we additionally require it to clear this bar before a *silent* execution. A
# match that passes token_set but fails this is demoted to a "did you mean?".
STRICT_THRESHOLD = 72


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
    from commands import apps, system, reminders, tiling, search, window_manager as wm
    from commands import windows as windows_cmd
    from core.dispatcher import _HELP_TEXT

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
        ('identify windows',        windows_cmd.identify_windows, ()),
        ('show open windows',       windows_cmd.identify_windows, ()),
        ('list open windows',       windows_cmd.identify_windows, ()),
        ('list windows',            windows_cmd.identify_windows, ()),
        ('what is open',            windows_cmd.identify_windows, ()),
        ('what windows are open',   windows_cmd.identify_windows, ()),
        ('whats open',              windows_cmd.identify_windows, ()),
        ('show me open windows',    windows_cmd.identify_windows, ()),
        ('identify zones',          system.identify_zones,    ()),
        ('show zones',              system.identify_zones,    ()),
        ('show tiling layouts',     system.identify_zones,    ()),
        ('show tiles',              system.identify_zones,    ()),
        ('show segments',           system.identify_zones,    ()),
        ('show the layouts',        system.identify_zones,    ()),
        ('move hud to primary',     wm.move_hud,              ('primary',)),
        ('set hud to primary',      wm.move_hud,              ('primary',)),
        ('pin hud to primary',      wm.move_hud,              ('primary',)),
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

    # YouTube/mpv lives in skills/youtube.py (PREEMPT) — its entry phrases are
    # claimed there before the fuzzy catalog is ever consulted, so no entries here.

    # ─── Apps from apps.json (open + close, both via spoken name) ────────
    for spoken in apps._load_apps().keys():
        cat.append((f'open {spoken}', apps.open_app,  (spoken,)))
        cat.append((spoken,           apps.open_app,  (spoken,)))
        cat.append((f'close {spoken}', apps.close_app, (spoken,)))

    # ─── User aliases (custom keywords → built-in functions) ─────────────
    cat += _alias_entries()

    return cat


def _alias_entries() -> list[tuple[str, Callable, tuple]]:
    """Pull custom alias phrases from aliases.json. Returns empty list on
    error so missing/bad files don't break dispatch."""
    from core import dispatcher as _disp
    out = []
    for phrase, key in _disp._load_aliases():
        handler = _disp.BUILTIN_MAP.get(key)
        if handler:
            out.append((phrase.lower(), handler, ()))
    return out


def best_match(text: str, catalog: list[tuple[str, Callable, tuple]]):
    """Return (canonical, callable, args, score, strict) for the best match, or
    None. `strict` is the length/order-sensitive token_sort_ratio against the
    chosen phrase — dispatch uses it to decide silent-exec vs "did you mean?"."""
    if not catalog or not text:
        return None
    phrases = [c[0] for c in catalog]
    # process.extractOne uses scorer to rank — returns (match, score, index)
    result = process.extractOne(text, phrases, scorer=_SCORER, score_cutoff=MED_THRESHOLD)
    if result is None:
        return None
    _, score, idx = result
    canonical, fn, args = catalog[idx]
    strict = fuzz.token_sort_ratio(' '.join(text.split()), ' '.join(canonical.split()))
    return canonical, fn, args, score, strict
