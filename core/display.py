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

from core import features as _features
from config import WS_HOST, WS_PORT

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
            'list_links':       [],
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

        async with websockets.serve(handler, WS_HOST, WS_PORT):
            await asyncio.Future()

    def _snapshot(self):
        with self._lock:
            s = dict(self._state)
            s['type']                  = 'state'
            s['log_entries']           = list(self._state['log_entries'])
            s['features']              = _features.all_features()
            s['feature_status']        = _features.all_status()
            s['feature_labels']        = _features.LABELS
            s['feature_alpha']         = _features.alpha_keys()
            s['feature_reasons']       = {
                k: _features.unavailable_reason(k)
                for k, v in _features.all_status().items()
                if v == 'unavailable'
            }
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
            self.open_command_editor()
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
        elif action == 'toggle_feature':
            key = data.get('key')
            if key and key in _features.DEFAULTS:
                # Only allow toggling features that are available
                if _features.get_status(key) == 'ok':
                    _features.set_feature(key, not _features.all_features()[key])
                self._broadcast()
        elif action == 'refresh_status':
            _features.refresh_status()
            self._broadcast()
        elif action == 'get_voices':
            from core.speaker import list_voices
            await self._push_one(ws, json.dumps({
                'type':    'voices_list',
                'voices':  list_voices(),
                'current': self._speaker.current_voice_id if self._speaker else None,
            }))

        # ── Running Programs panel ──────────────────────────────────────
        elif action == 'programs:get_list':
            from commands import programs as _progs
            payload = json.dumps({
                'type':  'programs_list',
                'items': _progs.get_panel_payload(),
            })
            await self._push_one(ws, payload)
        elif action == 'programs:bring_front':
            from core.window_ops import raise_to_top_no_focus
            raise_to_top_no_focus(int(data.get('hwnd', 0)))
        elif action == 'programs:send_back':
            from core.window_ops import send_to_bottom
            send_to_bottom(int(data.get('hwnd', 0)))
        elif action == 'programs:close':
            import ctypes as _c
            _c.windll.user32.PostMessageW(int(data.get('hwnd', 0)), 0x0010, 0, 0)  # WM_CLOSE
        elif action == 'programs:add_to_apps':
            from commands import programs as _progs
            result = _progs.add_to_apps(
                data.get('name', ''),
                data.get('exe',  ''),
                data.get('path', ''),
            )
            await self._push_one(ws, json.dumps({
                'type':   'programs_add_result',
                'ok':     bool(result.get('ok')),
                'error':  result.get('error', ''),
                'name':   data.get('name', ''),
            }))

        # ── Memory panel ────────────────────────────────────────────────
        elif action == 'memory:get_all':
            from core import memory as _mem
            await self._push_one(ws, json.dumps({
                'type':  'memory_all',
                'items': _mem.all_memories(),
            }))
        elif action == 'memory:set':
            from core import memory as _mem
            _mem.remember(data.get('key', ''), data.get('value', ''))
            payload = json.dumps({'type': 'memory_all', 'items': _mem.all_memories()})
            await self._push_all(payload)
        elif action == 'memory:delete':
            from core import memory as _mem
            _mem.forget(data.get('key', ''))
            payload = json.dumps({'type': 'memory_all', 'items': _mem.all_memories()})
            await self._push_all(payload)

        # ── Reminders panel ─────────────────────────────────────────────
        elif action == 'reminders:get_all':
            from commands import reminders as _rem
            await self._push_one(ws, json.dumps({
                'type':  'reminders_all',
                'items': _rem.get_panel_payload(),
            }))
        elif action == 'reminders:set':
            from commands import reminders as _rem
            result = _rem.panel_set(
                data.get('id', ''),
                data.get('message', ''),
                data.get('when', ''),
            )
            await self._push_one(ws, json.dumps({
                'type':  'reminders_set_result',
                'ok':    bool(result.get('ok')),
                'error': result.get('error', ''),
            }))
            await self._push_all(json.dumps({
                'type': 'reminders_all', 'items': _rem.get_panel_payload(),
            }))
        elif action == 'reminders:delete':
            from commands import reminders as _rem
            _rem.cancel_one(data.get('id', ''))
            await self._push_all(json.dumps({
                'type': 'reminders_all', 'items': _rem.get_panel_payload(),
            }))
        elif action == 'reminders:cancel_all':
            from commands import reminders as _rem
            _rem.cancel_all()
            await self._push_all(json.dumps({
                'type': 'reminders_all', 'items': _rem.get_panel_payload(),
            }))

        # ── API Keys / Integrations panel ────────────────────────────────
        elif action == 'integrations:get':
            loop = asyncio.get_running_loop()
            # Off-loop: setup status pings Ollama, so don't block the event loop.
            payload = await loop.run_in_executor(None, self._integrations_full)
            await self._push_one(ws, json.dumps(payload))
        elif action.startswith('integrations:install_'):
            service = action[len('integrations:install_'):]
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, lambda: self._install_integration(service))
            await self._push_one(ws, json.dumps({
                'type':    'integrations_install_result',
                'service': service,
                'ok':      bool(res.get('ok')),
                'message': res.get('message', ''),
            }))
            # Refresh the setup pills + the main feature snapshot (status may flip).
            payload = await loop.run_in_executor(None, self._integrations_full)
            await self._push_one(ws, json.dumps(payload))
            self._broadcast()
        elif action.startswith('integrations:set_'):
            service = action[len('integrations:set_'):]
            self._save_api_key(service, data.get('key', ''))
            await self._push_all(json.dumps(self._integrations_state()))
        elif action.startswith('integrations:test_'):
            service = action[len('integrations:test_'):]
            loop = asyncio.get_running_loop()
            # `key` lets the user test before saving; falls back to stored key.
            key = data.get('key') or None
            res = await loop.run_in_executor(None, lambda: self._test_api_key(service, key))
            await self._push_one(ws, json.dumps({
                'type':    'integrations_test_result',
                'service': service,
                'ok':      bool(res.get('ok')),
                'message': res.get('message', ''),
            }))

        # ── Local LLM fallback options (settings.json "llm"; core/llm_host) ──
        elif action == 'llm:get':
            from core import llm_host
            loop = asyncio.get_running_loop()
            state = await loop.run_in_executor(None, self._llm_state)
            await self._push_one(ws, json.dumps(state))
        elif action == 'llm:set':
            from core import llm_host
            loop = asyncio.get_running_loop()

            def _save_apply():
                llm_host.save_settings(data.get('settings') or {})
                llm_host.apply_settings()
                return self._llm_state()
            state = await loop.run_in_executor(None, _save_apply)
            await self._push_one(ws, json.dumps(state))
        elif action == 'intents:list':
            await self._push_one(ws, json.dumps(self._intents_list()))
        elif action == 'intents:delete':
            from core import intent_learning
            store = (intent_learning.imported() if data.get('store') == 'imported'
                     else intent_learning.learned())
            store.delete(str(data.get('tool', '')), str(data.get('phrase', '')))
            await self._push_one(ws, json.dumps(self._intents_list()))
        elif action == 'llm:export_intents':
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, self._export_intents)
            await self._push_one(ws, json.dumps(res))
        elif action == 'llm:import_intents':
            # The UI shows the "unexpected issues" warning BEFORE sending this.
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, self._import_intents)
            await self._push_one(ws, json.dumps(res))

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

    # ── API Keys / Integrations ─────────────────────────────────────────────

    def _load_settings_raw(self) -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            return {}

    # Optional integrations that install cleanly via pip (one-click from the UI).
    # System installers (Ollama) keep a guide link instead — see the panel.
    _INSTALLERS = {
        'rapidocr':     'rapidocr-onnxruntime',
        'uiautomation': 'uiautomation',
    }

    def _install_integration(self, service: str) -> dict:
        """Install an optional integration via pip. Returns {ok, message}."""
        import subprocess
        import sys
        pkg = self._INSTALLERS.get(service)
        if not pkg:
            return {'ok': False, 'message': f'No one-click installer for {service} — use the guide.'}
        try:
            p = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg],
                               capture_output=True, text=True, timeout=600)
        except Exception as e:
            return {'ok': False, 'message': f'Could not start install: {e}'}
        if p.returncode == 0:
            try:
                from core import features as _features
                _features.refresh_status()
            except Exception:
                pass
            return {'ok': True, 'message': f'{pkg} installed. Restart Eve if it isn’t detected.'}
        tail = (p.stderr or p.stdout or '').strip().splitlines()
        return {'ok': False, 'message': f'Install failed: {tail[-1][:120] if tail else "see console"}'}

    def _integrations_full(self) -> dict:
        """Key state + setup readiness for tool-based integrations (Ollama, OCR,
        UI Automation). Used by the Integrations panel's status pills."""
        state = self._integrations_state()
        state['setup'] = self._setup_status()
        return state

    def _setup_status(self) -> dict:
        """Readiness of optional tool integrations. May do a quick Ollama ping —
        callers run this off the event loop."""
        import importlib

        def _installed(mod: str) -> bool:
            try:
                importlib.import_module(mod)
                return True
            except Exception:
                return False

        out = {
            'uiautomation': {'ready': _installed('uiautomation'), 'detail': ''},
            'rapidocr':     {'ready': _installed('rapidocr_onnxruntime'), 'detail': ''},
        }
        ready, detail = False, 'not detected'
        try:
            import urllib.request
            from config import OLLAMA_HOST
            url = OLLAMA_HOST.rstrip('/') + '/api/tags'
            with urllib.request.urlopen(url, timeout=1.5) as r:
                models = (json.loads(r.read()).get('models') or [])
            ready = True
            detail = (f"running · {len(models)} model(s)" if models
                      else "running · no models pulled yet")
        except Exception:
            ready, detail = False, 'not detected'
        out['ollama'] = {'ready': ready, 'detail': detail}
        return out

    def _llm_state(self) -> dict:
        """Everything the LLM options card needs: current merged settings,
        the .gguf files available to pick from, and whether a server answers."""
        from core import llm_host
        s = llm_host.settings()
        return {
            'type':      'llm_state',
            'settings':  s,
            'models':    llm_host.list_model_files(),
            'server_up': llm_host._server_up(s['base_url']),
        }

    def _intents_list(self) -> dict:
        """Learned + imported mappings for the command-editor Learned tab,
        with the derived bits the UI shows (confidence %, generalizes flag)."""
        from core import intent_learning as il

        def rows(store, name):
            out = []
            for e in store.entries:
                conf = store.confidence(e)
                out.append({
                    'store':       name,
                    'phrase':      e.get('phrase', ''),
                    'tool':        e.get('tool', ''),
                    'args':        e.get('args') or {},
                    's':           e.get('s', 0),
                    'f':           e.get('f', 0),
                    'confidence':  round(conf, 2),
                    'generalizes': bool(e.get('pattern')) and conf >= il.TRUST_CONFIDENCE,
                    'destructive': e.get('tool') in il.DESTRUCTIVE_TOOLS,
                    'origin':      e.get('origin', ''),
                    'last_used':   e.get('last_used', ''),
                })
            return out
        return {'type': 'intents_list',
                'personal': rows(il.learned(), 'personal'),
                'imported': rows(il.imported(), 'imported')}

    def _export_intents(self) -> dict:
        from datetime import date
        from core import intent_learning
        try:
            path = str(Path.home() / "Desktop" /
                       f"eve-intents-{date.today():%Y%m%d}.json")
            n = intent_learning.export_intents(path)
            return {'type': 'llm_intents_result', 'ok': True,
                    'message': f"Exported {n} learned intents to {path}"}
        except Exception as e:
            return {'type': 'llm_intents_result', 'ok': False,
                    'message': f"Export failed: {e}"}

    def _import_intents(self) -> dict:
        from core import intent_learning
        try:
            # Native file picker via tkinter (stdlib) — shell-agnostic, works
            # identically under Electron and Tauri.
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
            path = filedialog.askopenfilename(
                title="Import Eve intents pack",
                filetypes=[("Eve intents export", "*.json"), ("All files", "*.*")])
            root.destroy()
            if not path:
                return {'type': 'llm_intents_result', 'ok': True, 'message': "Import cancelled."}
            added, updated, skipped = intent_learning.import_intents(path)
            return {'type': 'llm_intents_result', 'ok': True,
                    'message': f"Imported: {added} added, {updated} updated, {skipped} skipped."}
        except Exception as e:
            return {'type': 'llm_intents_result', 'ok': False,
                    'message': f"Import failed: {e}"}

    def _test_api_key(self, service: str, key):
        """Validate a key for the given service. brave → web search; anthropic /
        openai → cloud vision. Returns {ok, message}."""
        try:
            if service == 'brave':
                from commands import search as _search
                return _search.test_brave_key(key)
            if service in ('anthropic', 'openai'):
                from commands import vision as _vision
                return _vision.test_key(service, key)
        except Exception as e:
            return {'ok': False, 'message': f'Test failed: {e}'}
        return {'ok': False, 'message': f'No test for {service}.'}

    def _save_api_key(self, service: str, key: str):
        """Persist an API key under settings.json -> api_keys.<service>."""
        try:
            data = self._load_settings_raw()
            keys = data.get('api_keys') or {}
            keys[service] = (key or '').strip()
            data['api_keys'] = keys
            SETTINGS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"Error saving API key: {e}")

    def _integrations_state(self) -> dict:
        """Masked snapshot for the UI — never sends the full key back."""
        keys = self._load_settings_raw().get('api_keys') or {}
        out = {}
        for service, val in keys.items():
            val = (val or '').strip()
            out[service] = {
                'set':  bool(val),
                'hint': ('…' + val[-4:]) if len(val) >= 4 else ('set' if val else ''),
            }
        # Note whether an env var supplies a key even with nothing saved here.
        if 'brave' not in out:
            from commands.search import brave_key
            out['brave'] = {'set': bool(brave_key()), 'hint': ''}
        try:
            from commands.vision import vision_key
            for svc in ('anthropic', 'openai'):
                if not out.get(svc, {}).get('set'):
                    out[svc] = {'set': bool(vision_key(svc)), 'hint': out.get(svc, {}).get('hint', '')}
        except Exception:
            pass
        return {'type': 'integrations_state', 'services': out}

    def _load_apps(self) -> list:
        try:
            return json.loads(APPS_FILE.read_text())
        except Exception:
            return []

    # ── run_loop: launch the UI shell (Tauri), block until it exits ─────────
    #
    # Cutover (2026-07-10): Tauri is the default shell. Escape hatches while
    # Electron soaks toward deletion:
    #   EVE_NO_ELECTRON=1 / EVE_NO_UI=1 — spawn no shell (run `npm run tauri
    #     dev` yourself; Python core + WS server stay up until Ctrl-C).
    #   EVE_UI=electron — spawn the old Electron shell.
    # If the Tauri exe hasn't been built yet, falls back to Electron with a
    # hint (build: cd eve-tauri && npm run tauri build -- --no-bundle).

    def run_loop(self):
        if os.environ.get('EVE_NO_ELECTRON') or os.environ.get('EVE_NO_UI'):
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
            return

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        tauri_exe = os.path.join(root, 'eve-tauri', 'src-tauri', 'target', 'release',
                                 'eve-tauri.exe' if sys.platform == 'win32' else 'eve-tauri')

        if os.environ.get('EVE_UI', '').lower() != 'electron' and os.path.exists(tauri_exe):
            proc = subprocess.Popen([tauri_exe], cwd=root)
        else:
            if os.environ.get('EVE_UI', '').lower() != 'electron':
                print('[display] Tauri exe not found — falling back to Electron. '
                      'Build it: cd eve-tauri && npm run tauri build -- --no-bundle')
            ui_dir = os.path.join(root, 'ui')
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

    def snap_panel(self, panel_id: str, x: int, y: int, w: int, h: int):
        """Open the given Eve panel and place it at the given screen rect."""
        payload = json.dumps({
            'type':   'snap_panel',
            'panel':  panel_id,
            'bounds': {'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)},
        })
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def identify_monitors(self):
        """Briefly flash a big numbered card on each monitor for UX clarity."""
        payload = json.dumps({'type': 'identify_monitors'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def identify_zones(self):
        """Briefly overlay the saved tiling layout on each monitor that has one."""
        payload = json.dumps({'type': 'identify_zones'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def wm_apply_preset(self, monitor_ref: str, preset_key: str):
        """Voice WM control: apply a preset layout to the named monitor.
        monitor_ref is '1'..'10' or 'primary'; Electron resolves to a display."""
        payload = json.dumps({
            'type':       'wm_apply_preset',
            'monitorRef': str(monitor_ref),
            'presetKey':  str(preset_key),
        })
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def wm_move_hud(self, monitor_ref: str):
        """Voice WM control: pin the HUD overlay to the named monitor."""
        payload = json.dumps({
            'type':       'wm_move_hud',
            'monitorRef': str(monitor_ref),
        })
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def wm_set_orb_corner(self, corner: str):
        """Voice WM control: pin the orb (and routing-directory anchor) to a
        corner of the current HUD monitor. *corner* is one of
        'top-right', 'top-left', 'bottom-right', 'bottom-left'."""
        payload = json.dumps({
            'type':   'wm_set_orb_corner',
            'corner': str(corner),
        })
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def open_programs(self):
        """Open the Running Programs Electron panel."""
        payload = json.dumps({'type': 'open_programs'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def close_programs(self):
        payload = json.dumps({'type': 'close_programs'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def open_memory(self):
        """Open the Memory editor panel."""
        payload = json.dumps({'type': 'open_memory'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def close_memory(self):
        payload = json.dumps({'type': 'close_memory'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def open_reminders(self):
        """Open the Reminders editor panel."""
        payload = json.dumps({'type': 'open_reminders'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def close_reminders(self):
        payload = json.dumps({'type': 'close_reminders'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def open_integrations(self):
        """Open the API Keys / Integrations panel."""
        payload = json.dumps({'type': 'open_integrations'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def close_integrations(self):
        payload = json.dumps({'type': 'close_integrations'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def reminders_changed(self):
        """Broadcast the current reminder list so an open panel refreshes.
        Called from the background checker when a reminder fires/re-arms."""
        from commands import reminders as _rem
        payload = json.dumps({
            'type': 'reminders_all', 'items': _rem.get_panel_payload(),
        })
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def identify_windows(self, windows: list):
        """Briefly overlay numbered labels on every visible top-level window.
        *windows* is a list of {index, label, title, x, y, w, h} dicts."""
        payload = json.dumps({'type': 'identify_windows', 'windows': windows})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def open_command_editor(self):
        """Open the inline Electron-based command editor."""
        payload = json.dumps({'type': 'open_command_editor'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    def close_command_editor(self):
        payload = json.dumps({'type': 'close_command_editor'})
        asyncio.run_coroutine_threadsafe(self._push_all(payload), self._loop)

    # ── YouTube HUD browser ─────────────────────────────────────────────────

    def youtube_browse(self):
        self._emit({'type': 'youtube_browse'})
        # Number the tiles once the page has had a moment to load, so a numbered
        # list appears on open (no separate "number the videos" needed). Runs on
        # the WS loop — never blocks the voice thread.
        self._emit_after(2.5, {'type': 'youtube_number'})

    def youtube_scroll(self, direction: str):
        self._emit({'type': 'youtube_scroll', 'dir': str(direction)})

    def youtube_number(self):
        self._emit({'type': 'youtube_number'})

    def youtube_open(self, n: int):
        self._emit({'type': 'youtube_open', 'n': int(n)})

    def youtube_search(self, query: str):
        self._emit({'type': 'youtube_search', 'query': str(query)})

    def youtube_playpause(self):
        self._emit({'type': 'youtube_playpause'})

    def youtube_close(self):
        self._emit({'type': 'youtube_close'})

    def _emit(self, msg: dict):
        """Fire-and-forget broadcast of a typed directive to all UI clients."""
        asyncio.run_coroutine_threadsafe(
            self._push_all(json.dumps(msg)), self._loop
        )

    def _emit_after(self, delay: float, msg: dict):
        """Broadcast a directive after *delay* seconds, on the WS loop (never
        blocks the caller). Used for load-timed follow-ups like feed numbering."""
        async def _later():
            await asyncio.sleep(delay)
            await self._push_all(json.dumps(msg))
        asyncio.run_coroutine_threadsafe(_later(), self._loop)

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

    def show_list(self, items: list, status: str = 'Which video?', links: list = None):
        """Show a pick-list in the overlay. *links* is an optional parallel
        list of URLs (one per item); when present the renderer makes each row
        clickable to open it in the default browser."""
        with self._lock:
            self._state['list_items']  = list(items)
            self._state['list_links']  = list(links) if links else []
            self._state['list_status'] = status
        self._broadcast()

    def hide_list(self):
        with self._lock:
            self._state['list_items'] = []
            self._state['list_links'] = []
        self._broadcast()

    def show_thumbnail(self, video_url: str, title: str):
        m = re.search(r'[?&]v=([A-Za-z0-9_-]+)', video_url)
        if m:
            self.log('action', f'Now playing: {title}')

    def clear_thumbnail(self):
        pass
