"""Tests for Dynamic Intent Learning Phase 1 (core/intent_learning.py).

Covers the whole substrate: the execution verifier, Wilson-lower-bound
confidence, persisted per-intent counts (save → reload), and registry hydration
(builtins stay pinned at 1.0; learned intents get evidence-based confidence).

Run either way:
    pytest tests/
    python tests/test_intent_learning.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.intent_learning import verify, wilson_lower_bound, TrainingStore
from core.intent_registry import Intent, IntentRegistry


def _tmp():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)          # start absent; the store must tolerate a missing file
    return path


# ── verify(): the execution verifier ─────────────────────────────────────────

def test_verify_success_and_failure():
    assert verify("Done", None) is True
    assert verify("Opened Firefox.", None) is True
    assert verify(True, None) is True
    # failures: exception, None, False, or blank string
    assert verify(None, ValueError("boom")) is False
    assert verify(None, None) is False
    assert verify(False, None) is False
    assert verify("   ", None) is False


# ── Wilson lower bound: small-sample-honest confidence ───────────────────────

def test_wilson_zero_trials_is_zero_not_one():
    # A brand-new intent must NOT read as fully confident.
    assert wilson_lower_bound(0, 0) == 0.0


def test_wilson_monotonic_and_bounded():
    assert 0.0 <= wilson_lower_bound(1, 0) < 1.0        # one success ≠ certainty
    # more evidence at the same 100% rate → higher lower bound
    assert wilson_lower_bound(50, 0) > wilson_lower_bound(5, 0)
    # failures drag it down
    assert wilson_lower_bound(5, 5) < wilson_lower_bound(10, 0)
    assert wilson_lower_bound(100, 0) < 1.0


# ── TrainingStore: record → persist → reload ─────────────────────────────────

def test_record_counts_and_confidence():
    s = TrainingStore(_tmp())
    for _ in range(8):
        s.record("open_app", True)
    s.record("open_app", False)
    assert s.counts["open_app"] == [8, 1]
    assert 0.0 < s.confidence("open_app") < 1.0
    assert s.confidence("never_seen") == 0.0


def test_record_result_uses_verifier():
    s = TrainingStore(_tmp())
    s.record_result("go", "Done", None)          # verified success
    s.record_result("go", None, RuntimeError())  # verified failure
    assert s.counts["go"] == [1, 1]


def test_persistence_survives_reload():
    path = _tmp()
    s = TrainingStore(path)
    s.record("timer", True)
    s.record("timer", True)
    s.record("timer", False)
    # a fresh store on the same file sees the same counts
    reloaded = TrainingStore(path)
    assert reloaded.counts["timer"] == [2, 1]
    os.unlink(path)


def test_corrupt_file_degrades_to_empty():
    path = _tmp()
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    s = TrainingStore(path)                        # must not raise
    assert s.counts == {}
    os.unlink(path)


def test_malformed_rows_are_dropped():
    path = _tmp()
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"good": [3, 1], "bad": "oops", "short": [1]}')
    s = TrainingStore(path)
    assert s.counts == {"good": [3, 1]}
    os.unlink(path)


# ── apply_to(): hydrate the registry at startup ──────────────────────────────

def _h():  # noqa: dummy handler
    return "ok"


def test_apply_to_hydrates_counts_but_pins_builtin_confidence():
    reg = IntentRegistry()
    reg.add(Intent("builtin_one", _h, [r"foo"], source="builtin"))
    reg.add(Intent("learned_one", _h, [r"bar"], source="learned", confidence=0.0))

    s = TrainingStore(_tmp())
    for _ in range(20):
        s.record("builtin_one", True)
    for _ in range(20):
        s.record("learned_one", True)
    s.apply_to(reg)

    by_name = {it.name: it for it in reg.all()}
    # counts copied onto both
    assert by_name["builtin_one"].successes == 20
    assert by_name["learned_one"].successes == 20
    # builtin confidence stays pinned at 1.0; learned rises from evidence
    assert by_name["builtin_one"].confidence == 1.0
    assert 0.0 < by_name["learned_one"].confidence < 1.0


def test_apply_to_ignores_unknown_intents():
    reg = IntentRegistry()
    reg.add(Intent("known", _h, [r"x"]))
    s = TrainingStore(_tmp())
    s.record("known", True)
    s.record("ghost", True)          # no such intent in the registry
    s.apply_to(reg)                  # must not raise
    assert reg.all()[0].successes == 1


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
