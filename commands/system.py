import subprocess
from datetime import datetime
from pathlib import Path
import pyautogui


def open_editor() -> str:
    """Open the inline Electron command-editor panel (replaces the legacy
    tkinter editor.py subprocess)."""
    if _display is not None:
        _display.open_command_editor()
    return ""


def close_editor() -> str:
    if _display is not None:
        _display.close_command_editor()
    return ""


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


_display = None
_speaker = None


def set_display(display) -> None:
    global _display
    _display = display


def set_speaker(speaker) -> None:
    global _speaker
    _speaker = speaker


def silence_voice() -> str:
    if _speaker is not None:
        _speaker.speak("Going silent. Say enable voice to turn me back on.")
        _speaker.enabled = False
    return ""  # already spoken above; suppress the main.py speak() call


def enable_voice() -> str:
    if _speaker is not None:
        _speaker.enabled = True
    return "Voice enabled"


def toggle_voice() -> str:
    if _speaker is None:
        return "Voice toggle unavailable"
    if _speaker.enabled:
        _speaker.speak("Going silent. Say enable voice to turn me back on.")
        _speaker.enabled = False
        return ""
    _speaker.enabled = True
    return "Voice enabled"


def open_app_manager() -> str:
    if _display is not None:
        _display.open_app_manager()
    return "Opening app manager"


def open_window_manager() -> str:
    if _display is not None:
        _display.open_window_manager()
    return "Opening window manager"


def close_app_manager() -> str:
    if _display is not None:
        _display.close_app_manager()
    return "Closing app manager"


def close_window_manager() -> str:
    if _display is not None:
        _display.close_window_manager()
    return "Closing window manager"


def open_voice_settings() -> str:
    if _display is not None:
        _display.open_voice_settings()
    return "Opening voice settings"


def show_directory() -> str:
    if _display is not None:
        _display.show_directory()
    return ""


def hide_directory() -> str:
    if _display is not None:
        _display.hide_directory()
    return ""


def identify_monitors() -> str:
    if _display is not None:
        _display.identify_monitors()
    return ""


def identify_zones() -> str:
    if _display is not None:
        _display.identify_zones()
    return ""
