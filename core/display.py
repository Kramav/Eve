import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime

# NOTE: pywebview was removed. Transparency on Windows with pywebview's EdgeChromium
# backend is unreliable — background_color='#00000000' raises ValueError (only 3/6-char
# hex triplets accepted), and transparent=True alone doesn't reliably clear the
# WebView2 default background. Replaced with Electron, which has first-class
# transparent frameless window support via BrowserWindow({ transparent: true }).
#
# Architecture: Python runs a WebSocket server (port 7734). display.py methods
# update state and broadcast JSON to all connected Electron clients. Electron
# sends back actions (e.g. toggle_hud) as JSON messages.

WS_PORT = 7734


class Display:
    def __init__(self):
        self._lock    = threading.Lock()
        self._state   = {
            'mode':             'idle',
            'hud_visible':      False,
            'active_listening': False,
            'status_text':      '',
            'main_text':        '',
            'log_entries':      [],
            'list_items':       [],
            'list_status':      '',
        }
        self._clients = set()
        self._loop    = asyncio.new_event_loop()
        threading.Thread(target=self._start_loop, daemon=True).start()

    # ── WebSocket server ────────────────────────────────────────────────────

    def _start_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        import websockets

        async def handler(ws):
            self._clients.add(ws)
            # Send full current state on connect so Electron is immediately in sync
            await ws.send(self._snapshot())
            try:
                async for msg in ws:
                    try:
                        self._handle_action(json.loads(msg))
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                self._clients.discard(ws)

        async with websockets.serve(handler, '127.0.0.1', WS_PORT):
            await asyncio.Future()   # run forever

    def _snapshot(self):
        with self._lock:
            s = dict(self._state)
            s['log_entries']          = list(self._state['log_entries'])
            self._state['log_entries'] = []   # consume so entries arrive exactly once
            return json.dumps(s)

    def _handle_action(self, data):
        action = data.get('action')
        if action == 'toggle_hud':
            self.toggle_overlay()

    def _broadcast(self):
        payload = self._snapshot()
        asyncio.run_coroutine_threadsafe(self._push(payload), self._loop)

    async def _push(self, payload):
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── run_loop: launch Electron, block until it exits ─────────────────────

    def run_loop(self):
        ui_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'ui'))

        if sys.platform == 'win32':
            electron = os.path.join(ui_dir, 'node_modules', '.bin', 'electron.cmd')
            proc = subprocess.Popen([electron, '.'], cwd=ui_dir, shell=True)
        else:
            electron = os.path.join(ui_dir, 'node_modules', '.bin', 'electron')
            proc = subprocess.Popen([electron, '.'], cwd=ui_dir)

        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()

    # ── Public API (same interface as before) ───────────────────────────────

    def show(self, status: str = '', text: str = '', color: str = 'idle'):
        with self._lock:
            self._state['status_text'] = status
            self._state['main_text']   = text
        self._broadcast()

    def hide(self):
        with self._lock:
            self._state['status_text'] = ''
            self._state['main_text']   = ''
        self._broadcast()

    def update(self, status: str = None, text: str = None, color: str = 'idle'):
        with self._lock:
            if status is not None:
                self._state['status_text'] = status
            if text is not None:
                self._state['main_text'] = text
        self._broadcast()

    def set_mode(self, mode: str):
        with self._lock:
            self._state['mode'] = mode
        self._broadcast()

    def set_active_listening(self, enabled: bool):
        with self._lock:
            self._state['active_listening'] = enabled
        self._broadcast()

    def log(self, kind: str, text: str):
        ts = datetime.now().strftime('%H:%M:%S')
        with self._lock:
            self._state['log_entries'].append({'kind': kind, 'text': text, 'ts': ts})
        self._broadcast()

    def toggle_log(self):
        self.toggle_overlay()

    def toggle_overlay(self):
        with self._lock:
            self._state['hud_visible'] = not self._state['hud_visible']
        self._broadcast()

    def show_list(self, items: list, status: str = 'Which video?'):
        with self._lock:
            self._state['list_items']  = list(items)
            self._state['list_status'] = status
        self._broadcast()

    def hide_list(self):
        with self._lock:
            self._state['list_items'] = []
        self._broadcast()

    def show_thumbnail(self, video_url: str, title: str):
        m = re.search(r'[?&]v=([A-Za-z0-9_-]+)', video_url)
        if m:
            self.log('action', f'Now playing: {title}')

    def clear_thumbnail(self):
        pass
