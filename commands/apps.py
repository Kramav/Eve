import ctypes
import json
import os
import shutil
import subprocess
import threading
import winreg
from pathlib import Path
from core.response import Silent, Verified

_APPS_FILE = Path(__file__).parent.parent / "apps.json"

# Adaptive per-app launch timing. open_app verifies the app actually started by
# watching its process count; if a retry reveals the first launch HAD worked
# (just slowly) we learn to wait longer next time, so we stop double-launching
# slow apps. Persisted in app_launch_delays.json {appname: seconds}.
_LAUNCH_FILE  = Path(__file__).parent.parent / "app_launch_delays.json"
_BASE_DELAY   = 1.2    # default wait before the first launch check (seconds)
_SLOW_ANNOUNCE = 2.5   # at/above this learned delay, warn "this may take a moment"
_BUMP_STEP    = 1.5    # add this much when we detect a slow double-launch
_DECAY_STEP   = 0.3    # shave this much after a clean first-try success
_MAX_DELAY    = 8.0    # never wait longer than this

_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW — no console flash


def _count_proc(exe: str) -> int:
    """How many processes named *exe* (e.g. 'firefox.exe') are running."""
    if not exe:
        return 0
    try:
        out = subprocess.run(
            ['tasklist', '/fi', f'imagename eq {exe}', '/nh'],
            capture_output=True, text=True, creationflags=_NO_WINDOW).stdout
    except Exception:
        return 0
    return out.lower().count(exe.lower())


def _load_delays() -> dict:
    try:
        d = json.loads(_LAUNCH_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_delays(d: dict) -> None:
    try:
        _LAUNCH_FILE.write_text(json.dumps(d, indent=2))
    except Exception:
        pass


def _launch_delay(name: str) -> float:
    return float(_load_delays().get(name.lower(), _BASE_DELAY))


def _record_slow(name: str) -> None:
    """The app launched but slowly enough that we double-launched it — wait
    longer before judging it next time."""
    d = _load_delays()
    cur = float(d.get(name.lower(), _BASE_DELAY))
    d[name.lower()] = min(_MAX_DELAY, cur + _BUMP_STEP)
    _save_delays(d)


def _record_fast(name: str) -> None:
    """Confirmed on the first try — gently trim an inflated delay back down."""
    d = _load_delays()
    cur = float(d.get(name.lower(), _BASE_DELAY))
    new = max(_BASE_DELAY, cur - _DECAY_STEP)
    if new < cur:
        d[name.lower()] = new
        _save_delays(d)


def find_firefox() -> str | None:
    """Full path to firefox.exe via PATH → App Paths registry → standard install
    dirs, or None. Firefox often isn't on PATH on Windows, so check all three."""
    p = shutil.which('firefox')
    if p:
        return p
    resolved = _resolve_exe('firefox.exe')
    if resolved.lower().endswith('firefox.exe') and os.path.isfile(resolved):
        return resolved
    for base in (os.environ.get('PROGRAMFILES', r'C:\Program Files'),
                 os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')):
        cand = Path(base) / 'Mozilla Firefox' / 'firefox.exe'
        if cand.is_file():
            return str(cand)
    return None

_APP_PATHS_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\App Paths'),
    (winreg.HKEY_CURRENT_USER,  r'SOFTWARE\Microsoft\Windows\App Paths'),
]


def _resolve_exe(cmd: str) -> str:
    """Expand a bare exe name (e.g. 'firefox.exe') to its full path via App Paths registry."""
    if os.sep in cmd or '/' in cmd:
        return cmd
    bare = cmd if cmd.lower().endswith('.exe') else cmd + '.exe'
    for hive, base in _APP_PATHS_KEYS:
        try:
            key = winreg.OpenKey(hive, rf'{base}\{bare}')
            path = winreg.QueryValue(key, '').strip().strip('"')
            winreg.CloseKey(key)
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass
    return cmd

_CLOSE_MAP = {
    "chrome":    "chrome.exe",
    "firefox":   "firefox.exe",
    "edge":      "msedge.exe",
    "spotify":   "Spotify.exe",
    "discord":   "Discord.exe",
    "vs code":   "Code.exe",
    "vscode":    "Code.exe",
    "notepad":   "notepad.exe",
    "teams":     "Teams.exe",
    "zoom":      "Zoom.exe",
    "obs":       "obs64.exe",
    "vlc":       "vlc.exe",
    "slack":     "slack.exe",
    "task manager": "Taskmgr.exe",
    "taskmgr":      "Taskmgr.exe",
}


def _load_apps() -> dict:
    try:
        return {name.lower(): cmd for name, cmd in json.loads(_APPS_FILE.read_text())}
    except Exception:
        return {}


# ── Focus invariant on launch ────────────────────────────────────────────────
# Launching an app must not pull focus off a game / protected task. ShellExecute's
# nCmdShow=4 (SW_SHOWNOACTIVATE) is only a hint — apps routinely self-activate on
# startup — so when a protected app owns the screen we capture the prior foreground
# and hand focus back once the new window has settled. Gated by game_protection via
# essential.should_defer().

def _capture_focus_guard() -> int:
    """hwnd whose focus must be restored after a launch, or 0 if focus is free to
    move. Non-zero only when a game / protected app currently owns the screen."""
    from core import essential, window_ops
    if essential.should_defer():
        return window_ops.foreground_hwnd()
    return 0


def _restore_foreground(hwnd: int, attempts=(1.5, 1.5)) -> None:
    """Re-assert *hwnd* as the foreground window if a just-launched app stole it.
    Retries a couple of times to cover apps that activate late in startup; skips
    once the window is gone or already foreground (so we never fight the user)."""
    import time
    from core import window_ops, key_ops
    for wait in attempts:
        time.sleep(wait)
        if not window_ops.exists(hwnd):
            return
        if window_ops.foreground_hwnd() != hwnd:
            key_ops.focus_window(hwnd)


def open_app(name: str, snap_rect: tuple | None = None) -> str:
    """Launch an app. If *snap_rect* is provided (x, y, w, h in screen pixels),
    the new window is snapped there instead of centred on a free monitor."""
    name = name.strip()
    apps = _load_apps()
    cmd = apps.get(name.lower())

    if not cmd:
        return Silent(f"Unknown app: {name}")

    # Auto-snap on launch: if no explicit rect was passed but this app has a
    # saved zone assignment, place it there instead of centering on a monitor.
    if snap_rect is None:
        try:
            from commands import tiling
            snap_rect = tiling.zone_rect_for_app(name)
        except Exception:
            snap_rect = None

    cmd = _resolve_exe(cmd)

    def _spawn():
        # SW_SHOWNOACTIVATE = 4: OS-level hint to open without stealing focus
        ctypes.windll.shell32.ShellExecuteW(None, "open", cmd, None, None, 4)

    try:
        from core import monitor
        exe = os.path.basename(cmd)
        verifiable = exe.lower().endswith(".exe")
        proc_before = _count_proc(exe) if verifiable else 0

        # Capture the focus-restore target BEFORE launching (foreground is still
        # the user's task at this point).
        focus_guard = _capture_focus_guard()

        before = monitor.snapshot_windows(min_size=0)  # include compact overlay so it's never misidentified as new
        _spawn()
        if snap_rect is not None:
            threading.Thread(
                target=monitor.move_new_window_to_rect,
                args=(before, snap_rect),
                daemon=True,
            ).start()
        else:
            target = monitor.get_target_monitor()
            threading.Thread(
                target=monitor.move_new_window,
                args=(before, target),
                daemon=True,
            ).start()

        # Focus invariant: hand focus back to the game / protected task the launch
        # may have stolen it from.
        if focus_guard:
            threading.Thread(target=_restore_foreground,
                             args=(focus_guard,), daemon=True).start()

        # Can't tie this command to a process image — report optimistically.
        if not verifiable:
            return f"Opening {name}"

        # Already running? "open" just focuses/raises it — confirm quickly, and
        # never retry (a second launch would spawn a duplicate window).
        if proc_before > 0:
            return Verified(
                f"Opened {name}",
                check=lambda: _count_proc(exe) >= proc_before,
                on_fail=f"I tried to open {name} but couldn't confirm it.",
                delay=0.4,
            )

        # Cold launch — wait the learned delay, then confirm a process appeared.
        delay = _launch_delay(name)
        announce = (f"Opening {name} now. This may take a moment."
                    if delay >= _SLOW_ANNOUNCE else None)
        state = {"retried": False, "recorded": False}

        def _check():
            cnt = _count_proc(exe)
            ok = cnt > proc_before
            if ok and not state["recorded"]:
                state["recorded"] = True
                if state["retried"] and (cnt - proc_before) >= 2:
                    _record_slow(name)   # both launches took → wait longer next time
                elif not state["retried"]:
                    _record_fast(name)   # confirmed first try → trim the delay
            return ok

        def _retry():
            state["retried"] = True
            _spawn()

        return Verified(
            f"Opened {name}",
            check=_check,
            on_fail=f"I tried to open {name} twice but couldn't confirm it opened.",
            retry=_retry,
            announce=announce,
            delay=delay,
        )
    except Exception:
        return f"Couldn't open {name}"


def _resolve_close_exe(name: str) -> str:
    exe = _CLOSE_MAP.get(name.lower(), name)
    if not exe.endswith(".exe"):
        exe += ".exe"
    return exe


def _running_image_names() -> list:
    """Image names of every running process, e.g. ['chrome.exe', 'Taskmgr.exe']."""
    try:
        out = subprocess.run(['tasklist', '/fo', 'csv', '/nh'], capture_output=True,
                             text=True, creationflags=_NO_WINDOW).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('"'):
            names.append(line[1:].split('"', 1)[0])   # first CSV field
    return names


# Common system processes we never want to suggest closing/killing.
_SUGGEST_BLOCKLIST = {
    "explorer.exe", "svchost.exe", "system", "csrss.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "dwm.exe", "python.exe", "pythonw.exe",
    "conhost.exe", "runtimebroker.exe", "eve-tauri.exe", "electron.exe",
}


def _suggest_running(name: str):
    """Closest RUNNING process to the spoken `name`, or None. Returns the
    friendly base (no .exe). Used to turn a dead-end "isn't running" into an
    actionable "did you mean X?" — mishears ("chrom" → chrome) and unmapped
    apps whose window name ≈ image name."""
    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        return None
    procs = [p for p in _running_image_names() if p.lower() not in _SUGGEST_BLOCKLIST]
    if not procs:
        return None
    # Map each base name (lowercased, no .exe) → its display base, then fuzzy
    # match the spoken name against the bases.
    bases = {}
    for p in procs:
        base = p[:-4] if p.lower().endswith(".exe") else p
        bases.setdefault(base.lower(), base)   # first casing wins
    hit = process.extractOne(name.lower(), list(bases.keys()),
                             scorer=fuzz.token_sort_ratio, score_cutoff=72)
    if hit is None:
        return None
    return bases[hit[0]]                        # (choice, score, index) → choice


def _not_running(name: str, action):
    """Response when `name` isn't running: offer the closest running process as
    a 'did you mean X?' confirmation (a spoken yes re-runs `action` on it), or
    say so plainly. Skips the suggestion if it just resolves to the same name.

    Returns a `NeedConfirm` Outcome — the Conversation Engine speaks it and lets
    a bare 'yes' answer it; the legacy path (main._legacy_outcome) converts it
    back to a pending_confirm + spoken prompt, so it works either way."""
    sug = _suggest_running(name)
    if sug and sug.lower() != name.lower():
        from core.conversation import NeedConfirm
        return NeedConfirm(action=lambda: action(sug),
                           prompt=f"I don't see {name} running. Did you mean {sug}?")
    return f"{name} isn't running."


def close_app(name: str) -> str:
    """Graceful close — sends WM_CLOSE, lets the app save and exit cleanly."""
    name = name.strip()
    exe  = _resolve_close_exe(name)

    # Honest guard: if nothing's running under this exe name, don't taskkill a
    # phantom and let `_count_proc == 0` read as a false success (the old "close
    # task manager" → 'task manager.exe' bug). Offer the closest running process
    # as a spoken "did you mean X?" (answer yes → close it), else say so plainly.
    if _count_proc(exe) == 0:
        return _not_running(name, close_app)

    def _do():
        subprocess.run(['taskkill', '/im', exe], capture_output=True,
                       creationflags=_NO_WINDOW)

    try:
        _do()
        return Verified(
            f"Closed {name}",
            check=lambda: _count_proc(exe) == 0,
            # A graceful close can stall on an unsaved-changes dialog — say so
            # rather than claiming it closed.
            on_fail=f"I asked {name} to close, but it's still running — "
                    f"it may be waiting on you.",
            retry=_do,
            delay=0.8,
        )
    except Exception:
        return f"Couldn't close {name}"


def kill_app(name: str) -> str:
    """Force kill — immediately terminates the process, no save prompt."""
    name = name.strip()
    exe  = _resolve_close_exe(name)

    if _count_proc(exe) == 0:
        return _not_running(name, kill_app)

    def _do():
        subprocess.run(['taskkill', '/f', '/im', exe], capture_output=True,
                       creationflags=_NO_WINDOW)

    try:
        _do()
        return Verified(
            f"Killed {name}",
            check=lambda: _count_proc(exe) == 0,
            on_fail=f"I tried to kill {name}, but it's still running.",
            retry=_do,
            delay=0.6,
        )
    except Exception:
        return f"Couldn't kill {name}"
