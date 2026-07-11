"""Tests for the LLM fallback (commands/fallback.py, OpenAI protocol) and the
learned-intents loop (core/intent_learning.py LearnedStore).

Covers: phrase normalization, arg→pattern templating, capture/dedupe, the
exact-vs-trusted-pattern match ladder, the class-based destructive gate,
OpenAI response parsing (tool_calls / content / garbage), the capture hook on
verified tool success, and the learned tier's execute+record path. No live
LLM server anywhere — HTTP is monkeypatched.

Run either way:
    pytest tests/
    python tests/test_fallback.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core import intent_learning
from core.intent_learning import (LearnedStore, make_template, normalize,
                                  DESTRUCTIVE_TOOLS, TRUST_CONFIDENCE)
from commands import fallback


def _tmp():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)          # start absent; the store must tolerate a missing file
    return path


def _store():
    return LearnedStore(_tmp())


# ── normalize ────────────────────────────────────────────────────────────────

def test_normalize():
    assert normalize("  Throw  FIREFOX on my left screen!  ") == "throw firefox on my left screen"
    assert normalize("") == ""
    assert normalize(None) == ""


# ── make_template ────────────────────────────────────────────────────────────

def test_template_round_trips():
    import re
    phrase = normalize("throw firefox on my left screen")
    pat = make_template(phrase, {"app": "firefox", "zone": "left"})
    assert pat is not None
    m = re.fullmatch(pat, "throw chrome on my right screen")
    assert m and m.group("app") == "chrome" and m.group("zone") == "right"


def test_template_none_when_value_absent_or_ambiguous():
    # value not in the phrase verbatim
    assert make_template("open the browser", {"name": "firefox"}) is None
    # value appears twice → ambiguous span
    assert make_template("move left to the left", {"zone": "left"}) is None
    # nothing to generalize
    assert make_template("do the thing", {}) is None


def test_template_optional_none_arg_ok():
    pat = make_template(normalize("throw firefox on my left screen"),
                        {"app": "firefox", "zone": "left", "monitor": None})
    assert pat is not None


# ── LearnedStore: capture / match / trust ladder ─────────────────────────────

def test_capture_creates_and_dedupes():
    st = _store()
    e1 = st.capture("Throw Firefox on my LEFT screen", "snap_window",
                    {"app": "firefox", "zone": "left"})
    assert e1["s"] == 1 and e1["pattern"] is not None
    e2 = st.capture("throw firefox on my left screen", "snap_window",
                    {"app": "firefox", "zone": "left"})
    assert e2 is e1 and e1["s"] == 2 and len(st.entries) == 1
    # persists across reload
    st2 = LearnedStore(st.path)
    assert len(st2.entries) == 1 and st2.entries[0]["s"] == 2


def test_exact_match_serves_immediately_pattern_waits_for_trust():
    st = _store()
    st.capture("throw firefox on my left screen", "snap_window",
               {"app": "firefox", "zone": "left"})
    # exact phrase: served at one success
    hit = st.match("throw firefox on my left screen")
    assert hit and hit[1] == {"app": "firefox", "zone": "left"}
    # generalized phrase: NOT served yet (confidence below trust bar)
    assert st.match("throw chrome on my right screen") is None
    # two more verified successes → trusted → pattern serves with fresh args
    entry = st.entries[0]
    st.record(entry, True); st.record(entry, True)
    assert st.confidence(entry) >= TRUST_CONFIDENCE
    hit = st.match("throw chrome on my right screen")
    assert hit and hit[1]["app"] == "chrome" and hit[1]["zone"] == "right"


def test_destructive_never_served_but_still_captured():
    st = _store()
    e = st.capture("nuke chrome", "close_app", {"name": "chrome"})
    for _ in range(10):
        st.record(e, True)                      # evidence piles up…
    assert st.match("nuke chrome") is None      # …but class safety wins
    assert "close_app" in DESTRUCTIVE_TOOLS


def test_failures_drop_confidence_and_stamp():
    st = _store()
    e = st.capture("throw firefox on my left screen", "snap_window",
                   {"app": "firefox", "zone": "left"})
    st.record(e, True); st.record(e, True)      # trusted (3s/0f)
    assert st.match("throw chrome on my right screen") is not None
    st.record(e, False); st.record(e, False); st.record(e, False)
    assert e["f"] == 3 and e.get("last_failure")
    assert st.confidence(e) < TRUST_CONFIDENCE  # pattern serving revoked
    assert st.match("throw chrome on my right screen") is None
    # exact phrase still serves (same phrase = same meaning; failures are
    # execution trouble, not interpretation doubt)
    assert st.match("throw firefox on my left screen") is not None


# ── fallback: OpenAI response parsing + capture hook ─────────────────────────

def _settings(**over):
    from core import llm_host
    return {**llm_host.DEFAULTS, "enabled": True,
            "base_url": "http://127.0.0.1:1", "model": "eve-fallback", **over}


def _with(monkey_post, learned_store, fn, **settings_over):
    """Run fn() with fallback._post, the learned singleton, and the llm
    settings all swapped — fully hermetic regardless of settings.json."""
    from core import llm_host
    old_post, old_learned = fallback._post, intent_learning._learned
    old_settings = llm_host.settings
    fallback._post = monkey_post
    intent_learning._learned = learned_store
    llm_host.settings = lambda: _settings(**settings_over)
    try:
        return fn()
    finally:
        fallback._post = old_post
        intent_learning._learned = old_learned
        llm_host.settings = old_settings


def test_off_switch_short_circuits():
    from core import llm_host
    old = llm_host.settings
    llm_host.settings = lambda: _settings(enabled=False)
    try:
        assert fallback.answer("anything") is None
    finally:
        llm_host.settings = old


def test_tool_call_executes_and_captures():
    st = _store()
    calls = []
    old = fallback._TOOL_HANDLERS.get("snap_window")
    fallback._TOOL_HANDLERS["snap_window"] = (
        lambda a: calls.append(a) or "Snapped firefox to left.", None)
    resp = {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "snap_window",
        "arguments": '{"app": "firefox", "zone": "left"}'}}]}}]}
    try:
        out = _with(lambda body: resp, st, lambda: fallback.answer(
            "throw firefox on my left screen"))
    finally:
        fallback._TOOL_HANDLERS["snap_window"] = old
    assert out == "Snapped firefox to left."
    assert calls == [{"app": "firefox", "zone": "left"}]
    # verified success was captured as a learned candidate
    assert len(st.entries) == 1
    assert st.entries[0]["phrase"] == "throw firefox on my left screen"
    assert st.entries[0]["tool"] == "snap_window"


def test_content_answer_and_garbage():
    st = _store()
    resp = {"choices": [{"message": {"content": "Paris is the capital of France."}}]}
    assert _with(lambda b: resp, st, lambda: fallback.answer("capital of france")) \
        == "Paris is the capital of France."
    assert len(st.entries) == 0                 # plain answers never captured
    # server down / garbage → None, never raises
    assert _with(lambda b: None, st, lambda: fallback.answer("hm")) is None
    assert _with(lambda b: {"weird": 1}, st, lambda: fallback.answer("hm")) is None


def _with_stores(personal, imported, fn):
    old_l, old_i = intent_learning._learned, intent_learning._imported
    intent_learning._learned, intent_learning._imported = personal, imported
    try:
        return fn()
    finally:
        intent_learning._learned, intent_learning._imported = old_l, old_i


def test_learned_answer_executes_and_records():
    st = _store()
    st.capture("throw firefox on my left screen", "snap_window",
               {"app": "firefox", "zone": "left"})
    calls = []
    old = fallback._TOOL_HANDLERS.get("snap_window")
    fallback._TOOL_HANDLERS["snap_window"] = (
        lambda a: calls.append(a) or "Snapped.", None)
    try:
        out = _with_stores(st, _store(), lambda: fallback.learned_answer(
            "throw firefox on my left screen"))
        assert out == "Snapped."
        assert calls and st.entries[0]["s"] == 2    # capture(1) + verified exec(1)
        # no learned match → None (falls through to the LLM tier)
        assert _with_stores(st, _store(), lambda: fallback.learned_answer(
            "completely unknown gibberish")) is None
    finally:
        fallback._TOOL_HANDLERS["snap_window"] = old


def test_polite_failures_never_learn():
    """The poisoning guard: handlers report soft failures as spoken strings —
    those must not be captured, and a learned replay hitting one must record a
    failure and fall through (return None)."""
    from core.intent_learning import verify_for_learning
    from core.response import Silent, Verified
    for bad in ("Unknown app: flurbo", "I couldn't find 'monitor 5'.",
                "Sorry, that didn't work.", "YouTube isn't available right now.",
                Silent("Unknown app: x"), "Not recognized."):
        assert verify_for_learning(bad, None) is False, bad
    for good in ("Opening Firefox", "Snapped firefox to left.", "Done",
                 Verified("Moved it.", check=lambda: True, on_fail="nope"),
                 Silent("Ready")):   # Silent success-shaped strings still pass
        assert verify_for_learning(good, None) is True, good

    # end-to-end: LLM tool call returns an apology → nothing captured
    st = _store()
    old = fallback._TOOL_HANDLERS.get("open_app")
    fallback._TOOL_HANDLERS["open_app"] = (lambda a: "Unknown app: flurbo", None)
    resp = {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "open_app", "arguments": '{"name": "flurbo"}'}}]}}]}
    try:
        out = _with(lambda b: resp, st, lambda: fallback.answer("start up flurbo"))
    finally:
        fallback._TOOL_HANDLERS["open_app"] = old
    assert out == "Unknown app: flurbo"      # user still hears the reply
    assert st.entries == []                  # but nothing was learned

    # learned replay that now fails politely → recorded failure + fallthrough
    st.capture("start firefox please", "open_app", {"name": "firefox"})
    old = fallback._TOOL_HANDLERS.get("open_app")
    fallback._TOOL_HANDLERS["open_app"] = (lambda a: "I couldn't find that.", None)
    try:
        assert _with_stores(st, _store(), lambda: fallback.learned_answer(
            "start firefox please")) is None
    finally:
        fallback._TOOL_HANDLERS["open_app"] = old
    assert st.entries[0]["f"] == 1


# ── imported packs: tier order, export/import round trip, validation ─────────

def test_imported_tier_consulted_before_personal():
    personal, imported = _store(), _store()
    personal.capture("do the thing my way", "web_search", {"query": "personal"})
    imported.capture("do the thing my way", "web_search", {"query": "imported"})
    served = []
    old = fallback._TOOL_HANDLERS.get("web_search")
    fallback._TOOL_HANDLERS["web_search"] = (
        lambda a: served.append(a["query"]) or "Searched.", None)
    try:
        out = _with_stores(personal, imported,
                           lambda: fallback.learned_answer("do the thing my way"))
        assert out == "Searched." and served == ["imported"]
        # imported entry failing → falls through to the personal tier
        served.clear()
        fallback._TOOL_HANDLERS["web_search"] = (
            lambda a: ("Sorry, that failed." if a["query"] == "imported"
                       else "Searched."), None)
        out = _with_stores(personal, imported,
                           lambda: fallback.learned_answer("do the thing my way"))
        assert out == "Searched."
        assert imported.entries[0]["f"] == 1     # failure recorded on the pack entry
    finally:
        fallback._TOOL_HANDLERS["web_search"] = old


def test_export_import_round_trip_and_validation():
    import tempfile
    from core.intent_learning import export_intents, import_intents
    personal, imported = _store(), _store()
    personal.capture("throw firefox on my left screen", "snap_window",
                     {"app": "firefox", "zone": "left"})
    path = os.path.join(tempfile.mkdtemp(), "pack.json")

    def _round_trip():
        n = export_intents(path)
        assert n == 1
        added, updated, skipped = import_intents(path)
        assert (added, updated, skipped) == (1, 0, 0)
        assert imported.entries[0]["phrase"] == "throw firefox on my left screen"
        assert imported.entries[0]["origin"] == "pack.json"
        # re-import with no better evidence → skipped, not duplicated
        assert import_intents(path) == (0, 0, 1)
        assert len(imported.entries) == 1
    _with_stores(personal, imported, _round_trip)

    # malformed packs are rejected loudly, junk entries silently skipped
    bad = os.path.join(tempfile.mkdtemp(), "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"whatever": 1}, f)
    try:
        _with_stores(personal, imported, lambda: import_intents(bad))
        assert False, "should have raised"
    except ValueError:
        pass
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"kind": "eve-intents", "version": 1,
                   "entries": [{"phrase": "", "tool": "x"}, "garbage",
                               {"phrase": "ok phrase", "tool": "web_search",
                                "args": {"query": "q"}, "s": "NaN"}]}, f)
    res = _with_stores(personal, _store(), lambda: import_intents(bad))
    assert res == (0, 0, 3)


def test_store_delete():
    st = _store()
    st.capture("throw firefox on my left screen", "snap_window",
               {"app": "firefox", "zone": "left"})
    assert st.delete("snap_window", "throw firefox on my left screen") is True
    assert st.entries == []
    assert st.delete("snap_window", "gone already") is False
    # deletion persisted
    assert LearnedStore(st.path).entries == []


def test_tool_registry_consistent():
    assert set(fallback._TOOL_HANDLERS) == {t["function"]["name"] for t in fallback._TOOLS}


# ── busy-aware model choice + generated llama-swap config ────────────────────

def test_model_swaps_to_mini_when_busy():
    from core import llm_host, essential
    old_settings, old_defer, old_ram = llm_host.settings, essential.should_defer, fallback._ram_load_pct
    llm_host.settings = lambda: _settings()
    try:
        essential.should_defer = lambda: True          # game foreground
        fallback._ram_load_pct = lambda: 10
        assert fallback._model() == "eve-fallback-mini"
        essential.should_defer = lambda: False
        fallback._ram_load_pct = lambda: 95            # RAM pressure
        assert fallback._model() == "eve-fallback-mini"
        fallback._ram_load_pct = lambda: 10            # idle
        assert fallback._model() == "eve-fallback"
        llm_host.settings = lambda: _settings(swap_when_busy=False)
        essential.should_defer = lambda: True          # policy off → main model
        assert fallback._model() == "eve-fallback"
    finally:
        llm_host.settings, essential.should_defer, fallback._ram_load_pct = \
            old_settings, old_defer, old_ram


def test_ram_load_pct_sane():
    v = fallback._ram_load_pct()
    assert isinstance(v, int) and 0 <= v <= 100


def test_save_settings_merges_known_keys_only():
    import tempfile
    from core import llm_host
    old_root = llm_host._ROOT
    llm_host._ROOT = tempfile.mkdtemp()          # isolated settings.json
    try:
        s = llm_host.save_settings({"gpu": False, "busy_ram_pct": 70, "bogus_key": 1})
        assert s["gpu"] is False and s["busy_ram_pct"] == 70
        import json
        stored = json.load(open(os.path.join(llm_host._ROOT, "settings.json")))["llm"]
        assert stored == {"gpu": False, "busy_ram_pct": 70}   # unknown key dropped
        # second save merges, doesn't clobber
        llm_host.save_settings({"gpu": True})
        stored = json.load(open(os.path.join(llm_host._ROOT, "settings.json")))["llm"]
        assert stored == {"gpu": True, "busy_ram_pct": 70}
    finally:
        llm_host._ROOT = old_root


def test_apply_settings_respects_hand_edit_marker():
    import tempfile
    from core import llm_host
    cfg = os.path.join(tempfile.mkdtemp(), "llama-swap.yaml")
    old_cfg, old_find, old_settings = \
        config.LLAMA_SWAP_CONFIG, llm_host.find_llama_server, llm_host.settings
    config.LLAMA_SWAP_CONFIG = cfg
    llm_host.find_llama_server = lambda: r"C:\fake\llama-server.exe"
    llm_host.settings = lambda: _settings(enabled=False)   # never spawns
    try:
        # generated file (has marker) → settings change regenerates it
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(llm_host.GENERATED_MARKER + "\nOLD CONTENT\n")
        llm_host.apply_settings()
        text = open(cfg, encoding="utf-8").read()
        assert "OLD CONTENT" not in text and r"C:\fake\llama-server.exe" in text
        # hand-edited file (marker removed) → never touched
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("USER EDIT\n")
        llm_host.apply_settings()
        assert open(cfg, encoding="utf-8").read() == "USER EDIT\n"
    finally:
        config.LLAMA_SWAP_CONFIG = old_cfg
        llm_host.find_llama_server = old_find
        llm_host.settings = old_settings


def test_preload_option_warms_main_or_mini():
    from core import llm_host
    warmed = []
    old = (llm_host.settings, llm_host._server_up, llm_host._preload,
           llm_host._game_foreground, llm_host._start_game_watcher)
    llm_host._server_up = lambda *a, **k: True
    llm_host._preload = lambda s, model=None: warmed.append(model or s["model"])
    llm_host._start_game_watcher = lambda: None
    try:
        # preload off (default) → no warm-up
        llm_host.settings = lambda: _settings()
        assert llm_host.ensure_running() is True and warmed == []
        # preload on, desktop idle → warms the MAIN model
        llm_host.settings = lambda: _settings(preload=True)
        llm_host._game_foreground = lambda: False
        assert llm_host.ensure_running() is True and warmed == ["eve-fallback"]
        # gaming at startup → warms the SMALL model instead (GPU stays the game's)
        warmed.clear()
        llm_host._game_foreground = lambda: True
        assert llm_host.ensure_running() is True and warmed == ["eve-fallback-mini"]
    finally:
        (llm_host.settings, llm_host._server_up, llm_host._preload,
         llm_host._game_foreground, llm_host._start_game_watcher) = old


def test_game_transition_evicts_and_swaps():
    from core import llm_host
    actions = []
    old = (llm_host._unload, llm_host._preload)
    llm_host._unload = lambda base, model: actions.append(("unload", model))
    llm_host._preload = lambda s, model=None: actions.append(("warm", model or s["model"]))
    try:
        # game starts, preload on → evict main NOW + warm the small CPU model
        llm_host._on_transition(True, _settings(preload=True))
        assert actions == [("unload", "eve-fallback"), ("warm", "eve-fallback-mini")]
        # game starts, preload off → just the eviction
        actions.clear()
        llm_host._on_transition(True, _settings(preload=False))
        assert actions == [("unload", "eve-fallback")]
        # game ends, preload on → main warms back up
        actions.clear()
        llm_host._on_transition(False, _settings(preload=True))
        assert actions == [("warm", "eve-fallback")]
        # game ends, preload off → nothing (lazy load on next use)
        actions.clear()
        llm_host._on_transition(False, _settings(preload=False))
        assert actions == []
    finally:
        llm_host._unload, llm_host._preload = old


def test_swap_root_strips_v1():
    from core.llm_host import _swap_root
    assert _swap_root("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080"
    assert _swap_root("http://127.0.0.1:8080/v1/") == "http://127.0.0.1:8080"
    assert _swap_root("http://127.0.0.1:9000") == "http://127.0.0.1:9000"


def test_generate_config_discovers_and_substitutes():
    import tempfile
    from core import llm_host
    out = os.path.join(tempfile.mkdtemp(), "llama-swap.yaml")
    old_find, old_settings = llm_host.find_llama_server, llm_host.settings
    llm_host.find_llama_server = lambda: r"C:\fake\llama-server.exe"
    llm_host.settings = lambda: _settings()            # gpu: True default
    try:
        path = llm_host.generate_config(out)
        assert path == out
        text = open(out, encoding="utf-8").read()
        assert r"C:\fake\llama-server.exe" in text
        assert "-ngl 99" in text                       # gpu on
        assert "{{" not in text                        # no leftover markers
        assert "eve-fallback" in text
        # never overwrites: a second call returns the path untouched
        with open(out, "w", encoding="utf-8") as f:
            f.write("user edited")
        assert llm_host.generate_config(out) == out
        assert open(out, encoding="utf-8").read() == "user edited"
        # gpu off → no -ngl anywhere
        out2 = os.path.join(tempfile.mkdtemp(), "llama-swap.yaml")
        llm_host.settings = lambda: _settings(gpu=False)
        llm_host.generate_config(out2)
        assert "-ngl" not in open(out2, encoding="utf-8").read()
    finally:
        llm_host.find_llama_server, llm_host.settings = old_find, old_settings


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
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
