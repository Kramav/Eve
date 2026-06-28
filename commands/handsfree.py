"""Hands-free mouse control.

Enter with "hands free mode" / "mouse mode"; Eve then drives the cursor by
voice — nudge it over a Firefox link (or anything) and "click". Leaves with
"exit hands free mode" / "normal mode". While active, the dispatcher hands
every utterance to handle() FIRST (see core.dispatcher); anything this module
declines (returns None) falls through to normal routing, so "open firefox" /
"scroll down" etc. still work without leaving the mode.

Movement is relative nudging, not a labelled grid overlay — universal and
needs no browser extension or UI automation. ponytail: a numbered grid overlay
(Windows Voice-Access style) is the upgrade path if nudging proves fiddly for
dense link lists; add it when that's actually felt, not before.

The parse step is split from the pyautogui apply so it's testable without a
mouse — see _parse() + the __main__ self-check.
"""
import re

import pyautogui

from core import session as _sess
from core.session import Mode

# Nudge distances in pixels. "a little"/"a bit" → small, "a lot"/"far" → large.
_STEP = 120
_SMALL = 45
_LARGE = 320
_SCROLL = 400          # pyautogui scroll "clicks"; sign set per direction

_DIRS = {
    "up":    (0, -1), "down":  (0, 1),
    "left":  (-1, 0), "right": (1, 0),
}


def _distance(text: str, explicit: str | None) -> int:
    if explicit:
        return int(explicit)
    if re.search(r"\b(?:a\s+)?(?:little|bit|tiny|small|nudge|smidge)\b", text):
        return _SMALL
    if re.search(r"\b(?:a\s+)?(?:lot|far|lots|big|large|way)\b", text):
        return _LARGE
    return _STEP


def _parse(text: str):
    """Map an utterance to an action tuple, or None to decline.

    Returns one of:
      ("move",  dx, dy)        relative cursor move in pixels
      ("click", button, n)     n-click of 'left'|'right'|'middle'
      ("scroll", amount)       +up / -down wheel
      ("exit",)                leave hands-free mode
    """
    t = text.strip().lower()

    if re.search(r"\b(?:exit|leave|stop|end|quit)\s+(?:hands?[-\s]?free|mouse)\b"
                 r"|\bnormal\s+mode\b|\bhands?\s+on\b", t):
        return ("exit",)

    # Clicks — check before movement ("double click" has no direction anyway).
    if re.search(r"\bdouble[-\s]?click\b", t):
        return ("click", "left", 2)
    if re.search(r"\b(?:right[-\s]?click|right\s+mouse)\b", t):
        return ("click", "right", 1)
    if re.search(r"\b(?:middle[-\s]?click)\b", t):
        return ("click", "middle", 1)
    if re.search(r"\b(?:left[-\s]?click|click(?:\s+(?:it|here|there|that|the\s+link))?|"
                 r"select|press|tap)\b", t):
        return ("click", "left", 1)

    if re.search(r"\bscroll\s+up\b|\bpage\s+up\b", t):
        return ("scroll", _SCROLL)
    if re.search(r"\bscroll(?:\s+down)?\b|\bpage\s+down\b", t):
        return ("scroll", -_SCROLL)

    # Movement: "move right", "left a little", "go down 200", "up 50"
    m = re.search(r"(?:move|go|nudge|slide|cursor|mouse)?\s*"
                  r"\b(up|down|left|right)\b(?:\s+(?:by\s+)?(\d+))?", t)
    if m:
        ux, uy = _DIRS[m.group(1)]
        d = _distance(t, m.group(2))
        return ("move", ux * d, uy * d)

    return None


def _apply(action) -> str:
    kind = action[0]
    if kind == "move":
        _, dx, dy = action
        pyautogui.moveRel(dx, dy, duration=0.12)
        return f"Moved {'right' if dx > 0 else 'left' if dx < 0 else ''}" \
               f"{'down' if dy > 0 else 'up' if dy < 0 else ''}".strip() or "Moved"
    if kind == "click":
        _, button, n = action
        pyautogui.click(button=button, clicks=n, interval=0.08)
        label = {"left": "Clicked", "right": "Right-clicked", "middle": "Middle-clicked"}[button]
        return "Double-clicked" if n == 2 else label
    if kind == "scroll":
        pyautogui.scroll(action[1])
        return "Scrolled"
    return "Okay"


def enter() -> str:
    _sess.get().mode = Mode.HANDSFREE
    return ("Hands-free mode on. Say move up, down, left, or right to aim, "
            "then click. Say exit hands-free mode when you're done.")


def _exit() -> str:
    if _sess.get().mode == Mode.HANDSFREE:
        _sess.get().mode = Mode.IDLE
    return "Hands-free mode off."


def handle(text: str):
    """Mode handler: drive the mouse, or return None to let normal dispatch run."""
    action = _parse(text)
    if action is None:
        return None
    if action[0] == "exit":
        return _exit()
    return _apply(action)


if __name__ == "__main__":
    # Self-check: parsing only, no real mouse movement.
    assert _parse("move right") == ("move", _STEP, 0)
    assert _parse("left a little") == ("move", -_SMALL, 0)
    assert _parse("go down a lot") == ("move", 0, _LARGE)
    assert _parse("up 50") == ("move", 0, -50)
    assert _parse("click") == ("click", "left", 1)
    assert _parse("click the link") == ("click", "left", 1)
    assert _parse("double click") == ("click", "left", 2)
    assert _parse("right click") == ("click", "right", 1)
    assert _parse("scroll down") == ("scroll", -_SCROLL)
    assert _parse("scroll up") == ("scroll", _SCROLL)
    assert _parse("exit hands free mode") == ("exit",)
    assert _parse("normal mode") == ("exit",)
    # Unrelated utterances decline so normal dispatch still runs.
    assert _parse("open firefox") is None
    assert _parse("what time is it") is None
    print("handsfree self-check passed")
