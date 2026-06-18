"""Persistent memory + non-specific follow-ups.

Two responsibility groups:

  - Memory: `remember_voice` / `recall_voice` / `forget_voice` /
    `list_memories_voice` / `open_memory_panel` — wrap the `core.memory`
    JSON store for voice access.

  - Follow-ups: `undo` / `close_last` / `cancel_last` consult
    `session.last_action` (set by side-effecting handlers) to resolve
    pronoun commands like "go back" / "close that window" / "cancel it".
"""
import ctypes

from core import memory
from core import session as _sess
from core import timeparse

_display = None


def set_display(display):
    global _display
    _display = display


# ── Persistent memory ────────────────────────────────────────────────

def remember_voice(key: str, value: str) -> str:
    key = (key or '').strip()
    value = (value or '').strip()
    if memory.remember(key, value):
        return f"Got it — remembering {key} is {value}."
    return "Couldn't remember that."


def recall_voice(key: str) -> str:
    key = (key or '').strip()
    v = memory.recall(key)
    if v is None:
        return f"I don't remember {key}."
    return f"{key} is {v}."


def forget_voice(key: str) -> str:
    key = (key or '').strip()
    if memory.forget(key):
        return f"Forgot {key}."
    return f"I didn't have anything for {key}."


def list_memories_voice(key: str = '') -> str:
    """Voice: 'what do you remember' (no key) → list a few; with a key →
    same as recall_voice (the regex captures the optional 'about X' tail)."""
    if (key or '').strip():
        return recall_voice(key)
    d = memory.all_memories()
    if not d:
        return "I don't remember anything yet."
    pairs = ', '.join(f"{k} is {v}" for k, v in list(d.items())[:6])
    more  = '' if len(d) <= 6 else f", plus {len(d) - 6} more"
    return f"I remember: {pairs}{more}."


def open_memory_panel() -> str:
    if _display is not None:
        _display.open_memory()
    return ""


# ── Reminders ────────────────────────────────────────────────────────────

def open_reminders_panel() -> str:
    if _display is not None:
        _display.open_reminders()
    return ""


def remind(body: str) -> str:
    """Voice: 'remind me to <X> [<when>]'. Splits the task from a trailing
    time phrase. If no time is given, asks 'When?' and waits for the answer
    via the converse layer."""
    from commands import reminders

    message, when = timeparse.split(body or '')
    if when is not None:
        return reminders.schedule(message, when)

    # No time → ask, and claim the next utterance as the answer.
    task = (body or '').strip()

    def _await_time(text: str):
        t = text.strip().lower()
        if reminders._CANCEL_RE.search(t):
            _sess.clear_converse()
            return "Okay, never mind."
        w = timeparse.parse_when(t)
        if w is None:
            return None  # not a time — let it route normally
        _sess.clear_converse()
        return reminders.schedule(task, w)

    _sess.start_converse(_await_time, label="reminder when?", turns=3, ttl=60.0)
    return f"When should I remind you to {task}?"


# ── Non-specific follow-ups ──────────────────────────────────────────

def undo() -> str:
    """Voice: 'go back' / 'undo that' / 'revert'."""
    s = _sess.get()
    if not s.last_action or not s.last_action.undo:
        return "Nothing to undo."
    desc = s.last_action.description
    try:
        s.last_action.undo()
    except Exception as e:
        return f"Couldn't undo: {e}"
    s.last_action = None
    return f"Undid: {desc}."


def close_last() -> str:
    """Voice: 'close that window' / 'close that' / 'close it'."""
    s = _sess.get()
    if not s.last_action or not s.last_action.target_hwnd:
        return "No window to close."
    ctypes.windll.user32.PostMessageW(s.last_action.target_hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return "Closed."


def cancel_last() -> str:
    """Voice: 'cancel it' / 'cancel that'."""
    s = _sess.get()
    if not s.last_action or not s.last_action.cancelable:
        return "Nothing to cancel."
    desc = s.last_action.description
    try:
        s.last_action.cancelable()
    except Exception as e:
        return f"Couldn't cancel: {e}"
    s.last_action = None
    return f"Cancelled: {desc}."
