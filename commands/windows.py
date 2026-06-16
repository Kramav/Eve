"""Voice command: "identify windows" / "what's open" / "list open windows".

Enumerates all visible top-level windows and asks Electron to overlay a small
numbered label on each. The list comes from `commands.tiling.enumerate_windows()`
which already filters out Eve's own panels, the desktop, and shell windows.
"""
from commands import tiling

_display = None


def set_display(display):
    global _display
    _display = display


def identify_windows() -> str:
    if _display is None:
        return ""
    wins = tiling.enumerate_windows()
    # Strip the OS-level hwnd; Electron only needs the screen-pixel rect + label
    payload = []
    for i, w in enumerate(wins, start=1):
        exe = (w.get('exe') or '').replace('.exe', '')
        label = exe.title() if exe else (w.get('title') or '').split(' - ')[0].strip()
        payload.append({
            'index': i,
            'label': label or 'window',
            'title': w.get('title') or '',
            'x': w['x'], 'y': w['y'], 'w': w['w'], 'h': w['h'],
        })
    _display.identify_windows(payload)
    return ""
