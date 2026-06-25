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
from core.response import Silent, Panel, VideoList, SiteList
from core.session import get as _get_session, Mode as _Mode
from core import features as _features
from commands.reminders import start_checker
import commands.youtube as _yt_cmd
import commands.system as _sys_cmd
import commands.tiling as _tiling_cmd
import commands.window_manager as _wm_cmd
import commands.windows as _win_cmd
import commands.programs as _prog_cmd
import commands.context  as _ctx_cmd

def main():
    print("Starting Eve...")

    display = Display()
    speaker = Speaker()
    transcriber = Transcriber()
    listener = Listener()

    display.set_speaker(speaker)
    display.set_listener(listener)
    _tiling_cmd.set_display(display)
    _wm_cmd.set_display(display)
    _win_cmd.set_display(display)
    _prog_cmd.set_display(display)
    _ctx_cmd.set_display(display)

    _yt_cmd.set_display(display)
    _sys_cmd.set_display(display)
    _sys_cmd.set_speaker(speaker)

    # Drop-in skills (skills/*.py) — third-party commands without editing core.
    from core import skills
    skills.load(display)

    listener.set_speaking_event(speaker.is_speaking)

    def on_reminder(message: str):
        display.show(status="Reminder", text=message, color="listening")
        display.log("system", f"Reminder: {message}")
        from core import notify
        notify.toast("Reminder", message)   # persists in Action Center; best-effort
        speaker.speak(f"Reminder: {message}")
        time.sleep(3)
        display.hide()

    start_checker(on_reminder, on_change=display.reminders_changed)

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

            response = _dispatcher_mod.dispatch(text)
            print(f"Eve: {response}")

            # Panel actions (open/close/toggle a managed Electron window) are
            # already done by the handler via the Display; hide the HUD fast and
            # don't speak. Must be checked before Silent (Panel subclasses it).
            if isinstance(response, Panel):
                delay = 0
                return

            if isinstance(response, Silent):
                display.show(status=str(response), text="", color="error")
                display.log("error", str(response))
                display.set_mode("idle")
                delay = 1.5
                return

            if isinstance(response, (VideoList, SiteList)):
                # Site results carry URLs so the overlay rows can be clicked
                # open in the browser; video rows stay voice-select only.
                links = ([item.get('url') for item in response.items]
                         if isinstance(response, SiteList) else None)
                display.show_list(response.format_items(), status=str(response), links=links)
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
                if _features.get('tts'):
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
