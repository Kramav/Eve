"""
Utilities for placing newly opened app windows on an unused monitor without
stealing focus from the currently active window.
"""

import ctypes
import ctypes.wintypes
import json
import time
from pathlib import Path

_u32    = ctypes.windll.user32
_dwmapi = ctypes.windll.dwmapi

_SETTINGS_FILE = Path(__file__).parent.parent / 'settings.json'

_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def get_dwm_margins(hwnd: int) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) gap between the visible content of a
    DWM-composited window and its `GetWindowRect` bounds.

    On Win10/11, modern windows have an invisible margin (~7px) on three sides
    that's part of the drop-shadow / resize-hit-test area. SetWindowPos sets
    the *window* rect including this margin, which makes a "snapped" window
    appear narrower than its target zone. Adding these margins to the placement
    rect compensates so the *visible* content fills the zone."""
    win_rect = ctypes.wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(win_rect))
    vis_rect = ctypes.wintypes.RECT()
    hr = _dwmapi.DwmGetWindowAttribute(
        ctypes.wintypes.HWND(hwnd),
        ctypes.wintypes.DWORD(_DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(vis_rect),
        ctypes.sizeof(vis_rect),
    )
    if hr != 0:
        return 0, 0, 0, 0
    return (
        vis_rect.left   - win_rect.left,    # left  gap
        vis_rect.top    - win_rect.top,     # top   gap (usually 0)
        win_rect.right  - vis_rect.right,   # right gap
        win_rect.bottom - vis_rect.bottom,  # bottom gap
    )


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize',    ctypes.wintypes.DWORD),
        ('rcMonitor', ctypes.wintypes.RECT),
        ('rcWork',    ctypes.wintypes.RECT),
        ('dwFlags',   ctypes.wintypes.DWORD),
    ]

_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HMONITOR,
    ctypes.wintypes.HDC,
    ctypes.POINTER(ctypes.wintypes.RECT),
    ctypes.wintypes.LPARAM,
)

_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)

SWP_NOSIZE     = 0x0001
SWP_NOMOVE     = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_BOTTOM    = 1    # place behind all other windows
HWND_TOPMOST   = -1
HWND_NOTOPMOST = -2

# Proper signatures — without these the HMONITOR return defaults to 32-bit c_int
# and gets truncated on 64-bit Windows, so the "avoid the game's monitor" handle
# comparison in get_target_monitor() would silently never match.
_u32.MonitorFromWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.DWORD]
_u32.MonitorFromWindow.restype  = ctypes.c_void_p


def enumerate_work_areas() -> list[dict]:
    """Return every connected monitor's physical-pixel work-area rect.

    In a per-monitor-DPI-aware process (which main.py sets at startup),
    `EnumDisplayMonitors` reports each monitor's true physical bounds in
    the desktop virtual-screen coordinate system. Unlike Electron's
    DIP-based `screen.getAllDisplays()`, these values are directly usable
    with `SetWindowPos` and remain consistent across mixed-DPI setups.
    """
    out = []

    def _cb(hMon, *_):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if _u32.GetMonitorInfoW(hMon, ctypes.byref(info)):
            r = info.rcWork
            out.append({
                'x': r.left, 'y': r.top,
                'w': r.right - r.left,
                'h': r.bottom - r.top,
                'is_primary': bool(info.dwFlags & 1),  # MONITORINFOF_PRIMARY
            })
        return True

    _u32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    return out


def _rect_contains(rect_ltrb, point) -> bool:
    l, t, r, b = rect_ltrb
    px, py = point
    return l <= px < r and t <= py < b


def _select_target_monitor(monitors: list, avoid_index, companion_rect=None):
    """Pure companion-monitor picker (testable — no Win32).

    *monitors* is a list of ``{'rect': (l,t,r,b), 'is_primary': bool}`` in
    enumeration order. *avoid_index* is the index of the monitor hosting the
    user's task (the game) — we never place a new window there, so it doesn't
    land *behind* the game. This is why Eve strongly recommends a second monitor:
    with one, there's nowhere to put an opened window except behind the game
    (esp. exclusive fullscreen); with two, opened windows go to whichever screen
    the game *isn't* on.

    *companion_rect* (x, y, w, h) is the user-designated "Eve monitor" (for 3+
    monitor setups — see `eve_monitor_designated`). When it's a candidate (i.e.
    the game isn't on it) it wins; when the game IS on it, we fall back to the
    auto policy so opened windows still don't land behind the game.

    Returns the chosen work-area rect, or None when there's no other monitor
    (single-monitor → caller keeps the window behind the foreground, no focus
    steal). Among un-designated candidates, prefers the primary monitor (so
    windows land on your main screen when you game on a secondary), else the
    first other one.
    """
    if len(monitors) < 2:
        return None
    candidates = [m for i, m in enumerate(monitors) if i != avoid_index]
    if not candidates:
        return None
    # 1. Honor the designated Eve monitor if the game isn't sitting on it.
    if companion_rect:
        cx = companion_rect[0] + companion_rect[2] / 2
        cy = companion_rect[1] + companion_rect[3] / 2
        for m in candidates:
            if _rect_contains(m['rect'], (cx, cy)):
                return m['rect']
        # Designated monitor is where the game is → fall through to auto.
    # 2. Prefer the primary monitor as companion, else the first other one.
    for m in candidates:
        if m.get('is_primary'):
            return m['rect']
    return candidates[0]['rect']


# ── Eve-monitor designation (settings.json, written by Electron) ─────────────

def _read_settings() -> dict:
    try:
        return json.loads(_SETTINGS_FILE.read_text())
    except Exception:
        return {}


def _load_companion_rect():
    """Physical work-area rect (x, y, w, h) of the designated Eve monitor, or
    None. Electron writes `companionMonitorRect` via dipToScreenRect when the
    user designates a monitor, so the value is already in Win32 physical pixels."""
    r = _read_settings().get('companionMonitorRect')
    if isinstance(r, (list, tuple)) and len(r) == 4:
        try:
            return tuple(int(v) for v in r)
        except (TypeError, ValueError):
            return None
    return None


def eve_monitor_designated() -> bool:
    """True if the user has picked which monitor is Eve's (for opened windows)."""
    return bool(_read_settings().get('companionMonitorRect'))


# ── Designating the Eve monitor (Python-only — no Electron round-trip) ────────
# Deliberately resolved and persisted entirely in Python: enumerate_work_areas()
# already returns true physical rects (this process is per-monitor DPI aware), so
# we don't need Electron's dipToScreenRect. This keeps the feature off the Electron
# migration surface — see ROADMAP "Foundation" (Electron integration points).

def _resolve_ref_to_monitor(ref: str, monitors: list):
    """ref: '1'..'N' | 'primary' | 'left'|'middle'|'right'. → monitor dict | None."""
    if not monitors:
        return None
    if ref == 'primary':
        return next((m for m in monitors if m.get('is_primary')), None)
    if ref in ('left', 'middle', 'right'):
        ordered = sorted(monitors, key=lambda m: m['x'])
        if ref == 'left':
            return ordered[0]
        if ref == 'right':
            return ordered[-1]
        return ordered[len(ordered) // 2]
    if ref.isdigit():
        i = int(ref) - 1
        if 0 <= i < len(monitors):
            return monitors[i]
    return None


def _foreground_monitor(monitors: list):
    """The monitor the user's current window sits on ('this'/'current')."""
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return None
    r = ctypes.wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(r))
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    for m in monitors:
        if m['x'] <= cx < m['x'] + m['w'] and m['y'] <= cy < m['y'] + m['h']:
            return m
    return None


def _describe_monitor(m: dict, monitors: list) -> str:
    """A spoken label like 'right monitor (2560×1440)' so the user can confirm
    Eve picked the screen they meant (positional is numbering-agnostic)."""
    ordered = sorted(monitors, key=lambda x: x['x'])
    if m.get('is_primary'):
        pos = 'primary'
    elif len(ordered) >= 2 and m is ordered[0]:
        pos = 'left'
    elif len(ordered) >= 2 and m is ordered[-1]:
        pos = 'right'
    else:
        pos = 'middle'
    return f"{pos} monitor ({m['w']}×{m['h']})"


def _write_companion(rect, label: str) -> bool:
    """Persist companionMonitorRect [x,y,w,h] + a label to settings.json, merging
    with existing keys. Aborts rather than clobber if settings is unreadable."""
    try:
        raw = _SETTINGS_FILE.read_text() if _SETTINGS_FILE.exists() else '{}'
        data = json.loads(raw)
    except Exception:
        return False
    data['companionMonitorRect'] = [int(v) for v in rect]
    data['companionLabel']       = label
    try:
        _SETTINGS_FILE.write_text(json.dumps(data, indent=2))
        return True
    except Exception:
        return False


def set_eve_monitor(ref: str, monitors: list | None = None):
    """Designate which monitor Eve puts opened windows on. *ref* is
    '1'..'N' | 'primary' | 'left'|'middle'|'right' | 'this'|'current'.
    Returns (ok: bool, label_or_message: str)."""
    monitors = monitors if monitors is not None else enumerate_work_areas()
    if len(monitors) < 2:
        return False, "You only have one monitor, so there's nowhere separate to send Eve's windows."
    m = (_foreground_monitor(monitors) if ref in ('this', 'current')
         else _resolve_ref_to_monitor(ref, monitors))
    if not m:
        return False, ""
    label = _describe_monitor(m, monitors)
    if not _write_companion([m['x'], m['y'], m['w'], m['h']], label):
        return False, "I couldn't save that setting."
    return True, label


def count() -> int:
    """Number of connected monitors."""
    return len(enumerate_work_areas())


def companion_prompt():
    """A one-line nudge to designate an Eve monitor, or None. Fires only on 3+
    monitors when nothing is designated yet — with two monitors the choice is
    unambiguous (the screen the game isn't on) and no designation is needed."""
    if count() >= 3 and not eve_monitor_designated():
        return ("You have several monitors — tell me which is Eve's, so opened "
                "windows land there. Say, for example, 'set monitor 2 as Eve's monitor'.")
    return None


def get_target_monitor():
    """Work-area rect of a monitor NOT hosting the foreground window (the game),
    so opened windows appear beside the task rather than behind it. Prefers the
    designated Eve monitor when set. None when only one monitor is connected.
    See `_select_target_monitor` for the policy."""
    monitors = []

    def _cb(hMon, *_):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        _u32.GetMonitorInfoW(hMon, ctypes.byref(info))
        r = info.rcWork
        monitors.append({
            'handle':     hMon,
            'rect':       (r.left, r.top, r.right, r.bottom),
            'is_primary': bool(info.dwFlags & 1),   # MONITORINFOF_PRIMARY
        })
        return True

    _u32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)

    hwnd = _u32.GetForegroundWindow()
    current = _u32.MonitorFromWindow(hwnd, 2) if hwnd else None  # MONITOR_DEFAULTTONEAREST
    avoid_index = next((i for i, m in enumerate(monitors) if m['handle'] == current), None)
    return _select_target_monitor(monitors, avoid_index, _load_companion_rect())


def snapshot_windows(min_size: int = 100):
    """Frozenset of visible top-level HWNDs whose w and h both exceed min_size.

    Pass min_size=0 for the *before* snapshot taken prior to launching an app
    so that the compact Eve overlay (96×96) is included.  When the overlay later
    expands it keeps the same HWND and therefore never appears in (after − before).
    """
    found = set()

    def _cb(hwnd, _):
        if _u32.IsWindowVisible(hwnd):
            if min_size == 0:
                found.add(hwnd)
            else:
                r = ctypes.wintypes.RECT()
                _u32.GetWindowRect(hwnd, ctypes.byref(r))
                if (r.right - r.left) > min_size and (r.bottom - r.top) > min_size:
                    found.add(hwnd)
        return True

    _u32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return frozenset(found)


def move_new_window(before: frozenset, target, timeout: float = 8.0):
    """Daemon-thread target.

    Polls until a new visible window appears (not in *before*), then:
    - Multi-monitor (*target* is a rect): centres it on that monitor and
      puts it at the bottom of the z-order without activating.
    - Single-monitor (*target* is None): just sends it to the bottom of
      the z-order without moving or activating it.

    Neither path changes which window has keyboard focus.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        time.sleep(0.25)
        new = snapshot_windows() - before
        if not new:
            continue

        for hwnd in new:
            if target:
                mx, my, mr, mb = target
                mw, mh = mr - mx, mb - my
                r = ctypes.wintypes.RECT()
                _u32.GetWindowRect(hwnd, ctypes.byref(r))
                ww = r.right  - r.left
                wh = r.bottom - r.top
                nx = mx + max(0, (mw - ww) // 2)
                ny = my + max(0, (mh - wh) // 2)
                _u32.ShowWindow(hwnd, 4)   # SW_SHOWNOACTIVATE — restore if minimised
                _u32.SetWindowPos(
                    hwnd, HWND_BOTTOM, nx, ny, 0, 0,
                    SWP_NOSIZE | SWP_NOACTIVATE,
                )
            _u32.ShowWindow(hwnd, 3)       # SW_MAXIMIZE — fill monitor regardless of saved state
        return


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ('cbSize',           ctypes.wintypes.UINT),
        ('flags',            ctypes.wintypes.UINT),
        ('showCmd',          ctypes.wintypes.UINT),
        ('ptMinPosition',    ctypes.wintypes.POINT),
        ('ptMaxPosition',    ctypes.wintypes.POINT),
        ('rcNormalPosition', ctypes.wintypes.RECT),
    ]


def move_new_window_to_rect(before: frozenset, rect, timeout: float = 8.0):
    """Poll until a new visible window appears, then snap it to *rect*
    (x, y, w, h) without stealing focus.

    Used by snap-on-open: launch an app and place it directly in a zone.

    The app may launch maximised (Firefox, Chrome) — SetWindowPos is silently
    ignored on maximised windows, so we restore first.  We retry a few times
    in case Firefox launches an early splash and resizes its real window late.
    """
    deadline = time.monotonic() + timeout
    x, y, w, h = rect
    placed    = set()

    while time.monotonic() < deadline:
        time.sleep(0.25)
        new = snapshot_windows() - before - placed
        if not new:
            # keep re-asserting on already-placed windows for ~1s after first
            # placement, since Firefox often resizes itself shortly after.
            for hwnd in list(placed):
                _force_to_rect(hwnd, x, y, w, h)
            continue
        for hwnd in new:
            _force_to_rect(hwnd, x, y, w, h)
            placed.add(hwnd)
        # don't return immediately — wait one more poll cycle for late resizes
        if time.monotonic() > deadline - 1.0:
            return


def _force_to_rect(hwnd: int, x: int, y: int, w: int, h: int):
    wp = _WINDOWPLACEMENT()
    wp.cbSize = ctypes.sizeof(_WINDOWPLACEMENT)
    _u32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    if wp.showCmd == 3:  # SW_SHOWMAXIMIZED — un-maximize first or SetWindowPos is ignored
        _u32.ShowWindow(hwnd, 4)   # SW_SHOWNOACTIVATE
    # Expand the placement rect by DWM invisible borders so the *visible*
    # window fills the zone exactly. ml, mt, mr, mb are typically 7/0/7/7 on
    # Win10/11 modern apps and 0/0/0/0 for legacy windows.
    ml, mt, mr, mb = get_dwm_margins(hwnd)
    _u32.SetWindowPos(hwnd, None, x - ml, y - mt, w + ml + mr, h + mt + mb, SWP_NOACTIVATE)
    # Bring to top of regular z-order without stealing focus.
    from core.window_ops import raise_to_top_no_focus
    raise_to_top_no_focus(hwnd)
