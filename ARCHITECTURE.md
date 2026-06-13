# Eve — Architecture

> Living document. Update this when subsystems are added, responsibilities shift, or design decisions change.

---

## Overview

Eve is a fully local Windows voice assistant. It listens for a wake word, transcribes commands with Whisper, dispatches to handler functions, and surfaces results on a persistent overlay HUD. Everything runs on-device — no cloud APIs.

```
Microphone → Wake Word (openwakeword) → Record → Transcribe (Whisper)
    → Dispatch (regex intents + fuzzy fallback) → Response
    → TTS (pyttsx3) + HUD update (WebSocket → Electron)
```

---

## Technology Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Voice pipeline | Python | Audio, ML model loading, system integration |
| ASR | Whisper (local) | Accurate, fully offline |
| Wake word | openwakeword | Lightweight, runs on CPU |
| TTS | pyttsx3 (Zira) | Zero-latency, no network |
| Command dispatch | Python (regex + difflib) | Fast, hackable, no LLM needed |
| HUD / UI | Electron | Transparent frameless windows, CSS animations, first-class Windows DPI support |
| Python ↔ Electron | WebSocket (port 7734) | Simple, reconnect-safe, works across process restarts |
| Electron IPC | contextBridge / ipcMain | Secure context isolation for renderer ↔ main process |
| Windows API | Python ctypes | Direct Win32 for window manipulation — Electron cannot reach other apps' windows |
| Display metadata | Electron `screen` module | Natively exposes bounds, workArea, scaleFactor, displayFrequency — zero ctypes hazards |
| Settings | `settings.json` (project root) | Flat JSON, written by Electron, readable by Python |
| App config | `apps.json` (project root) | Spoken-name → exe-path map, managed via App Manager UI |

---

## Layered Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Voice Layer (Python)                                        │
│  listener.py  →  transcriber.py  →  dispatcher.py           │
│  speaker.py (TTS, async queue)                              │
└──────────────────────────┬──────────────────────────────────┘
                           │ function calls
┌──────────────────────────▼──────────────────────────────────┐
│  Command Layer (Python — commands/)                          │
│  apps.py  search.py  system.py  youtube.py  reminders.py    │
└──────────────────────────┬──────────────────────────────────┘
                           │ function calls
┌──────────────────────────▼──────────────────────────────────┐
│  Core Services (Python — core/)                              │
│  display.py     WebSocket server, state machine, UI bridge  │
│  monitor.py     Win32 window placement (focus-safe launch)  │
│  window_manager.py  Window enumeration + placement service  │
│  session.py     Global mode + list state                    │
│  app_scanner.py  Installed app discovery                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ WebSocket (ws://127.0.0.1:7734)
┌──────────────────────────▼──────────────────────────────────┐
│  Electron Main Process (ui/main.js)                          │
│  BrowserWindow management  ·  IPC handlers                  │
│  Electron screen API  ·  settings.json R/W                  │
└──────────┬──────────────────────┬───────────────────────────┘
           │ IPC (contextBridge)  │ IPC (contextBridge)
┌──────────▼──────────┐  ┌───────▼───────────────────────────┐
│  Overlay HUD        │  │  Tool Windows                      │
│  ui/src/            │  │  ui/src/app-manager/               │
│  index.html         │  │  ui/src/window-manager/            │
│  app.js  style.css  │  │  (IPC-driven, no WebSocket)        │
└─────────────────────┘  └────────────────────────────────────┘
```

---

## Subsystems

### Voice Pipeline

**Entry point:** `main.py`  
**Wake word:** `core/listener.py` runs openwakeword continuously on a background thread.  
**Recording:** Same thread captures audio until silence is detected (configurable threshold in `config.py`).  
**Transcription:** `core/transcriber.py` sends audio to local Whisper model. Output is lowercased, trailing punctuation stripped.  
**Dispatch:** `core/dispatcher.py` routes text through four passes in order: session state (LISTING/PLAYING), custom commands, aliases, built-in regex intents, fuzzy fallback.

### Dispatch Pipeline

Priority order — first match wins:

1. **Session-aware routing** — if `Mode.LISTING`, route to list-selection handlers (video or web search). If `Mode.PLAYING`, route to playback controls.
2. **Custom commands** (`custom_commands.json`) — user-defined phrase → shell command.
3. **Aliases** (`aliases.json`) — user-defined phrase → built-in function key.
4. **Built-in intents** (`INTENTS` list in `dispatcher.py`) — 30+ regex patterns mapped to handler functions. Order matters — more specific patterns (YouTube, Window Manager) come before generic ones (open/launch).
5. **Fuzzy fallback** (`_guess_dispatch`) — word substitution for common misheards + difflib match against known app names.

**Adding a new command:** Add a tuple `(r"pattern", handler_fn)` to `INTENTS` in `dispatcher.py`. If the command needs the `Display` object (to open a window), follow the `set_display()` injection pattern used in `commands/youtube.py` and `commands/system.py`.

### HUD Overlay

**Window:** Electron `BrowserWindow` — frameless, transparent, always-on-top, non-resizable.  
**Sizes:** `COMPACT` 96×96 (orb only) ↔ `EXPANDED` 380×620 (full HUD).  
**Positioning:** Always anchored to the top-right of the **saved** overlay display (`overlayDisplayId` from `settings.json`). The `set-size` IPC handler uses `getOverlayDisplay()` (saved preference), never dynamic window-center detection — this prevents monitor drift.  
**Toggle:** Orb click → WebSocket `toggle_hud` action → `display.toggle_overlay()` → state broadcast → renderer applies `hud-open` body class → `setSize(compact)` IPC → main.js repositions window.

### Display Management

**Responsibility split — this is intentional and permanent:**

| What | Owner | Rationale |
|------|-------|-----------|
| Display enumeration (geometry, refresh rate, DPI scale, hotplug) | Electron `screen` module | Natively provides `scaleFactor`, `displayFrequency`, `workArea`, `bounds.x/y`. Zero ctypes struct hazards. Has `display-added/removed/metrics-changed` events. |
| HUD window positioning | Electron `main.js` | Owns the `BrowserWindow` object |
| Moving other apps' windows between monitors | Python ctypes (`core/monitor.py`, `core/window_manager.py`) | Electron cannot `SetWindowPos` on foreign HWNDs. Win32 APIs required. |
| Overlay display preference | `settings.json` | Written by Electron, readable by Python |

**Electron `get-displays` IPC** returns: `id`, `index`, `label`, `x`, `y`, `width`, `height`, `workX/Y/Width/Height`, `scaleFactor`, `refreshRate`, `rotation`, `isPrimary`, `isPinned`.

### Window Manager (`core/window_manager.py`)

Provides structured Win32 window data — foundation for future orchestration.

**Current scope:**
- `enumerate_windows(min_size)` — returns `WindowInfo` list (hwnd, title, class, rect, monitor_id)
- `move_window_to_rect(hwnd, rect)` — centres a window on a monitor work area without activating it

**Future scope (not yet implemented):**
- `save_workspace(name)` — snapshot all window positions to JSON
- `restore_workspace(name)` — move windows to saved positions
- `move_app_to_monitor(app_name, monitor_index)` — "move Chrome to monitor 2"

### App Window Launch (`core/monitor.py`)

When `open_app` launches an executable, two things happen in parallel:  
1. `ShellExecuteW(..., SW_SHOWNOACTIVATE=4)` — OS-level hint to not steal focus  
2. Daemon thread polls `EnumWindows` for a new large window (100px+), then `SetWindowPos(HWND_BOTTOM, ...)` on the non-active monitor  

The before-snapshot uses `min_size=0` to include the compact 96×96 overlay HWND so it's never misidentified as the newly opened app when it expands.

### WebSocket Protocol

All messages are JSON. Python is the server, Electron renderers are clients. State is broadcast as complete snapshots — no diffs.

**Python → Electron (server push):**

| Type | When | Key fields |
|------|------|-----------|
| `state` | Any state change | `mode`, `hud_visible`, `active_listening`, `status_text`, `main_text`, `log_entries`, `list_items`, `list_status` |
| `apps_config` | Response to `get_apps_config` | `configured` |
| `scan_result` | Scan complete | `discovered`, `configured` |
| `save_result` | Response to `save_apps` | `success`, `error?` |
| `open_app_manager` | Voice command or programmatic | — |
| `open_window_manager` | Voice command or programmatic | — |

**Electron → Python (client actions):**

| Action | Sender | Effect |
|--------|--------|--------|
| `toggle_hud` | Overlay orb click | `display.toggle_overlay()` |
| `get_apps_config` | App Manager on open | Returns `apps_config` |
| `scan_apps` | App Manager Scan button | Async scan, returns `scan_result` |
| `save_apps` | App Manager Save button | Writes `apps.json` |

### Tool Windows

Tool windows (App Manager, Window Manager) are separate `BrowserWindow` instances opened by `main.js`. They communicate **exclusively via IPC** (preload bridge) — they do not connect to the Python WebSocket. Display data, pinning actions — all via `window.eve.*` IPC calls.

The overlay `app.js` listens for `open_app_manager` / `open_window_manager` WebSocket messages and relays them to `window.eve.openAppManager()` / `window.eve.openWindowManager()` IPC calls, which trigger `main.js` to create the window.

---

## File Map

```
Eve/
├── main.py                    Entry point — wires all components together
├── config.py                  Constants (wake word, Whisper model, thresholds)
├── apps.json                  Spoken-name → exe-path map (managed by App Manager)
├── settings.json              Electron-managed; overlayDisplayId, future prefs
├── custom_commands.json       User phrase → shell command
├── aliases.json               User phrase → built-in key
│
├── core/
│   ├── dispatcher.py          Intent routing (the brain)
│   ├── display.py             WebSocket server + state machine
│   ├── monitor.py             Win32 focus-safe app launch + window placement
│   ├── window_manager.py      Window enumeration + placement service
│   ├── session.py             Global mode + list state (IDLE/LISTING/PLAYING)
│   ├── response.py            Response types: Silent, VideoList, SiteList
│   ├── listener.py            Wake word + audio capture
│   ├── transcriber.py         Whisper ASR
│   ├── speaker.py             TTS async queue
│   ├── app_scanner.py         Installed app discovery (registry + Start Menu)
│   └── hot_reload.py          Dev file watcher
│
├── commands/
│   ├── apps.py                Launch/close apps (ShellExecuteW + placement thread)
│   ├── search.py              DDG scraper, web search list, site navigation
│   ├── system.py              Volume, media, time, screenshots, PC control
│   ├── youtube.py             yt-dlp search, mpv playback, list management
│   └── reminders.py           Timer/reminder storage + daemon checker
│
└── ui/
    ├── main.js                Electron main process
    ├── preload.js             IPC bridge (window.eve API)
    ├── package.json
    └── src/
        ├── index.html         Overlay HUD
        ├── app.js             HUD renderer
        ├── style.css          HUD styles
        ├── app-manager/       App configuration tool window
        │   ├── index.html
        │   ├── app.js
        │   └── style.css
        └── window-manager/    Display layout + HUD pin tool window
            ├── index.html
            ├── app.js
            └── style.css
```

---

## Design Principles

**Local-only.** No cloud dependencies. Everything runs on the user's machine. External calls are limited to DuckDuckGo HTML scraping and yt-dlp video search.

**Non-interruptive.** Any feature that opens a window must never steal focus or interrupt the active window. This is enforced at the OS level via `SW_SHOWNOACTIVATE` + `SetWindowPos(HWND_BOTTOM)`.

**Voice-first.** Every feature accessible via voice. UI is supplemental, not required. Users should be able to configure everything by speaking (with UI as an accelerator).

**Fail silent.** If a command handler raises an exception, the user sees a brief error status but Eve continues listening. No crashes, no hangs.

**No premature abstraction.** Handler functions are direct — no middleware, no plugin system, no event bus (beyond WebSocket state). Add complexity when there's a demonstrated need, not in anticipation of it.

---

## Future Roadmap

The following are planned extensions, in rough priority order. Update this section as things ship.

- [ ] **Window orchestration commands** — "move Chrome to monitor 2", "open Outlook on the left display" — Python `window_manager.py` primitives are in place, dispatcher intents needed
- [ ] **Workspace save/restore** — "save my work layout", "restore workspace" — snapshot window positions to named JSON presets
- [ ] **App-to-monitor assignment rules** — per-app default monitor preference in `apps.json`
- [ ] **Always-on mode** — persistent listening without wake word (opt-in)
- [ ] **Notification awareness** — read out system notifications on request
