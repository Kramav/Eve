import json
import shutil
import threading
from pathlib import Path

_FILE = Path(__file__).parent.parent / 'features.json'

DEFAULTS = {
    'tts':          True,
    'youtube':      True,    # YouTube HUD feed browser (default experience)
    'web_search':   True,
    'reminders':    True,
    'apps':         True,
    'tiling':       True,
    'notifications':   True,  # Windows toast on reminders (in addition to TTS)
    'game_protection': True,  # auto-protect a fullscreen/essential app from focus steal
    'verify_commands': True,  # confirm a command's side effect happened, retry once, report honestly
    # ── Alpha (experimental) — default off, each has a fallback when off ──
    'inapp_search': False,   # off → search falls back to opening Firefox
    'mpv_youtube':  False,   # off → YouTube uses the HUD feed instead of mpv
}

# Experimental features, grouped separately in the UI.
ALPHA = {'inapp_search', 'mpv_youtube'}

# Human-readable labels sent to the UI
LABELS = {
    'tts':          'Text-to-Speech',
    'youtube':      'YouTube',
    'web_search':   'Web Search',
    'reminders':    'Reminders & Timers',
    'apps':         'App Launcher',
    'tiling':       'Window Tiling',
    'notifications':   'Reminder Notifications (toast)',
    'game_protection': 'Game / Focus Protection',
    'verify_commands': 'Verify Commands Ran',
    'inapp_search': 'In-app Search Results (DDG)',
    'mpv_youtube':  'YouTube via mpv',
}

# Why a feature is unavailable — shown as a tooltip in the UI
_UNAVAILABLE_REASONS = {
    'mpv_youtube': 'mpv not found on PATH — install with: winget install mpv',
    'tts':         'Voice model missing — run setup.py to download it',
}

_lock   = threading.Lock()
_state:  dict = {}
_status: dict = {}   # 'ok' | 'unavailable' per feature key


# ── user preference store ────────────────────────────────────────────────────

def _load():
    global _state
    try:
        _state = {**DEFAULTS, **json.loads(_FILE.read_text())}
    except Exception:
        _state = dict(DEFAULTS)


def get(key: str) -> bool:
    """True only if the feature is user-enabled AND runtime-available."""
    return bool(_state.get(key, True)) and _status.get(key, 'ok') == 'ok'


def set_feature(key: str, value: bool):
    with _lock:
        _state[key] = bool(value)
        try:
            _FILE.write_text(json.dumps(_state, indent=2))
        except Exception:
            pass


def all_features() -> dict:
    return dict(_state)


def alpha_keys() -> list:
    """Keys that belong to the experimental 'Alpha' group (for UI grouping)."""
    return [k for k in DEFAULTS if k in ALPHA]


# ── runtime availability checks ──────────────────────────────────────────────

def _compute_status() -> dict:
    status = {}

    # mpv-based YouTube (alpha) requires mpv on PATH. The default YouTube HUD
    # browser does not, so 'youtube' stays available regardless.
    status['mpv_youtube'] = 'ok' if shutil.which('mpv') else 'unavailable'

    # TTS: needs a usable engine. Kokoro (if selected) needs its model files;
    # otherwise Piper needs its .onnx voice. "auto" is ok if either is present.
    try:
        from config import (TTS_ENGINE, TTS_DEFAULT_VOICE, TTS_VOICES_DIR,
                            TTS_KOKORO_MODEL, TTS_KOKORO_VOICES)
        engine = (TTS_ENGINE or 'piper').lower()
        piper_ok  = (Path(TTS_VOICES_DIR) / f'{TTS_DEFAULT_VOICE}.onnx').exists()
        kokoro_ok = Path(TTS_KOKORO_MODEL).exists() and Path(TTS_KOKORO_VOICES).exists()
        if engine == 'kokoro':
            ok = kokoro_ok or piper_ok        # kokoro engine falls back to piper
        elif engine == 'auto':
            ok = kokoro_ok or piper_ok
        else:
            ok = piper_ok
        status['tts'] = 'ok' if ok else 'unavailable'
    except Exception:
        status['tts'] = 'unavailable'

    # All other features are pure Python — always available
    for key in DEFAULTS:
        if key not in status:
            status[key] = 'ok'

    return status


def refresh_status():
    """Re-check runtime availability of all features. Called at startup and on demand."""
    global _status
    _status = _compute_status()


def get_status(key: str) -> str:
    """Returns 'ok' or 'unavailable'."""
    return _status.get(key, 'ok')


def all_status() -> dict:
    return dict(_status)


def unavailable_reason(key: str) -> str:
    return _UNAVAILABLE_REASONS.get(key, 'Feature unavailable')


# ── init ─────────────────────────────────────────────────────────────────────

_load()
refresh_status()
