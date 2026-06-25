import ctypes
import ctypes.wintypes
import json
from pathlib import Path

_LAYOUTS_FILE = Path(__file__).parent.parent / "tiling_layouts.json"
_APPS_FILE    = Path(__file__).parent.parent / "apps.json"

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

_SWP_NOACTIVATE = 0x0010
_SWP_NOMOVE     = 0x0002
_SWP_NOSIZE     = 0x0001
_HWND_TOPMOST    = -1
_HWND_NOTOPMOST  = -2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ('cbSize',           ctypes.wintypes.UINT),
        ('flags',            ctypes.wintypes.UINT),
        ('showCmd',          ctypes.wintypes.UINT),
        ('ptMinPosition',    ctypes.wintypes.POINT),
        ('ptMaxPosition',    ctypes.wintypes.POINT),
        ('rcNormalPosition', ctypes.wintypes.RECT),
    ]


# Spoken name → internal panel id. Matched as substrings of the heard text
# so "snap routing directory to top" and "snap the directory to top" both work.
_PANEL_ALIASES = {
    'routing directory': 'directory',
    'directory':         'directory',
    'overlay':           'directory',
    'hud':               'directory',
    'window manager':    'window_manager',
    'app manager':       'app_manager',
    'voice settings':    'voice_settings',
    'voice manager':     'voice_settings',
}

_display = None


def set_display(display):
    """Injected from main.py so snap_panel can broadcast to Electron."""
    global _display
    _display = display


def _load_apps() -> dict:
    """Map spoken-name (lower) → exe basename (lower) for HWND lookup."""
    try:
        return {s.lower(): Path(p).name.lower() for s, p in json.loads(_APPS_FILE.read_text())}
    except Exception:
        return {}


def _load_layouts() -> dict:
    try:
        return json.loads(_LAYOUTS_FILE.read_text())
    except Exception:
        return {}


def _get_process_exe(pid: int) -> str:
    """Basename only — what the rest of tiling uses for matching."""
    full = _get_process_exe_path(pid)
    return Path(full).name.lower() if full else ''


def _get_process_exe_path(pid: int) -> str:
    """Full path to the process executable. Empty string on failure."""
    handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ''
    try:
        buf  = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        _k32.CloseHandle(handle)
    return ''


# Windows we don't want to list / target — Eve's own panels and shell windows.
_SKIP_EXES = {
    'searchhost.exe', 'shellexperiencehost.exe', 'systemsettings.exe',
    'textinputhost.exe', 'startmenuexperiencehost.exe', 'searchapp.exe',
    'lockapp.exe', 'applicationframehost.exe', 'sihost.exe', 'dwm.exe',
    # Bare electron.exe is Eve itself; well-known Electron apps (Discord,
    # VS Code, Slack, Spotify) rename their binary so they aren't affected.
    'electron.exe',
}
_SKIP_TITLE_PREFIXES = ('Eve',)
_SKIP_TITLES_EXACT   = {'Program Manager', 'eve-ui'}


def _get_window_title(hwnd: int) -> str:
    n = _u32.GetWindowTextLengthW(hwnd)
    if n == 0:
        return ''
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def enumerate_windows(min_w: int = 200, min_h: int = 200) -> list[dict]:
    """All visible top-level windows with a title and a reasonable size.

    Returns dicts: {hwnd, title, exe, x, y, w, h}. Sorted in roughly
    natural reading order (top monitors first, then left-to-right within
    a row band, then top-to-bottom)."""
    out     = []
    pid_buf = ctypes.wintypes.DWORD()

    def _cb(hwnd, _):
        if not _u32.IsWindowVisible(hwnd):
            return True
        title = _get_window_title(hwnd)
        if not title:
            return True
        if any(title.startswith(p) for p in _SKIP_TITLE_PREFIXES):
            return True
        if title in _SKIP_TITLES_EXACT:
            return True
        r = ctypes.wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right - r.left
        h = r.bottom - r.top
        if w < min_w or h < min_h:
            return True
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
        exe = _get_process_exe(pid_buf.value)
        if exe in _SKIP_EXES:
            return True
        out.append({
            'hwnd': hwnd, 'title': title, 'exe': exe,
            'x': r.left, 'y': r.top, 'w': w, 'h': h,
        })
        return True

    _u32.EnumWindows(_WNDENUMPROC(_cb), 0)
    # Sort: row band of 200px (Y), then by X within row → mirrors how the
    # user scans their screen.
    out.sort(key=lambda v: (v['y'] // 200, v['x']))
    return out


# Alt-Tab filter constants
_GWL_EXSTYLE       = -20
_WS_EX_TOOLWINDOW  = 0x00000080
_WS_EX_APPWINDOW   = 0x00040000
_GA_ROOTOWNER      = 3

# Background tool-window titles that creep past the WS_EX_TOOLWINDOW filter
_PROGRAM_TITLE_SKIP = {
    'Default IME', 'MSCTFIME UI', 'GDI+ Window', 'OleMainThreadWndName',
    'BluetoothEvent', 'BluetoothNotificationAreaIconWindowClass',
    '.NET-BroadcastEventWindow.4.0.0.0', 'NVIDIA GeForce Overlay',
    'Windows Push Notifications Platform', 'Settings',
}


def enumerate_programs() -> list[dict]:
    """Foreground-eligible programs — what the user would see in Alt-Tab.

    Applies the standard Alt-Tab criteria so background tool windows like
    'Default IME' / 'MSCTFIME UI' / IME plumbing don't leak into the list:
      * Has a non-empty title
      * Top-level (GetAncestor(GA_ROOTOWNER) == hwnd, no owner)
      * Not a hidden tool window (WS_EX_TOOLWINDOW without WS_EX_APPWINDOW)
      * Exe not in shell-skip set
      * Visible OR minimized (we keep minimized; users still think of those
        as "open")
    """
    out     = []
    pid_buf = ctypes.wintypes.DWORD()

    def _cb(hwnd, _):
        # 1. Visible OR minimized (skip truly hidden background windows)
        if not (_u32.IsWindowVisible(hwnd) or _u32.IsIconic(hwnd)):
            return True

        # 2. Has a title
        title = _get_window_title(hwnd)
        if not title:
            return True
        if title in _PROGRAM_TITLE_SKIP:
            return True
        if any(title.startswith(p) for p in _SKIP_TITLE_PREFIXES):
            return True
        if title in _SKIP_TITLES_EXACT:
            return True

        # 3. Top-level (no owner) — filters out child / dialog windows
        if _u32.GetAncestor(hwnd, _GA_ROOTOWNER) != hwnd:
            return True

        # 4. Skip hidden tool windows — the standard Alt-Tab filter:
        # tool window WITHOUT app window flag = invisible to the taskbar.
        exstyle = _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        if (exstyle & _WS_EX_TOOLWINDOW) and not (exstyle & _WS_EX_APPWINDOW):
            return True

        # 5. Skip shell / Eve / unknown-exe entries
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
        path = _get_process_exe_path(pid_buf.value)
        exe  = Path(path).name.lower() if path else ''
        if not exe or exe in _SKIP_EXES:
            return True

        r = ctypes.wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append({
            'hwnd':      hwnd,
            'title':     title,
            'exe':       exe,
            'path':      path,
            'pid':       pid_buf.value,
            'visible':   bool(_u32.IsWindowVisible(hwnd)),
            'minimized': bool(_u32.IsIconic(hwnd)),
            'x': r.left, 'y': r.top,
            'w': r.right - r.left, 'h': r.bottom - r.top,
        })
        return True

    _u32.EnumWindows(_WNDENUMPROC(_cb), 0)
    out.sort(key=lambda p: ((p['exe'] or '').lower(), p['title'].lower()))
    return out


def find_window_by_spoken_name(spoken: str) -> dict | None:
    """Best-effort match against currently open windows by exe basename or title."""
    spoken = (spoken or '').strip().lower()
    if not spoken:
        return None
    best = None
    for w in enumerate_windows():
        exe_base = (w['exe'] or '').replace('.exe', '').lower()
        title    = w['title'].lower()
        score    = 0
        if spoken == exe_base:                       score = 100
        elif exe_base.startswith(spoken):            score = 92
        elif spoken in exe_base:                     score = 85
        elif spoken == title:                        score = 80
        elif spoken in title:                        score = 65
        elif title.split(' - ')[0].strip() == spoken: score = 70
        if score and (best is None or score > best[0]):
            best = (score, w)
    return best[1] if best else None


def _find_hwnd_for_exe(target_exe: str) -> int | None:
    result  = []
    pid_buf = ctypes.wintypes.DWORD()

    def _cb(hwnd, _):
        if not _u32.IsWindowVisible(hwnd):
            return True
        if _u32.GetWindowTextLengthW(hwnd) == 0:
            return True
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
        if _get_process_exe(pid_buf.value) == target_exe:
            result.append(hwnd)
            return False  # stop at first titled, visible window for this exe
        return True

    _u32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return result[0] if result else None


def _snap_hwnd_to_rect(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    # Un-maximise first — SetWindowPos is silently ignored on maximised windows
    wp = _WINDOWPLACEMENT()
    wp.cbSize = ctypes.sizeof(_WINDOWPLACEMENT)
    _u32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    if wp.showCmd == 3:  # SW_SHOWMAXIMIZED
        _u32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE — restore without stealing focus
    # Compensate for DWM invisible borders (~7px on most Win10/11 apps) so
    # the visible window fills the zone exactly instead of being inset.
    from core.monitor import get_dwm_margins
    ml, mt, mr, mb = get_dwm_margins(hwnd)
    ok = bool(_u32.SetWindowPos(
        hwnd, None, x - ml, y - mt, w + ml + mr, h + mt + mb, _SWP_NOACTIVATE,
    ))
    if ok:
        from core.window_ops import raise_to_top_no_focus
        raise_to_top_no_focus(hwnd)
    return ok


# Every saved monitor implicitly exposes a 'full' zone covering its entire
# work area, regardless of the tiling preset chosen in the Window Manager.
# Lets "snap firefox to monitor 2 full" / "to primary" work even when that
# monitor's saved layout is e.g. top-bottom.
_FULL_ZONE = {'name': 'full', 'x_pct': 0.0, 'y_pct': 0.0, 'w_pct': 1.0, 'h_pct': 1.0}


def _iter_zones(mon: dict):
    """Yield a monitor's saved zones, plus an implicit `full` zone if the
    saved layout doesn't already define one."""
    saw_full = False
    for z in mon.get('zones', []):
        yield z
        if z.get('name', '').lower() == 'full':
            saw_full = True
    if not saw_full:
        yield _FULL_ZONE


def _resolve_zone(zone_name: str, hwnd: int | None = None, monitor_id: str | None = None):
    """Return (monitor_dict, zone_dict) or (None, None).

    Resolution order (when no explicit monitor):
      1. Same monitor as *hwnd*'s current window (when given).
      2. Primary monitor (via Win32 `is_primary` flag).
      3. First saved monitor with a matching zone.

    When *monitor_id* is given, only that monitor's zones are searched —
    no fallback. Returns (None, None) if that monitor lacks the zone.
    """
    layouts  = _load_layouts()
    monitors = layouts.get('monitors', {})
    if not monitors:
        return None, None

    # Explicit-monitor path — strict lookup, no fallback
    if monitor_id is not None:
        mon = monitors.get(monitor_id)
        if mon is None:
            return None, None
        for zone in _iter_zones(mon):
            if zone['name'].lower() == zone_name:
                return mon, zone
        return None, None

    # Gather every (mon, zone) candidate
    win_cx, win_cy = None, None
    if hwnd is not None:
        r = ctypes.wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        win_cx = (r.left + r.right)  // 2
        win_cy = (r.top  + r.bottom) // 2

    candidates = []   # [(mon, zone, same_as_window)]
    for mon in monitors.values():
        wx, wy = mon['workX'], mon['workY']
        ww, wh = mon['workWidth'], mon['workHeight']
        same_mon = (
            win_cx is not None
            and wx <= win_cx < wx + ww
            and wy <= win_cy < wy + wh
        )
        for zone in _iter_zones(mon):
            if zone['name'].lower() == zone_name:
                candidates.append((mon, zone, same_mon))

    if not candidates:
        return None, None

    # 1. Window's current monitor
    for mon, zone, same in candidates:
        if same:
            return mon, zone

    # 2. Primary monitor (via Win32 is_primary flag, matched by work-area)
    from core.monitor import enumerate_work_areas
    primary_wa = next((w for w in enumerate_work_areas() if w.get('is_primary')), None)
    if primary_wa is not None:
        for mon, zone, _ in candidates:
            wa = _match_win32_workarea(mon)
            if wa is not None and wa['x'] == primary_wa['x'] and wa['y'] == primary_wa['y']:
                return mon, zone

    # 3. First match (deterministic by dict insertion order)
    mon, zone, _ = candidates[0]
    return mon, zone


def _resolve_monitor_by_ref(monitor_ref: str | None, monitors: dict):
    """Map a normalized monitor reference to a saved entry.

    monitor_ref accepts:
      - 'primary'             — Windows primary monitor
      - '1'..'N'              — Win32 enumeration index (matches Identify
                                Monitors overlay)
      - 'left' / 'middle' /
        'right'               — positional, by x coordinate (with y as
                                tie-breaker for vertical stacks)

    Returns (monitor_id, monitor_dict) or (None, None).
    """
    from core.monitor import enumerate_work_areas
    if not monitor_ref or not monitors:
        return None, None
    win32 = enumerate_work_areas()    # ordered by hMon, includes is_primary

    matched = []   # [(idx_in_win32_order, is_primary, x, y, mid, mon)]
    for mid, mon in monitors.items():
        wa = _match_win32_workarea(mon)
        if wa is None:
            continue
        try:
            i = next(k for k, w in enumerate(win32)
                     if w['x'] == wa['x'] and w['y'] == wa['y']) + 1
        except StopIteration:
            i = None
        matched.append((i, bool(wa.get('is_primary')), wa['x'], wa['y'], mid, mon))

    if monitor_ref == 'primary':
        for _, is_primary, _, _, mid, mon in matched:
            if is_primary:
                return mid, mon
        return None, None

    if monitor_ref in ('left', 'middle', 'right'):
        if not matched:
            return None, None
        # Sort by x (with y as tie-breaker for vertical stacks).
        by_x = sorted(matched, key=lambda m: (m[2], m[3]))
        if monitor_ref == 'left':
            _, _, _, _, mid, mon = by_x[0]
        elif monitor_ref == 'right':
            _, _, _, _, mid, mon = by_x[-1]
        else:  # middle — median (rounds toward right for even counts)
            _, _, _, _, mid, mon = by_x[len(by_x) // 2]
        return mid, mon

    try:
        n = int(monitor_ref)
        for i, _, _, _, mid, mon in matched:
            if i == n:
                return mid, mon
    except (TypeError, ValueError):
        pass
    return None, None


def positional_to_index(positional: str) -> str | None:
    """Convert 'left'/'middle'/'right' to a 1-based Win32-enumeration index
    string. Used by paths (e.g. move_hud) that need to hand a numeric ref
    over to Electron, which only understands 'primary' or '1'..'N'."""
    from core.monitor import enumerate_work_areas
    win32 = enumerate_work_areas()
    if not win32:
        return None
    # Preserve original indices while sorting by (x, y).
    indexed = sorted(enumerate(win32), key=lambda iw: (iw[1]['x'], iw[1]['y']))
    if positional == 'left':
        i, _ = indexed[0]
    elif positional == 'right':
        i, _ = indexed[-1]
    elif positional in ('middle', 'center'):
        i, _ = indexed[len(indexed) // 2]
    else:
        return None
    return str(i + 1)


def _match_win32_workarea(mon: dict):
    """Find the live Win32 monitor that best matches an Electron-saved entry.

    Strategy: compute the saved monitor's *approximate* physical size from
    `workWidth * scaleFactor` and `workHeight * scaleFactor`, then pick the
    Win32 monitor with the closest physical work-area size. Aspect ratio is
    invariant under DPI scaling, so this match is stable even when Electron
    DIPs and Win32 physicals disagree.
    """
    from core.monitor import enumerate_work_areas
    sf = mon.get('scaleFactor') or 1.0
    est_w = mon['workWidth']  * sf
    est_h = mon['workHeight'] * sf
    best, best_score = None, None
    for wa in enumerate_work_areas():
        score = abs(wa['w'] - est_w) + abs(wa['h'] - est_h)
        if best is None or score < best_score:
            best, best_score = wa, score
    # Tolerance: within ~10% of expected dimensions
    if best is None: return None
    if best_score < max(est_w, est_h) * 0.10:
        return best
    return None


def _zone_pixel_rect(mon: dict, zone: dict, physical: bool = False) -> tuple[int, int, int, int]:
    """Compute the screen rect for a zone.

    Electron saves bounds in DIPs (device-independent pixels). In a per-monitor
    DPI-aware Windows process, Win32 SetWindowPos uses *physical pixels* in a
    desktop coordinate space where each monitor occupies its own native pixel
    rectangle. With mixed DPI between monitors (e.g. primary at 125%, portrait
    at 100%), Electron-DIP coordinates and Win32-physical coordinates do NOT
    agree even after scaling — preceding monitors at different scales shift
    everything.

    Resolution order for physical mode:
      1. Use `physX/physY/physWidth/physHeight` saved by Electron's
         `screen.dipToScreenRect` (most accurate).
      2. Match the saved monitor to a live Win32 monitor and use its current
         physical work-area (handles older saves that pre-date physX, and
         survives the user re-arranging monitors).
      3. Fall back to `DIP × scaleFactor` (correct only when every monitor
         shares the same DPI).
    """
    if physical and all(k in mon for k in ('physX', 'physY', 'physWidth', 'physHeight')):
        wx, wy = mon['physX'], mon['physY']
        ww, wh = mon['physWidth'], mon['physHeight']
    elif physical:
        wa = _match_win32_workarea(mon)
        if wa is not None:
            wx, wy, ww, wh = wa['x'], wa['y'], wa['w'], wa['h']
        else:
            sf = mon.get('scaleFactor') or 1.0
            wx, wy = mon['workX'] * sf, mon['workY'] * sf
            ww, wh = mon['workWidth'] * sf, mon['workHeight'] * sf
    else:
        wx, wy = mon['workX'], mon['workY']
        ww, wh = mon['workWidth'], mon['workHeight']

    x = wx + zone['x_pct'] * ww
    y = wy + zone['y_pct'] * wh
    w = zone['w_pct'] * ww
    h = zone['h_pct'] * wh
    return round(x), round(y), round(w), round(h)


def _resolve_panel(app_name: str) -> str | None:
    """Substring match the heard text against panel aliases. Longest alias
    wins so 'routing directory' beats 'directory'."""
    for alias in sorted(_PANEL_ALIASES.keys(), key=len, reverse=True):
        if alias in app_name:
            return _PANEL_ALIASES[alias]
    return None


def _snap_panel(panel_id: str, app_name: str, zone_name: str,
                monitor_ref: str | None = None) -> str:
    # Resolve an explicit monitor qualifier ("monitor 2" / "primary" / "left")
    # so "snap hud to top-left of monitor 2" targets that specific display,
    # instead of falling back to primary/first-match like the bare form.
    monitor_id = None
    if monitor_ref:
        from commands.window_manager import _resolve_monitor_ref
        norm = _resolve_monitor_ref(monitor_ref)
        monitors = _load_layouts().get('monitors', {})
        monitor_id, _ = _resolve_monitor_by_ref(norm, monitors)
        if monitor_id is None:
            return f"I couldn't find {monitor_ref!r}."

    mon, zone = _resolve_zone(zone_name, hwnd=None, monitor_id=monitor_id)
    if zone is None:
        where = f" on {monitor_ref}" if monitor_ref else ""
        return f"No zone named '{zone_name}'{where} in any saved layout."
    # Electron's setBounds uses DIPs, not physical pixels.
    x, y, w, h = _zone_pixel_rect(mon, zone, physical=False)
    if _display is None:
        return f"Display not ready — can't snap {app_name}."
    _display.snap_panel(panel_id, x, y, w, h)
    where = f" on {monitor_ref}" if monitor_ref else ""
    return f"Snapped {app_name} to {zone_name}{where}"


def snap_app(app_name: str, zone_name: str, monitor_ref: str | None = None) -> str:
    """Snap a window to a saved zone.

    *monitor_ref*: optional spoken qualifier like "monitor 2" / "display two" /
    "primary" — when present, the zone lookup is restricted to that specific
    monitor (no fallback). Without it, the zone resolver prefers the window's
    current monitor → primary → first match. See _resolve_zone for details.
    """
    app_name  = app_name.strip().lower()
    zone_name = zone_name.strip().lower()

    # 1. Eve UI panel? (routing directory, window manager, app manager, voice settings)
    panel_id = _resolve_panel(app_name)
    if panel_id is not None:
        return _snap_panel(panel_id, app_name, zone_name, monitor_ref)

    # 2. Resolve explicit monitor qualifier ("monitor two", "primary", etc.)
    monitor_id = None
    if monitor_ref:
        from commands.window_manager import _resolve_monitor_ref
        norm = _resolve_monitor_ref(monitor_ref)        # '2', 'primary', or None
        monitors = _load_layouts().get('monitors', {})
        monitor_id, mon_dict = _resolve_monitor_by_ref(norm, monitors)
        if monitor_id is None:
            return f"I couldn't find {monitor_ref!r}."

    # 3. Real app — resolve spoken name → exe basename via apps.json
    apps_map = _load_apps()
    exe = apps_map.get(app_name)
    if exe is None:
        for spoken, candidate in apps_map.items():
            if app_name in spoken or spoken in app_name:
                exe = candidate
                app_name = spoken      # use the canonical spoken name for replies
                break

    # 4. Resolve target zone (no preferred window yet — explicit monitor wins
    # over window-monitor; bare snap will be re-resolved below once we have hwnd)
    mon, zone = _resolve_zone(zone_name, hwnd=None, monitor_id=monitor_id)
    if zone is None:
        if monitor_id is not None:
            return f"That monitor doesn't have a '{zone_name}' zone."
        return f"No zone named '{zone_name}' in any saved layout."

    # 5. Find the existing window — prefer apps.json exe match, fall back to
    # scanning ALL open windows by title/exe.
    hwnd = _find_hwnd_for_exe(exe) if exe else None
    if hwnd is None:
        match = find_window_by_spoken_name(app_name)
        if match:
            hwnd = match['hwnd']
            app_name = (match['exe'] or '').replace('.exe', '').lower() or match['title']

    if hwnd is None:
        # No open window. Launch only if we know the exe (apps.json had it).
        if exe is None:
            return (f"I don't see {app_name} open and it's not in the app manager. "
                    f"Open it first, or add it in the app manager to launch by voice.")
        from commands import apps as apps_cmd
        rect = _zone_pixel_rect(mon, zone, physical=True)
        apps_cmd.open_app(app_name, snap_rect=rect)
        return f"Opening {app_name} and snapping to {_describe_target(zone_name, monitor_ref)}"

    # 6. Existing window — re-resolve zone preferring same monitor, UNLESS the
    # user specified an explicit monitor (which we never override).
    if monitor_id is None:
        mon, zone = _resolve_zone(zone_name, hwnd=hwnd)
        if zone is None:
            return f"No zone named '{zone_name}' in any saved layout."

    # Capture the window's CURRENT rect BEFORE snapping, so 'go back' / 'undo'
    # can restore it. GetWindowRect returns Win32-physical coordinates which
    # _snap_hwnd_to_rect also speaks in — round-trip is exact.
    prev_r = ctypes.wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(prev_r))
    prev_x, prev_y = prev_r.left, prev_r.top
    prev_w = prev_r.right  - prev_r.left
    prev_h = prev_r.bottom - prev_r.top

    x, y, w, h = _zone_pixel_rect(mon, zone, physical=True)
    ok = _snap_hwnd_to_rect(hwnd, x, y, w, h)
    if not ok:
        return f"Couldn't move {app_name} — it may be protected"

    # Register for "go back" / "close that window".
    target_desc = _describe_target(zone_name, monitor_ref)
    from core import session as _sess_mod
    captured_hwnd = hwnd
    _sess_mod.set_last_action(_sess_mod.LastAction(
        description=f"snap {app_name} to {target_desc}",
        target_hwnd=captured_hwnd,
        undo=lambda: _snap_hwnd_to_rect(captured_hwnd, prev_x, prev_y, prev_w, prev_h),
    ))

    return f"Snapped {app_name} to {target_desc}"


def _describe_target(zone_name: str, monitor_ref: str | None) -> str:
    """Human-readable target for spoken confirmation.

    Examples:
        zone='full', monitor='monitor 1' -> 'monitor 1'        (omit 'full')
        zone='top',  monitor='monitor 2' -> 'top of monitor 2'
        zone='top',  monitor='primary'   -> 'top of primary'
        zone='top',  monitor=None        -> 'top'
        zone='full', monitor=None        -> 'full screen'
    """
    mon_label = ''
    if monitor_ref:
        from commands.window_manager import _resolve_monitor_ref
        norm = _resolve_monitor_ref(monitor_ref)
        if norm == 'primary':
            mon_label = 'primary'
        elif norm in ('left', 'middle', 'right'):
            mon_label = f'the {norm} monitor'
        elif norm:
            mon_label = f'monitor {norm}'
    if mon_label and zone_name == 'full':
        return mon_label
    if mon_label:
        return f'{zone_name} of {mon_label}'
    if zone_name == 'full':
        return 'full screen'
    return zone_name


# ── Workspace presets — save/restore all window positions by name ──────────
# Stored in tiling_layouts.json under "workspaces": {name: [ {exe,title,x,y,w,h}, ... ]}

def _save_layouts(data: dict) -> None:
    _LAYOUTS_FILE.write_text(json.dumps(data, indent=2))


def save_workspace(name: str) -> str:
    """Voice: 'save layout as work'. Snapshots every open window's position."""
    name = (name or '').strip().lower()
    if not name:
        return "What should I name this layout?"
    wins = [
        {'exe': w['exe'], 'title': w['title'],
         'x': w['x'], 'y': w['y'], 'w': w['w'], 'h': w['h']}
        for w in enumerate_windows()
    ]
    if not wins:
        return "No windows to save."
    data = _load_layouts()
    data.setdefault('workspaces', {})[name] = wins
    _save_layouts(data)
    return f"Saved {len(wins)} windows as '{name}'."


def restore_workspace(name: str) -> str:
    """Voice: 'restore work layout'. Greedily matches each saved window to an
    open one (by exe, then closest title) and moves it back."""
    name = (name or '').strip().lower()
    saved = _load_layouts().get('workspaces', {}).get(name)
    if not saved:
        return f"No layout named '{name}'."

    open_wins = enumerate_windows()
    used = set()
    moved = 0
    for entry in saved:
        match = _best_open_window(entry, open_wins, used)
        if match is None:
            continue
        used.add(match['hwnd'])
        if _snap_hwnd_to_rect(match['hwnd'], entry['x'], entry['y'],
                              entry['w'], entry['h']):
            moved += 1
    if moved == 0:
        return f"Restored '{name}' but none of those windows are open."
    return f"Restored '{name}' — moved {moved} window{'s' if moved != 1 else ''}."


def _best_open_window(entry: dict, open_wins: list, used: set) -> dict | None:
    """Pick the unused open window that best matches a saved entry: same exe
    first, then the closest title; falls back to title-only match."""
    exe   = (entry.get('exe') or '').lower()
    title = (entry.get('title') or '').lower()
    best  = None
    for w in open_wins:
        if w['hwnd'] in used:
            continue
        score = 0
        if exe and w['exe'].lower() == exe:
            score += 50
            if w['title'].lower() == title:
                score += 50
            elif title and (title in w['title'].lower() or w['title'].lower() in title):
                score += 25
        elif title and w['title'].lower() == title:
            score += 40
        if score and (best is None or score > best[0]):
            best = (score, w)
    return best[1] if best else None


def list_workspaces() -> str:
    """Voice: 'what layouts do I have'."""
    names = list(_load_layouts().get('workspaces', {}).keys())
    if not names:
        return "You haven't saved any layouts yet."
    return "Saved layouts: " + ", ".join(names) + "."


# ── Auto-snap on launch — per-app zone assignment ──────────────────────────
# Stored in tiling_layouts.json under "app_zones": {appname: {zone, monitor}}.
# apps.open_app() consults zone_rect_for_app() when launched with no explicit
# snap, so a saved app lands in its zone instead of centered on a monitor.

def set_app_zone(app_name: str, zone_name: str, monitor_ref: str | None = None) -> str:
    """Voice: 'always open firefox in top-left' / 'auto-snap discord to right'."""
    app_name  = (app_name or '').strip().lower()
    zone_name = (zone_name or '').strip().lower()
    if not app_name or not zone_name:
        return "Tell me an app and a zone, like 'always open firefox in top-left'."
    # Validate the zone resolves now so we don't save a dead assignment.
    monitor_id = _resolve_monitor_id(monitor_ref)
    if monitor_ref and monitor_id is None:
        return f"I couldn't find {monitor_ref!r}."
    mon, zone = _resolve_zone(zone_name, hwnd=None, monitor_id=monitor_id)
    if zone is None:
        return f"No zone named '{zone_name}' in any saved layout."
    data = _load_layouts()
    entry = {'zone': zone_name}
    if monitor_ref:
        entry['monitor'] = monitor_ref
    data.setdefault('app_zones', {})[app_name] = entry
    _save_layouts(data)
    where = f" on {monitor_ref}" if monitor_ref else ""
    return f"I'll open {app_name} in the {zone_name} zone{where} from now on."


def clear_app_zone(app_name: str) -> str:
    """Voice: 'stop auto-snapping firefox' / 'forget firefox's zone'."""
    app_name = (app_name or '').strip().lower()
    data = _load_layouts()
    zones = data.get('app_zones', {})
    if app_name not in zones:
        return f"{app_name} doesn't have a saved zone."
    del zones[app_name]
    _save_layouts(data)
    return f"Stopped auto-snapping {app_name}."


def _resolve_monitor_id(monitor_ref: str | None) -> str | None:
    """Spoken monitor ref → saved monitor id, or None."""
    if not monitor_ref:
        return None
    from commands.window_manager import _resolve_monitor_ref
    norm = _resolve_monitor_ref(monitor_ref)
    monitors = _load_layouts().get('monitors', {})
    mid, _ = _resolve_monitor_by_ref(norm, monitors)
    return mid


def zone_rect_for_app(app_name: str) -> tuple | None:
    """Physical-pixel (x, y, w, h) for an app's saved zone, or None if unset /
    unresolvable. Called by apps.open_app() to auto-snap on launch."""
    entry = _load_layouts().get('app_zones', {}).get((app_name or '').strip().lower())
    if not entry:
        return None
    monitor_id = _resolve_monitor_id(entry.get('monitor'))
    mon, zone = _resolve_zone(entry['zone'], hwnd=None, monitor_id=monitor_id)
    if zone is None:
        return None
    return _zone_pixel_rect(mon, zone, physical=True)


if __name__ == '__main__':
    # ponytail: matching logic self-check — no Win32 calls
    opens = [
        {'hwnd': 1, 'exe': 'chrome.exe', 'title': 'GitHub - Chrome'},
        {'hwnd': 2, 'exe': 'chrome.exe', 'title': 'Gmail - Chrome'},
        {'hwnd': 3, 'exe': 'code.exe',   'title': 'tiling.py - VS Code'},
    ]
    used: set = set()
    m = _best_open_window({'exe': 'chrome.exe', 'title': 'Gmail - Chrome'}, opens, used)
    assert m['hwnd'] == 2, m                      # exact title wins over sibling
    used.add(2)
    m = _best_open_window({'exe': 'chrome.exe', 'title': 'GitHub - Chrome'}, opens, used)
    assert m['hwnd'] == 1, m                      # used one is skipped
    m = _best_open_window({'exe': 'gone.exe', 'title': 'nope'}, opens, used)
    assert m is None, m                           # no match → None, not a wrong window
    print('ok')
