import io
import queue
import threading
import wave

import numpy as np
import sounddevice as sd
from piper.voice import PiperVoice

from config import TTS_VOICE_MODEL, TTS_SPEED


class Speaker:
    def __init__(self):
        self._q:          queue.Queue     = queue.Queue()
        self.is_speaking: threading.Event = threading.Event()
        self.enabled:     bool            = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        voice = PiperVoice.load(str(TTS_VOICE_MODEL))
        voice.config.length_scale = 1.0 / TTS_SPEED  # speed lives on config, not synthesize()
        while True:
            text, done = self._q.get()
            try:
                if text:
                    self.is_speaking.set()
                    try:
                        buf = io.BytesIO()
                        with wave.open(buf, 'wb') as wf:
                            voice.synthesize(text, wf)
                        buf.seek(0)
                        with wave.open(buf) as wf:
                            frames = wf.readframes(wf.getnframes())
                            rate   = wf.getframerate()
                        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                        sd.play(audio, rate)
                        sd.wait()
                    except Exception as e:
                        print(f"TTS error: {e}")
                    finally:
                        self.is_speaking.clear()
            finally:
                done.set()  # always unblock speak() callers even if synthesis fails

    def speak(self, text: str):
        if not self.enabled or not text:
            return
        done = threading.Event()
        self._q.put((text, done))
        done.wait()
