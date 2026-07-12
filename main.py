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
from core.response import Silent, Panel, VideoList, SiteList, Verified
from core.session import get as _get_session, Mode as _Mode
from core import features as _features
from core import verify as _verify
from core.conversation import (ConversationEngine as _Conversation,
                               UserTurn as _ConvUserTurn,
                               SilenceTimeout as _ConvSilence)
import config
from commands.reminders import start_checker

_CONV_MAX_TURNS = 15   # safety cap on follow-ups per wake (runaway guard)
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

    _sys_cmd.set_display(display)
    _sys_cmd.set_speaker(speaker)

    # Drop-in skills (skills/*.py) — third-party commands without editing core.
    # The YouTube skill (and any other) gets the Display via its setup(display).
    from core import skills
    skills.load(display)

    # LLM fallback host — spawn llama-swap if enabled and nothing is already
    # answering at config.LLM_BASE_URL. Threaded: the health probe must not
    # delay startup; the fallback just stays unavailable until it's up.
    from core import llm_host
    threading.Thread(target=llm_host.ensure_running, daemon=True).start()

    listener.set_speaking_event(speaker.is_speaking)

    def on_reminder(message: str):
        display.show(status="Reminder", text=message, color="listening")
        display.log("system", f"Reminder: {message}")
        if _features.get('notifications'):
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

    # 3+ monitors and no Eve monitor picked yet? Nudge the user to designate one
    # so opened windows have a dedicated home (see core.monitor.companion_prompt).
    from core import monitor as _monitor
    prompt = _monitor.companion_prompt()
    if prompt:
        display.log("system", prompt)
        try:
            from core import notify
            notify.toast("Eve — pick a monitor", prompt)
        except Exception:
            pass
        display.show(status="Multiple monitors detected", text=prompt, color="listening")
        time.sleep(6)
        display.hide()

    def on_wake():
        display.show(status="Listening...", text="", color="listening")
        display.set_mode("listening")

    # ── Response rendering (shared by the legacy path and the engine) ────────

    def _resolve(response):
        """Resolve a Verified response: speak the optimistic line up front, then
        confirm the side effect (retry once, report honestly). Feature-gated.
        Returns (response, verify_ok)."""
        verify_ok = True
        if isinstance(response, Verified):
            if not _features.get('verify_commands'):
                response = str(response)                # skip the check entirely
            else:
                if response.announce:
                    display.update(status="Eve", text=response.announce,
                                   color="processing")
                    display.log("action", response.announce)
                    if _features.get('tts'):
                        speaker.speak(response.announce)
                response, verify_ok = _verify.resolve(response)
        print(f"Eve: {response}")
        return response, verify_ok

    def _present(response, verify_ok):
        """Render a resolved response to the HUD (+ TTS). Returns
        (delay, keep_visible) for the post-command hide timing."""
        # Panel actions already happened via the Display; hide fast, don't
        # speak. Must be checked before Silent (Panel subclasses it).
        if isinstance(response, Panel):
            return 0, False
        if isinstance(response, Silent):
            display.show(status=str(response), text="", color="error")
            display.log("error", str(response))
            display.set_mode("idle")
            return 1.5, False
        if isinstance(response, (VideoList, SiteList)):
            links = ([item.get('url') for item in response.items]
                     if isinstance(response, SiteList) else None)
            display.show_list(response.format_items(), status=str(response), links=links)
            display.log("action", str(response))
            display.set_mode("playing")
            return 0, True
        if _get_session().mode == _Mode.PLAYING:
            display.set_mode("playing")
        else:
            display.set_mode("idle")
        if response:
            color = "processing" if verify_ok else "error"
            display.update(status="Eve", text=response, color=color)
            display.log("action" if verify_ok else "error", response)
            if _features.get('tts'):
                speaker.speak(response)
        return 2, False

    def _say(line):
        """Speak an engine-owned line (an ack or a cancel)."""
        display.update(status="Eve", text=line, color="processing")
        display.log("action", line)
        if _features.get('tts'):
            speaker.speak(line)

    # ── Conversation Engine (opt-in via features.json conversation_engine) ───
    # When on, Eve keeps the mic open after a reply so confirmations, follow-ups
    # and continuations need no wake word. See docs/CONVERSATION_ARCHITECTURE.md.

    def _engaged_signal():
        # The legacy multi-turn signals the engine bridges in Phase 1.
        s = _get_session()
        return s.pending_confirm is not None or (
            s.converse is not None and s.converse.alive())

    _conv = _Conversation(
        router=_dispatcher_mod.dispatch,
        engaged_signal=_engaged_signal,
        followup_ttl=config.CONV_FOLLOWUP_TTL,
        awaiting_ttl=config.CONV_AWAITING_TTL,
        extend_by=config.CONV_EXTEND_BY,
    )

    def _run_engine(first_text):
        """Drive the engine's turn loop, opening no-wake follow-up windows until
        the conversation ends or times out. Returns keep_visible."""
        keep_visible = False
        text = first_text
        for _ in range(_CONV_MAX_TURNS):
            step = _conv.handle(_ConvUserTurn(text))
            if step.say is not None:
                _say(step.say)
            else:
                response, verify_ok = _resolve(step.response)
                _, keep_visible = _present(response, verify_ok)
            if not step.listen:
                break
            audio = listener.listen_followup(step.ttl)
            if audio is None or getattr(audio, "size", 0) == 0:
                _conv.handle(_ConvSilence())
                break
            text = transcriber.transcribe(audio)
            if not text:
                _conv.handle(_ConvSilence())
                break
            text = re.sub(r"[.,!?]+$", "", text.strip())
            print(f"Heard (follow-up): {text}")
            display.update(status="Listening...", text=f'"{text}"', color="processing")
            display.set_mode("processing")
            display.log("heard", text)
        return keep_visible

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

            if _features.get('conversation_engine'):
                keep_visible = _run_engine(text)
            else:
                response, verify_ok = _resolve(_dispatcher_mod.dispatch(text))
                delay, keep_visible = _present(response, verify_ok)

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
