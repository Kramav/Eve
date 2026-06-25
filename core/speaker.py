"""Text-to-speech with a pluggable engine.

Two backends behind one tiny interface so the rest of Eve doesn't care which is
active:
  - Piper (default) — lightweight, ships in the repo.
  - Kokoro (opt-in)  — far more natural (Apache-2.0); needs `pip install
    kokoro-onnx` + the two model files in models/kokoro/. Selected via
    config.TTS_ENGINE = "kokoro" (or "auto" → Kokoro if available, else Piper).

The synth/queue/worker machinery is unchanged; only the actual text→audio and
parameter handling moved into engine classes. An engine is just:
    .voice_id              current voice name
    .synth(text)        -> (float32 mono np.ndarray, sample_rate) | (None, 0)
    .set_params(**p)       speed / noise_scale / noise_w / voice_id (ignored if N/A)
"""
import json
import queue
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd

from config import (TTS_ENGINE, TTS_VOICES_DIR, TTS_DEFAULT_VOICE, TTS_SPEED,
                    TTS_KOKORO_MODEL, TTS_KOKORO_VOICES, TTS_KOKORO_VOICE)

# A small, friendly subset of Kokoro's built-in voices for the UI dropdown.
_KOKORO_VOICES = [
    ('af_heart',   'Heart — American F'),
    ('af_bella',   'Bella — American F'),
    ('af_nicole',  'Nicole — American F'),
    ('am_michael', 'Michael — American M'),
    ('am_fenrir',  'Fenrir — American M'),
    ('bf_emma',    'Emma — British F'),
    ('bm_george',  'George — British M'),
]


def _engine_name() -> str:
    return (TTS_ENGINE or 'piper').lower()


def _kokoro_available() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
    except Exception:
        return False
    return TTS_KOKORO_MODEL.exists() and TTS_KOKORO_VOICES.exists()


def list_voices() -> list[dict]:
    """Voices for the active engine. Kokoro → built-in names; Piper → scan
    models/voices/ for .onnx + .onnx.json pairs."""
    name = _engine_name()
    if name == 'kokoro' or (name == 'auto' and _kokoro_available()):
        return [{'id': vid, 'label': label} for vid, label in _KOKORO_VOICES]

    if not TTS_VOICES_DIR.exists():
        return []
    voices = []
    for onnx in sorted(TTS_VOICES_DIR.glob('*.onnx')):
        meta = onnx.with_suffix('.onnx.json')
        if not meta.exists():
            continue
        stem  = onnx.stem
        label = stem
        try:
            data = json.loads(meta.read_text())
            lang = data.get('language', {}).get('name_english') or data.get('language', {}).get('code')
            speaker = data.get('dataset') or stem.split('-')[1] if '-' in stem else stem
            quality = data.get('audio', {}).get('quality') or stem.split('-')[-1]
            if lang:
                label = f"{speaker.title()} — {lang} ({quality})"
        except Exception:
            pass
        voices.append({'id': stem, 'label': label})
    return voices


def _voice_path(voice_id: str) -> Path:
    return TTS_VOICES_DIR / f"{voice_id}.onnx"


# ── Engines ────────────────────────────────────────────────────────────────

class _PiperEngine:
    def __init__(self, voice_id: str, speed: float):
        from piper.voice import PiperVoice
        self._PiperVoice = PiperVoice
        self._voice = PiperVoice.load(str(_voice_path(voice_id)))
        self._voice.config.length_scale = 1.0 / speed
        self.voice_id = voice_id

    def synth(self, text: str):
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return None, 0
        audio = np.concatenate([c.audio_float_array for c in chunks])
        return audio, chunks[0].sample_rate

    def set_params(self, speed=None, noise_scale=None, noise_w=None, voice_id=None):
        if voice_id and _voice_path(voice_id).exists():
            cfg = self._voice.config
            ls, ns, nw = cfg.length_scale, cfg.noise_scale, cfg.noise_w
            self._voice = self._PiperVoice.load(str(_voice_path(voice_id)))
            self._voice.config.length_scale = ls
            self._voice.config.noise_scale  = ns
            self._voice.config.noise_w      = nw
            self.voice_id = voice_id
        if speed       is not None: self._voice.config.length_scale = 1.0 / speed
        if noise_scale is not None: self._voice.config.noise_scale = noise_scale
        if noise_w     is not None: self._voice.config.noise_w = noise_w


class _KokoroEngine:
    sample_rate = 24000  # Kokoro outputs 24 kHz mono

    def __init__(self, voice_id: str, speed: float):
        from kokoro_onnx import Kokoro
        self._k = Kokoro(str(TTS_KOKORO_MODEL), str(TTS_KOKORO_VOICES))
        self.voice_id = voice_id
        self._speed = speed

    def synth(self, text: str):
        samples, sr = self._k.create(text, voice=self.voice_id,
                                     speed=self._speed, lang='en-us')
        return np.asarray(samples, dtype=np.float32), sr

    def set_params(self, speed=None, voice_id=None, **_ignored):
        if speed    is not None: self._speed = float(speed)
        if voice_id is not None: self.voice_id = str(voice_id)


def _make_engine():
    """Build the configured engine; fall back to Piper if Kokoro is requested
    but unavailable so TTS never silently dies at startup."""
    name = _engine_name()
    if name in ('kokoro', 'auto'):
        try:
            return _KokoroEngine(TTS_KOKORO_VOICE, TTS_SPEED)
        except Exception as e:
            if name == 'kokoro':
                print(f"Kokoro TTS unavailable ({e}); falling back to Piper.")
    return _PiperEngine(TTS_DEFAULT_VOICE, TTS_SPEED)


# ── Speaker ────────────────────────────────────────────────────────────────

class Speaker:
    def __init__(self):
        self._q:          queue.Queue     = queue.Queue()
        self.is_speaking: threading.Event = threading.Event()
        self.enabled:     bool            = True
        self._current_voice_id            = TTS_DEFAULT_VOICE
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        engine = _make_engine()
        self._current_voice_id = engine.voice_id
        while True:
            item = self._q.get()
            # sentinel: param update — (None, done, params)
            if item[0] is None:
                _, done, params = item
                try:
                    engine.set_params(**params)
                except Exception as e:
                    print(f"TTS param update error: {e}")
                self._current_voice_id = engine.voice_id
                done.set()
                continue
            text, done = item
            try:
                if text:
                    self.is_speaking.set()
                    try:
                        audio, sr = engine.synth(text)
                        if audio is not None and len(audio):
                            sd.play(audio, sr)
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

    def update_params(self, speed=None, noise_scale=None, noise_w=None, voice_id=None):
        params = {}
        if voice_id    is not None: params['voice_id']    = str(voice_id)
        if speed       is not None: params['speed']       = float(speed)
        if noise_scale is not None: params['noise_scale'] = float(noise_scale)
        if noise_w     is not None: params['noise_w']     = float(noise_w)
        if not params:
            return
        done = threading.Event()
        self._q.put((None, done, params))
        done.wait()

    @property
    def current_voice_id(self) -> str:
        return self._current_voice_id
