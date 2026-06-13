"""
core/window_manager.py

Window tracking and placement service.
Provides structured Win32 window data as the foundation for future
orchestration commands ("move Chrome to monitor 2", workspace presets).

Current scope: enumerate visible windows with geometry + monitor assignment,
and move a window to a target monitor work-area without activating it.

Future:
  - save_workspace(name)  / restore_workspace(name)
  - move_app_to_monitor(app_name, monitor_index)
"""

import ctypes
import ctypes.wintypes
from dataclasses import dataclass

_u32 = ctypes.windll.user32

_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)


@dataclass
class WindowInfo:
    hwnd:       int
    title:      str
    class_name: str
    x:          int
    y:          int
    width:      int
    height:     int
    monitor_id: int    # HMONITOR handle value

    def to_dict(self) -> dict:
        return {
            'hwnd':      self.hwnd,
            'title':     self.title,
            'className': self.class_name,
            'x':         self.x,
            'y':         self.y,
            'width':     self.width,
            'height':    self.height,
            'monitorId': self.monitor_id,
        }


def enumerate_windows(min_size: int = 100) -> list:
    """Return all visible top-level windows larger than min_size pixels on each axis."""
    results = []

    def _cb(hwnd, _):
        if not _u32.IsWindowVisible(hwnd):
            return True

        r = ctypes.wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right  - r.left
        h = r.bottom - r.top
        if w <= min_size or h <= min_size:
            return True

        title_buf = ctypes.create_unicode_buffer(256)
        _u32.GetWindowTextW(hwnd, title_buf, 256)

        class_buf = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(hwnd, class_buf, 256)

        hmon = _u32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST

        results.append(WindowInfo(
            hwnd       = hwnd,
            title      = title_buf.value,
            class_name = class_buf.value,
            x          = r.left,
            y          = r.top,
            width      = w,
            height     = h,
            monitor_id = hmon,
        ))
        return True

    _u32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return results


def move_window_to_rect(hwnd: int, target_rect: tuple) -> bool:
    """Centre hwnd on a monitor work-area rect (left, top, right, bottom).

    Does not activate the window or change keyboard focus.
    Returns True on success.
    """
    from core.monitor import SWP_NOSIZE, SWP_NOACTIVATE, HWND_BOTTOM

    mx, my, mr, mb = target_rect
    mw = mr - mx
    mh = mb - my

    r = ctypes.wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(r))
    ww = r.right  - r.left
    wh = r.bottom - r.top

    nx = mx + max(0, (mw - ww) // 2)
    ny = my + max(0, (mh - wh) // 2)

    _u32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE — restore if minimised
    result = _u32.SetWindowPos(
        hwnd, HWND_BOTTOM, nx, ny, 0, 0,
        SWP_NOSIZE | SWP_NOACTIVATE,
    )
    return bool(result)
