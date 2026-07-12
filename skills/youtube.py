"""YouTube / mpv — voice-driven YouTube, as a fully self-contained skill.

This used to live in `commands/youtube.py` with its routing woven through
`core/dispatcher.py` (4 entry intents + three `Mode`-gated dispatchers). It's now
a drop-in skill with **no core coupling**:

  - Entry commands ("open/browse youtube", "search youtube for X", "play X") are
    `INTENTS`. The skill is `PREEMPT` so they beat the app launcher / web search
    ("open youtube" must not become open_app("youtube")).
  - The stateful follow-ups (feed scrolling, list selection, mpv playback) use
    the **converse layer** (`core.session.start_converse`) instead of bespoke
    session modes. A single `_converse()` routes by `_state`
    (`feed` → `list` → `play`), so "scroll down" / "play number 2" / "skip 30
    seconds" land back here while a session is live, then cleanly fall through
    once it ends.

Two paths, gated by features.json:
  - `youtube` (default on): the Eve-owned HUD feed window (Electron, driven via
    the Display directives `youtube_*`).
  - `mpv_youtube` (alpha, default off): yt-dlp search → pick → play in mpv via
    its named-pipe IPC.
"""
import ctypes
import json
import re
import subprocess
import time
import urllib.parse
import webbrowser

import core.session as _sess_mod
from core.response import Silent, VideoList

PREEMPT = True          # entry intents must run before open_app / web_search
FEATURE = "youtube"     # whole skill off when the YouTube feature is disabled

_display  = None
_mpv_proc = None
_state    = None        # None | "feed" | "list" | "play"

# mpv named-pipe IPC (Windows)
_MPV_PIPE_NAME = "eve_mpv"
_MPV_PIPE_PATH = r"\\.\pipe\eve_mpv"
_k32 = ctypes.windll.kernel32

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8,
}


def setup(display=None) -> None:
    global _display
    _display = display


# ── converse plumbing ─────────────────────────────────────────────────────────

def _arm() -> None:
    """(Re)claim upcoming utterances for the active YouTube session. Generous
    budget so a long browse/playback session doesn't decay mid-use; explicit
    close (or session reset) clears it."""
    _sess_mod.start_converse(_converse, label=f"youtube:{_state}",
                             turns=999, ttl=3600.0)


def _end_session() -> None:
    global _state
    _state = None
    _sess_mod.clear_converse()


def _converse(text: str):
    """Single converse entry point — routes to the live sub-state's handler.
    Returns a response to claim the utterance, or None to decline (fall through
    to normal dispatch while keeping the session alive)."""
    if _state == "feed":
        return _feed_converse(text)
    if _state == "list":
        return _list_converse(text)
    if _state == "play":
        return _play_converse(text)
    return None


# ── mpv IPC ───────────────────────────────────────────────────────────────────

def _mpv_send(cmd: dict) -> bool:
    """Write one JSON command to mpv's named-pipe IPC. No-op if pipe is gone."""
    msg = (json.dumps(cmd) + "\n").encode("utf-8")
    h = _k32.CreateFileW(
        ctypes.c_wchar_p(_MPV_PIPE_PATH),
        ctypes.c_uint32(0xC0000000),  # GENERIC_READ | GENERIC_WRITE
        ctypes.c_uint32(0),
        None,
        ctypes.c_uint32(3),           # OPEN_EXISTING
        ctypes.c_uint32(0),
        None,
    )
    if h == -1:
        return False
    written = ctypes.c_uint32(0)
    _k32.WriteFile(h, msg, len(msg), ctypes.byref(written), None)
    _k32.CloseHandle(h)
    return True


def _start_mpv(url: str, title: str = "") -> None:
    global _mpv_proc
    if _mpv_proc and _mpv_proc.poll() is None:
        _mpv_send({"command": ["quit"]})
        time.sleep(0.3)
    _mpv_proc = subprocess.Popen(
        [
            "mpv",
            f"--input-ipc-server={_MPV_PIPE_NAME}",
            "--no-terminal",
            "--force-window=yes",
            "--geometry=640x360-0+0",   # bottom-right, doesn't steal focus
            f"--title=Eve ▶ {title}" if title else "--title=Eve Player",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if _display is not None:
        _display.show_thumbnail(url, title)


def _quit_mpv() -> None:
    global _mpv_proc
    if _mpv_proc and _mpv_proc.poll() is None:
        _mpv_send({"command": ["quit"]})
        time.sleep(0.3)
        if _mpv_proc.poll() is None:
            _mpv_proc.terminate()
    _mpv_proc = None
    if _display is not None:
        _display.clear_thumbnail()


# ── fetching ──────────────────────────────────────────────────────────────────

def _fmt_duration(seconds) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _search_videos(query: str) -> list:
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
        return [
            {
                "title":    e.get("title", ""),
                "duration": _fmt_duration(e.get("duration")),
                "url":      f"https://www.youtube.com/watch?v={e['id']}",
            }
            for e in (info.get("entries") or []) if e
        ]
    except Exception:
        return []


# ── entry handlers (INTENTS) ──────────────────────────────────────────────────

def browse_home_intent():
    """Open YouTube in the user's default browser (no in-app controls)."""
    webbrowser.open("https://www.youtube.com")
    return "Opening YouTube"


def browse_feed_intent():
    """Open the YouTube feed in the HUD browser and claim feed follow-ups.
    Display auto-numbers the tiles once the page loads, so a numbered list
    appears without a separate 'number the videos' command."""
    global _state
    if _display is None:
        return "The overlay isn't running."
    _display.youtube_browse()
    _state = "feed"
    _arm()
    return "Opening YouTube — say a number to pick a video."


def play_or_search(query: str):
    """Default YouTube verb. Searches the HUD feed unless the `mpv_youtube`
    alpha is on, in which case it does the yt-dlp + mpv search-and-play list."""
    global _state
    from core import features
    if features.get("mpv_youtube"):
        return play_query_intent(query)
    _state = "feed"
    _arm()
    return feed_search(query)


def play_query_intent(query: str):
    global _state
    query = re.sub(r"\s+on (?:youtube|yt)$", "", query.strip())
    videos = _search_videos(query)
    if not videos:
        webbrowser.open(
            f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
        )
        return f"Searching YouTube for {query}"
    sess = _sess_mod.get()
    sess.video_list = videos
    _state = "list"
    _arm()
    return VideoList(videos, message=f'Results for "{query}"')


# ── HUD feed control ──────────────────────────────────────────────────────────

def feed_scroll(direction: str) -> str:
    if _display is not None:
        _display.youtube_scroll(direction)
    return {"up": "Scrolling up", "top": "Back to top"}.get(direction, "Scrolling down")


def feed_number() -> str:
    if _display is not None:
        _display.youtube_number()
    return "Numbering videos"


def feed_open(n: int) -> str:
    if _display is not None:
        _display.youtube_open(n)
    return f"Opening video {n}"


def feed_search(query: str) -> str:
    query = re.sub(r"\s+on (?:youtube|yt)$", "", query.strip())
    if _display is not None:
        _display.youtube_search(query)
        _display.youtube_number()
    return f"Searching the feed for {query}"


def feed_playpause() -> str:
    if _display is not None:
        _display.youtube_playpause()
    return ""


def feed_close() -> str:
    if _display is not None:
        _display.youtube_close()
    _end_session()
    _sess_mod.reset()
    return "Closing YouTube"


# ── mpv list selection ────────────────────────────────────────────────────────

def select_by_index(n: int) -> str:
    global _state
    sess = _sess_mod.get()
    if not sess.video_list:
        return "No video list is active."
    if n < 1 or n > len(sess.video_list):
        return f"Say a number between 1 and {len(sess.video_list)}."
    video = sess.video_list[n - 1]
    sess.selected_url   = video["url"]
    sess.selected_title = video["title"]
    sess.mode = _sess_mod.Mode.PLAYING          # main.py reads this for the player visual
    _start_mpv(video["url"], video["title"])
    _state = "play"
    _arm()
    return f"Playing: {video['title']}"


def select_by_title(partial: str) -> str:
    sess = _sess_mod.get()
    if not sess.video_list:
        return "No video list is active."
    pl = partial.lower()
    for i, v in enumerate(sess.video_list):
        if pl in v["title"].lower():
            return select_by_index(i + 1)
    return f'No match for "{partial}". Try saying the number.'


def read_list() -> str:
    sess = _sess_mod.get()
    if not sess.video_list:
        return "No video list to read."
    parts = []
    for i, v in enumerate(sess.video_list, 1):
        dur = f", {v['duration']}" if v.get("duration") else ""
        parts.append(f"{i}. {v['title']}{dur}")
    return ". ".join(parts)


# ── mpv playback control ──────────────────────────────────────────────────────

def playback_control(action: str) -> str:
    if action in ("pause", "play", "resume"):
        _mpv_send({"command": ["cycle", "pause"]})
        return action.capitalize()
    if action in ("skip ahead", "forward"):
        _mpv_send({"command": ["seek", 10, "relative"]})
        return "Skipping ahead"
    if action in ("go back", "rewind"):
        _mpv_send({"command": ["seek", -10, "relative"]})
        return "Going back"
    if action in ("mute", "unmute"):
        _mpv_send({"command": ["cycle", "mute"]})
        return action.capitalize()
    if action == "next":
        _mpv_send({"command": ["playlist-next"]})
        return "Next video"
    if action == "fullscreen":
        _mpv_send({"command": ["cycle", "fullscreen"]})
        return "Toggling fullscreen"
    return f"Unknown playback command: {action}"


def skip_seconds(seconds: int) -> str:
    _mpv_send({"command": ["seek", seconds, "relative"]})
    return f"Skipped {seconds} seconds"


def back_seconds(seconds: int) -> str:
    _mpv_send({"command": ["seek", -seconds, "relative"]})
    return f"Went back {seconds} seconds"


def close_youtube() -> str:
    _quit_mpv()
    _end_session()
    _sess_mod.reset()
    return "YouTube closed"


# ── converse sub-handlers (port of the old Mode dispatchers) ───────────────────

def _feed_converse(text: str):
    """Commands while the YouTube HUD browser is open (was Mode.BROWSING)."""
    m = re.search(r"(?:search(?:\s+(?:youtube|yt))?(?:\s+for)?|find|look\s+up)\s+(.+)", text)
    if m:
        return feed_search(m.group(1).strip())

    if re.search(r"scroll\s+(?:to\s+(?:the\s+)?top|up\s+top|all\s+the\s+way\s+up)|back\s+to\s+top", text):
        return feed_scroll("top")
    if re.search(r"scroll\s+up|go\s+up|page\s+up", text):
        return feed_scroll("up")
    if re.search(r"scroll(?:\s+down)?|go\s+down|page\s+down|more", text):
        return feed_scroll("down")

    if re.search(r"(?:show|label)\s+(?:the\s+)?numbers?|number\s+(?:them|videos|the\s+videos)", text):
        return feed_number()

    m = re.search(r"(?:open|play|watch|select|pick)\s+(?:video\s+|number\s+)?(\d+)", text)
    if m:
        return feed_open(int(m.group(1)))
    m = re.search(r"^(?:number\s+)?(\d+)$", text)
    if m:
        return feed_open(int(m.group(1)))
    for word, n in _ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            return feed_open(n)

    if re.search(r"\b(?:pause|resume|play|stop)\b", text):
        return feed_playpause()
    if re.search(r"close\s+youtube|stop\s+youtube|exit\s+youtube|close\s+the\s+feed", text):
        return feed_close()

    return None  # decline → falls through to normal dispatch


def _list_converse(text: str):
    """Selection commands while an mpv video list is shown (was Mode.LISTING)."""
    m = re.search(r"(?:play|open|select|choose|pick)\s+(?:the\s+)?(?:number\s+)?(\d+)", text)
    if m:
        return select_by_index(int(m.group(1)))

    m = re.search(r"(?:^|number\s+)(\d+)$", text)
    if m:
        return select_by_index(int(m.group(1)))

    for word, n in _ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            return select_by_index(n)

    if re.search(r"read (?:the list|them|it|those)|read (?:it )?(?:again|back)", text):
        return read_list()

    m = re.search(r"(?:play|watch|open)\s+(.+)", text)
    if m:
        result = select_by_title(m.group(1))
        if result:
            return result

    if re.search(r"\b(?:cancel|never mind|forget it|exit|close|stop)\b", text):
        _end_session()
        _sess_mod.reset()
        return Silent("List closed")

    return None


def _play_converse(text: str):
    """Commands while an mpv video is playing (was Mode.PLAYING)."""
    global _state
    m = re.search(r"skip (?:ahead\s+)?(\d+)\s+seconds?", text)
    if m:
        return skip_seconds(int(m.group(1)))

    m = re.search(r"(?:go )?back (\d+)\s+seconds?", text)
    if m:
        return back_seconds(int(m.group(1)))

    if re.search(r"skip (?:ahead|forward)", text):
        return playback_control("skip ahead")
    if re.search(r"go back|rewind", text):
        return playback_control("go back")
    if re.search(r"\b(?:pause|resume)\b", text):
        return playback_control("pause")
    if re.search(r"\b(?:mute|unmute)\b", text):
        return playback_control("mute")
    if re.search(r"next (?:video|one)", text):
        return playback_control("next")
    if re.search(r"full ?screen", text):
        return playback_control("fullscreen")
    if re.search(r"(?:back to list|show list|show videos)", text):
        _state = "list"
        _arm()
        return VideoList(_sess_mod.get().video_list, message="Video list")
    if re.search(r"close youtube|stop youtube|exit youtube", text):
        return close_youtube()

    return None


# ── intents (PREEMPT — tried before the built-in table) ────────────────────────
# Order mirrors the old core block: feed-browse before browser-home, search/play
# before they can be read as open_app / web_search.
INTENTS = [
    # Explicit "in my browser" escape hatch — must precede the general catch so
    # "open youtube in my browser" / "youtube homepage" opts out of the HUD.
    (r"(?:open|show)\s+youtube\s+(?:home(?:page)?|in\s+(?:my|the)\s+browser)", browse_home_intent),
    # Default: the controllable HUD feed (numbered, voice-driven) — the whole
    # point of the YouTube feature. Catches "open youtube", "browse youtube",
    # "help me browse youtube", "show me youtube", "open the youtube feed".
    (r"(?:browse|open|launch|show)(?:\s+me)?\s+(?:the\s+)?(?:youtube|yt)(?:\s+feed)?\b|^youtube$", browse_feed_intent),
    (r"(?:search youtube|youtube)(?:\s+for)?\s+(.+)",            play_or_search),
    # \b prevents matching "play" inside "display", "watch" inside "watchful".
    (r"\b(?:play|watch)\s+(.+)",                                 play_or_search),
]
