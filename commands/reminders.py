"""Reminders & timers.

Backed by a JSON list at ~/.eve/reminders.json. Each entry:

    {
      "id":         "8f0a…",            # stable 8-char id
      "message":    "call mom",
      "trigger":    "2026-06-17T15:00:00",   # next fire time (ISO)
      "fired":      false,
      "recurrence": null | {              # None for one-shot
          "kind": "daily",   "hour": 8, "minute": 0
        # "kind": "weekly",  "hour": 9, "minute": 0, "weekdays": [0,4]
        # "kind": "interval","seconds": 1800
      },
      "created":    "2026-06-17T14:55:00"
    }

Supports relative ("in 5 minutes"), absolute ("at 3pm", "tomorrow at 9"),
and recurring ("every weekday at 7am") reminders, plus voice follow-ups
("cancel it" / "add 2 minutes") via the converse layer.
"""
import json
import re
import time
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from core import timeparse

_STORE = Path.home() / ".eve" / "reminders.json"
_STORE.parent.mkdir(exist_ok=True)
_lock = threading.Lock()


def _load() -> list:
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:
            return []
    return []


def _save(data: list):
    _STORE.write_text(json.dumps(data, indent=2))


def _fmt_minutes(minutes: float) -> str:
    return f"{minutes:g}"


# ── Recurrence helpers ─────────────────────────────────────────────────────

def _advance(entry: dict, now: datetime):
    """Next fire time for a recurring entry strictly after `now`, or None for
    one-shots (which are simply marked fired)."""
    rec = entry.get("recurrence")
    if not rec:
        return None
    kind = rec.get("kind")
    if kind == "interval":
        secs = max(1, int(rec.get("seconds", 60)))
        nxt = datetime.fromisoformat(entry["trigger"])
        while nxt <= now:
            nxt += timedelta(seconds=secs)
        return nxt
    hour = int(rec.get("hour", 9))
    minute = int(rec.get("minute", 0))
    if kind == "daily":
        cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        return cand
    if kind == "weekly":
        weekdays = rec.get("weekdays") or list(range(7))
        cands = []
        for wd in weekdays:
            days = (wd - now.weekday()) % 7
            c = now.replace(hour=hour, minute=minute, second=0, microsecond=0) \
                   + timedelta(days=days)
            if c <= now:
                c += timedelta(days=7)
            cands.append(c)
        return min(cands) if cands else None
    return None


def _recurrence_label(rec: dict) -> str:
    if not rec:
        return ""
    kind = rec.get("kind")
    if kind == "interval":
        secs = int(rec.get("seconds", 0))
        if secs % 3600 == 0:
            n = secs // 3600
            return "every hour" if n == 1 else f"every {n} hours"
        n = max(1, secs // 60)
        return "every minute" if n == 1 else f"every {n} minutes"
    clock = timeparse._fmt_clock(int(rec.get("hour", 9)), int(rec.get("minute", 0)))
    if kind == "daily":
        return f"every day at {clock}"
    if kind == "weekly":
        wds = rec.get("weekdays") or []
        if wds == [0, 1, 2, 3, 4]:
            who = "weekday"
        elif wds == [5, 6]:
            who = "weekend"
        elif len(wds) == 1:
            who = timeparse._WEEKDAY_NAMES[wds[0]]
        else:
            who = ", ".join(timeparse._WEEKDAY_NAMES[w][:3] for w in wds)
        return f"every {who} at {clock}"
    return ""


def _when_label(entry: dict, now: datetime = None) -> str:
    """Human label for when this fires — for speech and the UI panel."""
    if entry.get("recurrence"):
        return _recurrence_label(entry["recurrence"])
    now = now or datetime.now()
    trig = datetime.fromisoformat(entry["trigger"])
    clock = timeparse._fmt_clock(trig.hour, trig.minute)
    if trig.date() == now.date():
        return f"today at {clock}"
    if trig.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {clock}"
    return f"{trig.strftime('%a %b %d')} at {clock}"


# ── Creation ───────────────────────────────────────────────────────────────

def _create(message: str, trigger: datetime, recurrence=None) -> str:
    """Insert a reminder and register voice follow-ups. Returns its id."""
    rid = uuid.uuid4().hex[:8]
    with _lock:
        data = _load()
        data.append({
            "id":         rid,
            "message":    (message or "").strip(),
            "trigger":    trigger.isoformat(timespec="seconds"),
            "fired":      False,
            "recurrence": recurrence,
            "created":    datetime.now().isoformat(timespec="seconds"),
        })
        _save(data)
    return rid


def _register_followups(rid: str, what: str):
    from core import session as _sess_mod
    _sess_mod.start_converse(
        _followup_handler(rid, what),
        label=f"{what} {rid}",
        turns=5,
        ttl=180.0,
    )
    _sess_mod.set_last_action(_sess_mod.LastAction(
        description=what,
        cancelable=lambda: cancel_one(rid),
    ))


def schedule(message: str, when: dict, what: str = "reminder") -> str:
    """Create a reminder from a parsed `when` dict (see core.timeparse)."""
    rid = _create(message, when["trigger"], when.get("recurrence"))
    _register_followups(rid, what)
    msg = (message or "").strip()
    label = when.get("label") or _when_label(_get(rid))
    if msg:
        return f"Okay, I'll remind you to {msg} {label}."
    return f"Reminder set {label}."


def set_reminder(minutes: str, message: str, what: str = "reminder") -> str:
    """Relative reminder: 'remind me in N minutes to X'. Kept for the existing
    intent and for set_timer."""
    mins = float(minutes)
    trigger = datetime.now() + timedelta(minutes=mins)
    rid = _create(message, trigger, None)
    _register_followups(rid, what)
    return f"{what.capitalize()} set for {_fmt_minutes(mins)} minutes: {message}"


def set_timer(minutes: str) -> str:
    mins = float(minutes)
    return set_reminder(minutes, f"{_fmt_minutes(mins)} minute timer", what="timer")


# ── Targeted edits ─────────────────────────────────────────────────────────

def _get(rid: str):
    for r in _load():
        if r.get("id") == rid:
            return r
    return None


def cancel_one(rid: str):
    """Remove a single reminder by id. True if removed, None if absent."""
    with _lock:
        data = _load()
        kept = [r for r in data if r.get("id") != rid]
        if len(kept) == len(data):
            return None
        _save(kept)
    return True


def _reschedule(rid: str, when: datetime):
    with _lock:
        data = _load()
        for r in data:
            if r.get("id") == rid:
                r["trigger"] = when.isoformat(timespec="seconds")
                r["fired"] = False
                _save(data)
                return True
    return None


def _remaining_minutes(rid: str):
    now = datetime.now()
    r = _get(rid)
    if r and not r["fired"]:
        secs = (datetime.fromisoformat(r["trigger"]) - now).total_seconds()
        return max(0.0, secs / 60.0)
    return None


# ── Listing / bulk ──────────────────────────────────────────────────────────

def _pending(now: datetime = None) -> list:
    now = now or datetime.now()
    out = []
    for r in _load():
        if r.get("recurrence"):
            out.append(r)
        elif not r["fired"] and datetime.fromisoformat(r["trigger"]) > now:
            out.append(r)
    return sorted(out, key=lambda x: x["trigger"])


def list_reminders() -> str:
    pending = _pending()
    if not pending:
        return "No pending reminders"
    items = [f"{r['message']} {_when_label(r)}" for r in pending]
    return "Reminders: " + ", ".join(items)


def cancel_all() -> str:
    with _lock:
        _save([])
    return "All reminders cancelled"


def get_panel_payload() -> list:
    """List shaped for the Reminders UI panel."""
    now = datetime.now()
    out = []
    for r in _pending(now):
        out.append({
            "id":        r.get("id", ""),
            "message":   r.get("message", ""),
            "when":      _when_label(r, now),
            "trigger":   r.get("trigger", ""),
            "recurring": bool(r.get("recurrence")),
        })
    return out


def panel_set(rid: str, message: str, when_text: str) -> dict:
    """Create or update a reminder from the UI panel. `when_text` is a natural
    phrase ('tomorrow at 9am', 'every weekday at 7'). Returns {ok, error}."""
    when = timeparse.parse_when(when_text)
    if when is None:
        return {"ok": False, "error": f'Could not understand time "{when_text}"'}
    existing = _get(rid) if rid else None
    if existing:
        with _lock:
            data = _load()
            for r in data:
                if r.get("id") == rid:
                    r["message"] = (message or "").strip()
                    r["trigger"] = when["trigger"].isoformat(timespec="seconds")
                    r["recurrence"] = when.get("recurrence")
                    r["fired"] = False
                    _save(data)
                    break
        return {"ok": True}
    _create(message, when["trigger"], when.get("recurrence"))
    return {"ok": True}


# ── Converse follow-up handler ──────────────────────────────────────────────
_CANCEL_RE = re.compile(
    r"^(?:never\s*mind|forget\s+it|"
    r"(?:cancel|stop|kill|clear)(?:\s+(?:it|that|the\s+(?:timer|reminder)))?)\s*$",
    re.I,
)
_ADD_RE   = re.compile(r"\b(?:add|give\s+me|plus)\s+(\d+)\s+(?:more\s+)?minutes?\b", re.I)
_SET_RE   = re.compile(r"\bmake\s+it\s+(\d+)\s+minutes?\b", re.I)
_QUERY_RE = re.compile(r"\bhow\s+(?:long|much\s+time)\b|\bwhen\s+does\s+it\b", re.I)


def _followup_handler(rid: str, what: str):
    from core import session as _sess_mod

    def handle(text: str):
        if _CANCEL_RE.search(text):
            _sess_mod.clear_converse()
            return (f"{what.capitalize()} cancelled."
                    if cancel_one(rid) else f"That {what}'s already done.")

        m = _SET_RE.search(text)
        if m:
            mins = int(m.group(1))
            if _reschedule(rid, datetime.now() + timedelta(minutes=mins)):
                return f"{what.capitalize()} now set for {mins} minutes."
            return f"That {what}'s no longer running."

        m = _ADD_RE.search(text)
        if m:
            add = int(m.group(1))
            left = _remaining_minutes(rid)
            if left is None:
                return f"That {what}'s no longer running."
            total = left + add
            if _reschedule(rid, datetime.now() + timedelta(minutes=total)):
                return f"Added {add} minutes — {_fmt_minutes(round(total, 1))} minutes left."
            return f"That {what}'s no longer running."

        if _QUERY_RE.search(text):
            left = _remaining_minutes(rid)
            if left is None:
                return f"That {what}'s no longer running."
            return f"{_fmt_minutes(round(left, 1))} minutes left."

        return None  # decline

    return handle


# ── Background checker ───────────────────────────────────────────────────────

def start_checker(on_reminder, on_change=None):
    """Spawn the daemon that fires due reminders. `on_reminder(message)` is
    called when one triggers; optional `on_change()` fires whenever the store
    changes so a UI panel can refresh."""
    def _check():
        while True:
            try:
                with _lock:
                    data = _load()
                    now = datetime.now()
                    changed = False
                    fired_msgs = []
                    for r in data:
                        if r["fired"]:
                            continue
                        if datetime.fromisoformat(r["trigger"]) <= now:
                            fired_msgs.append(r["message"])
                            nxt = _advance(r, now)
                            if nxt is not None:        # recurring → re-arm
                                r["trigger"] = nxt.isoformat(timespec="seconds")
                            else:                       # one-shot → done
                                r["fired"] = True
                            changed = True
                    if changed:
                        _save(data)
                # Notify outside the lock so callbacks can read the store.
                for msg in fired_msgs:
                    try:
                        on_reminder(msg)
                    except Exception:
                        pass
                if changed and on_change:
                    try:
                        on_change()
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(15)

    threading.Thread(target=_check, daemon=True).start()
