"""Cross-process z-order manipulation that surfaces a window above the active
one *without* stealing keyboard focus.

The foreground lock silently downgrades a background process's attempt to
raise a window above the foreground window. The classic
`HWND_TOPMOST → HWND_NOTOPMOST` flip is unreliable: the final NOTOPMOST step
re-inserts the window at the top of the *non-topmost* band, and if the
foreground window was activated more recently it can end up right back on top —
so "bring to front" appears to do nothing.

The deterministic trick we use instead: **lower the foreground window to sit
directly beneath the target**. Lowering the foreground window is *not* blocked
by the foreground lock, and with `SWP_NOACTIVATE` the foreground window keeps
focus — it just moves one slot down the z-order, leaving the target visible
above it. We then also lift the target to the top of the band (via
AttachThreadInput + BringWindowToTop) so it clears any other windows too.

We never call `SetForegroundWindow` — focus stays with whatever the user was
using (typically a fullscreen game or the window they're typing in).
"""
import ctypes
from ctypes import wintypes

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
SWP_NOOWNERZORDER = 0x0200   # don't reorder the owner window
_SWP = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER

# ShowWindow commands
SW_SHOWNOACTIVATE = 4   # restore in normal size & position w/o activating
SW_SHOWNA         = 8   # show in current state w/o activating

# ── Proper ctypes signatures ────────────────────────────────────────────────
# Without these, return values default to 32-bit c_int and HWND args are
# truncated — which corrupts handle comparisons on 64-bit Windows.
_u32.GetForegroundWindow.restype = wintypes.HWND
_u32.IsWindow.argtypes           = [wintypes.HWND]
_u32.IsWindow.restype            = wintypes.BOOL
_u32.IsIconic.argtypes           = [wintypes.HWND]
_u32.IsIconic.restype            = wintypes.BOOL
_u32.IsWindowVisible.argtypes    = [wintypes.HWND]
_u32.IsWindowVisible.restype     = wintypes.BOOL
_u32.ShowWindow.argtypes         = [wintypes.HWND, ctypes.c_int]
_u32.ShowWindow.restype          = wintypes.BOOL
_u32.BringWindowToTop.argtypes   = [wintypes.HWND]
_u32.BringWindowToTop.restype    = wintypes.BOOL
_u32.SetWindowPos.argtypes       = [wintypes.HWND, wintypes.HWND,
                                    ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_u32.SetWindowPos.restype        = wintypes.BOOL
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
_u32.GetWindowThreadProcessId.restype  = wintypes.DWORD
_u32.AttachThreadInput.argtypes  = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_u32.AttachThreadInput.restype   = wintypes.BOOL
_k32.GetCurrentThreadId.restype  = wintypes.DWORD

# Pseudo-handle constants need to be passed as HWND, not Python ints, so the
# argtype conversion treats them as the intended special values.
_HWND_TOP = wintypes.HWND(HWND_TOP)


def _restore_if_needed(hwnd):
    """Un-minimise / show a window without activating it."""
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    elif not _u32.IsWindowVisible(hwnd):
        _u32.ShowWindow(hwnd, SW_SHOWNA)


def raise_to_top_no_focus(hwnd: int) -> bool:
    """Raise *hwnd* above the active window without activating it.

    Returns True on success (or no-op because *hwnd* is already foreground).
    Never steals focus from the current foreground window.
    """
    if not hwnd or not _u32.IsWindow(hwnd):
        return False

    fg = _u32.GetForegroundWindow()
    if fg == hwnd:
        return True

    _restore_if_needed(hwnd)

    # Primary, deterministic step: drop the foreground window to just below
    # hwnd. Placing fg *after* hwnd in the z-order puts hwnd visibly above it.
    # Lowering the foreground window is not restricted by the foreground lock,
    # and SWP_NOACTIVATE means fg keeps keyboard focus.
    if fg and fg != hwnd and _u32.IsWindow(fg):
        _u32.SetWindowPos(fg, hwnd, 0, 0, 0, 0, _SWP)

    # Belt-and-braces: also lift hwnd to the very top of the non-topmost band so
    # it clears any *other* windows too (not just the one that had focus).
    # AttachThreadInput lets this cross-process call beat the foreground lock.
    fg_thread = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_thread = _k32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(_u32.AttachThreadInput(my_thread, fg_thread, True))
    try:
        _u32.BringWindowToTop(hwnd)
        _u32.SetWindowPos(hwnd, _HWND_TOP, 0, 0, 0, 0, _SWP)
    finally:
        if attached:
            _u32.AttachThreadInput(my_thread, fg_thread, False)
    return True


def send_to_bottom(hwnd: int) -> bool:
    """Push *hwnd* to the bottom of the z-order. Downward z-order moves are not
    restricted by the foreground lock, so no AttachThreadInput needed."""
    if not hwnd or not _u32.IsWindow(hwnd):
        return False
    return bool(_u32.SetWindowPos(hwnd, wintypes.HWND(HWND_BOTTOM),
                                  0, 0, 0, 0, _SWP))
