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


def _load_apps() -> dict:
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


def snap_app(app_name: str, zone_name: str) -> str:
    app_name  = app_name.strip().lower()
    zone_name = zone_name.strip().lower()

    # Resolve spoken name → exe basename via apps.json
    apps = _load_apps()
    exe  = apps.get(app_name)
    if exe is None:
        for spoken, candidate in apps.items():
            if app_name in spoken or spoken in app_name:
                exe = candidate
                break
    if exe is None:
        return f"I don't know an app called {app_name}. Add it in the app manager first."

    hwnd = _find_hwnd_for_exe(exe)
    if hwnd is None:
        return f"{app_name.title()} isn't open. Open it first, then snap it to {zone_name}."

    layouts  = _load_layouts()
    monitors = layouts.get('monitors', {})
    if not monitors:
        return "No layout saved. Open the window manager and save a layout first."

    # Prefer zone on same monitor as the window; fall back to any matching zone
    r = ctypes.wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(r))
    win_cx = (r.left + r.right)  // 2
    win_cy = (r.top  + r.bottom) // 2

    best_mon, best_zone = None, None
    for mon in monitors.values():
        wx, wy = mon['workX'], mon['workY']
        ww, wh = mon['workWidth'], mon['workHeight']
        same_mon = wx <= win_cx < wx + ww and wy <= win_cy < wy + wh
        for zone in mon.get('zones', []):
            if zone['name'].lower() == zone_name:
                if same_mon:
                    best_mon, best_zone = mon, zone
                    break
                elif best_zone is None:
                    best_mon, best_zone = mon, zone
        if best_zone and same_mon:
            break

    if best_zone is None:
        return f"No zone named '{zone_name}' in any saved layout."

    wx, wy = best_mon['workX'], best_mon['workY']
    ww, wh = best_mon['workWidth'], best_mon['workHeight']
    x = round(wx + best_zone['x_pct'] * ww)
    y = round(wy + best_zone['y_pct'] * wh)
    w = round(best_zone['w_pct'] * ww)
    h = round(best_zone['h_pct'] * wh)

    ok = _snap_hwnd_to_rect(hwnd, x, y, w, h)
    return f"Snapped {app_name} to {zone_name}" if ok else f"Couldn't move {app_name} — it may be protected"
