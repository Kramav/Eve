"""Voice control over Discord — three modes:

  - In-call essentials (mute/deafen/disconnect) use Discord's user-configured
    global keybinds, so the user keeps their game/browser focus.
  - Navigation (channel/server/quick-switcher) focuses Discord briefly and
    restores the previous foreground.
  - Send message opens the quick switcher, types recipient, presses Enter,
    types the message, and sends. Multi-step + timing-dependent.

Keybinds are read from `discord_keys.json` at the repo root; defaults fill
in any missing fields so the file is safe to be incomplete.
"""
import json
import time
from pathlib import Path

from commands import tiling
from core import key_ops

_CONFIG = Path(__file__).parent.parent / "discord_keys.json"

_DEFAULTS = {
    "global_keybinds": {
        "mute":       "ctrl+shift+alt+m",
        "deafen":     "ctrl+shift+alt+d",
        "disconnect": "ctrl+shift+alt+h",
    },
    "in_app_shortcuts": {
        "next_channel":  "alt+down",
        "prev_channel":  "alt+up",
        "next_server":   "ctrl+alt+down",
        "prev_server":   "ctrl+alt+up",
        "quick_switch":  "ctrl+k",
    },
}


def _load_config() -> dict:
    """Read discord_keys.json with defaults filled in for missing keys."""
    merged = {k: dict(v) for k, v in _DEFAULTS.items()}
    try:
        data = json.loads(_CONFIG.read_text())
    except Exception:
        return merged
    for k, v in data.items():
        if isinstance(v, dict) and k in merged:
            merged[k].update(v)
    return merged


def _discord_hwnd() -> int | None:
    """Locate Discord's main window via the existing fuzzy matcher."""
    match = tiling.find_window_by_spoken_name("discord")
    return match['hwnd'] if match else None


# ── In-call essentials — global keybinds, no focus theft ──────────────────

def mute() -> str:
    key_ops.press_global(_load_config()['global_keybinds']['mute'])
    return "Toggled mute."


def deafen() -> str:
    key_ops.press_global(_load_config()['global_keybinds']['deafen'])
    return "Toggled deafen."


def disconnect() -> str:
    key_ops.press_global(_load_config()['global_keybinds']['disconnect'])
    return "Disconnecting from voice."


# ── Navigation — focus Discord briefly, restore previous foreground ───────

def _deferred() -> str | None:
    """If a protected program is active, return a decline message (these paths
    steal focus); else None to proceed."""
    from core import essential
    act = essential.active()
    if act:
        return f"Not switching to Discord — {act} is protected. Say 'stop protecting {act}' first."
    return None


def _focused_press(shortcut_key: str, success_msg: str) -> str:
    deferred = _deferred()
    if deferred:
        return deferred
    hwnd = _discord_hwnd()
    if not hwnd:
        return "Discord isn't open."
    hotkey = _load_config()['in_app_shortcuts'].get(shortcut_key)
    if not hotkey:
        return f"No shortcut configured for {shortcut_key}."
    key_ops.with_window_focused(hwnd, lambda: key_ops.press_global(hotkey))
    return success_msg


def next_channel() -> str:    return _focused_press('next_channel', 'Next channel.')
def prev_channel() -> str:    return _focused_press('prev_channel', 'Previous channel.')
def next_server()  -> str:    return _focused_press('next_server',  'Next server.')
def prev_server()  -> str:    return _focused_press('prev_server',  'Previous server.')
def quick_switcher() -> str:  return _focused_press('quick_switch', 'Opened Discord search.')


# ── Send message — focus Discord, open quick switcher, find, type, send ──

def send_message(recipient: str, text: str) -> str:
    deferred = _deferred()
    if deferred:
        return deferred
    hwnd = _discord_hwnd()
    if not hwnd:
        return "Discord isn't open."
    qs = _load_config()['in_app_shortcuts']['quick_switch']

    def do_it():
        # Clear any open modal/dialog so quick-switcher actually opens
        key_ops.press_global('escape'); time.sleep(0.06)
        key_ops.press_global(qs);       time.sleep(0.25)
        key_ops.type_text(recipient.strip()); time.sleep(0.35)  # fuzzy match
        key_ops.press_global('enter');  time.sleep(0.20)        # open DM/channel
        key_ops.type_text(text.strip())
        key_ops.press_global('enter')                            # send

    key_ops.with_window_focused(hwnd, do_it)
    return f"Messaged {recipient}: {text}"
