"""Routing tests for the dispatcher's INTENTS table + mode routing.

Characterizes which handler each spoken phrase maps to, WITHOUT executing the
handler (no windows open, no mic, no Electron). Guards the 30+ regex patterns
against silent breakage when intents are added or reordered.

Run either way:
    pytest tests/                # if pytest is installed
    python tests/test_dispatch.py   # zero-dependency fallback runner
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dispatcher as d
from core import features, session as S
from commands import (apps, search, system, youtube, windows as windows_cmd,
                      tiling, context as ctx_cmd, window_manager as wm,
                      handsfree as handsfree_cmd)


def route(text: str):
    """The handler dispatcher.INTENTS would fire for `text`, NOT executed.

    Mirrors dispatch()'s normalization (lowercase, strip trailing punctuation,
    strip wake prefix) and the _try_intents loop incl. feature gating, but
    returns the handler object instead of calling it."""
    text = text.strip().lower()
    text = re.sub(r"[.,!?]+$", "", text).strip()
    for prefix in d._WAKE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip(",. ")
            break
    for pattern, handler in d.INTENTS:
        if re.search(pattern, text):
            feat = d._HANDLER_FEATURE.get(handler)
            if feat and not features.get(feat):
                continue
            return handler
    return None


# ── System / media / panels ───────────────────────────────────────────────────

def test_system_and_media():
    assert route("mute") is system.toggle_mute
    assert route("volume up") is system.volume_up
    assert route("volume down") is system.volume_down
    assert route("what time is it") is system.get_time
    assert route("what is the date") is system.get_date
    assert route("take a screenshot") is system.screenshot


def test_panels_open_close():
    assert route("open app manager") is system.open_app_manager
    assert route("close app manager") is system.close_app_manager
    assert route("open window manager") is system.open_window_manager
    assert route("open voice settings") is system.open_voice_settings
    assert route("open command editor") is system.open_editor
    assert route("open api keys") is system.open_integrations


def test_directory_and_identify():
    # overlay/hud/log/history TOGGLE (consolidated from main.py's former
    # pre-dispatch _OVERLAY_TOGGLE; dispatch() is now the single router).
    assert route("show overlay") is system.toggle_overlay
    assert route("open hud") is system.toggle_overlay
    assert route("hide hud") is system.toggle_overlay
    assert route("toggle hud") is system.toggle_overlay
    assert route("hud") is system.toggle_overlay
    # the routing-directory WINDOW (distinct from the HUD overlay toggle)
    assert route("show directory") is system.show_directory
    assert route("hide directory") is system.hide_directory
    assert route("identify monitors") is system.identify_monitors
    assert route("identify zones") is system.identify_zones
    assert route("what's open") is windows_cmd.identify_windows


def test_consolidated_panel_routing():
    # Former main.py pre-dispatch blocks now live in INTENTS only.
    assert route("voice settings") is system.open_voice_settings          # bare form
    assert route("voice manager") is system.open_voice_settings
    assert route("open apps") is system.open_app_manager                  # _MANAGE_APPS
    assert route("manage apps") is system.open_app_manager
    assert route("configure my apps") is system.open_app_manager
    assert route("open command editor") is system.open_editor
    assert route("open window manager") is system.open_window_manager


# ── Apps ──────────────────────────────────────────────────────────────────────

def test_apps():
    assert route("open firefox") is apps.open_app
    assert route("close firefox") is apps.close_app


# ── YouTube: HUD default + search ────────────────────────────────────────────

def test_youtube_routing():
    assert route("browse youtube") is youtube.browse_feed_intent
    assert route("open the youtube feed") is youtube.browse_feed_intent
    assert route("play lofi beats") is youtube.play_or_search
    assert route("search youtube for jazz") is youtube.play_or_search


# ── Web search ────────────────────────────────────────────────────────────────

def test_web_search():
    assert route("search for puppies") is search.web_search_list
    assert route("go to github.com") is search.go_to_site


# ── Apps feature gate: disabling 'apps' skips open_app ───────────────────────

def test_feature_gate_skips_handler():
    prev = features.get("apps")
    try:
        features.set_feature("apps", False)
        # With apps off, "open firefox" must NOT route to apps.open_app.
        assert route("open firefox") is not apps.open_app
    finally:
        features.set_feature("apps", bool(prev))


# ── Mishear substitutions ─────────────────────────────────────────────────────

def test_mishear_subs():
    assert "hud" in d._apply_mishear_subs("show me the hood")
    assert "youtube" in d._apply_mishear_subs("open you tube")
    assert "app manager" in d._apply_mishear_subs("open at manager")
    # filler stripped
    assert d._apply_mishear_subs("please open firefox").strip() == "open firefox"


# ── Protected / essential programs ───────────────────────────────────────────

def test_protected_programs():
    assert route("protect elden ring") is ctx_cmd.protect_program
    assert route("treat elden ring as essential") is ctx_cmd.protect_program
    assert route("this is my game") is ctx_cmd.protect_program
    assert route("stop protecting elden ring") is ctx_cmd.unprotect_program
    assert route("what's protected") is ctx_cmd.list_protected
    # Must not be swallowed by snap/open_app
    assert route("protect discord") is ctx_cmd.protect_program


# ── Workspace presets ─────────────────────────────────────────────────────────

def test_workspace_presets():
    assert route("save layout as work") is tiling.save_workspace
    assert route("save this layout gaming") is tiling.save_workspace
    assert route("restore work layout") is tiling.restore_workspace
    assert route("restore the gaming layout") is tiling.restore_workspace
    assert route("load layout work") is tiling.restore_workspace
    assert route("what layouts do i have") is tiling.list_workspaces


# ── Auto-snap on launch ───────────────────────────────────────────────────────

def test_auto_snap_assignment():
    assert route("always open firefox in top-left") is tiling.set_app_zone
    assert route("always open discord in right zone on monitor 2") is tiling.set_app_zone
    assert route("auto snap spotify to bottom") is tiling.set_app_zone
    assert route("auto-snap code to left") is tiling.set_app_zone
    assert route("stop auto-snapping firefox") is tiling.clear_app_zone
    # Plain "open firefox" must still launch, not be eaten by the assignment intents
    assert route("open firefox") is apps.open_app


# ── Monitor naming + per-zone HUD targeting ──────────────────────────────────

def test_monitor_naming():
    assert route("name monitor 2 primary display") is wm.name_monitor
    assert route("label display 1 gaming") is wm.name_monitor
    assert route("name the left monitor coding") is wm.name_monitor


def test_hud_zone_monitor_targeting():
    # "move hud to <zone> of monitor N" snaps the panel, not just relocates orb
    assert route("move hud to top-left of monitor 1") is d._snap_hud_zone_monitor
    assert route("snap hud to bottom-right of monitor 2") is d._snap_hud_zone_monitor
    # Bare "move hud to monitor 2" (no zone) still relocates the orb
    assert route("move hud to monitor 2") is wm.move_hud
    # "move hud to top-left" (no monitor) still pins the orb to a corner
    assert route("move hud to top-left") is wm.move_orb_corner


# ── BROWSING mode routing (YouTube HUD) ──────────────────────────────────────
# _dispatch_browsing executes feed handlers, but with no Display wired they are
# safe no-ops that return their spoken-confirmation strings.

def test_browsing_mode_commands():
    prev_mode = S.get().mode
    try:
        S.get().mode = S.Mode.BROWSING
        assert d._dispatch_browsing("scroll down") is not None
        assert d._dispatch_browsing("scroll up") is not None
        assert d._dispatch_browsing("show numbers") is not None
        assert d._dispatch_browsing("open video 3") is not None
        assert d._dispatch_browsing("search for synthwave") is not None
        # Unrelated text falls through (returns None) so normal dispatch can run.
        assert d._dispatch_browsing("what time is it") is None
    finally:
        S.get().mode = prev_mode


# ── Startup on login ──────────────────────────────────────────────────────────

def test_autostart_routing():
    assert route("add eve to startup") is d._autostart_enable
    assert route("start eve when i log in") is d._autostart_enable
    assert route("remove eve from startup") is d._autostart_disable
    assert route("don't start eve at login") is d._autostart_disable
    # "start eve on login" must not be eaten by apps.open_app
    assert route("start eve on login") is d._autostart_enable
    assert route("start eve on login") is not apps.open_app


# ── Discord defers to a protected program (execution, light monkeypatch) ──────

def test_discord_navigation_defers_when_protected():
    from core import essential
    from commands import discord
    orig = essential.active
    try:
        essential.active = lambda: "eldenring"          # pretend a game is active
        for fn in (discord.next_channel, discord.prev_server, discord.quick_switcher):
            msg = fn().lower()
            assert "protected" in msg and "eldenring" in msg, (fn.__name__, msg)
        send = discord.send_message("alice", "hi").lower()
        assert "protected" in send
    finally:
        essential.active = orig


def test_discord_navigation_proceeds_when_unprotected():
    from core import essential
    from commands import discord
    orig_active, orig_hwnd = essential.active, discord._discord_hwnd
    try:
        essential.active = lambda: None                 # nothing protected
        discord._discord_hwnd = lambda: None            # Discord "not open"
        # With nothing protected, it gets past the gate to the hwnd check.
        assert "isn't open" in discord.next_channel().lower()
    finally:
        essential.active, discord._discord_hwnd = orig_active, orig_hwnd


# ── Fuzzy matcher must not silently misroute (regression) ─────────────────────

def test_fuzzy_does_not_silently_misroute():
    from core.dispatcher import dispatch
    # A short catalog phrase as a subset of a longer utterance must NOT silently
    # fire — it gets demoted to a confirmation prompt.
    for p in ["make my app manager full screen", "full screen apps manager"]:
        S.reset()
        r = str(dispatch(p)).lower()
        assert "did you mean" in r, (p, r)
    # Unrelated phrases must not become "Unknown app: …" via the open-prefix retry.
    for p in ["for untracked apps", "add another"]:
        S.reset()
        r = str(dispatch(p)).lower()
        assert "not recognized" in r, (p, r)
    S.reset()


# ── Drop-in skills ────────────────────────────────────────────────────────────

def test_all_intents_compile_and_are_callable():
    # Guards the hand-ordered regex wall: a typo'd pattern or a misreferenced
    # handler fails here instead of silently at runtime.
    for pattern, handler in d.INTENTS:
        re.compile(pattern)
        assert callable(handler), pattern


def test_skill_loading():
    from core import skills
    names = skills.load(display=None)
    assert "example_dice" in names, names
    assert "rolled" in (skills.dispatch("roll a die") or "").lower()
    coin = (skills.dispatch("flip a coin") or "").lower()
    assert "heads" in coin or "tails" in coin, coin
    # Non-skill text returns None so the dispatcher falls through to fallback.
    assert skills.dispatch("what time is it") is None


def test_skill_integration_through_dispatch():
    # A skill phrase with no built-in match routes through dispatch() to the skill.
    from core import skills
    skills.load(display=None)
    assert "rolled" in str(d.dispatch("roll 2d6")).lower()


# ── 3D printer skill ──────────────────────────────────────────────────────────
# Filename starts with a digit, so it can't be `import`ed normally; load it from
# its path the same way core.skills does. No network is touched: routing is
# checked against INTENTS, and handlers are exercised only with no printer
# configured (the guidance path).

def _printer_mod():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "skills", "3dprinter.py")
    spec = importlib.util.spec_from_file_location("eve_skill_3dprinter_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _printer_route(mod, text):
    for pattern, handler in mod.INTENTS:
        m = re.search(pattern, text)
        if m:
            return handler, m.groups()
    return None, ()


def test_printer_skill_loads():
    from core import skills
    names = skills.load(display=None)
    assert "3dprinter" in names, names


def test_printer_intents_route():
    p = _printer_mod()
    expect = {
        "how's my print":         p._status,
        "printer status":         p._status,
        "is the print done":      p._status,
        "how long left":          p._time_left,
        "time remaining":         p._time_left,
        "what's the nozzle temp": p._temps,
        "is the bed hot":         p._temps,
        "pause the print":        p._pause,
        "resume printing":        p._resume,
        "cancel the print":       p._cancel,
        "abort the job":          p._cancel,
        "preheat for petg":       p._preheat,
        "warm up the printer":    p._preheat,
        "cool down the printer":  p._cooldown,
    }
    for text, handler in expect.items():
        hit, _ = _printer_route(p, text)
        assert hit is handler, (text, getattr(hit, "__name__", hit))


def test_printer_preheat_captures_material():
    # The material is captured and passed positionally; a bare preheat is None.
    p = _printer_mod()
    _, groups = _printer_route(p, "preheat for petg")
    assert groups == ("petg",), groups
    _, groups = _printer_route(p, "preheat")
    assert groups == (None,), groups


def test_printer_backend_selection():
    p = _printer_mod()
    assert isinstance(p._BACKENDS["prusa"]({"host": "1.2.3.4", "api_key": "k"}),
                      p.PrusaBackend)
    assert isinstance(p._BACKENDS["bambu"]({"host": "1.2.3.4", "serial": "s",
                                            "access_code": "c"}),
                      p.BambuBackend)
    # Missing required fields surface as a PrinterError, never a raw exception.
    for cls, bad in ((p.PrusaBackend, {}), (p.BambuBackend, {"host": "x"})):
        try:
            cls(bad)
            assert False, f"{cls.__name__} accepted incomplete config"
        except p.PrinterError:
            pass


def test_printer_unknown_type_is_friendly():
    p = _printer_mod()
    orig = p._config
    p._config = lambda: {"type": "ultimaker"}
    try:
        try:
            p._backend()
            assert False, "unknown type should raise"
        except p.PrinterError as e:
            assert "ultimaker" in str(e).lower()
    finally:
        p._config = orig


def test_printer_unconfigured_speaks_guidance():
    # With no printer configured, every handler returns spoken guidance instead
    # of raising (which the skill loader would treat as a non-match) or hanging
    # on the network.
    p = _printer_mod()
    orig = p._config
    p._config = lambda: {}
    try:
        for fn in (p._status, p._temps, p._time_left, p._pause, p._cooldown,
                   p._preheat):
            assert "printer" in str(fn()).lower(), fn.__name__
    finally:
        p._config = orig


def test_printer_cancel_requires_confirmation():
    # Cancel is destructive: it arms Eve's single-turn yes/no confirmation and
    # does NOT stop the print itself.
    p = _printer_mod()
    S.get().pending_confirm = None
    try:
        resp = p._cancel()
        pending = S.get().pending_confirm
        assert pending is not None and pending[2] == "cancel the print"
        assert pending[0] is p._do_cancel
        assert "confirm" in str(resp).lower()
    finally:
        S.get().pending_confirm = None


# ── Hands-free mouse mode + interface synonym ────────────────────────────────

def test_handsfree_and_interface():
    assert route("enter hands free mode") is handsfree_cmd.enter
    assert route("hands-free mode") is handsfree_cmd.enter
    assert route("mouse mode") is handsfree_cmd.enter
    # "interface" is a spoken synonym for the HUD/overlay (toggles, same as
    # "show hud"/"hide hud" — the toggle intent sits above directory show/hide).
    assert route("hide interface") is system.toggle_overlay
    assert route("show interface") is system.toggle_overlay
    # In HANDSFREE mode, movement/click route to the mode handler; unrelated
    # phrases decline so normal commands still work.
    assert handsfree_cmd._parse("move right")[0] == "move"
    assert handsfree_cmd._parse("click")[0] == "click"
    assert handsfree_cmd._parse("open firefox") is None


def test_snap_dangling_zone_prompts():
    # "snap steam to" (cut off) must ask where, not error with zone='to'.
    S.reset()
    r = str(tiling.snap_app("steam", "to")).lower()
    assert "where" in r, r
    S.reset()


# ── Zero-dependency runner ────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    total = len(tests)
    print(f"\n{total - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
