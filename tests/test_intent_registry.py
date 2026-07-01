"""Tests for the Tier-A declarative intent registry (core/intent_registry.py).

The headline property: routing is correct REGARDLESS of registration order —
the exact fragility tests/test_intent_audit.py found in the current wall (every
"open <panel>" phrase also matches apps.open_app and only wins by position).

Run either way:
    pytest tests/
    python tests/test_intent_registry.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.intent_registry import Intent, IntentRegistry, _captured_len


def _panel():   return "panel"
def _openapp(name): return f"open:{name}"
def _snap(app, zone): return f"snap:{app}:{zone}"
def _front(app):    return f"front:{app}"


# ── Order-independence (the whole point) ─────────────────────────────────────

def test_specificity_beats_registration_order():
    r = IntentRegistry()
    # Register the CATCH-ALL FIRST — in the old wall this would shadow the panel.
    r.add(Intent("open_app", _openapp, [r"(?:open|launch)\s+(.+)"]))
    r.add(Intent("open_panel", _panel, [r"open (?:the )?app manager"]))

    it, m = r.best("open app manager")               # panel captures nothing → wins
    assert it.name == "open_panel"
    it, m = r.best("open firefox")                   # only the catch-all matches
    assert it.name == "open_app"


def test_reversed_registration_same_result():
    # Same two intents, opposite insertion order → identical routing.
    r = IntentRegistry()
    r.add(Intent("open_panel", _panel, [r"open (?:the )?app manager"]))
    r.add(Intent("open_app", _openapp, [r"(?:open|launch)\s+(.+)"]))
    assert r.best("open app manager")[0].name == "open_panel"


def test_snap_beats_bare_zorder_by_literal_match():
    # Mirrors the audit's "snap firefox to top" (matches snap AND bare z-order).
    r = IntentRegistry()
    r.add(Intent("zorder", _front, [r"^([\w]+(?:\s+[\w]+){0,2})\s+to\s+(?:front|top)$"]))
    r.add(Intent("snap", _snap, [r"snap\s+(.+?)\s+to\s+([\w-]+)$"]))
    # snap captures "firefox"+"top" (10) < z-order capturing "snap firefox" (12).
    assert r.best("snap firefox to top")[0].name == "snap"


# ── Priority + specificity mechanics ─────────────────────────────────────────

def test_priority_overrides_specificity():
    r = IntentRegistry()
    r.add(Intent("literal", _panel, [r"do the thing"], priority=0))
    r.add(Intent("override", _openapp, [r"do (.+)"], priority=100))  # catch-all but high prio
    assert r.best("do the thing")[0].name == "override"


def test_captured_len_helper():
    import re
    assert _captured_len(re.search(r"a(.+)c", "abbbc")) == 3
    assert _captured_len(re.search(r"literal", "literal")) == 0


# ── Feature gating (injected, decoupled from core.features) ──────────────────

def test_feature_gate_skips_disabled():
    r = IntentRegistry()
    r.add(Intent("gated", _openapp, [r"play (.+)"], feature="youtube"))
    off = {"youtube": False}
    assert r.best("play jazz", feature_get=off.get) is None
    on = {"youtube": True}
    assert r.best("play jazz", feature_get=on.get)[0].name == "gated"


# ── resolve() runs the handler with captured groups ──────────────────────────

def test_resolve_calls_handler_with_groups():
    r = IntentRegistry()
    r.add(Intent("snap", _snap, [r"snap\s+(.+?)\s+to\s+([\w-]+)$"]))
    r.add(Intent("time", lambda: "the time", [r"what time is it"]))
    it, result = r.resolve("snap firefox to top")
    assert result == "snap:firefox:top"
    it, result = r.resolve("what time is it")         # no groups → bare call
    assert result == "the time"
    assert r.resolve("gibberish that matches nothing") is None


# ── Learning metadata (Dynamic Intent Learning groundwork) ───────────────────

def test_learning_metadata_defaults_and_counters():
    it = Intent("x", _panel, [r"x"])
    assert it.source == "builtin" and it.confidence == 1.0
    assert it.successes == 0 and it.failures == 0 and it.last_failure is None
    it.record_success(); it.record_success(); it.record_failure()
    assert it.successes == 2 and it.failures == 1 and it.last_failure is not None
    learned = Intent("y", _panel, [r"y"], source="learned", confidence=0.42)
    assert learned.source == "learned" and learned.confidence == 0.42


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
