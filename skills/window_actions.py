"""Window quick-actions — voice control over the window you're working in and
the desktop as a whole. A drop-in skill (no core edits).

Try:
  "minimize this" / "minimize this window"     → minimize the focused window
  "maximize this" / "maximize this window"     → maximize it
  "restore this"  / "unmaximize this"          → back to normal size
  "always on top" / "pin this on top"          → toggle keep-above-others
  "unpin this"    / "stop always on top"       → clear keep-above
  "close this window" / "close this"           → politely close it (WM_CLOSE)
  "minimize all"  / "minimize everything"      → minimize every window
  "show desktop"                               → show/hide the desktop
  "bring my windows back" / "restore all"      → undo minimize-all

These complement the tiling/snap grammar (which *places* windows) with the
show-state verbs (minimize/maximize/restore/pin/close).

PREEMPT: these run BEFORE the built-in table so specific phrases like "close
this window" aren't swallowed by the generic "close <app>" launcher intent. The
patterns are deliberately narrow (they require "this/current/active window" or
"all/everything/desktop"), so they never shadow a normal command. Pronoun forms
that reference a prior action ("close that window", "close it") are intentionally
left to the core follow-up handler and are NOT matched here.
"""
from core import window_ops as _w
from core import key_ops as _k
from core.response import Verified

PREEMPT = True


# ── helpers ──────────────────────────────────────────────────────────────────

def _target():
    """(hwnd, friendly-name) for the window the user is working in, or (0, '')."""
    hwnd = _w.foreground_hwnd()
    if not _w.exists(hwnd):
        return 0, ''
    title = _w.window_title(hwnd).strip()
    # Keep the spoken name short — window titles are often "Doc — App — extra".
    short = title.split(' — ')[0].split(' - ')[0].strip() if title else ''
    return hwnd, short


def _no_window() -> str:
    return "I don't see an active window to act on."


# ── this-window actions (return Verified so the command check confirms them) ──

def _minimize_this():
    hwnd, name = _target()
    if not hwnd:
        return _no_window()
    _w.minimize(hwnd)
    label = f"Minimized {name}" if name else "Minimized this window"
    return Verified(label, check=lambda: _w.is_minimized(hwnd),
                    on_fail="I couldn't minimize that window.", delay=0.25)


def _maximize_this():
    hwnd, name = _target()
    if not hwnd:
        return _no_window()
    _w.maximize(hwnd)
    label = f"Maximized {name}" if name else "Maximized this window"
    return Verified(label, check=lambda: _w.is_maximized(hwnd),
                    on_fail="I couldn't maximize that window.", delay=0.25)


def _restore_this():
    hwnd, name = _target()
    if not hwnd:
        return _no_window()
    _w.restore(hwnd)
    label = f"Restored {name}" if name else "Restored this window"
    return Verified(label,
                    check=lambda: not _w.is_maximized(hwnd) and not _w.is_minimized(hwnd),
                    on_fail="I couldn't restore that window.", delay=0.25)


def _pin_on_top():
    hwnd, name = _target()
    if not hwnd:
        return _no_window()
    want = not _w.is_topmost(hwnd)          # toggle
    _w.set_topmost(hwnd, want)
    who = name or "this window"
    label = f"{who} is now pinned on top" if want else f"{who} is no longer on top"
    return Verified(label, check=lambda: _w.is_topmost(hwnd) == want,
                    on_fail="I couldn't change the always-on-top setting.", delay=0.2)


def _unpin_on_top():
    hwnd, name = _target()
    if not hwnd:
        return _no_window()
    _w.set_topmost(hwnd, False)
    who = name or "this window"
    return Verified(f"{who} is no longer on top",
                    check=lambda: not _w.is_topmost(hwnd),
                    on_fail="I couldn't unpin that window.", delay=0.2)


def _close_this():
    hwnd, name = _target()
    if not hwnd:
        return _no_window()
    _w.close_window(hwnd)
    who = name or "this window"
    # The app may prompt to save, so closing can legitimately not complete.
    return Verified(f"Closing {who}", check=lambda: not _w.exists(hwnd),
                    on_fail=f"{who} is still open — it may be asking to save.",
                    delay=0.4)


# ── desktop-wide actions (no single hwnd to verify → plain spoken reply) ──────

def _minimize_all():
    _k.press_global('win+m')
    return "Minimized everything."


def _restore_all():
    _k.press_global('win+shift+m')
    return "Brought your windows back."


def _show_desktop():
    _k.press_global('win+d')
    return "Showing the desktop."


# ── intents (narrow on purpose; PREEMPT means they run before built-ins) ──────

_THIS = r"(?:this|the\s+current|current|active)(?:\s+window)?"

INTENTS = [
    # desktop-wide first (distinct wording from the this-window forms)
    (r"\bminimi[sz]e\s+(?:all|everything|all\s+(?:my\s+)?windows)\b",       _minimize_all),
    (r"\b(?:(?:restore|bring\s+back|bring\s+up|un-?minimi[sz]e)\s+(?:all|everything|(?:all\s+)?my\s+windows)"
     r"|bring\s+(?:all\s+|my\s+)?windows\s+back)\b", _restore_all),
    (r"\bshow\s+(?:the\s+)?desktop\b",                                       _show_desktop),

    # this-window state verbs
    (rf"\bminimi[sz]e\s+{_THIS}\b",                                          _minimize_this),
    (rf"\bmaximi[sz]e\s+{_THIS}\b",                                          _maximize_this),
    (rf"\b(?:restore|unmaximi[sz]e|un-?minimi[sz]e)\s+{_THIS}\b",            _restore_this),

    # always-on-top: unpin forms before the pin/toggle form
    (r"\b(?:unpin|stop\s+(?:always\s+on\s+top|pinning|keeping\s+(?:this|it)\s+on\s+top)|"
     r"remove\s+always\s+on\s+top|(?:no\s+longer|not)\s+always\s+on\s+top)\b",  _unpin_on_top),
    (r"\b(?:always\s+on\s+top|keep\s+(?:this|it)\s+on\s+top|"
     r"pin\s+(?:this|it)?\s*(?:window\s+)?(?:on|to)\s+top)\b",                   _pin_on_top),

    # close the focused window (NOT "close that/it" — those are core follow-ups)
    (rf"\bclose\s+{_THIS}\b",                                                _close_this),
]
