"""Voice handlers for the Window Manager — apply layouts and move the HUD
to specific monitors. The actual file write and Electron-side state changes
happen in `ui/main.js` (it's the source of truth for display indexing); this
module just normalizes spoken phrases and tells Electron what to do via WS.
"""
import re

_display = None


def set_display(display):
    global _display
    _display = display


# ─── Spoken word → 1-based monitor index ────────────────────────────────────
_NUMBER_WORDS = {
    'one':   1, 'two':   2, 'three': 3, 'four':  4, 'five':  5,
    'six':   6, 'seven': 7, 'eight': 8, 'nine':  9, 'ten':   10,
    'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
    'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
}


def _resolve_monitor_ref(text: str):
    """Parse a monitor reference out of a spoken fragment. Returns one of:
      - '1'..'10' (Win32 enumeration index, matches Identify Monitors)
      - 'primary' (Windows primary)
      - 'left' / 'middle' / 'right' (positional — by x coordinate)
      - None if nothing matched
    """
    if not text:
        return None
    t = text.strip().lower()
    if 'primary' in t or 'main display' in t or 'main screen' in t:
        return 'primary'
    # Positional aliases. "center" is normalized to "middle".
    for keyword, normalized in (
        ('leftmost',  'left'),
        ('rightmost', 'right'),
        ('center',    'middle'),
        ('middle',    'middle'),
        ('left',      'left'),
        ('right',     'right'),
    ):
        if re.search(rf'\b{keyword}\b', t):
            return normalized
    # Digit
    m = re.search(r'\b(\d{1,2})\b', t)
    if m:
        return m.group(1)
    # Number word
    for word, num in _NUMBER_WORDS.items():
        if re.search(rf'\b{word}\b', t):
            return str(num)
    return None


# ─── Spoken preset name → PRESETS key in ui/main.js ─────────────────────────
# Order matters: longer / more-specific phrases first so they match before
# partial overlaps (e.g. "main and right" before "right").
_PRESET_ALIASES = [
    # main-stack
    (r'\bmain\s+(?:and\s+|plus\s+|\+\s*)?stack\b'
     r'|\b1\s*\+?\s*2\s*stack\b|\bone\s*plus\s*two\s*stack\b',                                     'main-stack'),
    # main-right
    (r'\bmain\s+(?:and\s+|plus\s+|\+\s*)?right\b',                                                 'main-right'),
    # grid-4
    (r'\b(?:two\s*by\s*two|2\s*x\s*2|two\s*x\s*two|2\s*by\s*2'
     r'|grid(?:\s+of\s+(?:4|four))?|four\s*zone(?:s)?|quad(?:rant)?)\b',                           'grid-4'),
    # left-right
    (r'\bleft\s+(?:and\s+|or\s+)?right\b'
     r'|\bleft[-+/]right\b|\bside\s*by\s*side\b|\bhorizontal(?:\s*split)?\b',                      'left-right'),
    # top-bottom
    (r'\btop\s+(?:and\s+|or\s+)?bottom\b'
     r'|\btop[-+/]bottom\b|\bvertical(?:\s*split)?\b|\bstacked\b',                                 'top-bottom'),
    # full (must be last — most generic)
    (r'\b(?:full(?:\s*screen)?|single(?:\s*zone)?|whole|maximize|maxim[ie]sed?|one\s*zone)\b',     'full'),
]

_PRESET_LABELS = {
    'full':       'full screen',
    'top-bottom': 'top and bottom',
    'left-right': 'left and right',
    'main-right': 'main plus right',
    'main-stack': 'main with stack',
    'grid-4':     'two by two grid',
}


def _resolve_preset(text: str):
    """Return PRESETS key or None."""
    if not text:
        return None
    t = text.lower()
    for pat, key in _PRESET_ALIASES:
        if re.search(pat, t):
            return key
    return None


# ─── Voice intents ──────────────────────────────────────────────────────────

def set_monitor_layout(monitor_text: str, preset_text: str) -> str:
    """Voice: 'set monitor 1 to 2x2 grid' → applies grid-4 to monitor 1."""
    ref    = _resolve_monitor_ref(monitor_text)
    preset = _resolve_preset(preset_text)
    if ref is None:
        return f"I couldn't tell which monitor you meant from '{monitor_text}'."
    if preset is None:
        return f"I don't recognize the layout '{preset_text}'. Try: full, top and bottom, left and right, main plus right, main stack, or two by two grid."
    if _display is None:
        return "Window manager isn't ready yet."
    _display.wm_apply_preset(ref, preset)
    label = 'primary' if ref == 'primary' else f'monitor {ref}'
    return f"Set {label} to {_PRESET_LABELS[preset]}."


def move_hud(monitor_text: str) -> str:
    """Voice: 'move HUD to monitor 2' / 'to left' / 'to primary' → repositions orb."""
    ref = _resolve_monitor_ref(monitor_text)
    if ref is None:
        return f"I couldn't tell which monitor you meant from '{monitor_text}'."
    if _display is None:
        return "Window manager isn't ready yet."

    # Electron only knows 'primary' or a numeric index. Translate positional
    # aliases here so Python's Win32 ordering matches Electron's screen.getAllDisplays.
    if ref in ('left', 'middle', 'right'):
        from commands.tiling import positional_to_index
        idx = positional_to_index(ref)
        if idx is None:
            return f"I couldn't find the {ref} monitor."
        label = f'the {ref} monitor'
        _display.wm_move_hud(idx)
    else:
        label = 'primary' if ref == 'primary' else f'monitor {ref}'
        _display.wm_move_hud(ref)
    return f"Moved HUD to {label}."


def set_eve_monitor(monitor_text: str) -> str:
    """Voice: 'set monitor 2 as Eve's monitor' / 'use my right screen for Eve' /
    'make this Eve's monitor' → designate where Eve places opened windows (for 3+
    monitor setups). Resolved and saved entirely in Python (no Electron)."""
    from core import monitor
    t = (monitor_text or '').strip().lower()
    if re.search(r'\b(this|current|here)\b', t):
        ref = 'this'
    else:
        ref = _resolve_monitor_ref(t)
    if ref is None:
        return (f"I couldn't tell which monitor you meant from '{monitor_text}'. "
                "Try 'monitor 2', 'my right monitor', or 'this monitor'.")
    ok, label = monitor.set_eve_monitor(ref)
    if not ok:
        return label or "I couldn't set that as Eve's monitor."
    return f"Set your {label} as Eve's monitor. I'll put opened windows there."


def name_monitor(monitor_text: str, label: str) -> str:
    """Voice: 'name monitor 2 primary display' → save a display-only label for
    that monitor in tiling_layouts.json. The label is for the user's reference
    (spoken back, shown in the WM panel); it doesn't change routing."""
    ref = _resolve_monitor_ref(monitor_text)
    if ref is None:
        return f"I couldn't tell which monitor you meant from '{monitor_text}'."
    label = (label or '').strip().strip('"\'')
    if not label:
        return "What name should I give it?"

    from commands.tiling import _load_layouts, _save_layouts, _resolve_monitor_by_ref
    data     = _load_layouts()
    monitors = data.get('monitors', {})
    # Prefer keying by the stable saved monitor id so the name sticks to the
    # physical display; fall back to the spoken ref if nothing matches.
    mid, _ = _resolve_monitor_by_ref(ref, monitors)
    key = mid if mid is not None else ref
    data.setdefault('monitor_names', {})[key] = label
    _save_layouts(data)

    # ponytail: name is saved + spoken back; WM panel will show it on next read.
    # Add a Python→Electron layouts-reload push if live UI refresh is wanted.
    spoken = 'primary' if ref == 'primary' else (
        f'the {ref} monitor' if ref in ('left', 'middle', 'right') else f'monitor {ref}')
    return f"Named {spoken} '{label}'."


_VERTICAL_ALIASES   = {'top': 'top', 'upper': 'top', 'bottom': 'bottom', 'lower': 'bottom'}
_HORIZONTAL_ALIASES = {'left': 'left', 'right': 'right'}


def move_orb_corner(vertical: str, horizontal: str) -> str:
    """Voice: 'move orb to top-left' / 'put hud in upper right corner' →
    pin the orb (and routing-directory anchor) to the named corner of the
    current HUD monitor."""
    v = _VERTICAL_ALIASES.get((vertical or '').strip().lower())
    h = _HORIZONTAL_ALIASES.get((horizontal or '').strip().lower())
    if not v or not h:
        return f"I didn't recognize that corner."
    if _display is None:
        return "Window manager isn't ready yet."
    corner = f'{v}-{h}'
    _display.wm_set_orb_corner(corner)
    return f"Moved orb to the {v} {h}."


# ─── Z-order voice commands ────────────────────────────────────────────────

def _spoken_label_for(match: dict) -> str:
    """Pretty-print the matched app for the spoken response."""
    exe = (match.get('exe') or '').replace('.exe', '').lower()
    return exe or match.get('title') or 'window'


def bring_to_front(app_text: str) -> str:
    """Voice: 'bring discord to front' → raise without stealing focus."""
    from commands.tiling import find_window_by_spoken_name
    from core.window_ops import raise_to_top_no_focus
    match = find_window_by_spoken_name(app_text)
    if match is None:
        return f"I don't see {app_text} open."
    ok = raise_to_top_no_focus(match['hwnd'])
    label = _spoken_label_for(match)
    if ok:
        # Register for "close that window" follow-up
        from core import session as _sess_mod
        _sess_mod.set_last_action(_sess_mod.LastAction(
            description=f"bring {label} to the front",
            target_hwnd=match['hwnd'],
        ))
        return f"Brought {label} to the front."
    return f"Couldn't bring {label} to the front."


def send_to_back(app_text: str) -> str:
    """Voice: 'send discord to back' → push to bottom of z-order."""
    from commands.tiling import find_window_by_spoken_name
    from core.window_ops import send_to_bottom
    match = find_window_by_spoken_name(app_text)
    if match is None:
        return f"I don't see {app_text} open."
    ok = send_to_bottom(match['hwnd'])
    label = _spoken_label_for(match)
    return f"Sent {label} to the back." if ok else f"Couldn't send {label} to the back."
