import json
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

_STORE = Path.home() / ".eve" / "reminders.json"
_STORE.parent.mkdir(exist_ok=True)


def _load() -> list:
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:
            return []
    return []


def _save(data: list):
    _STORE.write_text(json.dumps(data, indent=2))


def set_reminder(minutes: str, message: str) -> str:
    trigger = (datetime.now() + timedelta(minutes=float(minutes))).isoformat()
    data = _load()
    data.append({"message": message.strip(), "trigger": trigger, "fired": False})
    _save(data)
    return f"Reminder set for {minutes} minutes: {message}"


def set_timer(minutes: str) -> str:
    return set_reminder(minutes, f"{minutes} minute timer")


def list_reminders() -> str:
    now = datetime.now()
    pending = [
        r for r in _load()
        if not r["fired"] and datetime.fromisoformat(r["trigger"]) > now
    ]
    if not pending:
        return "No pending reminders"
    items = [
        f"{datetime.fromisoformat(r['trigger']).strftime('%I:%M %p')}: {r['message']}"
        for r in sorted(pending, key=lambda x: x["trigger"])
    ]
    return "Reminders: " + ", ".join(items)


def cancel_all() -> str:
    _save([])
    return "All reminders cancelled"


def start_checker(on_reminder):
    def _check():
        while True:
            try:
                data = _load()
                now = datetime.now()
                changed = False
                for r in data:
                    if not r["fired"] and datetime.fromisoformat(r["trigger"]) <= now:
                        on_reminder(r["message"])
                        r["fired"] = True
                        changed = True
                if changed:
                    _save(data)
            except Exception:
                pass
            time.sleep(30)

    threading.Thread(target=_check, daemon=True).start()
