"""Vision backend cascade — pure logic against fakes.

No OCR, ONNX, network, or real screenshot: backends are faked, image helpers
use a tiny in-memory PIL image. Covers cascade order, availability filtering,
key resolution, model-JSON parsing + coordinate scaling, and phash stability.

    pytest tests/        |        python tests/test_vision.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands import vision as V


class _FakeBackend:
    def __init__(self, name, els, avail=True):
        self.name, self._els, self._avail = name, els, avail
    def available(self):
        return self._avail
    def elements(self, img):
        return self._els


# ── Cascade ────────────────────────────────────────────────────────────────

def test_cascade_returns_first_nonempty(monkeypatch):
    a = _FakeBackend("a", None)                      # available but yields nothing
    b = _FakeBackend("b", [V._el("Hit", "text", 0, 0, 10, 10)])
    c = _FakeBackend("c", [V._el("Late", "text", 0, 0, 10, 10)])
    monkeypatch.setattr(V, "_make", lambda n: {"a": a, "b": b, "c": c}[n])
    out = V.detect(img=object(), backends=["a", "b", "c"])
    assert len(out) == 1 and out[0]["label"] == "Hit"


def test_cascade_skips_unavailable(monkeypatch):
    a = _FakeBackend("a", [V._el("Skip", "text", 0, 0, 10, 10)], avail=False)
    b = _FakeBackend("b", [V._el("Use", "text", 0, 0, 10, 10)])
    monkeypatch.setattr(V, "_make", lambda n: {"a": a, "b": b}[n])
    out = V.detect(img=object(), backends=["a", "b"])
    assert out[0]["label"] == "Use"


def test_available_backends_filters(monkeypatch):
    a = _FakeBackend("a", None, avail=False)
    b = _FakeBackend("b", [1])
    monkeypatch.setattr(V, "_make", lambda n: {"a": a, "b": b}[n])
    monkeypatch.setattr(V.config, "VISION_BACKENDS", ["a", "b"])
    assert V.available_backends() == ["b"]


# ── Key resolution ───────────────────────────────────────────────────────────

def test_vision_key_settings_then_env(monkeypatch):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"api_keys": {"anthropic": "sk-from-settings"}}, f)
        path = f.name
    try:
        monkeypatch.setattr(V, "_SETTINGS_FILE", __import__("pathlib").Path(path))
        assert V.vision_key("anthropic") == "sk-from-settings"
        # openai not in settings → falls back to config env attr
        monkeypatch.setattr(V.config, "OPENAI_API_KEY", "sk-env", raising=False)
        assert V.vision_key("openai") == "sk-env"
    finally:
        os.unlink(path)


# ── Model JSON parsing + coordinate scaling ─────────────────────────────────

def test_parse_json_array_tolerates_fences():
    assert V._parse_json_array('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert V._parse_json_array('here you go: [1, 2] done') == [1, 2]
    assert V._parse_json_array("not json") is None


def test_elements_from_model_scales_coordinates():
    items = [{"label": "Link", "type": "link", "x": 10, "y": 20, "w": 30, "h": 40}]
    els = V._elements_from_model(items, scale=2.0)
    assert els[0]["bounds"] == (20, 40, 60, 80)
    assert els[0]["center"] == (50, 80)        # x+w/2 scaled = 20+30
    # Malformed rows are dropped, not fatal.
    assert V._elements_from_model([{"label": "x"}], 1.0) is None


def test_el_center():
    e = V._el("L", "text", 100, 200, 40, 20)
    assert e["center"] == (120, 210)


# ── Key tester + imports ─────────────────────────────────────────────────────

def test_test_key_no_key():
    assert V.test_key("anthropic", "")["ok"] is False
    assert V.test_key("openai", "")["ok"] is False
    assert V.test_key("bogus", "x")["ok"] is False


def test_display_integrations_imports():
    # core.display routes integrations:test_<service> to vision.test_key for the
    # cloud services — confirm the wiring imports cleanly (no cycle, names exist).
    import core.display  # noqa: F401
    assert hasattr(core.display.Display, "_test_api_key")
    assert hasattr(core.display.Display, "_setup_status")
    assert hasattr(core.display.Display, "_integrations_full")
    assert callable(V.test_key)


def test_install_integration_mapping():
    # One-click installers exist for the pip-based tiers; unknown service is a
    # clean decline (no subprocess run). Don't trigger a real install here.
    import core.display
    D = core.display.Display
    assert {"rapidocr", "uiautomation"} <= set(D._INSTALLERS)

    class _S:                                    # stand-in self (carries the map)
        _INSTALLERS = D._INSTALLERS
    res = D._install_integration(_S(), "ollama")  # system installer → no pip run
    assert res["ok"] is False and "guide" in res["message"].lower()


def test_setup_status_shape():
    # The Integrations panel reads tool-readiness from here. _setup_status uses
    # no instance state, so call it on the class (self unused). May briefly ping
    # Ollama (1.5s timeout) → 'not detected' when absent.
    import core.display
    st = core.display.Display._setup_status(None)
    assert {"uiautomation", "rapidocr", "ollama"} <= set(st)
    for entry in st.values():
        assert "ready" in entry and isinstance(entry["ready"], bool)


# ── Image helpers (tiny in-memory PIL image) ────────────────────────────────

def test_downscale_and_phash():
    from PIL import Image
    big = Image.new("RGB", (2000, 1000), (123, 50, 7))
    sent, scale = V._downscale(big, 1280)
    assert sent.width == 1280
    assert abs(scale - 2000 / 1280) < 1e-6     # maps sent px → original px
    # Identical images hash equal; a different image hashes differently.
    other = Image.new("RGB", (2000, 1000), (0, 0, 0))
    assert V.phash(big) == V.phash(Image.new("RGB", (2000, 1000), (123, 50, 7)))
    assert V.phash(big) != V.phash(other)


# ── Zero-dependency runner (with a minimal monkeypatch shim) ─────────────────

if __name__ == "__main__":
    import inspect

    class _MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val, raising=True):
            had = hasattr(obj, name)
            self._undo.append((obj, name, getattr(obj, name, None), had))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val, had in reversed(self._undo):
                if had:
                    setattr(obj, name, val)
                else:
                    try: delattr(obj, name)
                    except Exception: pass

    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        mp = _MP()
        try:
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
