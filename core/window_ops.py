"""Cross-process z-order manipulation that bypasses Windows' foreground lock
without stealing keyboard focus.

The foreground lock silently downgrades `SetWindowPos(HWND_TOP, …)` and even
the classic `HWND_TOPMOST → HWND_NOTOPMOST` flip when the calling process
isn't the foreground. AutoHotkey, pywinauto, and similar tools work around it
by calling `AttachThreadInput` to share input state with the foreground thread,
making Windows treat the SetWindowPos call as authoritative.

We never call `SetForegroundWindow` — focus stays with whatever the user was
using (typically a fullscreen game).
"""
import ctypes
import ctypes.wintypes

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

# z-order targets
HWND_TOP        =  0
HWND_BOTTOM     =  1
HWND_TOPMOST    = -1
HWND_NOTOPMOST  = -2

# SetWindowPos flags
SWP_NOSIZE      = 0x0001
SWP_NOMOVE      = 0x0002
SWP_NOACTIVATE  = 0x0010

# ShowWindow commands
SW_SHOWNOACTIVATE = 4   # restore in normal size & position w/o activating
SW_SHOWNA         = 8   # show in current state w/o activating


def raise_to_top_no_focus(hwnd: int) -> bool:
    """Raise *hwnd* above every non-topmost window without activating it.

    Returns True on success (or no-op because *hwnd* is already foreground).
    Never steals focus from the current foreground window.

    Three-step belt-and-braces:
      1. Restore if minimised (SW_SHOWNOACTIVATE) or show if hidden (SW_SHOWNA).
      2. AttachThreadInput piggybacks on the foreground thread so cross-process
         SetWindowPos calls succeed.
      3. HWND_TOP, then a HWND_TOPMOST → HWND_NOTOPMOST flip as fallback.
    """
    if not hwnd or not _u32.IsWindow(hwnd):
        return False
    if _u32.GetForegroundWindow() == hwnd:
        return True

    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    elif not _u32.IsWindowVisible(hwnd):
        _u32.ShowWindow(hwnd, SW_SHOWNA)

    fg_hwnd   = _u32.GetForegroundWindow()
    fg_thread = _u32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
    my_thread = _k32.GetCurrentThreadId()

    attached = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(_u32.AttachThreadInput(my_thread, fg_thread, True))

    try:
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        _u32.SetWindowPos(hwnd, HWND_TOP,       0, 0, 0, 0, flags)
        # The TOPMOST flip is redundant after AttachThreadInput succeeds, but
        # harmless and useful when AttachThreadInput returned False (rare).
        _u32.SetWindowPos(hwnd, HWND_TOPMOST,   0, 0, 0, 0, flags)
        _u32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    finally:
        if attached:
            _u32.AttachThreadInput(my_thread, fg_thread, False)
    return True


def send_to_bottom(hwnd: int) -> bool:
    """Push *hwnd* to the bottom of the z-order. Downward z-order moves are
    not restricted by the foreground lock, so no AttachThreadInput needed."""
    if not hwnd or not _u32.IsWindow(hwnd):
        return False
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    return bool(_u32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, flags))
