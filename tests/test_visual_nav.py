"""Visual Navigation skill — parser, planner, handler, and wiring.

All deterministic: the parser is pure, and the planner/handler run against
INJECTED fake providers so nothing touches UI Automation or the real mouse.

Run either way:
    pytest tests/
    python tests/test_visual_nav.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills import visual_nav as vn
from core import features, session as S


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeAcc:
    """Stand-in AccessibilityProvider returning two clickable links."""
    def elements(self):
        return [
            {"label": "First link",  "type": "Hyperlink", "bounds": (10, 20, 100, 30), "center": (60, 35)},
            {"label": "Second link", "type": "Hyperlink", "bounds": (10, 60, 100, 30), "center": (60, 75)},
        ]


class _FakeInput:
    def __init__(self):
        self.clicks = []
        self.moves = []
        self.scrolls = []
    def click(self, x=None, y=None, button="left", clicks=1):
        self.clicks.append((x, y, button, clicks))
    def move_rel(self, dx, dy):
        self.moves.append((dx, dy))
    def scroll(self, amount):
        self.scrolls.append(amount)
    def type_text(self, text):
        self.typed = text
    def hotkey(self, combo):
        self.key = combo


def _fake_planner():
    """A planner with fakes + a fixed cache key so no foreground lookup runs."""
    p = vn.NavigationPlanner(accessibility=_FakeAcc(), vision=vn.VisionProvider(),
                             inp=_FakeInput())
    p._key = lambda: ("fake", "win")          # avoid ctypes foreground calls
    return p


# ── Parser ─────────────────────────────────────────────────────────────────────

def test_parse_selection():
    assert vn._parse("open number 2") == ("select", 2, "click")
    assert vn._parse("click the third video") == ("select", 3, "click")
    assert vn._parse("select the first search result") == ("select", 1, "click")
    assert vn._parse("open the second browser tab") == ("select", 2, "click")
    assert vn._parse("double click number 4") == ("select", 4, "double")
    assert vn._parse("right click number 5") == ("select", 5, "right")
    assert vn._parse("the second one") == ("select", 2, "click")
    assert vn._parse("number 3") == ("select", 3, "click")


def test_parse_mouse_and_modes():
    assert vn._parse("move right") == ("move", vn._STEP, 0)
    assert vn._parse("left a little") == ("move", -vn._SMALL, 0)
    assert vn._parse("go down a lot") == ("move", 0, vn._LARGE)
    assert vn._parse("click") == ("click", "left", 1)
    assert vn._parse("double click") == ("click", "left", 2)
    assert vn._parse("right click") == ("click", "right", 1)
    assert vn._parse("scroll down") == ("scroll", -vn._SCROLL)
    assert vn._parse("scroll up") == ("scroll", vn._SCROLL)
    assert vn._parse("what can i click") == ("list",)
    assert vn._parse("refresh") == ("list",)
    assert vn._parse("type hello world") == ("type", "hello world")
    assert vn._parse("press enter") == ("key", "enter")
    assert vn._parse("exit hands free mode") == ("exit",)
    assert vn._parse("normal mode") == ("exit",)


def test_parse_declines_unrelated():
    # No select verb → None at parse time, so normal dispatch still runs.
    assert vn._parse("what time is it") is None
    assert vn._parse("remind me to call mom") is None


def test_parse_select_by_description():
    # A select verb + phrase becomes select_desc; the handler decides whether it
    # matches a visible element (else it declines and 'open firefox' launches).
    assert vn._parse("open tutorial") == ("select_desc", "tutorial", "click")
    assert vn._parse("click the play button") == ("select_desc", "play button", "click")
    assert vn._parse("open firefox") == ("select_desc", "firefox", "click")
    # Cursor-word clicks stay bare clicks, not description selects.
    assert vn._parse("click here") == ("click", "left", 1)


# ── Planner ────────────────────────────────────────────────────────────────────

def test_planner_clicks_element_center():
    p = _fake_planner()
    el = p.act(2, "click")
    assert el["label"] == "Second link"
    assert p.input.clicks[0][:2] == (60, 75)          # clicked the element's center
    assert p.act(99) is None                          # out of range
    p.act(1, "double")
    assert p.input.clicks[-1][3] == 2                  # double = 2 clicks


def test_planner_caches_until_key_changes():
    p = _fake_planner()
    first = p.elements()
    assert len(first) == 2
    # Same key → cached object returned (no re-scan).
    assert p.elements() is first


# ── Handler (full path through the injected planner) ───────────────────────────

def test_handler_routes(monkeypatch):
    p = _fake_planner()
    monkeypatch.setattr(vn, "_planner", p)
    monkeypatch.setattr(vn, "_display", None)

    assert "2 things" in vn.handle("what can i click")
    assert vn.handle("open number 2") == "Clicked Second link."
    assert vn.handle("move right").startswith("Moved")
    assert p.input.moves[-1] == (vn._STEP, 0)
    assert vn.handle("scroll down") == "Scrolled"
    assert p.input.scrolls[-1] == -vn._SCROLL
    # Declines unrelated → None (falls through to normal dispatch).
    assert vn.handle("open firefox") is None


def test_select_by_description(monkeypatch):
    # Fuzzy-match a spoken phrase to an element label and click it; decline
    # (return None) when nothing matches so normal dispatch still runs.
    class _Acc:
        def elements(self):
            return [
                {"label": "Claude Tutorial", "type": "video", "bounds": (0, 0, 80, 40), "center": (40, 20)},
                {"label": "Settings Page",   "type": "link",  "bounds": (0, 50, 80, 40), "center": (40, 70)},
            ]
    p = vn.NavigationPlanner(accessibility=_Acc(), vision=vn.VisionProvider(), inp=_FakeInput())
    p._key = lambda: ("k", "k")
    monkeypatch.setattr(vn, "_planner", p)
    monkeypatch.setattr(vn, "_display", None)

    assert vn.handle("open the claude tutorial") == "Clicked Claude Tutorial."
    assert p.input.clicks[-1][:2] == (40, 20)
    # No matching element → decline so the app launcher can run.
    assert vn.handle("open firefox") is None


def test_enter_starts_converse_and_exit_clears():
    S.reset()
    msg = vn.enter()
    assert "hands-free" in msg.lower()
    assert S.get().converse is not None                # claims upcoming utterances
    assert vn._exit() == "Hands-free mode off."
    assert S.get().converse is None
    S.reset()


# ── Wiring / feature gate ──────────────────────────────────────────────────────

def test_feature_registered():
    assert vn.FEATURE == "visual_nav"
    assert "visual_nav" in features.DEFAULTS
    assert features.DEFAULTS["visual_nav"] is False    # opt-in
    assert "visual_nav" in features.ALPHA
    # INTENTS entry phrases avoid open_app verbs but still match the bare forms.
    import re
    pat = vn.INTENTS[0][0]
    assert re.search(pat, "hands free mode")
    assert re.search(pat, "mouse mode")
    assert re.search(pat, "enter hands-free mode")


def test_skill_loads_and_core_decoupled():
    # Dispatcher imports cleanly = no leftover handsfree references in core.
    import core.dispatcher  # noqa: F401
    # Mode.HANDSFREE was removed (skill uses the Converse layer instead).
    assert not hasattr(S.Mode, "HANDSFREE")
    # The skill is discovered by the drop-in loader (gating happens at dispatch).
    from core import skills
    assert "visual_nav" in skills.load(display=None)


# ── Zero-dependency runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import types

    class _MP:
        """Minimal monkeypatch shim so the file runs without pytest."""
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._undo):
                setattr(obj, name, val)

    tests = []
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            tests.append(v)

    failed = 0
    for t in tests:
        mp = _MP()
        try:
            import inspect
            if "monkeypatch" in inspect.signature(t).parameters:
                t(mp)
            else:
                t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
        finally:
            mp.undo()

    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
