from pathlib import Path

WAKE_WORD = "hey_jarvis"   # pre-trained model name from openwakeword
WHISPER_MODEL = "small.en"  # tiny.en (fastest) / base.en / small.en (most accurate)
SILENCE_THRESHOLD = 800    # mic amplitude to consider silence (0–32768); raise if recording runs long
SILENCE_DURATION_S = 1.5   # seconds of silence to stop recording

# Piper TTS — place model files in the models/ folder at the project root.
# Download: en_US-lessac-medium.onnx + en_US-lessac-medium.onnx.json (~63 MB)
# Other voices: https://huggingface.co/rhasspy/piper-voices
TTS_VOICE_MODEL = Path(__file__).parent / "models" / "en_US-lessac-medium.onnx"
TTS_SPEED       = 1.0   # speech rate: 1.0 = normal, 0.8 = slower, 1.2 = faster

# Custom commands are managed via editor.py — no need to edit here.
