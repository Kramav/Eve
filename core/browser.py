"""Chosen-browser primitive — open a URL in the user's browser, surfaced *beside*
the current task without stealing focus.

This is the flagship "look something up over a running game" path, generalized off
the old Firefox-only code in commands/search.py. Two responsibilities:

  1. **Chosen browser** — configurable via settings.json `browser`
     (`firefox` | `chrome` | `edge` | `brave` | `default` | a full exe path).
     A *known* browser lets us identify its window afterward and raise it without
     focus; `default` uses the OS handler (webbrowser) with no raise, since we
     can't reliably identify that window.

  2. **Focus invariant** — the browser is launched with `SW_SHOWNOACTIVATE` and
     then raised via `core.window_ops.raise_to_top_no_focus`. If a game / fullscreen
     app owns the screen, the page is left in the *background* rather than raised
     over it (with a second monitor it lands there; see core.monitor).

Kept in `core/` (an OS-integration primitive) so any feature — web search,
go-to-site, result-click — opens URLs the same focus-safe way.
"""
import json
import threading
import time
import webbrowser
from pathlib import Path

_SETTINGS_FILE = Path(__file__).parent.parent / 'settings.json'

# key → launch/find exe basename
_KNOWN = {
    'firefox': 'firefox.exe',
    'chrome':  'chrome.exe',
    'edge':    'msedge.exe',
    'brave':   'brave.exe',
}
# key → spoken window-match name (find_window_by_spoken_name scores exe + title)
_MATCH = {'firefox': 'firefox', 'chrome': 'chrome', 'edge': 'edge', 'brave': 'brave'}

_DEFAULT = 'firefox'   # preserves prior behavior; falls back to OS default if absent


def _read_settings() -> dict:
    try:
        return json.loads(_SETTINGS_FILE.read_text())
    except Exception:
        return {}


def configured_browser() -> str:
    """The user's chosen browser key (or raw path). Defaults to 'firefox'."""
    return (_read_settings().get('browser') or _DEFAULT).strip().lower()


def _find_exe(key: str):
    """Absolute path to a known browser's exe, or None if not installed."""
    if key == 'firefox':
        from commands.apps import find_firefox
        return find_firefox()
    exe = _KNOWN.get(key)
    if not exe:
        return None
    import shutil
    p = shutil.which(exe)
    if p:
        return p
    # Registry App Paths (installers register these) — HKCU then HKLM.
    import winreg
    sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, sub) as k:
                val, _ = winreg.QueryValueEx(k, None)
                if val and Path(val).exists():
                    return val
        except OSError:
            pass
    # Common install locations as a last resort.
    import os
    pf   = os.environ.get('ProgramFiles', r'C:\Program Files')
    pfx  = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    lad  = os.environ.get('LOCALAPPDATA', '')
    candidates = {
        'chrome': [rf"{pf}\Google\Chrome\Application\chrome.exe",
                   rf"{pfx}\Google\Chrome\Application\chrome.exe",
                   rf"{lad}\Google\Chrome\Application\chrome.exe"],
        'edge':   [rf"{pfx}\Microsoft\Edge\Application\msedge.exe",
                   rf"{pf}\Microsoft\Edge\Application\msedge.exe"],
        'brave':  [rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe",
                   rf"{pfx}\BraveSoftware\Brave-Browser\Application\brave.exe",
                   rf"{lad}\BraveSoftware\Brave-Browser\Application\brave.exe"],
    }
    for path in candidates.get(key, []):
        if path and Path(path).exists():
            return path
    return None


def resolve_browser(configured: str | None = None, finder=None):
    """Resolve the chosen browser to a spec dict, or None to mean "use the OS
    default browser" (no focus-safe raise possible).

    Returns {'key', 'path', 'match'} or None. *finder* is injectable for tests.
    """
    key = configured if configured is not None else configured_browser()
    key = (key or '').strip().lower()
    if not key or key == 'default':
        return None
    # A raw exe path.
    if key not in _KNOWN and (key.endswith('.exe') or '\\' in key or '/' in key):
        return {'key': 'custom', 'path': key, 'match': Path(key).stem} if Path(key).exists() else None
    if key not in _KNOWN:
        return None
    path = (finder or _find_exe)(key)
    if not path:
        return None
    return {'key': key, 'path': path, 'match': _MATCH.get(key, key)}


def open_url(url: str) -> None:
    """Open *url* in the chosen browser, surfaced beside the task (no focus steal).
    Falls back to the OS default browser when no known browser is configured/found."""
    spec = resolve_browser()
    if not spec:
        webbrowser.open(url)          # OS default — can't identify its window to raise
        return
    import ctypes
    # SW_SHOWNOACTIVATE = 4: ask the OS to open without taking foreground.
    ctypes.windll.shell32.ShellExecuteW(None, "open", spec['path'], url, None, 4)
    threading.Thread(target=_raise_when_ready, args=(spec['match'],), daemon=True).start()


def _raise_when_ready(match_name: str) -> None:
    """After the browser window appears, raise it above the task WITHOUT focus —
    unless a game owns the screen, in which case leave it backgrounded."""
    from core.window_ops import raise_to_top_no_focus, fullscreen_app_running
    from commands.tiling import find_window_by_spoken_name
    time.sleep(1.2)                   # let the browser create/raise its window
    if fullscreen_app_running():
        return                        # focus invariant: don't fight a fullscreen game
    match = find_window_by_spoken_name(match_name)
    if match:
        raise_to_top_no_focus(match['hwnd'])


def set_browser(key: str) -> tuple[bool, str]:
    """Persist the chosen browser to settings.json. *key* is a known name,
    'default', or a raw exe path. Returns (ok, resolved_label_or_message)."""
    key = (key or '').strip().lower()
    if key == 'default':
        label = 'your default browser'
    elif key in _KNOWN:
        if _find_exe(key) is None:
            return False, f"I couldn't find {key} installed."
        label = key
    elif key.endswith('.exe') and Path(key).exists():
        label = Path(key).stem
    else:
        return False, (f"I don't recognize the browser '{key}'. "
                       "Try Firefox, Chrome, Edge, Brave, or default.")
    try:
        raw = _SETTINGS_FILE.read_text() if _SETTINGS_FILE.exists() else '{}'
        data = json.loads(raw)
    except Exception:
        return False, "I couldn't save that setting."
    data['browser'] = key
    try:
        _SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        return False, "I couldn't save that setting."
    return True, label
