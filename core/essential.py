"""Protected ("essential") programs — a dynamic set Eve must never steal focus
from. Typically a game: while it's focused, Eve defers focus-stealing actions
(Discord nav, etc.) and keeps it in front of overlays.

Two sources decide whether protection is active *right now*:
  1. The user's saved list in settings.json -> `essential_programs` (matched
     against the foreground window's exe basename or title).
  2. Auto-detect: any borderless/exclusive fullscreen foreground app is treated
     as the current essential while it owns the screen, even if unlisted.

`active()` returns the protected name in effect, or None. `should_defer()` is
the one bool callers gate on.
"""
import ctypes
import json
from ctypes import wintypes
from pathlib import Path

from core.window_ops import fullscreen_app_running

_SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_u32.GetForegroundWindow.restype = wintypes.HWND


# ── settings.json list ─────────────────────────────────────────────────────

def _read() -> dict:
    try:
        return json.loads(_SETTINGS_FILE.read_text())
    except Exception:
        return {}


def list_all() -> list[str]:
    data = _read()
    return [str(x).lower() for x in data.get("essential_programs", [])]


def _write_list(names: list[str]) -> None:
    data = _read()
    data["essential_programs"] = names
    _SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def add(name: str) -> bool:
    """Add a program to the protected list. Returns False if already present."""
    name = (name or "").strip().lower()
    if not name:
        return False
    names = list_all()
    if name in names:
        return False
    names.append(name)
    _write_list(names)
    return True


def remove(name: str) -> bool:
    """Remove by name or substring match. Returns False if nothing matched."""
    name = (name or "").strip().lower()
    names = list_all()
    keep = [n for n in names if n != name and name not in n]
    if len(keep) == len(names):
        return False
    _write_list(keep)
    return True


# ── live foreground inspection ─────────────────────────────────────────────

def _foreground_exe_title() -> tuple[str, str]:
    """(exe basename lower without '.exe', window title lower) for the
    foreground window, or ('', '')."""
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    n = _u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    title = buf.value.lower()

    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if h:
        try:
            size = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(size.value)
            if _k32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)):
                exe = Path(path.value).stem.lower()  # 'eldenring.exe' -> 'eldenring'
        finally:
            _k32.CloseHandle(h)
    return exe, title


def active() -> str | None:
    """Name of the protected program currently in the foreground, or None.

    Matches the saved list against the foreground exe/title; failing that,
    auto-protects a borderless/fullscreen foreground app."""
    exe, title = _foreground_exe_title()
    for name in list_all():
        if name and (name in exe or name in title):
            return name
    if fullscreen_app_running():
        return exe or title or "fullscreen app"
    return None


def should_defer() -> bool:
    """True if Eve should avoid stealing focus right now."""
    return active() is not None


def foreground_name() -> str:
    """Best label for the current foreground window (exe, else title)."""
    exe, title = _foreground_exe_title()
    return exe or title or ""


if __name__ == "__main__":
    # ponytail: live smoke test — list round-trips through a temp settings file
    # Run as a module so package imports resolve:  python -m core.essential
    import tempfile, os
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    _SETTINGS_FILE = Path(tmp)  # type: ignore  # noqa: F811
    assert list_all() == []
    assert add("eldenring") is True
    assert add("eldenring") is False           # dupe rejected
    assert "eldenring" in list_all()
    assert remove("elden") is True             # substring removal
    assert list_all() == []
    os.unlink(tmp)
    print("foreground now:", foreground_name(), "| defer:", should_defer())
    print("ok")
