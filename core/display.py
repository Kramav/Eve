import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

# NOTE: pywebview was removed. Transparency on Windows with pywebview's EdgeChromium
# backend is unreliable — background_color='#00000000' raises ValueError (only 3/6-char
# hex triplets accepted), and transparent=True alone doesn't reliably clear the
# WebView2 default background. Replaced with Electron, which has first-class
# transparent frameless window support via BrowserWindow({ transparent: true }).
#
# Architecture: Python runs a WebSocket server (port 7734). display.py methods
# update state and broadcast JSON to all connected Electron clients. Electron
# sends back actions (e.g. toggle_hud) as JSON messages.
#
# All messages include a "type" field so multiple windows (overlay, app manager)
# can filter for the messages they care about.

WS_PORT        = 7734
APPS_FILE      = Path(__file__).parent.parent / 'apps.json'
SETTINGS_FILE  = Path(__file__).parent.parent / 'settings.json'

_VOICE_DEFAULTS = {'speed': 1.0, 'noise_scale': 0.667, 'noise_w': 0.8}
_VOICE_KEYS     = ('speed', 'noise_scale', 'noise_w', 'voice_id')


class Display:
    def __init__(self):
        self._speaker  = None
        self._listener = None
        self._lock     = threading.Lock()
        self._state    = {
            'mode':             'idle',
            'hud_visible':      False,
            'active_listening': False,
            'listener_enabled': True,
            'status_text':      '',
            'main_text':        '',
            'log_entries':      [],
            'list_items':       [],
            'list_status':      '',
            'voice_params':     self._load_voice_settings(),
        }
        self._clients          = set()
        self._last_scan_payload = None
        self._loop             = asyncio.new_event_loop()
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
            # Re-send last scan result if one exists so a reconnecting client
            # doesn't get stuck with a disabled Scan button
            if self._last_scan_payload:
                await self._push_one(ws, self._last_scan_payload)
            try:
                async for msg in ws:
                    try:
                        await self._handle_action_async(json.loads(msg), ws)
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                self._clients.discard(ws)

        async with websockets.serve(handler, '127.0.0.1', WS_PORT):
            await asyncio.Future()

    def _snapshot(self):
        with self._lock:
            s = dict(self._state)
            s['type']                  = 'state'
            s['log_entries']           = list(self._state['log_entries'])
            self._state['log_entries'] = []
            return json.dumps(s)

    async def _handle_action_async(self, data, ws):
        action = data.get('action')
        if action == 'toggle_hud':
            self.toggle_overlay()
        elif action == 'directory_opened':
            with self._lock: self._state['hud_visible'] = True
        elif action == 'directory_closed':
            with self._lock: self._state['hud_visible'] = False
        elif action == 'open_command_editor':
            from commands.system import open_editor
            open_editor()
        elif action == 'get_apps_config':
            await self._send_apps_config(ws)
        elif action == 'scan_apps':
            asyncio.ensure_future(self._do_scan())
        elif action == 'save_apps':
            await self._save_apps_async(data.get('apps', []), ws)
        elif action == 'set_voice_settings':
            params = self._clean_voice_params(data.get('params', {}))
            self._save_voice_settings(params)
            with self._lock:
                self._state['voice_params'] = params
            if self._speaker:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(loop.run_in_executor(
                    None, lambda: self._speaker.update_params(**params)
                ))
        elif action == 'test_voice':
            loop = asyncio.get_running_loop()
            params = self._clean_voice_params(data.get('params') or {})
            if params and self._speaker:
                await loop.run_in_executor(None, lambda: self._speaker.update_params(**params))
            if self._speaker:
                asyncio.ensure_future(loop.run_in_executor(
                    None, self._speaker.speak, "Testing voice settings. How does this sound?"
                ))
        elif action == 'set_listener_enabled':
            self.set_listener_enabled(bool(data.get('enabled', True)))
        elif action == 'toggle_listener':
            with self._lock:
                cur = self._state['listener_enabled']
            self.set_listener_enabled(not cur)
        elif action == 'get_voices':
            from core.speaker import list_voices
            await self._push_one(ws, json.dumps({
                'type':    'voices_list',
                'voices':  list_voices(),
                'current': self._speaker.current_voice_id if self._speaker else None,
            }))

    def _broadcast(self):
        payload = self._snapshot()
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    async def _push_all(self, payload):
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def _push_one(self, ws, payload):
        try:
            await ws.send(payload)
        except Exception:
            self._clients.discard(ws)

    # ── App manager actions ─────────────────────────────────────────────────

    async def _send_apps_config(self, ws):
        configured = self._load_apps()
        payload = json.dumps({'type': 'apps_config', 'configured': configured})
        await self._push_one(ws, payload)

    async def _do_scan(self):
        from core import app_scanner
        loop = asyncio.get_running_loop()
        discovered = await loop.run_in_executor(None, app_scanner.scan)
        configured = self._load_apps()
        payload = json.dumps({
            'type':       'scan_result',
            'discovered': discovered,
            'configured': configured,
        })
        self._last_scan_payload = payload
        await self._push_all(payload)

    async def _save_apps_async(self, apps: list, ws):
        try:
            APPS_FILE.write_text(json.dumps(apps, indent=2))
            result = {'type': 'save_result', 'success': True}
        except Exception as e:
            result = {'type': 'save_result', 'success': False, 'error': str(e)}
        await self._push_one(ws, json.dumps(result))

    def set_listener(self, listener):
        self._listener = listener
        try:
            listener.set_enabled(self._state['listener_enabled'])
        except Exception as e:
            print(f"Error syncing listener state: {e}")

    def set_listener_enabled(self, enabled: bool):
        with self._lock:
            self._state['listener_enabled'] = bool(enabled)
        if self._listener:
            try:
                self._listener.set_enabled(bool(enabled))
            except Exception as e:
                print(f"Error toggling listener: {e}")
        self._broadcast()

    def set_speaker(self, speaker):
        self._speaker = speaker
        # apply persisted voice/params to the freshly-started speaker
        saved = self._state.get('voice_params') or {}
        params = self._clean_voice_params(saved)
        if params:
            try:
                speaker.update_params(**params)
            except Exception as e:
                print(f"Error applying saved voice params: {e}")

    def _clean_voice_params(self, raw: dict) -> dict:
        out = {}
        for k in _VOICE_KEYS:
            if k not in raw:
                continue
            v = raw[k]
            if k == 'voice_id':
                if v: out[k] = str(v)
            else:
                try: out[k] = float(v)
                except (TypeError, ValueError): pass
        return out

    def _load_voice_settings(self) -> dict:
        try:
            data = json.loads(SETTINGS_FILE.read_text())
            return {**_VOICE_DEFAULTS, **data.get('voice', {})}
        except Exception:
            return dict(_VOICE_DEFAULTS)

    def _save_voice_settings(self, params: dict):
        try:
            data = {}
            try:
                data = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                pass
            data['voice'] = params
            SETTINGS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"Error saving voice settings: {e}")

    def _load_apps(self) -> list:
        try:
            return json.loads(APPS_FILE.read_text())
        except Exception:
            return []

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

    # ── Public API: trigger tool windows from Python ────────────────────────

    def open_app_manager(self):
        payload = json.dumps({'type': 'open_app_manager'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def open_window_manager(self):
        payload = json.dumps({'type': 'open_window_manager'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def open_voice_settings(self):
        payload = json.dumps({'type': 'open_voice_settings'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def close_app_manager(self):
        payload = json.dumps({'type': 'close_app_manager'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def close_window_manager(self):
        payload = json.dumps({'type': 'close_window_manager'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    # ── Public API: HUD state (same interface as before) ────────────────────

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
            opening = self._state['hud_visible']
        directive = 'show_directory' if opening else 'hide_directory'
        asyncio.run_coroutine_threadsafe(
            self._push_all(json.dumps({'type': directive})), self._loop
        )
        self._broadcast()

    def show_directory(self):
        with self._lock:
            if self._state['hud_visible']:
                return
            self._state['hud_visible'] = True
        asyncio.run_coroutine_threadsafe(
            self._push_all(json.dumps({'type': 'show_directory'})), self._loop
        )
        self._broadcast()

    def hide_directory(self):
        with self._lock:
            if not self._state['hud_visible']:
                return
            self._state['hud_visible'] = False
        asyncio.run_coroutine_threadsafe(
            self._push_all(json.dumps({'type': 'hide_directory'})), self._loop
        )
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
