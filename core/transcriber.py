import numpy as np
from faster_whisper import WhisperModel
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE

_NOISE = {"", "[blank_audio]", "[music]", "(ambient noise)"}


def _resolve_device_compute() -> tuple[str, str]:
    """Pick (device, compute_type) from config. 'auto' probes for an NVIDIA GPU
    via CTranslate2 (faster-whisper's backend) and falls back to CPU. compute
    defaults to float16 on GPU / int8 on CPU unless WHISPER_COMPUTE overrides."""
    device = (WHISPER_DEVICE or "auto").lower()
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    compute = WHISPER_COMPUTE.strip() or ("float16" if device == "cuda" else "int8")
    return device, compute


def _resolve_model(device: str) -> str:
    """Resolve WHISPER_MODEL. 'auto' picks the best model for the device:
    distil-large-v3 on GPU (top accuracy, fast), distil-small.en on CPU."""
    m = (WHISPER_MODEL or "auto").strip()
    if m.lower() == "auto":
        return "distil-large-v3" if device == "cuda" else "distil-small.en"
    return m


class Transcriber:
    def __init__(self):
        device, compute = _resolve_device_compute()
        model = _resolve_model(device)
        print(f"Loading Whisper ({model}) on {device}/{compute}...")
        try:
            self._model = WhisperModel(model, device=device, compute_type=compute)
        except Exception as e:
            # cuDNN missing, driver mismatch, etc. — degrade to CPU rather than
            # failing to start. Forced "cpu" that fails re-raises (real problem).
            if device == "cpu":
                raise
            print(f"Whisper GPU init failed ({e}); falling back to CPU.")
            self._model = WhisperModel(_resolve_model("cpu"), device="cpu",
                                       compute_type="int8")

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) == 0:
            return ""
        # Tuned for short, independent command utterances:
        #   condition_on_previous_text=False → no context bleed / hallucination
        #     loops between separate commands.
        #   temperature=0 → deterministic, fastest decode (no fallback sampling).
        #   vad_filter trims leading/trailing silence so decode is shorter.
        segments, _ = self._model.transcribe(
            audio, beam_size=5, language="en", vad_filter=True,
            condition_on_previous_text=False, temperature=0.0,
        )
        text = " ".join(s.text.strip() for s in segments).strip().lower()
        return "" if text in _NOISE else text
