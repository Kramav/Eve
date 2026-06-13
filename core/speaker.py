import queue
import threading

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
        voice.config.length_scale = 1.0 / TTS_SPEED
        while True:
            text, done = self._q.get()
            try:
                if text:
                    self.is_speaking.set()
                    try:
                        chunks = list(voice.synthesize(text))
                        if chunks:
                            audio = np.concatenate([c.audio_float_array for c in chunks])
                            sd.play(audio, chunks[0].sample_rate)
                            sd.wait()
                    except Exception as e:
                        print(f"TTS error: {e}")
                    finally:
                        self.is_speaking.clear()
            finally:
                done.set()

    def speak(self, text: str):
        if not self.enabled or not text:
            return
        done = threading.Event()
        self._q.put((text, done))
        done.wait()
