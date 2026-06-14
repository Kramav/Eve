import ctypes
import ctypes.wintypes
import json
from pathlib import Path

_LAYOUTS_FILE = Path(__file__).parent.parent / "tiling_layouts.json"
_APPS_FILE    = Path(__file__).parent.parent / "apps.json"

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

_SWP_NOACTIVATE = 0x0010
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
    handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ''
    try:
        buf  = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
    finally:
        _k32.CloseHandle(handle)
    return ''


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
    return bool(_u32.SetWindowPos(hwnd, None, x, y, w, h, _SWP_NOACTIVATE))


def _resolve_zone(zone_name: str, hwnd: int | None = None):
    """Return (monitor_dict, zone_dict) or (None, None).

    When *hwnd* is given, prefer a zone on the same monitor as the window;
    otherwise pick the first matching zone in any monitor."""
    layouts  = _load_layouts()
    monitors = layouts.get('monitors', {})
    if not monitors:
        return None, None

    win_cx, win_cy = None, None
    if hwnd is not None:
        r = ctypes.wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        win_cx = (r.left + r.right)  // 2
        win_cy = (r.top  + r.bottom) // 2

    best_mon, best_zone = None, None
    for mon in monitors.values():
        wx, wy = mon['workX'], mon['workY']
        ww, wh = mon['workWidth'], mon['workHeight']
        same_mon = (
            win_cx is not None
            and wx <= win_cx < wx + ww
            and wy <= win_cy < wy + wh
        )
        for zone in mon.get('zones', []):
            if zone['name'].lower() != zone_name:
                continue
            if same_mon:
                return mon, zone
            if best_zone is None:
                best_mon, best_zone = mon, zone
    return best_mon, best_zone


def _zone_pixel_rect(mon: dict, zone: dict, physical: bool = False) -> tuple[int, int, int, int]:
    """Compute the screen rect for a zone.

    Electron saves bounds in DIPs (device-independent pixels — 1 DIP == 1 pixel
    only when the monitor is at 100% scale).  Win32 SetWindowPos in a DPI-aware
    process uses physical pixels; Electron's setBounds uses DIPs.  Pass
    physical=True for Win32 (real apps), False for Electron panels.
    """
    wx, wy = mon['workX'], mon['workY']
    ww, wh = mon['workWidth'], mon['workHeight']
    x = wx + zone['x_pct'] * ww
    y = wy + zone['y_pct'] * wh
    w = zone['w_pct'] * ww
    h = zone['h_pct'] * wh
    if physical:
        sf = mon.get('scaleFactor') or 1.0
        x *= sf; y *= sf; w *= sf; h *= sf
    return round(x), round(y), round(w), round(h)


def _resolve_panel(app_name: str) -> str | None:
    """Substring match the heard text against panel aliases. Longest alias
    wins so 'routing directory' beats 'directory'."""
    for alias in sorted(_PANEL_ALIASES.keys(), key=len, reverse=True):
        if alias in app_name:
            return _PANEL_ALIASES[alias]
    return None


def _snap_panel(panel_id: str, app_name: str, zone_name: str) -> str:
    mon, zone = _resolve_zone(zone_name, hwnd=None)
    if zone is None:
        return f"No zone named '{zone_name}' in any saved layout."
    # Electron's setBounds uses DIPs, not physical pixels.
    x, y, w, h = _zone_pixel_rect(mon, zone, physical=False)
    if _display is None:
        return f"Display not ready — can't snap {app_name}."
    _display.snap_panel(panel_id, x, y, w, h)
    return f"Snapped {app_name} to {zone_name}"


def snap_app(app_name: str, zone_name: str) -> str:
    app_name  = app_name.strip().lower()
    zone_name = zone_name.strip().lower()

    # 1. Eve UI panel? (routing directory, window manager, app manager, voice settings)
    panel_id = _resolve_panel(app_name)
    if panel_id is not None:
        return _snap_panel(panel_id, app_name, zone_name)

    # 2. Real app — resolve spoken name → exe basename via apps.json
    apps_map = _load_apps()
    exe = apps_map.get(app_name)
    if exe is None:
        for spoken, candidate in apps_map.items():
            if app_name in spoken or spoken in app_name:
                exe = candidate
                app_name = spoken      # use the canonical spoken name for replies
                break
    if exe is None:
        return f"I don't know an app called {app_name}. Add it in the app manager first."

    # 3. Resolve target zone (no preferred monitor yet — window may not exist)
    mon, zone = _resolve_zone(zone_name, hwnd=None)
    if zone is None:
        return f"No zone named '{zone_name}' in any saved layout."

    hwnd = _find_hwnd_for_exe(exe)
    if hwnd is None:
        # Open + snap: launch the app and let monitor.move_new_window_to_rect
        # snap the new window once it appears. Win32 wants physical pixels.
        from commands import apps as apps_cmd
        rect = _zone_pixel_rect(mon, zone, physical=True)
        apps_cmd.open_app(app_name, snap_rect=rect)
        return f"Opening {app_name} and snapping to {zone_name}"

    # Existing window — re-resolve zone preferring same monitor
    mon, zone = _resolve_zone(zone_name, hwnd=hwnd)
    if zone is None:
        return f"No zone named '{zone_name}' in any saved layout."

    x, y, w, h = _zone_pixel_rect(mon, zone, physical=True)
    ok = _snap_hwnd_to_rect(hwnd, x, y, w, h)
    return f"Snapped {app_name} to {zone_name}" if ok else f"Couldn't move {app_name} — it may be protected"
