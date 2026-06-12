import re
import time
import threading
from core.listener import Listener
from core.transcriber import Transcriber
import core.dispatcher as _dispatcher_mod
from core.hot_reload import start as _start_hot_reload
from core.speaker import Speaker
from core.display import Display
from core.response import Silent, VideoList
from core.session import get as _get_session, Mode as _Mode
from commands.reminders import start_checker
import commands.youtube as _yt_cmd

_OVERLAY_TOGGLE = re.compile(
    # Full phrase: "show/hide/close the overlay / log / history"
    r"(?:show|open|hide|close|toggle) (?:the )?(?:overlay|visual overlay|log|activity log|history)"
    # Bare word or on/off suffix — Whisper often drops the leading verb
    r"|^overlay(?: on| off)?$"
)


def main():
    print("Starting Eve...")

    display = Display()
    speaker = Speaker()
    transcriber = Transcriber()
    listener = Listener()

    _yt_cmd.set_display(display)

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

            response = _dispatcher_mod.dispatch(text)
            print(f"Eve: {response}")

            if isinstance(response, Silent):
                display.show(status=str(response), text="", color="error")
                display.log("error", str(response))
                display.set_mode("idle")
                delay = 1.5
                return

            if isinstance(response, VideoList):
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
