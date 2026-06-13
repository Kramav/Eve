import difflib
import json
import re
import subprocess
from pathlib import Path
from commands import apps, system, search, reminders, youtube
import core.session as _sess_mod
from core.session import Mode

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


# (regex pattern, handler) — first match wins, captured groups passed as args
INTENTS = [
    # Command editor
    (r"(?:open|edit|show|launch) (?:the )?(?:command editor|my commands|eve commands|commands)", system.open_editor),
    (r"(?:close|quit|exit|dismiss) (?:the )?(?:command editor|my commands|eve commands|commands)", system.close_editor),
    (r"kill (?:the )?(?:command editor|my commands|eve commands|commands)",                        system.close_editor),

    # Window Manager — before generic open/launch to prevent misrouting to open_app
    (r"(?:open|show|launch) (?:the )?window manager",          system.open_window_manager),
    (r"(?:close|quit|exit|dismiss) (?:the )?window manager",   system.close_window_manager),
    (r"kill (?:the )?window manager",                           system.close_window_manager),

    # App Manager
    (r"(?:open|show|launch) (?:the )?app manager",             system.open_app_manager),
    (r"(?:close|quit|exit|dismiss) (?:the )?app manager",      system.close_app_manager),
    (r"kill (?:the )?app manager",                             system.close_app_manager),

    # Help
    (r"help|what can (?:you|eve) do|list commands|show commands", _help),

    # YouTube — before apps and web search to prevent misrouting
    # "open youtube" / "show youtube" must not fall through to open_app
    # "search youtube" must not fall through to web_search
    (r"(?:open|launch|browse|show(?: me)?) (?:youtube|yt)(?:\s+home(?:page)?)?|^youtube$", youtube.browse_home_intent),
    (r"(?:search youtube|youtube)(?:\s+for)?\s+(.+)",             youtube.play_query_intent),
    (r"(?:play|watch)\s+(.+)",                                    youtube.play_query_intent),

    # Apps — close is graceful, kill is force-terminate
    (r"(?:open|launch|start|pull up|bring up|fire up|boot up|load up|run|start up)\s+(.+)", apps.open_app),
    (r"(?:close|quit|exit)\s+(.+)",                               apps.close_app),
    (r"kill\s+(.+)",                                              apps.kill_app),

    # Direct navigation
    (r"(?:go to|navigate to|take me to|visit|browse to)\s+(.+)",  search.go_to_site),

    # Web search (after YouTube so "search youtube" is caught above)
    (r"(?:search for|look up|google|search|find)\s+(.+)",         search.web_search_list),

    # Date / time
    (r"what(?:'?s| is) (?:the )?time",                          system.get_time),
    (r"what(?:'?s| is) (?:(?:today(?:'?s?)? )?date|day is it)", system.get_date),

    # Reminders / timers
    (r"remind me in (\d+) minutes? to (.+)",                    reminders.set_reminder),
    (r"set (?:a )?timer for (\d+) minutes?",                    reminders.set_timer),
    (r"(?:what are my reminders|list reminders|any reminders)",  reminders.list_reminders),
    (r"cancel (?:all )?(?:my )?(?:reminders|timers)",           reminders.cancel_all),

    # Volume / media
    (r"volume up",                                               system.volume_up),
    (r"volume down",                                             system.volume_down),
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

_WAKE_PREFIXES = ("hey jarvis", "hey eve", "jarvis", "eve")

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
    """Handle commands when a list is displayed — routes to site or video logic."""
    from core.response import Silent
    sess = _sess_mod.get()

    # Route to web search selection if a site list is active
    if sess.site_list:
        return _dispatch_site_listing(text)

    # ── YouTube video list ──────────────────────────────────────────────────

    # "play number N" / "select 3" / "open the 2nd"
    m = re.search(r"(?:play|open|select|choose|pick)\s+(?:the\s+)?(?:number\s+)?(\d+)", text)
    if m:
        return youtube.select_by_index(int(m.group(1)))

    # bare digit: "2" / "number 2"
    m = re.search(r"(?:^|number\s+)(\d+)$", text)
    if m:
        return youtube.select_by_index(int(m.group(1)))

    # ordinal words: "the third one", "second"
    for word, n in _ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            return youtube.select_by_index(n)

    # read the list aloud
    if re.search(r"read (?:the list|them|it|those)|read (?:it )?(?:again|back)", text):
        return youtube.read_list()

    # title match: "play [partial title]"
    m = re.search(r"(?:play|watch|open)\s+(.+)", text)
    if m:
        result = youtube.select_by_title(m.group(1))
        if result:
            return result

    # cancel / exit list
    if re.search(r"\b(?:cancel|never mind|forget it|exit|close|stop)\b", text):
        _sess_mod.reset()
        return Silent("List closed")

    return None  # fall through to normal dispatch


def _dispatch_playing(text: str):
    """Handle commands when a video is playing."""
    from core.response import VideoList

    # Skip ahead N seconds
    m = re.search(r"skip (?:ahead\s+)?(\d+)\s+seconds?", text)
    if m:
        return youtube.skip_seconds(int(m.group(1)))

    # Go back N seconds
    m = re.search(r"(?:go )?back (\d+)\s+seconds?", text)
    if m:
        return youtube.back_seconds(int(m.group(1)))

    if re.search(r"skip (?:ahead|forward)", text):
        return youtube.playback_control("skip ahead")
    if re.search(r"go back|rewind", text):
        return youtube.playback_control("go back")
    if re.search(r"\b(?:pause|resume)\b", text):
        return youtube.playback_control("pause")
    if re.search(r"\b(?:mute|unmute)\b", text):
        return youtube.playback_control("mute")
    if re.search(r"next (?:video|one)", text):
        return youtube.playback_control("next")
    if re.search(r"full ?screen", text):
        return youtube.playback_control("fullscreen")
    if re.search(r"(?:back to list|show list|show videos)", text):
        sess = _sess_mod.get()
        sess.mode = Mode.LISTING
        return VideoList(sess.video_list, message="Video list")
    if re.search(r"close youtube|stop youtube|exit youtube", text):
        return youtube.close_youtube()

    return None  # fall through to normal dispatch


# Words that speech recognition commonly substitutes for trigger words
_MISHEAR_SUBS = [
    (r'\bin\b',  'open'),   # "in firefox"   → "open firefox"
    (r'\bam\b',  'open'),   # "am firefox"   → "open firefox"
    (r'\bon\b',  'open'),   # "on firefox"   → "open firefox"
    (r'\bat\b',  'app'),    # "at manager"   → "app manager"
    (r'\band\b', 'open'),   # "and firefox"  → "open firefox"
]


def _guess_dispatch(text: str):
    """Fuzzy fallback: substitute misheared trigger words then retry intents;
    fall back to difflib match against known app spoken names."""

    # Pass 1: word substitutions — replace a misheared word and re-run intents
    for pat, rep in _MISHEAR_SUBS:
        corrected = re.sub(pat, rep, text)
        if corrected == text:
            continue
        for pattern, handler in INTENTS:
            m = re.search(pattern, corrected)
            if m:
                groups = m.groups()
                return handler(*groups) if groups else handler()

    # Pass 2: fuzzy match the whole phrase against known app spoken names
    known = apps._load_apps()   # {spoken_name: exe_path}
    if known:
        matches = difflib.get_close_matches(text, known.keys(), n=1, cutoff=0.6)
        if matches:
            return apps.open_app(matches[0])

    # Pass 3: prefix retry — prepend "open" and re-run intents
    # Catches bare names like "firefox", "window manager", "youtube"
    prefixed = f"open {text}"
    for pattern, handler in INTENTS:
        m = re.search(pattern, prefixed)
        if m:
            groups = m.groups()
            return handler(*groups) if groups else handler()

    return None


def dispatch(text: str):
    text = text.strip().lower()
    text = re.sub(r"[.,!?]+$", "", text).strip()  # strip trailing punctuation Whisper adds

    # Strip wake word if Whisper caught it
    for prefix in _WAKE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip(",. ")
            break

    # --- State-aware routing ---
    sess = _sess_mod.get()
    if sess.mode == Mode.LISTING:
        result = _dispatch_listing(text)
        if result is not None:
            return result
    elif sess.mode == Mode.PLAYING:
        result = _dispatch_playing(text)
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

    # Built-in intents
    for pattern, handler in INTENTS:
        m = re.search(pattern, text)
        if m:
            groups = m.groups()
            return handler(*groups) if groups else handler()

    # Fuzzy guess — handle speech-recognition mishears before giving up
    guess = _guess_dispatch(text)
    if guess is not None:
        return guess

    return "Not recognized. Say 'help' to hear available commands."
