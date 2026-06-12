import json
import subprocess
from pathlib import Path
from core.response import Silent

_APPS_FILE = Path(__file__).parent.parent / "apps.json"

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

    # Quote paths that contain spaces so start "" handles them correctly
    quoted = f'"{cmd}"' if (" " in cmd and not cmd.startswith('"')) else cmd
    try:
        subprocess.Popen(f'start "" {quoted}', shell=True)
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
