import numpy as np
from faster_whisper import WhisperModel
from config import WHISPER_MODEL

_NOISE = {"", "[blank_audio]", "[music]", "(ambient noise)"}


class Transcriber:
    def __init__(self):
        print(f"Loading Whisper ({WHISPER_MODEL})...")
        self._model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) == 0:
            return ""
        segments, _ = self._model.transcribe(
            audio, beam_size=5, language="en", vad_filter=True
        )
        text = " ".join(s.text.strip() for s in segments).strip().lower()
        return "" if text in _NOISE else text
