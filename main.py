import ctypes
import re
import time
import threading

# Make Python per-monitor DPI aware BEFORE any Win32 windowing calls.
# Without this, Python is in legacy DPI-unaware mode and Win32 lies about
# coordinates on mixed-DPI multi-monitor setups, so tiling_layouts.json
# values (saved by Electron, which is DPI aware) don't line up with where
# SetWindowPos actually places windows.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from core.listener import Listener
from core.transcriber import Transcriber
import core.dispatcher as _dispatcher_mod
from core.hot_reload import start as _start_hot_reload
from core.speaker import Speaker
from core.display import Display
from core.response import Silent, VideoList, SiteList
from core.session import get as _get_session, Mode as _Mode
from commands.reminders import start_checker
import commands.youtube as _yt_cmd
import commands.system as _sys_cmd
import commands.tiling as _tiling_cmd

_OVERLAY_TOGGLE = re.compile(
    # Full phrase: "show/hide/close the overlay / hud / log / history"
    # \s+ tolerates non-breaking spaces; (?:\w+\s+){0,2}? swallows filler words
    # Whisper inserts (e.g. "show an overlay", "show me the hud").
    r"\b(?:show|open|hide|close|toggle)\s+(?:\w+\s+){0,2}?(?:overlay|hud|log|history)\b"
    # Bare word or on/off suffix — Whisper often drops the leading verb
    r"|^(?:overlay|hud)(?:\s+(?:on|off))?$",
    re.I,
)

_MANAGE_APPS = re.compile(
    r"\b(?:manage|open|show|edit|configure)\b.{0,20}\bapps?\b"
    r"|\b(?:open|show|launch)\s+(?:the\s+)?app\s+manager\b",
    re.I,
)

_MANAGE_WINDOWS = re.compile(
    r"\b(?:open|show|launch)\s+(?:the\s+)?window\s*manager\b",
    re.I,
)

_OPEN_DIRECTORY = re.compile(
    r"\b(?:show|open|launch)\s+(?:the\s+)?(?:routing\s+)?directory\b",
    re.I,
)

_CLOSE_DIRECTORY = re.compile(
    r"\b(?:close|hide|dismiss)\s+(?:the\s+)?(?:routing\s+)?directory\b",
    re.I,
)

_VOICE_SETTINGS = re.compile(
    r"\b(?:open|show|launch)\s+(?:the\s+)?voice\s+(?:settings?|config(?:uration)?|options?|manager)\b"
    r"|\bvoice\s+(?:settings?|manager)\b",
    re.I,
)

_COMMAND_EDITOR = re.compile(
    r"\b(?:open|show|edit|launch)\s+(?:the\s+)?command(?:s|\s+editor)\b",
    re.I,
)


def main():
    print("Starting Eve...")

    display = Display()
    speaker = Speaker()
    transcriber = Transcriber()
    listener = Listener()

    display.set_speaker(speaker)
    display.set_listener(listener)
    _tiling_cmd.set_display(display)

    _yt_cmd.set_display(display)
    _sys_cmd.set_display(display)
    _sys_cmd.set_speaker(speaker)

    listener.set_speaking_event(speaker.is_speaking)

    def on_reminder(message: str):
        display.show(status="Reminder", text=message, color="listening")
        display.log("system", f"Reminder: {message}")
        speaker.speak(f"Reminder: {message}")
        time.sleep(3)
        display.hide()

    start_checker(on_reminder)

    print("Ready. Say 'Hey Jarvis' to wake Eve up.")
    display.show(status="Ready  —  say Hey Jarvis", text="", color="idle")
    display.log("system", "Eve started")
    time.sleep(2)
    display.hide()

    def on_wake():
        display.show(status="Listening...", text="", color="listening")
        display.set_mode("listening")

    def on_command(audio):
        delay        = 2
        keep_visible = False
        try:
            display.hide_list()
            display.update(status="Thinking...", color="processing")
            display.set_mode("processing")
            text = transcriber.transcribe(audio)

            if not text:
                delay = 0
                return

            text = re.sub(r"[.,!?]+$", "", text.strip())

            print(f"Heard: {text}")
            display.update(text=f'"{text}"')
            display.log("heard", text)

            if _OVERLAY_TOGGLE.search(text.lower()):
                display.toggle_overlay()
                delay = 0
                return

            if _OPEN_DIRECTORY.search(text):
                display.show_directory()
                delay = 0
                return

            if _CLOSE_DIRECTORY.search(text):
                display.hide_directory()
                delay = 0
                return

            if _MANAGE_APPS.search(text):
                display.open_app_manager()
                delay = 0
                return

            if _MANAGE_WINDOWS.search(text):
                display.open_window_manager()
                delay = 0
                return

            if _VOICE_SETTINGS.search(text):
                display.open_voice_settings()
                delay = 0
                return

            if _COMMAND_EDITOR.search(text):
                from commands.system import open_editor
                open_editor()
                delay = 0
                return

            response = _dispatcher_mod.dispatch(text)
            print(f"Eve: {response}")

            if isinstance(response, Silent):
                display.show(status=str(response), text="", color="error")
                display.log("error", str(response))
                display.set_mode("idle")
                delay = 1.5
                return

            if isinstance(response, (VideoList, SiteList)):
                display.show_list(response.format_items(), status=str(response))
                display.log("action", str(response))
                display.set_mode("playing")
                keep_visible = True
                delay = 0
                return

            # Check if a video was just selected (session entered PLAYING)
            if _get_session().mode == _Mode.PLAYING:
                display.set_mode("playing")
            else:
                display.set_mode("idle")

            if response:
                display.update(status="Eve", text=response, color="processing")
                display.log("action", response)
                speaker.speak(response)

        except Exception as e:
            print(f"Command error: {e}")
            display.show(status="Error — something went wrong", text="", color="error")
            display.log("error", str(e))
            display.set_mode("idle")
            delay = 1.5

        finally:
            if delay:
                time.sleep(delay)
            if not keep_visible:
                display.set_mode("idle")
                display.hide()

    _start_hot_reload()
    threading.Thread(
        target=listener.run,
        kwargs={'on_wake': on_wake, 'on_command': on_command},
        daemon=True,
    ).start()
    display.run_loop()


if __name__ == "__main__":
    main()
