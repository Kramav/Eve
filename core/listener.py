import queue
import threading
import sounddevice as sd
import numpy as np
from openwakeword.model import Model
from config import WAKE_WORD, SILENCE_THRESHOLD, SILENCE_DURATION_S

SAMPLE_RATE = 16000
CHUNK_SIZE = 1280  # 80ms — required by openwakeword


class Listener:
    _MAX_RECORD_CHUNKS = 375  # ~30 second hard cap

    def __init__(self):
        print(f"Loading wake word model ({WAKE_WORD})...")
        self._model        = Model(wakeword_models=[WAKE_WORD], inference_framework="onnx")
        self._q:           queue.Queue    = queue.Queue()
        self._is_speaking: threading.Event | None = None
        self.enabled                              = True

    def _callback(self, indata, frames, time_info, status):
        self._q.put(indata.copy())

    def set_speaking_event(self, event: threading.Event) -> None:
        """Wire up the speaker's is_speaking flag so wake-word detection
        is suppressed while TTS is playing (prevents mic feedback loops)."""
        self._is_speaking = event

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def run(self, on_wake=None, on_command=None):
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=CHUNK_SIZE, callback=self._callback):
            while True:
                try:
                    self._drain()
                    self._wait_for_wake_word()
                    if on_wake:
                        on_wake()
                    audio = self._record_command()
                    if on_command and len(audio) > 0:
                        on_command(audio)
                except Exception as e:
                    print(f"Listener error (continuing): {e}")
                    self._drain()

    def _drain(self):
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def _wait_for_wake_word(self):
        while True:
            chunk = self._q.get().flatten()
            if not self.enabled:
                continue  # drain mic while disabled; re-enable resumes immediately
            if self._is_speaking and self._is_speaking.is_set():
                continue  # drain mic while TTS is playing
            if self._model.predict(chunk).get(WAKE_WORD, 0) > 0.5:
                return

    def _record_command(self) -> np.ndarray:
        silence_limit = int(SILENCE_DURATION_S * SAMPLE_RATE / CHUNK_SIZE)
        frames = []
        silence_count = 0

        while len(frames) < self._MAX_RECORD_CHUNKS:
            try:
                chunk = self._q.get(timeout=0.5)
            except queue.Empty:
                break

            flat = chunk.flatten()
            frames.append(flat.astype(np.float32) / 32768.0)

            if np.abs(flat).mean() < SILENCE_THRESHOLD:
                silence_count += 1
                if silence_count >= silence_limit:
                    break
            else:
                silence_count = 0

        return np.concatenate(frames) if frames else np.array([])
