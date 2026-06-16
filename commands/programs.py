"""Voice + panel integration for the Running Programs detector.

Three entry points:
  * `list_running_aloud()`   — speak the top-N program names ("What's running")
  * `open_panel()`            — open the Running Programs Electron panel
  * helpers used by display.py to satisfy live panel requests over WS
"""
import json
from pathlib import Path

from commands import tiling

_APPS_FILE = Path(__file__).parent.parent / "apps.json"
_display   = None

# How many programs to read out for the spoken list. More than this gets
# annoying fast.
_SPEAK_LIMIT = 6


def set_display(display):
    global _display
    _display = display


# ─── Voice ──────────────────────────────────────────────────────────────────

def list_running_aloud() -> str:
    """Voice: 'what's running' / 'list running programs' → spoken summary."""
    progs = _dedupe_by_exe(tiling.enumerate_programs())
    names = [_pretty_name(p) for p in progs if not p.get('minimized')] \
            or [_pretty_name(p) for p in progs]
    if not names:
        return "No programs are open."
    if len(names) == 1:
        return f"You have {names[0]} open."
    visible = names[:_SPEAK_LIMIT]
    more = len(names) - len(visible)
    head = ', '.join(visible[:-1])
    last = visible[-1]
    base = f"You have {head}, and {last} open"
    if more > 0:
        base += f" — plus {more} more"
    return base + '.'


def open_panel() -> str:
    """Voice: 'show running programs' / 'open programs panel' → open the panel."""
    if _display is not None:
        _display.open_programs()
    return ""


# ─── Helpers used by display.py WS handlers ─────────────────────────────────

def get_panel_payload() -> list[dict]:
    """Snapshot of programs for the panel renderer. Augments each entry with
    whether it's already in apps.json."""
    apps_keys = _apps_keys()
    progs = _dedupe_by_exe(tiling.enumerate_programs())
    out = []
    for p in progs:
        exe_base = (p.get('exe') or '').replace('.exe', '').lower()
        name     = _pretty_name(p)
        out.append({
            'hwnd':      p['hwnd'],
            'pid':       p.get('pid', 0),
            'exe':       p.get('exe') or '',
            'path':      p.get('path') or '',
            'name':      name,
            'title':     p.get('title') or '',
            'minimized': bool(p.get('minimized')),
            'visible':   bool(p.get('visible')),
            'in_apps':   any(k == exe_base or k == name.lower() for k in apps_keys),
        })
    return out


def add_to_apps(name: str, exe_basename: str, path: str = '') -> dict:
    """Append a new [name, command] pair to apps.json.

    *path* (the full executable path) is preferred over *exe_basename* — it
    avoids ambiguity when multiple installed apps share a binary name and
    means `apps.open_app` doesn't have to do a registry App-Paths lookup.
    """
    name = (name or '').strip().lower()
    path = (path or '').strip()
    exe  = (exe_basename or '').strip()
    if not name or (not path and not exe):
        return {'ok': False, 'error': 'name + path required'}
    # Choose what we actually store: full path if we have it, else the bare
    # exe name (which apps._resolve_exe will look up via App Paths registry).
    command = path if path else (exe if exe.lower().endswith('.exe') else exe + '.exe')
    try:
        data = json.loads(_APPS_FILE.read_text()) if _APPS_FILE.exists() else []
    except Exception:
        data = []
    # Don't duplicate
    for entry in data:
        if isinstance(entry, (list, tuple)) and len(entry) >= 1 and entry[0].lower() == name:
            return {'ok': False, 'error': f"{name!r} is already in apps"}
    data.append([name, command])
    try:
        _APPS_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    return {'ok': True}


# ─── Internal ───────────────────────────────────────────────────────────────

def _apps_keys() -> set[str]:
    try:
        data = json.loads(_APPS_FILE.read_text())
        return {entry[0].lower().replace('.exe', '') for entry in data
                if isinstance(entry, (list, tuple)) and len(entry) >= 1}
    except Exception:
        return set()


def _pretty_name(p: dict) -> str:
    """Strip '.exe' from the binary and Title-Case it for display."""
    exe = (p.get('exe') or '').replace('.exe', '')
    if exe:
        return exe.title()
    return (p.get('title') or 'window').split(' - ')[0].strip()


def _dedupe_by_exe(progs: list[dict]) -> list[dict]:
    """Multiple windows from the same process? Keep one — prefer non-minimized."""
    seen: dict[str, dict] = {}
    for p in progs:
        key = (p.get('exe') or '').lower()
        if not key:
            continue
        existing = seen.get(key)
        if existing is None:
            seen[key] = p
        elif existing.get('minimized') and not p.get('minimized'):
            seen[key] = p
    return list(seen.values())
