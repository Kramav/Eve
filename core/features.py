import json
import threading
from pathlib import Path

_FILE = Path(__file__).parent.parent / 'features.json'

DEFAULTS = {
    'tts':        True,
    'youtube':    True,
    'web_search': True,
    'reminders':  True,
    'apps':       True,
    'tiling':     True,
}

# Human-readable labels sent to the UI
LABELS = {
    'tts':        'Text-to-Speech',
    'youtube':    'YouTube',
    'web_search': 'Web Search',
    'reminders':  'Reminders & Timers',
    'apps':       'App Launcher',
    'tiling':     'Window Tiling',
}

_lock  = threading.Lock()
_state: dict = {}


def _load():
    global _state
    try:
        _state = {**DEFAULTS, **json.loads(_FILE.read_text())}
    except Exception:
        _state = dict(DEFAULTS)


def get(key: str) -> bool:
    return _state.get(key, True)


def set_feature(key: str, value: bool):
    with _lock:
        _state[key] = bool(value)
        try:
            _FILE.write_text(json.dumps(_state, indent=2))
        except Exception:
            pass


def all_features() -> dict:
    return dict(_state)


_load()
