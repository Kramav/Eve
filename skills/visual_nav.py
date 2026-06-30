"""Visual Navigation — optional hands-free mouse + element navigation skill.

Drop-in skill (loads via core/skills.py, gated on the `visual_nav` feature
flag). Replaces the old in-core hands-free mode. Lets you drive the mouse by
voice AND pick interactive elements (links, videos, list items, tabs) by
number without any site-specific API.

Cheapest-method-first (priority order):
  1. Native accessibility (UI Automation) — real control tree of the focused
     window. AccessibilityProvider.
  2. Browser automation — future provider slot (no automation dep today).
  3. Native input — pyautogui move/click/scroll/type. InputController.
  4. On-demand screenshot → multimodal vision — VisionProvider (STUB for now;
     wire a local Ollama-vision / CV backend behind it later). Never continuous CV.

The NavigationPlanner tries accessibility first, then vision (None today).

Modal capture uses the existing Converse layer (core.session) rather than a
core Mode, so this stays a pure drop-in skill: entering claims upcoming
utterances *before* built-in intents (essential — "open number 2" would
otherwise be eaten by apps.open_app). A clean decline falls through so normal
commands still work while the mode is on.

    "hands free mode" / "mouse mode"      → enter
    "what can I click"                    → list interactive elements
    "open number 2" / "click the third"   → move mouse to that element + click
    "move right" / "click" / "scroll down"→ direct mouse control
    "exit hands free mode"                → leave

Self-check: `python -m skills.visual_nav` (parser + planner against fakes;
no real UIA, no real mouse).
"""
import re

import pyautogui

from core import key_ops

PRIORITY = 0
FEATURE = "visual_nav"          # gated; skill is skipped when the flag is off

_display = None                 # cached Display (for show_list), set by setup()


def setup(display=None):
    global _display
    _display = display


# ── Mouse nudge tuning (carried over from the old hands-free mode) ───────────
_STEP, _SMALL, _LARGE, _SCROLL = 120, 45, 320, 400
_DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_SELECT_VERB = r"(?:open|click|select|choose|pick|press|launch|go\s+to|tap|activate)"
_SELECT_NOUN = r"(?:number|link|item|video|result|option|tab|row|file|game|title|button|one)"


# ── Providers ────────────────────────────────────────────────────────────────

class InputController:
    """Tier 3 — native mouse/keyboard via pyautogui + key_ops. The action layer
    every provider routes through once an element/target is resolved."""

    def move_to(self, x, y):
        pyautogui.moveTo(x, y, duration=0.12)

    def move_rel(self, dx, dy):
        pyautogui.moveRel(dx, dy, duration=0.12)

    def click(self, x=None, y=None, button="left", clicks=1):
        if x is not None:
            pyautogui.click(x, y, button=button, clicks=clicks, interval=0.08)
        else:
            pyautogui.click(button=button, clicks=clicks, interval=0.08)

    def scroll(self, amount):
        pyautogui.scroll(amount)

    def type_text(self, text):
        key_ops.type_text(text)

    def hotkey(self, combo):
        key_ops.press_global(combo)


class AccessibilityProvider:
    """Tier 1 — UI Automation control tree of the FOREGROUND window. Import is
    lazy + fully guarded so a missing `uiautomation` (or any UIA hiccup) just
    yields None and the planner falls back, never crashing the skill.

    ponytail: walking a browser's full tree can be slow — capped by visit/result/
    depth limits below. Bump them only if real use shows links being missed."""

    _INTERACTIVE = {
        "HyperlinkControl", "ButtonControl", "ListItemControl", "TabItemControl",
        "MenuItemControl", "EditControl", "CheckBoxControl", "RadioButtonControl",
        "ComboBoxControl", "TreeItemControl", "SplitButtonControl",
    }
    _MAX_VISIT, _MAX_RESULTS, _MAX_DEPTH = 1500, 60, 14

    def available(self) -> bool:
        try:
            import uiautomation  # noqa: F401
            return True
        except Exception:
            return False

    def elements(self):
        try:
            import ctypes
            import uiautomation as auto

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return None
            root = auto.ControlFromHandle(hwnd)
            if root is None:
                return None

            out, visited = [], 0
            stack = [(root, 0)]
            while stack and visited < self._MAX_VISIT and len(out) < self._MAX_RESULTS:
                ctl, depth = stack.pop()
                visited += 1
                try:
                    if ctl.ControlTypeName in self._INTERACTIVE:
                        name = (ctl.Name or "").strip()
                        rect = ctl.BoundingRectangle
                        if name and rect and rect.width() > 0 and rect.height() > 0:
                            out.append({
                                "label":  name[:80],
                                "type":   ctl.ControlTypeName.replace("Control", ""),
                                "bounds": (rect.left, rect.top, rect.width(), rect.height()),
                                "center": (rect.xcenter(), rect.ycenter()),
                            })
                    if depth < self._MAX_DEPTH:
                        stack.extend((c, depth + 1) for c in ctl.GetChildren())
                except Exception:
                    continue

            # De-dupe, then sort into reading order (top→bottom, left→right).
            seen, uniq = set(), []
            for e in out:
                k = (e["label"], e["center"])
                if k not in seen:
                    seen.add(k)
                    uniq.append(e)
            uniq.sort(key=lambda e: (e["center"][1], e["center"][0]))
            return uniq or None
        except Exception:
            return None


class VisionProvider:
    """Tier 4 (last resort) — on-demand screenshot → backend cascade
    (commands/vision.py: OCR / ONNX / cloud / Ollama). NO continuous CV.

    Caches its result keyed by a perceptual hash of the screen, so repeated
    "what can I click" on an unchanged screen reuses the last detection instead
    of re-running OCR. A scroll changes the hash → automatic re-scan."""

    def __init__(self):
        self._hash = None
        self._cache = None

    def available(self) -> bool:
        from commands import vision
        return bool(vision.available_backends())

    def elements(self):
        from commands import vision
        img = vision.grab()
        if img is None:
            return None
        h = vision.phash(img)
        if h and h == self._hash and self._cache is not None:
            return self._cache
        els = vision.detect(img) or None
        self._hash, self._cache = h, els
        return els


class NavigationPlanner:
    """Picks the cheapest provider that yields elements, numbers them, and drives
    the InputController to act on a chosen one. Providers are injectable so the
    logic is testable without UIA or a real mouse."""

    def __init__(self, accessibility=None, vision=None, inp=None):
        self.acc = accessibility if accessibility is not None else AccessibilityProvider()
        self.vision = vision if vision is not None else VisionProvider()
        self.input = inp if inp is not None else InputController()
        self._cache = None
        self._cache_key = None

    def _key(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            return (hwnd, buf.value)
        except Exception:
            return None

    def elements(self, refresh=False):
        key = self._key()
        if not refresh and self._cache is not None and key == self._cache_key:
            return self._cache
        els = self.acc.elements()
        if not els:
            els = self.vision.elements()    # None today (stub)
        els = els or []
        self._cache, self._cache_key = els, key
        return els

    def act(self, n, sub="click"):
        els = self.elements()
        if not els or n < 1 or n > len(els):
            return None
        el = els[n - 1]
        cx, cy = el["center"]
        if sub == "double":
            self.input.click(cx, cy, clicks=2)
        elif sub == "right":
            self.input.click(cx, cy, button="right")
        else:
            self.input.click(cx, cy)
        return el


_planner = NavigationPlanner()


# ── Utterance parsing ────────────────────────────────────────────────────────

def _distance(text: str, explicit: str | None) -> int:
    if explicit:
        return int(explicit)
    if re.search(r"\b(?:a\s+)?(?:little|bit|tiny|small|nudge|smidge)\b", text):
        return _SMALL
    if re.search(r"\b(?:a\s+)?(?:lot|far|lots|big|large|way)\b", text):
        return _LARGE
    return _STEP


def _ordinal(text: str):
    for word, n in _ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            return n
    return None


def _parse(text: str):
    """Map an utterance to an action tuple, or None to decline (fall through).

    Tuples:
      ("exit",)
      ("list",)                       list/refresh interactive elements
      ("select", n, sub)              pick element n; sub = click|double|right
      ("move", dx, dy)                relative cursor nudge
      ("click", button, n)           click at the current cursor
      ("scroll", amount)             +up / -down
      ("type", text)                 type literal text
      ("key", name)                  press a single key
    """
    t = text.strip().lower()

    if re.search(r"\b(?:exit|leave|stop|end|quit|close)\s+"
                 r"(?:hands?[-\s]?free|mouse|navigation|visual)\b"
                 r"|\bnormal\s+mode\b|\bhands?\s+on\b", t):
        return ("exit",)

    if re.search(r"\bwhat\s+can\s+i\s+(?:click|select|press|do)\b"
                 r"|\b(?:show|list|read)\s+(?:me\s+)?(?:the\s+)?"
                 r"(?:links?|elements?|items?|options?|videos?|results?|buttons?)\b"
                 r"|\bwhat'?s\s+clickable\b|\b(?:refresh|rescan|re-?scan)\b", t):
        return ("list",)

    # Selection sub-action (applies to "double click number 2" / "right click 3")
    sub = "double" if re.search(r"\bdouble[-\s]?click\b", t) else \
          "right" if re.search(r"\bright[-\s]?click\b", t) else "click"

    # Numbered selection: verb+number, noun+number, or a bare number.
    m = (re.search(rf"{_SELECT_VERB}\s+(?:the\s+)?(?:{_SELECT_NOUN}\s+)?(\d+)\b", t)
         or re.search(rf"\b{_SELECT_NOUN}\s+(\d+)\b", t)
         or re.search(r"^\s*(\d+)\s*$", t))
    if m:
        return ("select", int(m.group(1)), sub)

    # Ordinal selection — require a select verb or noun so stray ordinals in
    # normal speech don't trigger it.
    n = _ordinal(t)
    if n is not None and re.search(rf"{_SELECT_VERB}|\b{_SELECT_NOUN}\b", t):
        return ("select", n, sub)

    # Description selection: "open the tutorial video" / "click the play button".
    # The handler fuzzy-matches against the current element labels and DECLINES
    # (returns None) on no match, so "open firefox" still falls through to launch.
    m = re.search(r"(?:open|click|select|choose|pick|launch|go\s+to)\s+"
                  r"(?:the\s+)?(.+)$", t)
    if m and m.group(1).strip() not in ("it", "here", "there", "that", "this"):
        return ("select_desc", m.group(1).strip(), sub)

    m = re.search(r"\btype\s+(.+)$", t)
    if m:
        return ("type", m.group(1).strip())
    m = re.search(r"\bpress\s+(enter|return|escape|esc|tab|space|backspace|"
                  r"delete|up|down|left|right|home|end|pageup|pagedown)\b",
                  t.replace("page up", "pageup").replace("page down", "pagedown"))
    if m:
        return ("key", m.group(1))

    # Bare clicks at the current cursor position.
    if sub == "double":
        return ("click", "left", 2)
    if sub == "right":
        return ("click", "right", 1)
    if re.search(r"\bmiddle[-\s]?click\b", t):
        return ("click", "middle", 1)
    if re.search(r"\b(?:left[-\s]?click|click(?:\s+(?:it|here|there|that))?|tap)\b", t):
        return ("click", "left", 1)

    if re.search(r"\bscroll\s+up\b|\bpage\s+up\b", t):
        return ("scroll", _SCROLL)
    if re.search(r"\bscroll(?:\s+down)?\b|\bpage\s+down\b|\bmore\b", t):
        return ("scroll", -_SCROLL)

    m = re.search(r"(?:move|go|nudge|slide|cursor|mouse)?\s*"
                  r"\b(up|down|left|right)\b(?:\s+(?:by\s+)?(\d+))?", t)
    if m:
        ux, uy = _DIRS[m.group(1)]
        d = _distance(t, m.group(2))
        return ("move", ux * d, uy * d)

    return None


# ── Mode handler (Converse) + entry/exit ─────────────────────────────────────

def _speak_list():
    els = _planner.elements(refresh=True)
    if not els:
        tail = ("." if _planner.vision.available()
                else "; on-demand vision isn't set up yet.")
        return ("I can't read any clickable elements here — this app may not "
                "expose accessibility info" + tail)
    numbered = [f"{i}. {el['label']}" for i, el in enumerate(els, 1)]
    if _display is not None:
        try:
            _display.show_list(numbered, status="What can I click?")
            # Numbered coordinate tags over each element (reuses the window-id
            # overlay — its payload is a generic {index,label,x,y,w,h}).
            _display.identify_windows([
                {"index": i, "label": el["label"][:30],
                 "title": el.get("type", ""),
                 "x": el["bounds"][0], "y": el["bounds"][1],
                 "w": el["bounds"][2], "h": el["bounds"][3]}
                for i, el in enumerate(els, 1)
            ])
        except Exception:
            pass
    preview = "; ".join(numbered[:8])
    more = "" if len(els) <= 8 else f", and {len(els) - 8} more"
    return f"I see {len(els)} things. {preview}{more}. Say 'open number 2' to pick one."


def _do_select(n: int, sub: str):
    el = _planner.act(n, sub)
    if el is None:
        els = _planner.elements()
        if not els:
            return "I don't have a list yet — say 'what can I click' first."
        return f"I only see {len(els)} things. Pick 1 to {len(els)}."
    verb = {"double": "Double-clicked", "right": "Right-clicked"}.get(sub, "Clicked")
    return f"{verb} {el['label']}."


_DESC_THRESHOLD = 70   # rapidfuzz token_set_ratio cutoff for spoken description


def _do_select_desc(phrase: str, sub: str):
    """Fuzzy-match a spoken description to the best on-screen element and click
    it. Returns None (decline) when nothing matches, so 'open firefox' (not a
    visible element) falls through to the normal app launcher."""
    els = _planner.elements()
    if not els:
        return None
    try:
        from rapidfuzz import fuzz, process
    except Exception:
        return None
    labels = [e["label"] for e in els]
    # rapidfuzz passes score_cutoff/processor into the scorer — accept **kw.
    def _scorer(a, b, **_):
        return fuzz.token_set_ratio(" ".join(a.split()), " ".join(b.split()))
    match = process.extractOne(phrase, labels, scorer=_scorer,
                               score_cutoff=_DESC_THRESHOLD)
    if match is None:
        return None
    _, _score, idx = match
    el = _planner.act(idx + 1, sub)
    if el is None:
        return None
    verb = {"double": "Double-clicked", "right": "Right-clicked"}.get(sub, "Clicked")
    return f"{verb} {el['label']}."


def _apply_input(action) -> str:
    kind = action[0]
    if kind == "move":
        _, dx, dy = action
        _planner.input.move_rel(dx, dy)
        horiz = "right" if dx > 0 else "left" if dx < 0 else ""
        vert = "down" if dy > 0 else "up" if dy < 0 else ""
        return f"Moved {horiz}{vert}".strip() or "Moved"
    if kind == "click":
        _, button, n = action
        _planner.input.click(button=button, clicks=n)
        label = {"left": "Clicked", "right": "Right-clicked", "middle": "Middle-clicked"}[button]
        return "Double-clicked" if n == 2 else label
    if kind == "scroll":
        _planner.input.scroll(action[1])
        return "Scrolled"
    if kind == "type":
        _planner.input.type_text(action[1])
        return f"Typed {action[1]}"
    if kind == "key":
        _planner.input.hotkey(action[1])
        return f"Pressed {action[1]}"
    return "Okay"


def handle(text: str):
    """Converse handler: claim mouse/selection/exit utterances; return None to
    decline so normal dispatch still runs (e.g. 'open firefox' mid-mode)."""
    action = _parse(text)
    if action is None:
        return None
    kind = action[0]
    if kind == "exit":
        return _exit()
    if kind == "list":
        return _speak_list()
    if kind == "select":
        return _do_select(action[1], action[2])
    if kind == "select_desc":
        return _do_select_desc(action[1], action[2])
    return _apply_input(action)


def enter():
    from core import session as _sess
    _sess.start_converse(handle, label="visual nav", turns=9999, ttl=300.0)
    return ("Hands-free mode on. Say 'what can I click' to list things, then "
            "'open number 2'. Or just say move, click, or scroll. Say 'exit "
            "hands-free mode' when you're done.")


def _exit():
    from core import session as _sess
    _sess.clear_converse()
    return "Hands-free mode off."


# Entry phrases deliberately avoid apps.open_app verbs (open/start/launch/run/…)
# since a skill is matched AFTER built-ins — "start hands free mode" would
# otherwise be read as launching an app. "hands free mode" / "mouse mode" win.
INTENTS = [
    (r"\b(?:enter|activate|begin|engage|use)\s+(?:into\s+)?"
     r"(?:hands?[-\s]?free|visual\s+nav(?:igation)?|navigation|mouse)(?:\s+mode)?\b"
     r"|\bhands?[-\s]?free\s+mode\b|\bmouse\s+(?:mode|control)\b"
     r"|\bvisual\s+navigation\b|\bcontrol\s+(?:the\s+)?mouse\b",          enter),
]
# Checks live in tests/test_visual_nav.py (parser + planner against fakes).
