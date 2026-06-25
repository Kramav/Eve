"""Tests for post-execution verification — core.verify.resolve(), the Verified
wrapper, the adaptive app-launch delay learning, and the printer-control
verifiers. No real OS actions or network: handlers are driven with fakes and the
check delays are zeroed so the suite stays fast and deterministic.

Run either way:
    pytest tests/
    python tests/test_verify.py
"""
import importlib.util
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.response import Silent, Verified
from core import verify


# ── generic resolver ──────────────────────────────────────────────────────────

def test_non_verified_passes_through():
    assert verify.resolve("hello") == ("hello", True)
    s = Silent("status only")
    msg, ok = verify.resolve(s)
    assert msg is s and ok is True


def test_confirmed_first_try_does_not_retry():
    calls = {"check": 0, "retry": 0}

    def check():
        calls["check"] += 1
        return True

    v = Verified("Opened Foo", check=check, on_fail="nope",
                 retry=lambda: calls.__setitem__("retry", calls["retry"] + 1),
                 delay=0)
    assert verify.resolve(v) == ("Opened Foo", True)
    assert calls["retry"] == 0, "should not retry once already confirmed"


def test_retry_once_then_confirmed():
    calls = {"check": 0, "retry": 0}

    def check():
        calls["check"] += 1
        return calls["check"] >= 2          # fails first, passes after retry

    def retry():
        calls["retry"] += 1

    v = Verified("ok", check=check, on_fail="nope", retry=retry, delay=0)
    assert verify.resolve(v) == ("ok", True)
    assert calls["retry"] == 1


def test_never_confirmed_reports_failure_and_retries_once():
    calls = {"retry": 0}
    v = Verified("ok", check=lambda: False, on_fail="couldn't confirm",
                 retry=lambda: calls.__setitem__("retry", calls["retry"] + 1),
                 delay=0)
    assert verify.resolve(v) == ("couldn't confirm", False)
    assert calls["retry"] == 1, "retry-once policy"


def test_failure_without_retry_callable():
    v = Verified("ok", check=lambda: False, on_fail="nope", delay=0)
    assert verify.resolve(v) == ("nope", False)


def test_check_that_raises_counts_as_unconfirmed():
    def boom():
        raise RuntimeError("backend down")

    v = Verified("ok", check=boom, on_fail="nope", delay=0)
    assert verify.resolve(v) == ("nope", False)


# ── adaptive app-launch delay learning ────────────────────────────────────────

def test_app_delay_bumps_decays_and_clamps():
    from commands import apps
    tmp = os.path.join(tempfile.mkdtemp(), "delays.json")
    orig = apps._LAUNCH_FILE
    apps._LAUNCH_FILE = type(orig)(tmp)
    try:
        # default before anything is learned
        assert apps._launch_delay("foo") == apps._BASE_DELAY

        # a slow double-launch bumps the wait
        apps._record_slow("foo")
        assert apps._launch_delay("foo") == apps._BASE_DELAY + apps._BUMP_STEP

        # a clean first-try success trims it back
        apps._record_fast("foo")
        assert apps._launch_delay("foo") == (
            apps._BASE_DELAY + apps._BUMP_STEP - apps._DECAY_STEP)

        # never decays below the base
        for _ in range(50):
            apps._record_fast("foo")
        assert apps._launch_delay("foo") == apps._BASE_DELAY

        # never grows past the cap
        for _ in range(50):
            apps._record_slow("foo")
        assert apps._launch_delay("foo") == apps._MAX_DELAY
    finally:
        apps._LAUNCH_FILE = orig


def test_close_app_returns_verified():
    # Closing a name nothing is running under is harmless; the verifier confirms
    # immediately because no such process exists.
    from commands import apps
    r = apps.close_app("definitely-not-a-real-app-zzz")
    assert isinstance(r, Verified)
    r.delay = 0
    msg, ok = verify.resolve(r)
    assert ok and "Closed" in msg


# ── feature flag ──────────────────────────────────────────────────────────────

def test_verify_feature_flag_defaults_on():
    from core import features
    assert features.DEFAULTS.get("verify_commands") is True
    assert "verify_commands" in features.LABELS


# ── printer-control verifiers ─────────────────────────────────────────────────

def _printer_mod():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "skills", "3dprinter.py")
    spec = importlib.util.spec_from_file_location("eve_skill_3dprinter_vtest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakePrinter:
    """Minimal backend: pause/resume/cancel flip a state; status reports it."""
    def __init__(self, state):
        self.state = state
        self.calls = {"pause": 0, "resume": 0, "cancel": 0}

    def pause(self):  self.calls["pause"] += 1;  self.state = "paused"
    def resume(self): self.calls["resume"] += 1; self.state = "printing"
    def cancel(self): self.calls["cancel"] += 1; self.state = "idle"

    def status(self):
        return {"state": self.state}


def _resolve_fast(resp):
    resp.delay = 0                      # skip the real 2s printer settle wait
    return verify.resolve(resp)


def test_printer_pause_confirms_state_change():
    p = _printer_mod()
    fake = _FakePrinter("printing")
    p._backend = lambda: fake
    r = p._pause()
    assert isinstance(r, Verified)
    msg, ok = _resolve_fast(r)
    assert ok and "Paused" in msg
    assert fake.calls["pause"] == 1, "confirmed first try — no retry"


def test_printer_pause_failure_retries_then_reports():
    p = _printer_mod()

    class Stuck(_FakePrinter):
        def pause(self):                 # accepts the command but never pauses
            self.calls["pause"] += 1

    stuck = Stuck("printing")
    p._backend = lambda: stuck
    r = p._pause()
    msg, ok = _resolve_fast(r)
    assert not ok and "still" in msg.lower()
    assert stuck.calls["pause"] == 2, "initial send + one retry"


def test_printer_cancel_confirms_not_printing():
    p = _printer_mod()
    fake = _FakePrinter("printing")
    p._backend = lambda: fake
    # _do_cancel is what the yes/no confirmation actually runs.
    r = p._do_cancel()
    assert isinstance(r, Verified)
    msg, ok = _resolve_fast(r)
    assert ok and "Cancel" in msg
    assert fake.state != p.PRINTING


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
