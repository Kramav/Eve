"""Persistent key/value memory for Eve.

Simple JSON store at `eve_memory.json` at the repo root. Keys are
case-folded to lowercase on save and lookup so "Work monitor" and
"work monitor" hit the same slot. Values are stored as-is.

All public functions are thread-safe via a module-level lock — fine
for our single-process Eve runtime, though contention is essentially
zero (memory ops happen at voice-command speed).
"""
import json
from pathlib import Path
from threading import Lock

_FILE = Path(__file__).parent.parent / "eve_memory.json"
_lock = Lock()


def _load() -> dict:
    if not _FILE.exists():
        return {}
    try:
        return json.loads(_FILE.read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    _FILE.write_text(json.dumps(d, indent=2))


def remember(key: str, value: str) -> bool:
    """Store *value* under *key* (case-folded). Returns False if key empty."""
    key = (key or '').strip().lower()
    if not key:
        return False
    with _lock:
        d = _load()
        d[key] = (value or '').strip()
        _save(d)
    return True


def recall(key: str) -> str | None:
    """Retrieve the value for *key*, or None if not remembered."""
    with _lock:
        return _load().get((key or '').strip().lower())


def forget(key: str) -> bool:
    """Drop *key* if present. Returns True on a real removal."""
    with _lock:
        d = _load()
        k = (key or '').strip().lower()
        if k not in d:
            return False
        del d[k]
        _save(d)
    return True


def all_memories() -> dict:
    """Snapshot of every stored key/value pair."""
    with _lock:
        return dict(_load())
