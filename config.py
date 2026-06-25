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
# optionally answer with a local Ollama model instead of "Not recognized".
#   "ollama" → POST to OLLAMA_HOST; "none" → keep the plain not-recognized reply.
# Requires Ollama running locally (https://ollama.com) with the model pulled:
#   ollama pull llama3
FALLBACK_LLM   = os.environ.get("FALLBACK_LLM", "none")   # "ollama" | "none"
OLLAMA_HOST    = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL   = os.environ.get("OLLAMA_MODEL", "llama3")

# Custom commands are managed via editor.py — no need to edit here.
