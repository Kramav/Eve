import json
import queue
import threading
import time
from pathlib import Path
import sounddevice as sd
import numpy as np
from openwakeword.model import Model
from config import WAKE_WORD, SILENCE_THRESHOLD, SILENCE_DURATION_S, WAKE_COOLDOWN_S

SAMPLE_RATE = 16000
CHUNK_SIZE = 1280  # 80ms — required by openwakeword

_SETTINGS_FILE = Path(__file__).parent.parent / 'settings.json'


def resolve_wake_word() -> str:
    """User's chosen wake-word model name. Prefers settings.json `wake_word`
    (set from the UI, no config edit needed); falls back to config.WAKE_WORD."""
    try:
        w = json.loads(_SETTINGS_FILE.read_text()).get('wake_word')
        if w and str(w).strip():
            return str(w).strip()
    except Exception:
        pass
    return WAKE_WORD


class Listener:
    _MAX_RECORD_CHUNKS = 375  # ~30 second hard cap

    def __init__(self):
        self._wake_word = resolve_wake_word()
        print(f"Loading wake word model ({self._wake_word})...")
        self._model        = Model(wakeword_models=[self._wake_word], inference_framework="onnx")
        self._q:           queue.Queue    = queue.Queue()
        self._is_speaking: threading.Event | None = None
        self.enabled                              = True
        self._cooldown_until                      = 0.0

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
                    # Refractory window: after responding (incl. TTS), don't let
                    # Eve's own voice / echo re-trigger the wake word.
                    self._cooldown_until = time.monotonic() + WAKE_COOLDOWN_S
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
        # Reset the wake model's audio/prediction buffers before the first
        # detection of a new wait so any TTS/echo captured at the speech
        # boundary can't accumulate into a false trigger.
        reset_pending = True
        while True:
            chunk = self._q.get().flatten()
            if not self.enabled:
                reset_pending = True
                continue  # drain mic while disabled; re-enable resumes immediately
            if self._is_speaking and self._is_speaking.is_set():
                reset_pending = True
                continue  # drain mic while TTS is playing
            if time.monotonic() < self._cooldown_until:
                reset_pending = True
                continue  # drain mic during the post-response refractory window
            if reset_pending:
                self._model.reset()
                reset_pending = False
            if self._model.predict(chunk).get(self._wake_word, 0) > 0.5:
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
