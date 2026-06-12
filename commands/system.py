import subprocess
import sys
from datetime import datetime
from pathlib import Path
import pyautogui

_EDITOR = Path(__file__).parent.parent / "editor.py"


def open_editor() -> str:
    subprocess.Popen([sys.executable, str(_EDITOR)])
    return "Opening command editor"


def get_time() -> str:
    return datetime.now().strftime("It's %I:%M %p")


def get_date() -> str:
    return datetime.now().strftime("Today is %A, %B %d")


def volume_up() -> str:
    pyautogui.press("volumeup", presses=5)
    return "Volume up"


def volume_down() -> str:
    pyautogui.press("volumedown", presses=5)
    return "Volume down"


def toggle_mute() -> str:
    pyautogui.press("volumemute")
    return "Muted"


def media_play_pause() -> str:
    pyautogui.press("playpause")
    return ""


def media_next() -> str:
    pyautogui.press("nexttrack")
    return "Next track"


def media_prev() -> str:
    pyautogui.press("prevtrack")
    return "Previous track"


def screenshot() -> str:
    path = Path.home() / "Desktop" / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    pyautogui.screenshot(str(path))
    return "Screenshot saved to desktop"


def sleep_pc() -> str:
    subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
    return "Going to sleep"


def shutdown() -> str:
    subprocess.run("shutdown /s /t 30", shell=True)
    return "Shutting down in 30 seconds. Say cancel shutdown to stop."


def cancel_shutdown() -> str:
    subprocess.run("shutdown /a", shell=True)
    return "Shutdown cancelled"
