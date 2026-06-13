"""
Utilities for placing newly opened app windows on an unused monitor without
stealing focus from the currently active window.
"""

import ctypes
import ctypes.wintypes
import time

_u32 = ctypes.windll.user32


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
HWND_BOTTOM    = 1   # place behind all other windows


def get_target_monitor():
    """Return the work-area rect of a monitor NOT currently hosting the
    foreground window.  Returns None when only one monitor is connected."""
    monitors = []

    def _cb(hMon, *_):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        _u32.GetMonitorInfoW(hMon, ctypes.byref(info))
        r = info.rcWork
        monitors.append({'handle': hMon, 'rect': (r.left, r.top, r.right, r.bottom)})
        return True

    _u32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    if len(monitors) < 2:
        return None

    hwnd = _u32.GetForegroundWindow()
    current = _u32.MonitorFromWindow(hwnd, 2) if hwnd else None  # MONITOR_DEFAULTTONEAREST

    for m in monitors:
        if m['handle'] != current:
            return m['rect']
    return None


def snapshot_windows():
    """Frozenset of visible top-level HWNDs with dimensions > 100 px."""
    found = set()

    def _cb(hwnd, _):
        if _u32.IsWindowVisible(hwnd):
            r = ctypes.wintypes.RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(r))
            if (r.right - r.left) > 100 and (r.bottom - r.top) > 100:
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
                _u32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE — restore if minimised
                _u32.SetWindowPos(
                    hwnd, HWND_BOTTOM, nx, ny, 0, 0,
                    SWP_NOSIZE | SWP_NOACTIVATE,
                )
            else:
                _u32.SetWindowPos(
                    hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                )
        return
