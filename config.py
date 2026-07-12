import os
from pathlib import Path

WAKE_WORD = "hey_jarvis"   # pre-trained model name from openwakeword

# Spoken wake-prefixes stripped off the front of a transcript before routing
# (Whisper often transcribes the wake word itself). Override via the
# EVE_WAKE_PREFIXES env var as a comma-separated list.
WAKE_PREFIXES = tuple(
    p.strip().lower() for p in
    os.environ.get("EVE_WAKE_PREFIXES", "hey jarvis,hey eve,jarvis,eve").split(",")
    if p.strip()
)

# WebSocket bridge between Python (server) and Electron (clients).
# NOTE: the Electron renderer files hardcode this port in their CSP
# `connect-src` + `WS_URL`; if you change it here, update ui/src/**/*.{js,html}
# and ui/main.js too (ponytail: a fixed port is fine for a localhost-only bridge;
# wire a build-time inject only if a configurable port is ever actually needed).
WS_HOST = os.environ.get("EVE_WS_HOST", "127.0.0.1")
WS_PORT = int(os.environ.get("EVE_WS_PORT", "7734"))

# Speech-recognition model. "auto" picks the best for your hardware:
#   GPU → distil-large-v3  (near top accuracy, ~6× faster than large-v3)
#   CPU → distil-small.en  (fast + accurate for English commands)
# Override with any faster-whisper name: tiny.en / base.en / small.en /
# distil-small.en / distil-medium.en / distil-large-v3 / large-v3 / large-v3-turbo.
WHISPER_MODEL = os.environ.get("EVE_WHISPER_MODEL", "auto")

# Speech-recognition compute device.
#   "auto" → use an NVIDIA GPU if one is present, else CPU (recommended).
#   "cuda" → force GPU (needs a CUDA-capable GPU + cuDNN; much faster).
#   "cpu"  → force CPU.
# WHISPER_COMPUTE picks the numeric precision; "" auto-selects per device
# (float16 on GPU, int8 on CPU). On low-VRAM GPUs try "int8_float16".
WHISPER_DEVICE  = os.environ.get("EVE_WHISPER_DEVICE", "auto")   # auto | cuda | cpu
WHISPER_COMPUTE = os.environ.get("EVE_WHISPER_COMPUTE", "")
SILENCE_THRESHOLD = 800    # mic amplitude to consider silence (0–32768); raise if recording runs long
SILENCE_DURATION_S = 2.5   # seconds of silence to stop recording (raise to tolerate longer mid-sentence pauses; lower for snappier end-of-speech)

# After Eve answers, ignore the wake word for this long so its own TTS / room
# echo can't falsely re-trigger "Hey Jarvis" and record your next words.
# Lower it if re-waking feels sluggish; raise it if echo still bounces it back.
WAKE_COOLDOWN_S = 1.0

# Where "take a screenshot" saves PNGs. Override via EVE_SCREENSHOT_DIR.
SCREENSHOT_DIR    = Path(os.environ.get("EVE_SCREENSHOT_DIR", str(Path.home() / "Desktop")))

# TTS engine: "piper" (default, lightweight) | "kokoro" (much more natural,
# Apache-2.0) | "auto" (use Kokoro if installed + models present, else Piper).
TTS_ENGINE = os.environ.get("EVE_TTS_ENGINE", "piper")

# Piper TTS — drop .onnx + .onnx.json pairs into models/voices/ (any number).
# Download voices: https://huggingface.co/rhasspy/piper-voices
TTS_VOICES_DIR    = Path(__file__).parent / "models" / "voices"
TTS_DEFAULT_VOICE = "en_US-lessac-medium"   # filename stem (without .onnx)
TTS_SPEED         = 1.0   # speech rate: 1.0 = normal, 0.8 = slower, 1.2 = faster

# Kokoro TTS (used when TTS_ENGINE is "kokoro"/"auto"). Setup:
#   pip install kokoro-onnx        (onnxruntime-based, no torch)
#   download two files once into models/kokoro/ from
#   https://github.com/thewh1teagle/kokoro-onnx/releases →
#       kokoro-v1.0.onnx   and   voices-v1.0.bin
_KOKORO_DIR       = Path(__file__).parent / "models" / "kokoro"
TTS_KOKORO_MODEL  = Path(os.environ.get("EVE_KOKORO_MODEL",  str(_KOKORO_DIR / "kokoro-v1.0.onnx")))
TTS_KOKORO_VOICES = Path(os.environ.get("EVE_KOKORO_VOICES", str(_KOKORO_DIR / "voices-v1.0.bin")))
TTS_KOKORO_VOICE  = os.environ.get("EVE_KOKORO_VOICE", "af_heart")  # see kokoro voice list

# Web search — DuckDuckGo is primary (free, no key). Brave is used only as a
# fallback when DDG returns nothing, to conserve the free tier's monthly quota.
#
# You normally DON'T need to touch this: set the key from the "API Keys" panel
# (say "open API keys", or open it from the routing directory). That saves to
# settings.json and takes priority. This env var is just an optional override
# for headless/CI setups. Get a free key at https://brave.com/search/api/.
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

# LLM fallback — when nothing matches an intent and the fuzzy guess is too weak,
# ask a LOCAL model instead of saying "Not recognized" (chat-completions wire
# format = works with llama-swap [default], bare llama-server, Ollama /v1,
# LM Studio; 127.0.0.1 only, nothing leaves the machine).
#
# These are the env-var DEFAULTS; the settings.json "llm" section overrides
# them at runtime (panel-editable, like api_keys/voice) — every knob incl.
# gpu offload, busy-swap policy, model files: see core/llm_host.py DEFAULTS.
# Verified successful tool-calls feed Dynamic Intent Learning (learned_intents.json).
FALLBACK_LLM = os.environ.get("FALLBACK_LLM", "local")    # "local" | "none"
LLM_BASE_URL = os.environ.get("EVE_LLM_URL", "http://127.0.0.1:8080/v1")
LLM_MODEL    = os.environ.get("EVE_LLM_MODEL", "eve-fallback")  # a llama-swap model name

# llama-swap auto-spawn: if the fallback is on but nothing answers at the base
# URL, main.py launches llama-swap (and kills it on exit). The config file is
# GENERATED from llama-swap.example.yaml on first run — llama-server and model
# paths are discovered, never committed. Run llama-swap as a service yourself
# and Eve detects it and skips all of this.
LLAMA_SWAP_EXE    = os.environ.get("EVE_LLAMA_SWAP", "")
LLAMA_SWAP_CONFIG = str(Path(__file__).parent / "llama-swap.yaml")

# Ollama host — still used by the optional "ollama" VISION backend below.
OLLAMA_HOST    = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# ── Visual Navigation skill — vision fallback (commands/vision.py) ────────────
# When the accessibility tier (UI Automation) can't read a window's elements,
# the VisionProvider tries these backends in order, cheapest-first, and uses the
# first that returns anything. Capture is on-demand (one screenshot), never
# continuous CV. Backends:
#   "rapidocr" → OCR text boxes on CPU (no GPU, no key, no network). DEFAULT.
#   "onnx_ui"  → small ONNX UI-element detector for icon/no-text buttons (CPU).
#   "claude" / "gpt" → cloud multimodal; off-machine compute, needs an API key.
#   "ollama"   → local Ollama vision model; needs a GPU to be usable.
# Default is OCR only — heavy/keyed tiers never fire unless you opt in here.
VISION_BACKENDS     = [b.strip() for b in
                       os.environ.get("EVE_VISION_BACKENDS", "rapidocr").split(",")
                       if b.strip()]
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "moondream")
# Cloud-vision keys. Like BRAVE_API_KEY, prefer the API-Keys panel (settings.json
# api_keys.anthropic / .openai); these env vars are headless/CI overrides.
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY", "")

# ── Conversation Engine (opt-in via features.json `conversation_engine`) ──────
# Windows Eve keeps the mic open (no wake word) after a reply. See
# docs/CONVERSATION_ARCHITECTURE.md.
#   FOLLOWUP_TTL — grace window after a normal reply (a natural continuation).
#   AWAITING_TTL — window while waiting for a confirmation / clarification / slot.
#   EXTEND_BY    — added when the user says "hold on" / "one moment".
CONV_FOLLOWUP_TTL = float(os.environ.get("EVE_CONV_FOLLOWUP_TTL", "6"))
CONV_AWAITING_TTL = float(os.environ.get("EVE_CONV_AWAITING_TTL", "12"))
CONV_EXTEND_BY    = float(os.environ.get("EVE_CONV_EXTEND_BY", "20"))

# Custom commands are managed via editor.py — no need to edit here.
