import ctypes
import json
import os
import subprocess
import threading
import winreg
from pathlib import Path
from core.response import Silent

_APPS_FILE = Path(__file__).parent.parent / "apps.json"

_APP_PATHS_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\App Paths'),
    (winreg.HKEY_CURRENT_USER,  r'SOFTWARE\Microsoft\Windows\App Paths'),
]


def _resolve_exe(cmd: str) -> str:
    """Expand a bare exe name (e.g. 'firefox.exe') to its full path via App Paths registry."""
    if os.sep in cmd or '/' in cmd:
        return cmd
    bare = cmd if cmd.lower().endswith('.exe') else cmd + '.exe'
    for hive, base in _APP_PATHS_KEYS:
        try:
            key = winreg.OpenKey(hive, rf'{base}\{bare}')
            path = winreg.QueryValue(key, '').strip().strip('"')
            winreg.CloseKey(key)
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass
    return cmd

_CLOSE_MAP = {
    "chrome":    "chrome.exe",
    "firefox":   "firefox.exe",
    "edge":      "msedge.exe",
    "spotify":   "Spotify.exe",
    "discord":   "Discord.exe",
    "vs code":   "Code.exe",
    "vscode":    "Code.exe",
    "notepad":   "notepad.exe",
    "teams":     "Teams.exe",
    "zoom":      "Zoom.exe",
    "obs":       "obs64.exe",
    "vlc":       "vlc.exe",
    "slack":     "slack.exe",
}


def _load_apps() -> dict:
    try:
        return {name.lower(): cmd for name, cmd in json.loads(_APPS_FILE.read_text())}
    except Exception:
        return {}


def open_app(name: str) -> str:
    name = name.strip()
    apps = _load_apps()
    cmd = apps.get(name.lower())

    if not cmd:
        return Silent(f"Unknown app: {name}")

    cmd = _resolve_exe(cmd)
    try:
        from core import monitor
        before = monitor.snapshot_windows()
        target = monitor.get_target_monitor()
        # SW_SHOWNOACTIVATE = 4: OS-level hint to open without stealing focus
        ctypes.windll.shell32.ShellExecuteW(None, "open", cmd, None, None, 4)
        threading.Thread(
            target=monitor.move_new_window,
            args=(before, target),
            daemon=True,
        ).start()
        return f"Opening {name}"
    except Exception:
        return f"Couldn't open {name}"


def close_app(name: str) -> str:
    name = name.strip()
    exe = _CLOSE_MAP.get(name.lower(), name)
    if not exe.endswith(".exe"):
        exe += ".exe"
    try:
        subprocess.run(f"taskkill /f /im {exe}", shell=True, capture_output=True)
        return f"Closed {name}"
    except Exception:
        return f"Couldn't close {name}"
