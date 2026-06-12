import queue
import threading
import pyttsx3
from config import TTS_RATE


class Speaker:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        engine = pyttsx3.init()
        engine.setProperty("rate", TTS_RATE)
        for voice in engine.getProperty("voices"):
            if "zira" in voice.name.lower():  # Windows built-in female voice
                engine.setProperty("voice", voice.id)
                break
        while True:
            text, done = self._q.get()
            if text:
                engine.say(text)
                engine.runAndWait()
            done.set()

    def speak(self, text: str):
        done = threading.Event()
        self._q.put((text, done))
        done.wait()
