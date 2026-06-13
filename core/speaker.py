import json
import queue
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
from piper.voice import PiperVoice

from config import TTS_VOICES_DIR, TTS_DEFAULT_VOICE, TTS_SPEED


def list_voices() -> list[dict]:
    """Scan models/voices/ for .onnx + .onnx.json pairs. Returns [{id, label}, ...]."""
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


class Speaker:
    def __init__(self):
        self._q:          queue.Queue     = queue.Queue()
        self.is_speaking: threading.Event = threading.Event()
        self.enabled:     bool            = True
        self._current_voice_id            = TTS_DEFAULT_VOICE
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        voice = PiperVoice.load(str(_voice_path(self._current_voice_id)))
        voice.config.length_scale = 1.0 / TTS_SPEED
        while True:
            item = self._q.get()
            # sentinel: param update — (None, done, params)
            if item[0] is None:
                _, done, params = item
                if 'voice_id' in params:
                    new_id = params['voice_id']
                    path = _voice_path(new_id)
                    if path.exists():
                        try:
                            # capture current tuning so swap preserves it
                            ls = voice.config.length_scale
                            ns = voice.config.noise_scale
                            nw = voice.config.noise_w
                            voice = PiperVoice.load(str(path))
                            voice.config.length_scale = ls
                            voice.config.noise_scale  = ns
                            voice.config.noise_w      = nw
                            self._current_voice_id    = new_id
                        except Exception as e:
                            print(f"Voice load error ({new_id}): {e}")
                if 'speed' in params:
                    voice.config.length_scale = 1.0 / params['speed']
                if 'noise_scale' in params:
                    voice.config.noise_scale = params['noise_scale']
                if 'noise_w' in params:
                    voice.config.noise_w = params['noise_w']
                done.set()
                continue
            text, done = item
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
