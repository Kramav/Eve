"""Vision backends for the Visual Navigation skill (Phase 2).

When the accessibility tier (UI Automation) can't read a window's interactive
elements, the skill's VisionProvider asks this module to detect them from a
single on-demand screenshot. NO continuous computer vision.

A cascade of backends, tried cheapest-first (order from config.VISION_BACKENDS):

  "rapidocr" → OCR text boxes on CPU (no GPU, no key, no network). DEFAULT.
  "onnx_ui"  → small ONNX UI-element detector for icon/no-text buttons (CPU).
  "claude" / "gpt" → cloud multimodal (off-machine compute, needs an API key).
  "ollama"   → local Ollama vision model (needs a GPU to be usable).

Every backend is duck-typed: `available() -> bool` and
`elements(img) -> list | None`, where `img` is a full-resolution PIL screenshot
and the returned elements are in SCREEN coordinates:

    {"label", "type", "bounds": (x, y, w, h), "center": (cx, cy), "confidence"}

Each backend's heavy import is lazy + guarded, so a missing dependency yields
None (the cascade falls through) instead of crashing the skill.

Self-checks live in tests/test_vision.py (cascade order, key resolution, JSON
parsing, scaling, phash — all against fakes; no OCR/ONNX/network).
"""
import base64
import io
import json
import os
import re
import urllib.error
import urllib.request

import config

_TIMEOUT_S = 30
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Cost-effective vision-capable defaults (overridable). Haiku 4.5 is $1/$5 per
# MTok — right for a high-frequency per-screenshot fallback.
_CLAUDE_VISION_MODEL = os.environ.get("EVE_CLOUD_VISION_MODEL", "claude-haiku-4-5")
_OPENAI_VISION_MODEL = os.environ.get("EVE_OPENAI_VISION_MODEL", "gpt-4o-mini")

_SETTINGS_FILE = __import__("pathlib").Path(__file__).parent.parent / "settings.json"

_PROMPT = (
    "This is a {w}x{h} pixel screenshot. List the interactive elements a user "
    "could click — links, buttons, videos, list items, tabs, search results. "
    "Respond with ONLY a JSON array, no prose, each item: "
    '{{"label": "<visible text>", "type": "<link|button|video|item|tab>", '
    '"x": <int>, "y": <int>, "w": <int>, "h": <int>}} where x,y,w,h are the '
    "element's pixel bounding box in this image. Cap at 40 items."
)


# ── Screenshot + image helpers ───────────────────────────────────────────────

def grab():
    """Full-resolution screenshot as a PIL image, or None if unavailable."""
    try:
        import pyautogui
        return pyautogui.screenshot()
    except Exception:
        return None


def to_base64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _downscale(img, max_w: int):
    """Return (image, scale) where scale maps sent-image px → original px."""
    if img.width <= max_w:
        return img, 1.0
    ratio = max_w / float(img.width)
    new = img.resize((max_w, int(img.height * ratio)))
    return new, 1.0 / ratio          # multiply model coords by this → screen px


def phash(img) -> str:
    """Cheap perceptual hash (8x8 grayscale) for screen-change detection."""
    try:
        import numpy as np
        from hashlib import md5
        thumb = img.resize((8, 8)).convert("L")
        return md5(np.asarray(thumb).tobytes()).hexdigest()
    except Exception:
        return ""


def _el(label, type_, x, y, w, h, conf=1.0):
    x, y, w, h = int(x), int(y), int(w), int(h)
    return {"label": str(label).strip()[:80], "type": type_,
            "bounds": (x, y, w, h), "center": (x + w // 2, y + h // 2),
            "confidence": float(conf)}


def _parse_json_array(text: str):
    """Pull a JSON array out of a model's text response (tolerates ``` fences)."""
    if not text:
        return None
    text = text.strip()
    if "[" in text and "]" in text:
        text = text[text.index("["): text.rindex("]") + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else None
    except Exception:
        return None


def _elements_from_model(items, scale: float):
    """Normalize a model's [{label,type,x,y,w,h}] into screen-space elements."""
    out = []
    for it in items or []:
        try:
            label = it.get("label") or it.get("text") or ""
            x, y = float(it["x"]) * scale, float(it["y"]) * scale
            w, h = float(it["w"]) * scale, float(it["h"]) * scale
            if label and w > 0 and h > 0:
                out.append(_el(label, it.get("type", "item"), x, y, w, h,
                               float(it.get("confidence", 0.9))))
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


# ── Set-of-marks (precise boxes from OCR, semantics from the model) ──────────
# Vision LLMs hallucinate pixel coordinates. Instead of asking the model for
# boxes, we get candidate boxes from OCR (pixel-accurate), draw NUMBERED marks on
# the screenshot, and ask the model only which numbers are clickable + what they
# are. Geometry stays exact; the model just filters + labels.

_SOM_PROMPT = (
    "Each candidate UI element in this screenshot is outlined by a numbered red "
    "box. Return ONLY a JSON array of the ones a user could click (links, "
    "buttons, videos, list items, tabs) — skip plain body text and labels. "
    'Each item: {"n": <the box number>, "label": "<short name>", '
    '"type": "<link|button|video|item|tab>"}. Cap at 40.'
)


def _candidate_boxes(img, cap=40):
    """Geometry-only candidate boxes (pixel-accurate) from OCR. Empty when OCR is
    unavailable or finds nothing — caller then falls back to direct detection."""
    ocr = OcrBackend()
    try:
        if not ocr.available():
            return []
        els = ocr.elements(img) or []
    except Exception:
        return []
    return [e["bounds"] for e in els[:cap]]


def _mark_image(img, boxes):
    """Draw a numbered red box over each candidate; returns a new PIL image."""
    try:
        from PIL import ImageDraw
        out = img.copy()
        d = ImageDraw.Draw(out)
        for i, (x, y, w, h) in enumerate(boxes, 1):
            d.rectangle([x, y, x + w, y + h], outline=(255, 40, 40), width=2)
            d.text((x + 3, y + 1), str(i), fill=(255, 40, 40))
        return out
    except Exception:
        return img


def _elements_from_marks(items, boxes):
    """Map the model's picks ({n,label,type}) back to candidate box geometry, so
    coordinates come from OCR (precise) and only labels come from the model."""
    out = []
    for it in items or []:
        try:
            n = int(it["n"])
            if 1 <= n <= len(boxes):
                x, y, w, h = boxes[n - 1]
                out.append(_el(it.get("label") or f"item {n}", it.get("type", "item"),
                               x, y, w, h, float(it.get("confidence", 0.9))))
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def _model_detect(img, call):
    """Multimodal detection. SET-OF-MARKS when OCR yields candidate boxes
    (numbered overlay → model picks by number → pixel-accurate boxes); otherwise
    ask the model for boxes directly. `call` is (b64_png, prompt) -> text|None."""
    boxes = _candidate_boxes(img)
    if boxes:
        marked, _ = _downscale(_mark_image(img, boxes), 1280)
        els = _elements_from_marks(
            _parse_json_array(call(to_base64(marked), _SOM_PROMPT)), boxes)
        if els:
            return els
    # No candidate boxes (or set-of-marks returned nothing) → direct detection.
    sent, scale = _downscale(img, 1280)
    text = call(to_base64(sent), _PROMPT.format(w=sent.width, h=sent.height))
    return _elements_from_model(_parse_json_array(text), scale)


# ── API-key resolution (mirrors commands.search.brave_key) ───────────────────

def vision_key(service: str) -> str:
    """settings.json api_keys.<service> → config env var. '' if unset."""
    try:
        data = json.loads(_SETTINGS_FILE.read_text())
        k = (data.get("api_keys") or {}).get(service) or ""
        if k.strip():
            return k.strip()
    except Exception:
        pass
    env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(service)
    if env:
        try:
            return (getattr(config, env, "") or "").strip()
        except Exception:
            return ""
    return ""


def test_key(service: str, key: str = None) -> dict:
    """Validate a cloud-vision API key with a minimal request. {ok, message}."""
    key = (key or vision_key(service)).strip()
    if not key:
        return {"ok": False, "message": "No key provided."}
    if service == "anthropic":
        url, headers = _ANTHROPIC_URL, {"x-api-key": key, "anthropic-version": "2023-06-01"}
        body = {"model": _CLAUDE_VISION_MODEL, "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}]}
    elif service == "openai":
        url, headers = _OPENAI_URL, {"Authorization": f"Bearer {key}"}
        body = {"model": _OPENAI_VISION_MODEL, "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}]}
    else:
        return {"ok": False, "message": f"Unknown service {service}."}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            resp.read()
        return {"ok": True, "message": "Key works — cloud vision is ready."}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "message": "Key rejected (invalid or unauthorized)."}
        if e.code == 429:
            return {"ok": True, "message": "Key valid (rate-limited right now)."}
        return {"ok": False, "message": f"HTTP {e.code} from {service}."}
    except Exception:
        return {"ok": False, "message": f"Couldn't reach {service}."}


def _http_post(url: str, headers: dict, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


# ── Backends ─────────────────────────────────────────────────────────────────

class OcrBackend:
    """Tier 1 default — RapidOCR text boxes on CPU. No GPU, no key, no network."""
    name = "rapidocr"
    _engine = None

    def available(self) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except Exception:
            return False

    def _get_engine(self):
        if OcrBackend._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            OcrBackend._engine = RapidOCR()
        return OcrBackend._engine

    def elements(self, img):
        try:
            import numpy as np
            result, _ = self._get_engine()(np.asarray(img))
            if not result:
                return None
            out = []
            for box, text, score in result:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x, y = min(xs), min(ys)
                w, h = max(xs) - x, max(ys) - y
                if (text or "").strip() and w > 0 and h > 0:
                    out.append(_el(text, "text", x, y, w, h, float(score)))
            return out or None
        except Exception:
            return None


class OnnxUiBackend:
    """Tier 2 — ONNX UI-element detector for icon/no-text buttons OCR misses.
    Dormant until a model file is dropped at models/ui_detector.onnx.

    ponytail: inference I/O wiring is filled in when a specific detector model
    is chosen (Phase 2d). available()=False keeps the cascade slot inert today."""
    name = "onnx_ui"
    _MODEL = __import__("pathlib").Path(__file__).parent.parent / "models" / "ui_detector.onnx"

    def available(self) -> bool:
        try:
            import onnxruntime  # noqa: F401
            return self._MODEL.exists()
        except Exception:
            return False

    def elements(self, img):
        return None


class CloudVisionBackend:
    """Tier 3 — cloud multimodal (Claude / GPT) via stdlib urllib. Off-machine
    compute, ideal for weak hardware; needs an API key. Off by default."""

    def __init__(self, service="claude"):
        self.service = service
        self.name = service

    def available(self) -> bool:
        key = vision_key("anthropic" if self.service == "claude" else "openai")
        return bool(key)

    def elements(self, img):
        # Set-of-marks (precise boxes from OCR) when available, else direct.
        return _model_detect(img, self._claude if self.service == "claude" else self._gpt)

    def _claude(self, b64, prompt):
        key = vision_key("anthropic")
        if not key:
            return None
        data = _http_post(_ANTHROPIC_URL, {
            "x-api-key": key, "anthropic-version": "2023-06-01",
        }, {
            "model": _CLAUDE_VISION_MODEL, "max_tokens": 4096,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                    "media_type": "image/png", "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        })
        try:
            return data["content"][0]["text"]
        except (TypeError, KeyError, IndexError):
            return None

    def _gpt(self, b64, prompt):
        key = vision_key("openai")
        if not key:
            return None
        data = _http_post(_OPENAI_URL, {"Authorization": f"Bearer {key}"}, {
            "model": _OPENAI_VISION_MODEL, "max_tokens": 4096,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{b64}"}},
            ]}],
        })
        try:
            return data["choices"][0]["message"]["content"]
        except (TypeError, KeyError, IndexError):
            return None


class OllamaVisionBackend:
    """Tier 4 — local Ollama vision model. Needs a GPU to be usable. Off by
    default (only runs when 'ollama' is in config.VISION_BACKENDS)."""
    name = "ollama"

    def available(self) -> bool:
        return "ollama" in getattr(config, "VISION_BACKENDS", [])

    def elements(self, img):
        def call(b64, prompt):
            host = getattr(config, "OLLAMA_HOST", "http://localhost:11434").rstrip("/")
            data = _http_post(f"{host}/api/generate", {}, {
                "model": getattr(config, "OLLAMA_VISION_MODEL", "moondream"),
                "prompt": prompt, "images": [b64], "stream": False,
            })
            return data.get("response") if data else None
        # Set-of-marks (precise boxes from OCR) when available, else direct.
        return _model_detect(img, call)


def _make(name: str):
    if name in ("claude", "gpt"):
        return CloudVisionBackend(name)
    return {"rapidocr": OcrBackend, "onnx_ui": OnnxUiBackend,
            "ollama": OllamaVisionBackend}.get(name, lambda: None)()


# ── Cascade ──────────────────────────────────────────────────────────────────

def configured_backends():
    return [b for b in (getattr(config, "VISION_BACKENDS", ["rapidocr"]) or []) if b]


def available_backends():
    """Names of configured backends whose deps/keys are actually present."""
    out = []
    for name in configured_backends():
        b = _make(name)
        try:
            if b is not None and b.available():
                out.append(name)
        except Exception:
            pass
    return out


def detect(img=None, backends=None):
    """Run the configured cascade against one screenshot. First backend that
    yields elements wins. Returns [] (never None) so callers can cache."""
    img = img if img is not None else grab()
    if img is None:
        return []
    for name in (backends if backends is not None else configured_backends()):
        b = _make(name)
        if b is None:
            continue
        try:
            if not b.available():
                continue
            els = b.elements(img)
            if els:
                return els
        except Exception:
            continue
    return []
