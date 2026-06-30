import json
import re
import subprocess
from pathlib import Path
from commands import apps, system, search, reminders, tiling, window_manager as wm
from commands import windows as windows_cmd
from commands import programs as programs_cmd
from commands import discord as discord_cmd
from commands import context as ctx_cmd


def _send_to_discord(text, recipient):
    """Argument flip for the 'send X to Y on discord' pattern."""
    return discord_cmd.send_message(recipient, text)


# Startup-on-login shims — lazily import the registry helper so importing the
# dispatcher (e.g. in tests) never touches winreg unless the intent fires.
def _autostart_enable():
    from core import autostart; return autostart.enable()
def _autostart_disable():
    from core import autostart; return autostart.disable()
def _autostart_status():
    from core import autostart; return autostart.status_voice()
import core.session as _sess_mod
from core.session import Mode
from core import features as _features

_COMMANDS_FILE = Path(__file__).parent.parent / "custom_commands.json"
_ALIASES_FILE  = Path(__file__).parent.parent / "aliases.json"


def _load_custom() -> list:
    if _COMMANDS_FILE.exists():
        try:
            return json.loads(_COMMANDS_FILE.read_text())
        except Exception:
            return []
    return []


def _load_aliases() -> list:
    if _ALIASES_FILE.exists():
        try:
            return json.loads(_ALIASES_FILE.read_text())
        except Exception:
            return []
    return []


# Maps alias keys (stored in aliases.json) → handler functions
BUILTIN_MAP = {
    "get_time":          system.get_time,
    "get_date":          system.get_date,
    "volume_up":         system.volume_up,
    "volume_down":       system.volume_down,
    "toggle_mute":       system.toggle_mute,
    "play_pause":        system.media_play_pause,
    "next_track":        system.media_next,
    "prev_track":        system.media_prev,
    "screenshot":        system.screenshot,
    "list_reminders":    reminders.list_reminders,
    "cancel_reminders":  reminders.cancel_all,
    "open_editor":       system.open_editor,
    "sleep":             system.sleep_pc,
    "shutdown":          system.shutdown,
    "cancel_shutdown":   system.cancel_shutdown,
}

_HELP_TEXT = (
    "I can search and play YouTube, open and close apps, search the web, "
    "go to websites, set reminders and timers, control volume and media, "
    "take screenshots, and control your PC. "
    "Say 'open command editor' to add custom commands."
)


def _help() -> str:
    return _HELP_TEXT


# Snap shims — let the dispatcher pass regex groups positionally regardless of
# whether the monitor qualifier was captured before or after the zone.
def _snap_zone_monitor(app, zone, monitor): return tiling.snap_app(app, zone, monitor)
def _snap_monitor_zone(app, monitor, zone): return tiling.snap_app(app, zone, monitor)
# "snap firefox to monitor 2" with no zone implies the implicit full zone —
# every saved monitor exposes 'full' even when its preset is e.g. top-bottom.
def _snap_monitor_only(app, monitor):       return tiling.snap_app(app, 'full', monitor)
# "move hud to top-left of monitor 1" — snap the directory panel into a named
# zone on a specific monitor (the 'move' verb otherwise just relocates the orb).
def _snap_hud_zone_monitor(zone, monitor):  return tiling.snap_app('hud', zone, monitor)


# (regex pattern, handler) — first match wins, captured groups passed as args
INTENTS = [
    # HUD / overlay toggle — consolidated from main.py's former _OVERLAY_TOGGLE
    # pre-dispatch block (now deleted; dispatch() is the single router). MUST be
    # above the directory show/hide intents so "show hud" / "hide hud" / bare
    # "hud" all *toggle* the overlay as before, rather than show/hide it.
    (r"\b(?:show|open|hide|close|toggle)\s+(?:\w+\s+){0,2}?(?:overlay|hud|log|history)\b"
     r"|^(?:overlay|hud)(?:\s+(?:on|off))?$",                                                system.toggle_overlay),

    # Command editor
    (r"(?:open|edit|show|launch) (?:the )?(?:command editor|my commands|eve commands|commands)", system.open_editor),
    (r"(?:close|quit|exit|dismiss) (?:the )?(?:command editor|my commands|eve commands|commands)", system.close_editor),
    (r"kill (?:the )?(?:command editor|my commands|eve commands|commands)",                        system.close_editor),

    # Window Manager — before generic open/launch to prevent misrouting to open_app
    (r"(?:open|show|launch) (?:the )?window manager",          system.open_window_manager),
    (r"(?:close|quit|exit|dismiss) (?:the )?window manager",   system.close_window_manager),
    (r"kill (?:the )?window manager",                           system.close_window_manager),

    # ── Non-specific follow-ups (must come before snap / open_app) ────────
    (r"\b(?:go\s+back|undo(?:\s+that)?|revert(?:\s+that)?)\b",                             ctx_cmd.undo),
    (r"\bclose\s+(?:that(?:\s+window)?|it|the\s+(?:last\s+)?window)\b",                    ctx_cmd.close_last),
    (r"\bcancel\s+(?:that|it|the\s+last\s+(?:one|thing))\b",                               ctx_cmd.cancel_last),

    # Protected / essential programs — beat snap/open_app so "protect X" /
    # "stop protecting X" aren't read as app or z-order commands.
    (r"\bstop\s+protecting\s+(.+)$",                                                       ctx_cmd.unprotect_program),
    (r"\b(?:that|this|it)(?:'?s)?\s+(?:is\s+)?(?:not|no\s+longer)\s+(?:essential|protected|my\s+game)\b", ctx_cmd.unprotect_program),
    (r"\b(?:treat|mark|set)\s+(.+?)\s+as\s+(?:essential|protected|my\s+game)\b",           ctx_cmd.protect_program),
    (r"\bprotect\s+(.+)$",                                                                 ctx_cmd.protect_program),
    (r"\bthis\s+is\s+my\s+game\b",                                                         ctx_cmd.protect_program),
    (r"\bdon'?t\s+(?:steal\s+focus|interrupt)\b",                                          ctx_cmd.protect_program),
    (r"\bwhat(?:'?s| is| are\s+you)\s+protect(?:ed|ing)\b",                                ctx_cmd.list_protected),

    # Memory panel + memory writes — high priority to beat snap/open_app
    (r"(?:open|show)\s+(?:the\s+)?(?:memory|memories|brain)",                              ctx_cmd.open_memory_panel),
    (r"\bremember(?:\s+that)?\s+(?:my\s+)?(.+?)\s+(?:is|equals)\s+(.+)$",                  ctx_cmd.remember_voice),
    (r"\bforget\s+(?:my\s+)?(?:about\s+)?(.+)$",                                           ctx_cmd.forget_voice),

    # ── Discord navigation + messaging ────────────────────────────────────
    # Must come BEFORE snap and apps.open_app so:
    #   - "open discord search" routes to quick_switcher, not open_app
    #   - "send hello to alice on discord" routes to send_message, not snap
    (r"\bnext\s+(?:discord\s+)?channel\b",                                                    discord_cmd.next_channel),
    (r"\b(?:previous|prev|last|back)\s+(?:discord\s+)?channel\b",                             discord_cmd.prev_channel),
    (r"\bnext\s+(?:discord\s+)?server\b",                                                     discord_cmd.next_server),
    (r"\b(?:previous|prev|last)\s+(?:discord\s+)?server\b",                                   discord_cmd.prev_server),
    (r"\b(?:open|show)\s+(?:the\s+)?(?:discord\s+)?(?:quick\s+)?(?:switcher|search)\b",       discord_cmd.quick_switcher),
    (r"\b(?:tell|dm|message)\s+(\S+)\s+(.+)$",                                                discord_cmd.send_message),
    (r"\bsend\s+(.+?)\s+to\s+(\S+)\s+on\s+discord\b",                                         _send_to_discord),

    # Running Programs panel — open the live program-detector panel.
    # MUST come before "identify windows" so "running programs" / "what programs"
    # routes here, not to the visual identifier.
    (r"\b(?:open|show|launch)\s+(?:the\s+)?running\s+programs?(?:\s+panel)?\b",                                 programs_cmd.open_panel),
    (r"\b(?:open|show|launch)\s+(?:the\s+)?programs?\s+(?:panel|detector|list)\b",                              programs_cmd.open_panel),

    # Speak-list — "what's running" / "what programs are running" / "list programs"
    (r"\bwhat(?:'?s)?\s+(?:\w+\s+){0,2}?programs?\s+(?:are\s+(?:running|open)|do\s+i\s+have)\b",                programs_cmd.list_running_aloud),
    (r"\blist\s+(?:the\s+)?(?:running\s+)?programs?\b",                                                          programs_cmd.list_running_aloud),

    # Identify Windows — overlay a numbered tag on each open top-level window.
    # MUST come before Identify Monitors / Zones so "identify windows" /
    # "what's open" doesn't get swallowed by them.
    (r"\b(?:identify|show|list|reveal)\s+(?:\w+\s+){0,2}?(?:open\s+)?(?:windows?|apps?\s+(?:open|running))\b",  windows_cmd.identify_windows),
    (r"\bwhat(?:'?s)?\s+(?:\w+\s+){0,3}?(?:open|running)\b",                                                     windows_cmd.identify_windows),

    # Identify Zones — overlay saved tiling layouts on each monitor.
    # MUST come before Identify Monitors so "identify segments"/"identify tiles"/
    # "show zones" don't get swallowed by the monitors regex below.
    (r"\b(?:identify|show|reveal|display)\s+(?:\w+\s+){0,2}?(?:zones?|segments?|tiles?|tiling(?:\s+layouts?)?|layouts?)\b",  system.identify_zones),

    # Name a monitor — display-only label saved in tiling_layouts.json.
    # MUST come before Identify Monitors so "name/label monitor 2 gaming" (which
    # has a name tail) isn't swallowed by the identify pattern's shared verbs.
    (r"(?:name|label|call)\s+(?:the\s+)?(?:monitor|display|screen)\s+(\S+)\s+(?:as\s+)?(.+)$",  wm.name_monitor),
    (r"(?:name|label|call)\s+(?:the\s+)?(primary|leftmost|rightmost|left|middle|center|right)\s+"
     r"(?:monitor|display|screen)\s+(?:as\s+)?(.+)$",                                            wm.name_monitor),

    # Identify Monitors — flash a numbered card on each monitor.
    # (?:\w+\s+){0,2}? tolerates filler like "show me monitor numbers" / "label all displays"
    (r"\b(?:identify|show|label|number)\s+(?:\w+\s+){0,2}?(?:monitors?|displays?|screens?)\b",  system.identify_monitors),
    (r"which\s+monitor\s+is\s+which",                                                            system.identify_monitors),

    # ── Workspace presets — save/restore all window positions by name ─────
    # Before snap/open_app so "save layout as work" / "restore work layout"
    # aren't read as app or snap commands.
    (r"\bsave\s+(?:this\s+)?(?:layout|workspace|window\s+layout)\s+(?:as\s+)?(.+)$",       tiling.save_workspace),
    (r"\b(?:restore|load|apply)\s+(?:the\s+)?(.+?)\s+(?:layout|workspace)$",               tiling.restore_workspace),
    (r"\b(?:restore|load|apply)\s+(?:layout|workspace)\s+(.+)$",                           tiling.restore_workspace),
    (r"\b(?:what|which|list)\s+(?:layouts?|workspaces?)(?:\s+do\s+i\s+have)?\b",            tiling.list_workspaces),

    # ── Voice WM mutation ─────────────────────────────────────────────────
    # "set monitor 1 to 2x2 grid", "make monitor two top and bottom",
    # "change display three to full screen" — connector to/into/as is optional
    (r"(?:set|make|change|configure)\s+(?:the\s+)?(?:monitor|display|screen)\s+(\S+)\s+(?:(?:to|into|as)\s+)?(.+)",  wm.set_monitor_layout),
    # "monitor 2 grid" / "display three full" (no verb form)
    (r"^(?:monitor|display|screen)\s+(\S+)\s+(.+)$",                                                                 wm.set_monitor_layout),

    # "move HUD to top-left of monitor 1" — snap the directory panel into a
    # named zone on a specific monitor. MUST come before move_orb_corner /
    # move_hud, which would otherwise eat it and ignore the zone.
    (r"(?:move|set|pin|put|send|snap)\s+(?:the\s+)?(?:hud|orb|overlay|directory)\s+"
     r"(?:to|in|on|at|into|onto)\s+(?:the\s+)?([\w-]+)(?:\s+(?:zone|half|section))?\s+"
     r"(?:on|of)\s+(?:the\s+)?(.+)$",                                                            _snap_hud_zone_monitor),

    # "move hud to monitor 2", "set hud to primary", "pin hud to display 1"
    # Move orb to a screen corner — MUST come before the bare move_hud pattern
    # below, otherwise (.+) eats the whole "top-left" / "bottom right corner" tail.
    (r"(?:move|set|pin|put|place|send)\s+(?:the\s+)?(?:hud|orb|overlay)\s+"
     r"(?:to|in|on|at|onto|into)\s+(?:the\s+)?"
     r"(top|upper|bottom|lower)[-\s]+(left|right)(?:\s+corner)?$",                                              wm.move_orb_corner),

    (r"(?:move|set|pin|put|send)\s+(?:the\s+)?(?:hud|orb|overlay)\s+(?:to|on|onto)\s+(.+)",                     wm.move_hud),

    # Routing directory / overlay / HUD — show & hide the on-screen command list.
    # After move_hud so "move the hud to ..." isn't swallowed by the bare "hud".
    (r"(?:open|show|display|bring up)\s+(?:the\s+)?(?:routing\s+directory|directory|overlay|hud)", system.show_directory),
    (r"(?:close|hide|dismiss|quit|exit)\s+(?:the\s+)?(?:routing\s+directory|directory|overlay|hud)", system.hide_directory),

    # Voice Settings — before the generic open/launch app intent so it isn't
    # misrouted to open_app. Includes the bare "voice settings" / "voice manager"
    # form that main.py's former pre-dispatch _VOICE_SETTINGS block handled.
    (r"(?:open|show|launch) (?:the )?voice (?:settings?|manager|config(?:uration)?|options?)"
     r"|\bvoice\s+(?:settings?|manager)\b",                                              system.open_voice_settings),

    # API Keys / Integrations
    (r"(?:open|show|edit) (?:the )?(?:api keys?|integrations?|settings)",  system.open_integrations),
    (r"(?:close|quit|exit|dismiss) (?:the )?(?:api keys?|integrations?)",   system.close_integrations),

    # App Manager. Verb must sit DIRECTLY before "apps"/"app manager" — the old
    # ".{0,20}apps" gap matched unrelated phrases ("for untracked apps", "show me
    # the apps I downloaded"). Must stay above apps.open_app so "open apps" opens
    # the manager, not an app literally named "apps".
    (r"\b(?:manage|configure|edit)\s+(?:my\s+|the\s+)?apps?\b"
     r"|\b(?:open|show|launch)\s+(?:the\s+|my\s+)?apps?\b"
     r"|\b(?:open|show|launch|manage|edit|configure)\s+(?:the\s+)?app\s*manager\b",  system.open_app_manager),
    (r"(?:close|quit|exit|dismiss) (?:the )?app manager",      system.close_app_manager),
    (r"kill (?:the )?app manager",                             system.close_app_manager),

    # Help
    (r"help|what can (?:you|eve) do|list commands|show commands", _help),

    # YouTube/mpv lives in skills/youtube.py (PREEMPT), tried before this table.

    # Auto-snap on launch — persist a default zone for an app. MUST come before
    # the snap patterns (so "auto snap discord to right" assigns rather than
    # snapping now) and before the Apps open intent (so "always open firefox in
    # top-left" isn't read as opening an app named "firefox in top-left").
    (r"\bstop\s+auto[-\s]?snapping\s+(.+)$",                                              tiling.clear_app_zone),
    (r"\b(?:always|automatically)\s+(?:open|launch|put|snap)\s+(.+?)\s+(?:in|to|at)\s+"
     r"(?:the\s+)?([\w-]+)(?:\s+(?:zone|half|section))?\s+(?:on|of)\s+(.+)$",             tiling.set_app_zone),
    (r"\b(?:always|automatically)\s+(?:open|launch|put|snap)\s+(.+?)\s+(?:in|to|at)\s+"
     r"(?:the\s+)?([\w-]+)(?:\s+(?:zone|half|section))?$",                                tiling.set_app_zone),
    (r"\bauto[-\s]?snap\s+(.+?)\s+(?:in|to|at)\s+(?:the\s+)?([\w-]+)(?:\s+(?:zone|half|section))?$",  tiling.set_app_zone),

    # Tiling — before generic apps patterns so "snap/move X to Y" doesn't route to open_app.
    # Explicit-monitor variants come FIRST so the monitor qualifier isn't swallowed by the
    # bare-zone pattern's [\w-]+ zone capture.
    #
    # Positional aliases recognized: primary | left/leftmost | middle/center | right/rightmost.
    # Optional trailing "monitor"/"display"/"screen" word is fine after positionals.

    # "snap X to top of monitor 2" / "snap X to top of left monitor" /
    # "snap X to top on primary" / "snap X to top of middle"
    (r"(?:snap|move|send|bring|put)\s+(.+?)\s+to\s+(?:the\s+)?"
     r"([\w-]+)(?:\s+(?:zone|half|section))?\s+(?:on|of)\s+(?:the\s+)?"
     r"(primary|leftmost|rightmost|left|middle|center|right"
       r"|(?:left|right|leftmost|rightmost|middle|center)\s+(?:monitor|display|screen)"
       r"|(?:monitor|display|screen)\s+\S+(?:\s+\S+)?)$",                             _snap_zone_monitor),

    # "snap X to monitor 2 top" / "snap X to display two bottom"
    (r"(?:snap|move|send|bring|put)\s+(.+?)\s+to\s+(?:the\s+)?"
     r"(?:monitor|display|screen)\s+(\S+(?:\s+\S+)?)\s+([\w-]+)$",                    _snap_monitor_zone),

    # Positional + REQUIRED "monitor" + zone:  "snap X to left monitor top"
    (r"(?:snap|move|send|bring|put)\s+(.+?)\s+to\s+(?:the\s+)?"
     r"(primary|leftmost|rightmost|left|middle|center|right)"
     r"\s+(?:monitor|display|screen)\s+([\w-]+)$",                                    _snap_monitor_zone),

    # Positional + zone (no "monitor" word):  "snap X to left top" / "primary full".
    # Negative lookahead prevents matching "to left monitor" with zone="monitor" —
    # those fall through to the monitor-only patterns below.
    (r"(?:snap|move|send|bring|put)\s+(.+?)\s+to\s+(?:the\s+)?"
     r"(primary|leftmost|rightmost|left|middle|center|right)"
     r"(?!\s+(?:monitor|display|screen))\s+([\w-]+)$",                                _snap_monitor_zone),

    # Monitor only — no zone, implies the implicit 'full' zone.
    # "snap X to monitor 2" / "bring X to display one"
    (r"(?:snap|move|send|bring|put)\s+(.+?)\s+to\s+(?:the\s+)?"
     r"(?:monitor|display|screen)\s+(\S+(?:\s+\S+)?)$",                _snap_monitor_only),
    # Positional + optional "monitor":  "snap X to left" / "snap X to right monitor"
    (r"(?:snap|move|send|bring|put)\s+(.+?)\s+to\s+(?:the\s+)?"
     r"(primary|leftmost|rightmost|left|middle|center|right)"
     r"(?:\s+(?:monitor|display|screen))?$",                           _snap_monitor_only),

    # Z-order — "bring [app] to front" / "send [app] to back" without focus steal.
    # MUST come before the bare snap patterns so "front/back/etc." aren't captured
    # as zone names by the [\w-]+ zone group. Noun anchored with $ so
    # "bring discord to top of monitor 2" still falls to snap A.
    # Split: 'bring'/'move' exclude 'top' (would collide with the snap 'top' zone);
    # raise/pop/surface include 'top' (they're not snap verbs).
    (r"\b(?:bring|move)\s+(.+?)\s+(?:to\s+(?:the\s+)?)?"
     r"(?:front|forward|foreground|up)$",                              wm.bring_to_front),
    (r"\b(?:raise|pop|surface)\s+(.+?)\s+(?:to\s+(?:the\s+)?)?"
     r"(?:front|forward|foreground|top|up)$",                          wm.bring_to_front),
    # 'send' excludes 'bottom' (would collide with a saved 'bottom' zone);
    # push/sink/drop include it.
    (r"\bsend\s+(.+?)\s+(?:to\s+(?:the\s+)?)?"
     r"(?:back|backward|background|behind|down)$",                     wm.send_to_back),
    (r"\b(?:push|sink|drop)\s+(.+?)\s+(?:to\s+(?:the\s+)?)?"
     r"(?:back|backward|background|bottom|behind|down)$",              wm.send_to_back),

    # Bare snap — snap/move/send tolerate missing "to"; bring/put REQUIRE "to" so
    # "bring up firefox" still routes to open_app and doesn't get misread as
    # snap_app("up","firefox").
    (r"\b(?:snap|move|send)\s+(.+?)\s+(?:to\s+)?(?:the\s+)?([\w-]+)(?:\s+(?:zone|half|section))?$", tiling.snap_app),
    (r"\b(?:bring|put)\s+(.+?)\s+to\s+(?:the\s+)?([\w-]+)(?:\s+(?:zone|half|section))?$",           tiling.snap_app),

    # Startup on login — register/unregister Eve in the HKCU Run key. MUST come
    # before the Apps open intent (verbs include "start"/"run") so "start eve on
    # login" isn't read as opening an app called "eve on login".
    # Disable patterns FIRST — "don't start eve at login" contains "start eve at
    # login", which the enable pattern would otherwise grab.
    (r"(?:don'?t|do not|stop|disable)\s+(?:starting|start|launch(?:ing)?|run(?:ning)?)\s+(?:eve\s+)?(?:on|at|when\s+i)?\s*(?:log\s*in|login|startup|boot|start\s*up)",  _autostart_disable),
    (r"\b(?:remove|disable)\s+(?:eve\s+)?(?:from\s+)?(?:windows\s+)?startup\b",                  _autostart_disable),
    (r"(?:start|launch|run|open)\s+(?:eve\s+)?(?:on|at|when\s+i)\s+(?:log\s*in|login|startup|boot|start\s*up)",  _autostart_enable),
    (r"\b(?:add|enable)\s+(?:eve\s+)?(?:to\s+)?(?:windows\s+)?startup\b",                       _autostart_enable),
    (r"\b(?:do you|will you|are you set to)\s+start\s+(?:on|at)\s+(?:log\s*in|login|startup)\b", _autostart_status),

    # Timer creation — MUST come before the Apps open intent, whose verb list
    # includes "start"/"run", so "start a 10 minute timer" isn't read as
    # opening an app called "a 10 minute timer".
    # "set a timer for 5 minutes" / "set timer 5 minutes" / "start a 5 minute timer"
    (r"(?:set|start|begin|create)\s+(?:a\s+)?timer\s+(?:for\s+|of\s+)?(\d+)\s+minutes?",  reminders.set_timer),
    (r"(?:set|start|begin|create)\s+(?:a\s+)?(\d+)[\s-]+minutes?\s+timer",                reminders.set_timer),

    # Apps — close is graceful, kill is force-terminate
    (r"(?:open|launch|start|pull up|bring up|fire up|boot up|load up|run|start up)\s+(.+)", apps.open_app),
    (r"(?:close|quit|exit)\s+(.+)",                               apps.close_app),
    (r"kill\s+(.+)",                                              apps.kill_app),

    # Direct navigation
    (r"(?:go to|navigate to|take me to|visit|browse to)\s+(.+)",  search.go_to_site),

    # Web search (after YouTube so "search youtube" is caught above)
    (r"(?:search for|look up|google|search|find)\s+(.+)",         search.web_search_list),

    # Date / time
    (r"what(?:'?s| is) (?:the )?time|what time is it",          system.get_time),
    (r"what(?:'?s| is) (?:the |today(?:'?s?)? )?(?:date|day is it)", system.get_date),

    # Memory recall — "what is my X" / "what's my X" / "what do you remember"
    # Placed AFTER time/date so "what's the time" still wins (no `my` qualifier).
    (r"\bwhat\s+do\s+you\s+remember(?:\s+about\s+(.+))?$",                                 ctx_cmd.list_memories_voice),
    (r"\bwhat(?:'?s|\s+is)\s+my\s+(.+)$",                                                  ctx_cmd.recall_voice),

    # Reminders / timers
    # Reminders panel (visual) — before the speak-list and generic "remind me".
    (r"(?:open|show|edit)\s+(?:the\s+|my\s+)?reminders?(?:\s+(?:panel|list|manager))?$",  ctx_cmd.open_reminders_panel),
    (r"remind me in (\d+) minutes? to (.+)",                    reminders.set_reminder),
    # Absolute / recurring / multi-turn: "remind me to call mom at 3pm",
    # "remind me to stretch every day at 8", "remind me to take pills" (→ "when?")
    (r"remind me (?:to |that |about )?(.+)",                    ctx_cmd.remind),
    (r"(?:what are my reminders|list reminders|any reminders)",  reminders.list_reminders),
    (r"cancel (?:all )?(?:my )?(?:reminders|timers)",           reminders.cancel_all),

    # Voice toggle — before system mute so "mute voice" doesn't hit toggle_mute
    (r"(?:silence|be quiet|shut up|mute (?:voice|eve|speech)|disable (?:voice|speech|tts))", system.silence_voice),
    (r"(?:enable|unmute|turn on) (?:voice|speech|tts)",         system.enable_voice),
    (r"toggle (?:voice|speech|tts)",                            system.toggle_voice),

    # Volume / media
    (r"volume up",                                               system.volume_up),
    (r"volume down",                                             system.volume_down),

    # ── Discord in-call (mute/deafen/disconnect) ─────────────────────────
    # Global keybinds — no focus theft. Qualified mute/unmute only; the bare
    # (?:mute|unmute) system pattern below still wins for plain "mute".
    (r"\bmute\s+(?:me|myself|mic|microphone|discord|voice)\b",                                discord_cmd.mute),
    (r"\bunmute\s+(?:me|myself|mic|microphone|discord|voice)\b",                              discord_cmd.mute),
    (r"\bdeafen(?:\s+(?:me|myself|discord))?\b",                                              discord_cmd.deafen),
    (r"\bundeafen(?:\s+(?:me|discord))?\b",                                                   discord_cmd.deafen),
    (r"\b(?:disconnect|hang\s+up)(?:\s+(?:from\s+)?(?:voice|call|discord))?\b",               discord_cmd.disconnect),
    (r"\bleave\s+(?:the\s+)?(?:voice|call|discord)\b",                                        discord_cmd.disconnect),

    (r"(?:mute|unmute)",                                         system.toggle_mute),
    (r"(?:pause|play|resume)",                                   system.media_play_pause),
    (r"next (?:song|track|one)",                                 system.media_next),
    (r"(?:previous|last|back) (?:song|track|one)",              system.media_prev),

    # System
    (r"take (?:a )?screenshot",                                  system.screenshot),
    (r"cancel (?:the )?shutdown",                                system.cancel_shutdown),
    (r"(?:shut down|shutdown|turn off)(?: the computer)?",       system.shutdown),
    (r"(?:go to )?sleep",                                        system.sleep_pc),
]

# Bare-form z-order fallbacks — "firefox to front" / "google chrome to back".
# App capped at 1-3 words so common phrases ("go to back", "we have to win")
# only match when they end in an actual z-order noun. Must run BEFORE the
# web-search intent (which treats "google ..." as a search verb), but as the
# LAST entry in INTENTS so explicit verb forms always win.
_BARE_ZORDER_INTENTS = [
    (r"^([\w]+(?:\s+[\w]+){0,2})\s+to\s+(?:the\s+)?"
     r"(?:front|forward|foreground|top|up)$",                          wm.bring_to_front),
    (r"^([\w]+(?:\s+[\w]+){0,2})\s+to\s+(?:the\s+)?"
     r"(?:back|backward|background|bottom|behind|down)$",              wm.send_to_back),
]
# Splice them in just before the web-search pattern so "google chrome to front"
# beats "google <query>", while explicit z-order verbs higher up still win.
_WEB_SEARCH_IDX = next(
    i for i, (pat, h) in enumerate(INTENTS) if 'search for' in pat
)
INTENTS[_WEB_SEARCH_IDX:_WEB_SEARCH_IDX] = _BARE_ZORDER_INTENTS

# Maps handler → feature key so disabled features are silently skipped.
# Looked up by dispatch() inside the INTENTS loop: if the matched handler's
# feature is OFF (or unavailable), the match is treated as if it never fired
# and dispatch continues to the next pattern.
_HANDLER_FEATURE = {
    search.go_to_site:          'web_search',
    search.web_search_list:     'web_search',
    reminders.set_reminder:     'reminders',
    reminders.set_timer:        'reminders',
    reminders.list_reminders:   'reminders',
    reminders.cancel_all:       'reminders',
    apps.open_app:              'apps',
    apps.close_app:             'apps',
    apps.kill_app:              'apps',
    tiling.snap_app:            'tiling',
}

from config import WAKE_PREFIXES as _WAKE_PREFIXES

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8,
}


def _dispatch_site_listing(text: str):
    """Handle selection commands when a web search result list is displayed."""
    from core.response import Silent

    # "go to 3" / "open 2" / "go to link 3" / "open the 2nd link"
    m = re.search(
        r"(?:open|go to|visit|select|choose|pick)\s+"
        r"(?:the\s+)?(?:link\s+)?(?:number\s+)?(\d+)(?:\s+link)?",
        text
    )
    if m:
        return search.select_site(int(m.group(1)))

    # "link 3" / "number 3" / bare "3"
    m = re.search(r"(?:^|(?:link|number)\s+)(\d+)(?:\s+link)?$", text)
    if m:
        return search.select_site(int(m.group(1)))

    # ordinals: "first" / "the second one" / "go to the third link"
    for word, n in _ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            return search.select_site(n)

    if re.search(r"read (?:the list|them|it|those)|read (?:it )?(?:again|back)", text):
        return search.read_site_list()

    # domain/title keyword: "open the wikipedia one" / "go to reddit"
    m = re.search(r"(?:open|go to|visit|the)\s+(?:the\s+)?(.+?)(?:\s+(?:link|one|result))?$", text)
    if m:
        return search.select_site_by_title(m.group(1))

    if re.search(r"\b(?:cancel|never mind|forget it|exit|close|stop)\b", text):
        _sess_mod.reset()
        return Silent("Search closed")

    return None


def _dispatch_listing(text: str):
    """Handle selection commands when a web-search result list is displayed.
    (YouTube's own list/playback follow-ups live in skills/youtube.py via the
    converse layer.)"""
    sess = _sess_mod.get()
    if sess.site_list:
        return _dispatch_site_listing(text)
    return None  # fall through to normal dispatch


# Words/phrases Whisper commonly substitutes for trigger words.
# Patterns are matched as whole words (\b) on lowercased text. Order matters:
# multi-word phrases before single-word ones so they win.
_MISHEAR_SUBS = [
    # Multi-word phrases that mean a panel name
    (r'\bvoice\s+manor\b',          'voice manager'),
    (r'\bvoice\s+center\b',         'voice settings'),
    (r'\bvocal\s+settings\b',       'voice settings'),
    (r'\bvocal\s+manager\b',        'voice manager'),
    (r'\bcommands?\s+editor\b',     'command editor'),
    (r'\brooting\s+directory\b',    'routing directory'),
    (r'\bwriting\s+directory\b',    'routing directory'),
    (r'\boverlay?\s+directory\b',   'routing directory'),

    # "show me" / "tell me" filler that lures regex away
    (r'\bshow\s+me\b',              'show'),
    (r'\btell\s+me\b',              'show'),
    (r'\bcan\s+you\s+(?:please\s+)?', ''),
    (r'\bplease\b',                 ''),

    # Common single-word verb mishears
    (r'\bin\b',                     'open'),
    (r'\bam\b',                     'open'),
    (r'\bon\b',                     'open'),
    (r'\band\b',                    'open'),
    (r'\bup\b(?!\s+(?:to|by))',     'open'),  # "up firefox" → "open firefox"
    (r'\bopened\b',                 'open'),
    (r'\bopens\b',                  'open'),
    (r'\bopening\b',                'open'),
    (r'\blaunched\b',               'launch'),
    (r'\bstarted\b',                'start'),

    # Noun mishears
    (r'\bat\b(?=\s+manager)',       'app'),     # "at manager" → "app manager"
    (r'\bhood\b',                   'hud'),     # "hood" → "hud"
    (r'\bhead\b',                   'hud'),     # "show head" → "show hud"
    (r'\bhugh\b',                   'hud'),
    (r'\boverly\b',                 'overlay'),
    (r'\boverlaid\b',               'overlay'),
    (r'\boverleaf\b',               'overlay'),

    # YouTube mishears
    (r'\byou\s+tube\b',             'youtube'),
    (r'\byou\s+too\b',              'youtube'),
]


def _apply_mishear_subs(text: str) -> str:
    out = text
    for pat, rep in _MISHEAR_SUBS:
        out = re.sub(pat, rep, out)
    return re.sub(r'\s+', ' ', out).strip()


def _try_intents(text: str):
    """Run text against the INTENTS regex table. Returns response or None."""
    for pattern, handler in INTENTS:
        m = re.search(pattern, text)
        if m:
            feat = _HANDLER_FEATURE.get(handler)
            if feat and not _features.get(feat):
                continue
            groups = m.groups()
            return handler(*groups) if groups else handler()
    return None


# Verbs Whisper sometimes glues to the next word, producing things like
# "snapfirefox to bottom" or "openchrome". When the text begins with one of
# these followed immediately by another letter, _try_unstick splits and retries.
_STICK_VERBS = (
    'snap', 'move', 'send',                    # tiling
    'open', 'close', 'kill', 'launch', 'start', 'run',  # apps
    'show', 'hide', 'identify', 'list',        # UI
    'set', 'make', 'change',                   # WM mutation
    'pin', 'put',                              # HUD move
)


def _try_unstick(text: str):
    """If text starts with a known verb fused to a word ('snapfirefox'),
    split and re-run INTENTS. Returns handler result or None."""
    for verb in _STICK_VERBS:
        if (text.startswith(verb)
                and len(text) > len(verb)
                and text[len(verb)].isalpha()):
            corrected = verb + ' ' + text[len(verb):]
            result = _try_intents(corrected)
            if result is not None:
                return result
    return None


def _guess_dispatch(text: str):
    """Tiered fuzzy fallback:
      1. Mishear substitutions, then retry INTENTS.
      2. Prefix-retry ("open <text>") through INTENTS.
      3. Phrase-similarity score against the intent catalog:
            score ≥ HIGH  → execute silently
            score ≥ MED   → ask "did you mean X?" via pending_confirm
            score < MED   → no match
    """
    from core import intent_match
    from core.response import Silent

    # 1. mishear-subs then regex retry
    corrected = _apply_mishear_subs(text)
    if corrected != text:
        result = _try_intents(corrected)
        if result is not None:
            return result

    # 2. unstick — Whisper sometimes glues a verb to the next word
    #    ("snapfirefox to bottom" -> "snap firefox to bottom")
    result = _try_unstick(corrected)
    if result is not None:
        return result

    # 3. prefix retry — catches bare names: "firefox", "window manager".
    #    But open_app's pattern matches ANY text and returns Silent("Unknown
    #    app: …") for names it doesn't know, which would turn every unrecognized
    #    phrase into "Unknown app: <phrase>". Only accept a *real* match.
    prefixed = f"open {corrected}"
    result = _try_intents(prefixed)
    if result is not None and not _is_unknown_app(result):
        return result

    # 4. catalog scoring
    catalog = intent_match.build_catalog()
    match   = intent_match.best_match(corrected, catalog)
    if match is None:
        return None
    canonical, fn, args, score, strict = match

    # Silent-exec only when BOTH the lenient (token_set) and strict (token_sort)
    # scores are high — strict guards against a short catalog phrase matching as
    # a subset of a much longer utterance (e.g. "make my app manager full
    # screen" → "app manager"). Otherwise demote to a confirmation.
    if score >= intent_match.HIGH_THRESHOLD and strict >= intent_match.STRICT_THRESHOLD:
        return fn(*args)

    # Medium confidence → store and ask. Next utterance handles yes/no.
    sess = _sess_mod.get()
    sess.pending_confirm = (fn, args, canonical)
    return Silent(f"Did you mean: {canonical}?")


def _is_unknown_app(result) -> bool:
    """True if `result` is open_app's 'Unknown app: …' Silent placeholder."""
    from core.response import Silent
    return isinstance(result, Silent) and str(result).lower().startswith("unknown app")


# ── Yes/no confirmation handling (single-turn converse) ────────────────────
_YES_RE = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|do it|please|go ahead|confirmed?)\b", re.I)
_NO_RE  = re.compile(r"^(?:no|nope|nah|cancel|never ?mind|forget it|stop)\b", re.I)


def _handle_confirmation(text: str):
    """Resolve any pending 'did you mean' suggestion. Returns the handler
    response if the utterance was yes/no, or None to fall through."""
    from core.response import Silent
    sess = _sess_mod.get()
    pending = sess.pending_confirm
    if pending is None:
        return None
    fn, args, canonical = pending
    if _YES_RE.search(text):
        sess.pending_confirm = None
        return fn(*args)
    if _NO_RE.search(text):
        sess.pending_confirm = None
        return Silent("Cancelled")
    # Any other utterance: clear pending and let it route normally
    sess.pending_confirm = None
    return None


def _handle_converse(text: str):
    """Give an active converse context first crack at the utterance, ahead of
    normal intent matching. Returns the handler's response if it claims the
    utterance, else None to fall through. Expired or erroring contexts are
    cleared; a clean decline leaves the context in place for a later, clearer
    follow-up (it still decays via TTL)."""
    sess = _sess_mod.get()
    conv = sess.converse
    if conv is None:
        return None
    if not conv.alive():
        sess.converse = None
        return None
    try:
        result = conv.handler(text)
    except Exception:
        sess.converse = None
        return None
    if result is None:
        return None
    conv.touch()
    if not conv.alive():
        sess.converse = None
    return result


def dispatch(text: str):
    text = text.strip().lower()
    text = re.sub(r"[.,!?]+$", "", text).strip()  # strip trailing punctuation Whisper adds

    # Strip wake word if Whisper caught it
    for prefix in _WAKE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip(",. ")
            break

    # --- Pending confirmation? ---
    # If the previous utterance was a medium-confidence guess, this utterance
    # may be a yes/no answer. _handle_confirmation returns the executed result
    # on yes, "Cancelled" on no, or None to fall through to normal dispatch.
    confirmed = _handle_confirmation(text)
    if confirmed is not None:
        return confirmed

    # --- Active converse context? ---
    # A handler (e.g. a just-set timer) may have claimed upcoming utterances so
    # follow-ups like "cancel it" / "add 2 minutes" route straight back to it
    # without re-matching from scratch. Runs before normal routing; declines
    # fall through.
    claimed = _handle_converse(text)
    if claimed is not None:
        return claimed

    # --- State-aware routing ---
    sess = _sess_mod.get()
    # In-app web-search result lists; YouTube's own list/playback follow-ups are
    # handled by skills/youtube.py via the converse layer above.
    if sess.mode == Mode.LISTING and _features.get('inapp_search'):
        result = _dispatch_listing(text)
        if result is not None:
            return result

    # User-defined custom commands (custom_commands.json)
    for phrase, command in _load_custom():
        if phrase.lower() in text:
            subprocess.Popen(command, shell=True)
            return "Done"

    # User-defined aliases for built-in functions (aliases.json)
    for phrase, key in _load_aliases():
        if phrase.lower() in text:
            handler = BUILTIN_MAP.get(key)
            if handler:
                return handler()

    # PREEMPT skills (skills/*.py with PREEMPT=True) run just BEFORE the built-in
    # table so they can own phrases the built-ins would otherwise claim — e.g.
    # YouTube's "open youtube" beating the app launcher's "open X". Placed here
    # (after custom commands/aliases) to keep the priority the in-table YouTube
    # intents used to have.
    from core import skills
    pre = skills.dispatch_preempt(text)
    if pre is not None:
        return pre

    # Built-in intents
    for pattern, handler in INTENTS:
        m = re.search(pattern, text)
        if m:
            groups = m.groups()
            return handler(*groups) if groups else handler()

    # Drop-in skills (skills/*.py) — extend the built-ins without editing core.
    # Tried after built-ins so they add commands rather than override them.
    skill_result = skills.dispatch(text)
    if skill_result is not None:
        return skill_result

    # Fuzzy guess — handle speech-recognition mishears before giving up
    guess = _guess_dispatch(text)
    if guess is not None:
        return guess

    # Local LLM fallback (opt-in via config.FALLBACK_LLM = "ollama"). Returns
    # None when off/unavailable, so we fall through to the plain reply.
    from commands import fallback
    llm = fallback.answer(text)
    if llm is not None:
        return llm

    return "Not recognized. Say 'help' to hear available commands."
