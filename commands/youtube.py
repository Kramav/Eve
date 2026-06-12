import ctypes
import json
import re
import subprocess
import time
import urllib.parse
import webbrowser

import core.session as _sess_mod
from core.session import Mode

_display  = None
_mpv_proc = None

# mpv named-pipe IPC (Windows)
_MPV_PIPE_NAME = "eve_mpv"                  # value passed to --input-ipc-server
_MPV_PIPE_PATH = r'\\.\pipe\eve_mpv'        # path used by CreateFileW

_k32 = ctypes.windll.kernel32


def set_display(d) -> None:
    global _display
    _display = d


# ── mpv IPC ───────────────────────────────────────────────────────────────────

def _mpv_send(cmd: dict) -> bool:
    """Write one JSON command to mpv's named-pipe IPC. No-op if pipe is gone."""
    msg = (json.dumps(cmd) + '\n').encode('utf-8')
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


# ── Fetching ──────────────────────────────────────────────────────────────────

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


# ── Public intent handlers ────────────────────────────────────────────────────

def browse_home_intent():
    """Open YouTube in the user's default browser."""
    webbrowser.open("https://www.youtube.com")
    return "Opening YouTube"


def play_query_intent(query: str):
    from core.response import VideoList
    query = re.sub(r"\s+on (?:youtube|yt)$", "", query.strip())
    videos = _search_videos(query)
    if not videos:
        webbrowser.open(
            f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
        )
        return f"Searching YouTube for {query}"
    sess = _sess_mod.get()
    sess.video_list = videos
    sess.mode = Mode.LISTING
    return VideoList(videos, message=f'Results for "{query}"')


# ── Selection ─────────────────────────────────────────────────────────────────

def select_by_index(n: int) -> str:
    sess = _sess_mod.get()
    if not sess.video_list:
        return "No video list is active."
    if n < 1 or n > len(sess.video_list):
        return f"Say a number between 1 and {len(sess.video_list)}."
    video = sess.video_list[n - 1]
    sess.selected_url   = video["url"]
    sess.selected_title = video["title"]
    sess.mode = Mode.PLAYING
    _start_mpv(video["url"], video["title"])
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


# ── Playback control ──────────────────────────────────────────────────────────

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


# ── Cleanup ───────────────────────────────────────────────────────────────────

def close_youtube() -> str:
    _quit_mpv()
    _sess_mod.reset()
    return "YouTube closed"
