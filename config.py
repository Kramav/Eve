WAKE_WORD = "hey_jarvis"   # pre-trained model name from openwakeword
WHISPER_MODEL = "small.en"  # tiny.en (fastest) / base.en / small.en (most accurate)
TTS_RATE = 175             # words per minute
SILENCE_THRESHOLD = 800    # mic amplitude to consider silence (0–32768); raise if recording runs long
SILENCE_DURATION_S = 1.5   # seconds of silence to stop recording

# Custom commands are managed via editor.py — no need to edit here.
