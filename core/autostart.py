"""Run Eve automatically at Windows login via the per-user Run registry key
(HKCU\\…\\CurrentVersion\\Run). No admin rights, no Task Scheduler, and trivially
reversible — the user can also just delete the 'Eve' value in regedit.

The registered command launches this same checkout with the same interpreter:
    "<pythonw.exe>" "<repo>/main.py"
pythonw is preferred so no console window flashes; falls back to the current
interpreter if pythonw isn't alongside it.
"""
import sys
import winreg
from pathlib import Path

_RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE    = "Eve"
_MAIN_PY  = Path(__file__).parent.parent / "main.py"


def _launch_command() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    return f'"{runner}" "{_MAIN_PY}"'


def enable() -> str:
    cmd = _launch_command()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        winreg.SetValueEx(key, _VALUE, 0, winreg.REG_SZ, cmd)
    return "Eve will now start automatically when you log in."


def disable() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _VALUE)
        return "Eve will no longer start at login."
    except FileNotFoundError:
        return "Eve wasn't set to start at login."


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE)
        return True
    except FileNotFoundError:
        return False


# Voice handlers ------------------------------------------------------------

def enable_voice() -> str:
    return enable()


def disable_voice() -> str:
    return disable()


def status_voice() -> str:
    return ("Eve is set to start at login." if is_enabled()
            else "Eve is not set to start at login.")


if __name__ == "__main__":
    # ponytail: don't mutate the registry in a smoke test — just prove the
    # command string is well-formed and is_enabled() runs.
    cmd = _launch_command()
    assert cmd.endswith('main.py"') and "python" in cmd.lower(), cmd
    print("launch command:", cmd)
    print("currently enabled:", is_enabled())
    print("ok")
