import os
from pathlib import Path

WAKE_WORD = "hey_jarvis"   # pre-trained model name from openwakeword
WHISPER_MODEL = "small.en"  # tiny.en (fastest) / base.en / small.en (most accurate)
SILENCE_THRESHOLD = 800    # mic amplitude to consider silence (0–32768); raise if recording runs long
SILENCE_DURATION_S = 2.5   # seconds of silence to stop recording (raise to tolerate longer mid-sentence pauses; lower for snappier end-of-speech)

# After Eve answers, ignore the wake word for this long so its own TTS / room
# echo can't falsely re-trigger "Hey Jarvis" and record your next words.
# Lower it if re-waking feels sluggish; raise it if echo still bounces it back.
WAKE_COOLDOWN_S = 1.0

# Piper TTS — drop .onnx + .onnx.json pairs into models/voices/ (any number).
# Download voices: https://huggingface.co/rhasspy/piper-voices
TTS_VOICES_DIR    = Path(__file__).parent / "models" / "voices"
TTS_DEFAULT_VOICE = "en_US-lessac-medium"   # filename stem (without .onnx)
TTS_SPEED         = 1.0   # speech rate: 1.0 = normal, 0.8 = slower, 1.2 = faster

# Web search — DuckDuckGo is primary (free, no key). Brave is used only as a
# fallback when DDG returns nothing, to conserve the free tier's monthly quota.
#
# You normally DON'T need to touch this: set the key from the "API Keys" panel
# (say "open API keys", or open it from the routing directory). That saves to
# settings.json and takes priority. This env var is just an optional override
# for headless/CI setups. Get a free key at https://brave.com/search/api/.
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

# Custom commands are managed via editor.py — no need to edit here.
