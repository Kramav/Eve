"""Cross-process keyboard injection helpers.

Two ways to send keys from Eve:

  1. **Global hotkey** — `press_global("ctrl+shift+alt+m")` sends a key
     event at the OS keyboard-hook level. Lands wherever the OS routes it.
     Discord's user-configured global keybinds (Toggle Mute / Toggle Deafen /
     Disconnect) catch theirs even when the user is focused elsewhere. No
     focus theft.

  2. **Focused** — `with_window_focused(hwnd, action)` brings *hwnd* to the
     foreground WITH keyboard focus, runs *action*, then restores the
     previous foreground. Used for shortcuts Discord only listens to while
     focused (Alt+Up/Down, Ctrl+K). Briefly steals focus.

Never call `SetForegroundWindow` directly without AttachThreadInput — the
Windows foreground lock will silently downgrade it for cross-process calls.
"""
import time
import ctypes

import pyautogui


_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

# Don't abort mid-send if the cursor hits a corner
pyautogui.FAILSAFE = False
# Tiny inter-key gap — reliable for Discord without being too slow
pyautogui.PAUSE = 0.04

SW_RESTORE = 9


def press_global(hotkey: str) -> None:
    """Send a hotkey at the OS keyboard-event level.

    Format: '+'-separated, e.g. 'ctrl+shift+alt+m'.
    Single keys also work: 'enter', 'escape', 'tab'.
    """
    keys = [k.strip() for k in hotkey.split('+') if k.strip()]
    if not keys:
        return
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)


def focus_window(hwnd: int) -> bool:
    """Bring *hwnd* to the foreground WITH keyboard focus.

    Mirror of `core.window_ops.raise_to_top_no_focus`, but explicitly calls
    SetForegroundWindow so the keyboard lands here. Uses AttachThreadInput
    so the cross-process call succeeds against the foreground lock.
    """
    if not hwnd or not _u32.IsWindow(hwnd):
        return False
    if _u32.GetForegroundWindow() == hwnd:
        return True
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, SW_RESTORE)

    fg_hwnd   = _u32.GetForegroundWindow()
    fg_thread = _u32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
    my_thread = _k32.GetCurrentThreadId()
    attached  = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(_u32.AttachThreadInput(my_thread, fg_thread, True))
    try:
        _u32.BringWindowToTop(hwnd)
        _u32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _u32.AttachThreadInput(my_thread, fg_thread, False)
    return _u32.GetForegroundWindow() == hwnd


def with_window_focused(hwnd: int, action, restore_focus: bool = True) -> bool:
    """Focus *hwnd*, run callable *action*, optionally restore the previous
    foreground window. Returns True if focus succeeded; False otherwise.
    """
    prev = _u32.GetForegroundWindow()
    if not focus_window(hwnd):
        return False
    time.sleep(0.08)   # let the foreground transition settle before sending keys
    try:
        action()
    finally:
        if restore_focus and prev and prev != hwnd:
            focus_window(prev)
    return True


def type_text(text: str) -> None:
    """Type literal text into the currently-focused window."""
    pyautogui.typewrite(text, interval=0.01)
