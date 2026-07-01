# Eve — a focus-preserving voice assistant for Windows

**Control your PC by voice without losing focus on what you're doing.** Eve was born from a simple
want: to look something up *while playing a game* — a boss fight, a recipe, a wiki page — without
alt-tabbing out and breaking the moment. Everything grew from there.

Fully local and free: wake word, speech recognition, and text-to-speech all run on your machine — no
cloud, no API keys.

---

## Philosophy

> **The core idea: do things on your PC without pulling you out of what you're doing.**

Most voice assistants treat voice as a *convenience*. Eve is built for the situation where voice is the
*only* thing that works — your hands are on the keyboard and mouse, and a game (or a document, or a
video) owns the screen. In that moment you can't reach for a mouse-driven menu and you can't afford to
alt-tab away. Voice + **never stealing focus** is the only combination that beats the status quo.

That leads to one hard, non-negotiable rule:

> **The focus invariant — Eve never takes focus from your task unless you explicitly ask it to.**
> Snapping a window, launching an app, searching the web, reading a reminder: all of it happens
> *beside* what you're doing, never *in front of* it. A command that yanks focus off your game is a
> bug, not a trade-off. (The Win32 z-order work in [`core/window_ops.py`](core/window_ops.py) — raising
> a window above the foreground *without* activating it — is the heart of this, and the heart of Eve.)

**What Eve is:**
- **Identity** — a voice-driven, *focus-preserving* overlay for Windows. The differentiator is the
  *feel*: it acts without interrupting you.
- **Flagship** — the game-guide companion: search and surface information over a running game, hands-free.
- **Foundation** — general OS UI/UX control (windows, apps, tiling, system) so it's genuinely useful to
  anyone, every day — not just while gaming. *Gaming is the lens we design and demo through, not a set of
  game-specific features.*

**Design principles** — every change optimizes for: speed · responsiveness · smooth UX · elegant UI ·
low resource use · clean architecture · maintainability · extensibility. **Every feature must justify
its cost in complexity and performance.** A fast, polished, intuitive assistant beats a feature-packed
one that's slower or harder to maintain. We avoid feature bloat.

### How Eve is organized (core vs. skills vs. integrations)

Three layers, and it's worth keeping them straight:

- **Core (the kernel)** — the machine that runs everything: the always-on loop (wake word → speech →
  dispatch → response → overlay/voice) and the **OS-integration primitives** (`window_ops`, `key_ops`,
  `monitor`, `notify`) plus the UX contract (response types, the HUD). Core is *not* a feature; it's what
  features are built on. The focus-preserving primitives live here. This defines how Eve *feels*.
- **Capabilities** — everything a user experiences as "Eve can do X." These are either **built-in
  features** (`commands/`, in-box and often central, e.g. tiling, search, reminders) or **skills**
  (`skills/*.py`, self-contained, optional, deletable single files that extend Eve *without editing
  core*). The line between the two is centrality/maturity, not a hard wall. Everything is voice-invoked —
  that's not what makes something a skill; being a droppable, optional capability is.
- **Integrations** — the *connection to an external system* (a device, a service, an API key). An
  integration isn't a code tier — it's something a capability *owns*, usually behind a backend
  abstraction (the 3D-printer **skill** integrates with PrusaLink/Bambu; web search integrates with the
  Brave API). The **Integrations** panel in the UI just manages those credentials/setup.

**The test for where something belongs:** does it define how Eve feels, or is it relied on by many
features? → core. Is it one capability a user might never touch? → skill. Does it talk to an outside
device/service/key? → it has an integration. And above all: **does it respect the focus invariant?** If
not, it doesn't ship.

---

## System Requirements

- **Windows 10/11**
- **Python 3.14** (other versions untested)
- **Node.js 18+** — required to run the Electron UI
- **[mpv](https://mpv.io/installation/)** — media player used for YouTube playback (must be on PATH)
- **Two or more monitors — *strongly recommended*** (Eve works with one; see below)

### Multiple monitors (strongly recommended)

Eve runs fine on a single monitor, but it's **meaningfully better with two**, and the reason ties
directly to the [focus invariant](#philosophy):

- **A second screen is where opened content goes.** When you launch an app or pull up a search result
  while a game (or any full-attention task) owns your main screen, Eve places that window on the monitor
  the game *isn't* on — visible *beside* your task instead of behind it. It adapts to whichever screen
  you're gaming on, so it's always the "other" one.
- **It's the clean workaround for exclusive-fullscreen games.** A truly exclusive-fullscreen game won't
  share its screen or its focus — nothing can overlay it. With a second monitor that stops mattering:
  Eve's windows (and the HUD) live over there, so you get the result without the game ever losing focus
  or flickering.

**On a single monitor Eve still works** — voice commands, window control, and the overlay HUD all
function. The trade-off is that an opened window has nowhere to go *but* behind a fullscreen game, and an
exclusive-fullscreen title may hide the overlay entirely. If you game in **borderless windowed** mode
(most modern titles), the HUD can still draw over it; exclusive fullscreen is the case a second monitor
solves.

---

## Installation

### Option A — Automated (recommended)

#### 1. Install prerequisites

- **Python 3.11+** — [python.org](https://www.python.org/downloads/)
- **Node.js 18+** — [nodejs.org](https://nodejs.org/) (needed for the Electron UI)
- **mpv** — YouTube playback: `winget install mpv`, then restart your terminal

#### 2. Run setup

```powershell
python setup.py
```

This handles everything else automatically: Python packages, wake word models, the Piper TTS voice model, and Electron. Re-running is safe — all steps check before acting.

---

### Option B — Manual

Follow these steps if you prefer not to use the setup script, or if something in the automated setup fails.

#### 1. Install prerequisites

- **Python 3.11+** — [python.org](https://www.python.org/downloads/)
- **Node.js 18+** — [nodejs.org](https://nodejs.org/)
- **mpv** — `winget install mpv` (restart terminal after)

#### 2. Install Python packages

```powershell
python -m pip install -r requirements.txt
```

> Always use `python -m pip install`, not `python pip install`.

#### 3. Download wake word models

```powershell
python -c "import openwakeword; openwakeword.utils.download_models()"
```

Downloads the pre-trained wake word models (including `hey_jarvis`) into the openwakeword package directory. Only needed once.

#### 4. Download the Piper TTS voice model

The default voice is `en_US-lessac-medium`. Download both files from [huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices):

- `en_US-lessac-medium.onnx` (~63 MB)
- `en_US-lessac-medium.onnx.json`

Direct links:
```
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Place both files in `models/voices/` (create the folder if it doesn't exist):
```
Eve/
  models/
    voices/
      en_US-lessac-medium.onnx
      en_US-lessac-medium.onnx.json
```

To use a different voice, update `TTS_DEFAULT_VOICE` in `config.py` to match the model filename stem and download that model instead.

#### 5. Install Electron

```powershell
cd ui
npm install
cd ..
```

#### 6. Create default config files

Create `features.json` in the Eve root folder:

```json
{
  "tts": true,
  "youtube": true,
  "web_search": true,
  "reminders": true,
  "apps": true,
  "tiling": true
}
```

Create `apps.json` in the Eve root folder (can be empty to start):

```json
[]
```

---

### 3. Configure your apps (optional)

`apps.json` in the Eve folder tells Eve which apps to launch by voice. You can manage this through the **App Manager** UI (say `open app manager`) or edit the file directly:

```json
[
  {"name": "Firefox", "path": "C:\\Program Files\\Mozilla Firefox\\firefox.exe", "spoken": "firefox"},
  {"name": "Spotify",  "path": "C:\\Users\\YOU\\AppData\\Roaming\\Spotify\\Spotify.exe", "spoken": "spotify"},
  {"name": "VS Code",  "path": "C:\\Users\\YOU\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe", "spoken": "vs code"}
]
```

---

## Running Eve

```powershell
python main.py
```

Say **"Hey Jarvis"** to wake Eve up, then speak your command.

The **Routing Directory** window opens automatically — this is the main control panel. The small orb in the top-right corner is the always-visible status indicator.

---

## UI Overview

### Orb (top-right corner)
Always-visible animated circle that shows Eve's current state by color and animation. Click it to open/close the Routing Directory.

### Routing Directory
The main control panel. Opens automatically on launch. Contains:
- **Module tiles** — quick access to App Manager, Window Manager, Command Editor, Voice Settings
- **Feature toggles** — enable/disable individual capabilities (TTS, YouTube, Web Search, Reminders, App Launcher, Window Tiling)
- **Activity feed** — live log of everything Eve hears and does

### App Manager
Discover installed apps on your system and configure which ones Eve can launch by voice. Opens via the Routing Directory or by saying `open app manager`.

### Window Manager
Visual layout manager for snapping windows to zones. Opens via the Routing Directory or by saying `open window manager`.

### Voice Settings
Configure TTS voice, speed, and other audio parameters. Opens via the Routing Directory or by saying `open voice settings`.

### Command Editor
Add custom voice commands and aliases via a GUI. Opens via the Routing Directory, by saying `open command editor`, or by running `python editor.py`.

---

## Voice Commands

### YouTube

| Say | Action |
|-----|--------|
| `play lo-fi music` | Search YouTube and show top 5 results |
| `search youtube for cooking` | Explicit YouTube search |
| `play number 2` / `play the third one` | Play a result from the list |
| `browse youtube` / `open youtube` | Open youtube.com in browser |
| `pause` / `resume` | Toggle playback |
| `skip ahead` / `go back` | Jump ±10 seconds |
| `skip 30 seconds` / `go back 15 seconds` | Jump a specific amount |
| `mute` / `unmute` | Toggle mute |
| `fullscreen` | Toggle fullscreen |
| `next video` | Skip to next result |
| `show list` / `back to list` | Return to the results list |
| `close youtube` | Stop playback |

YouTube plays in mpv. Playback control works without the window having focus.

### Apps

| Say | Action |
|-----|--------|
| `open firefox` | Launch an app configured in apps.json |
| `close chrome` | Gracefully close a running app |
| `kill chrome` | Force-terminate a running app |

### Window Tiling

| Say | Action |
|-----|--------|
| `snap firefox to left` | Snap an app to a named zone |
| `move chrome to top-right` | Move an app to a zone |
| `send notepad to bottom` | Send an app to a zone |

Zones are defined in `tiling_layouts.json` and configured via the Window Manager.

### Web

| Say | Action |
|-----|--------|
| `search for python tutorials` | Google search — shows result list |
| `go to github.com` | Open a URL directly |
| `open the first one` / `go to 2` | Navigate to a search result |
| `open the wikipedia one` | Navigate by keyword |

### System

| Say | Action |
|-----|--------|
| `what time is it` | Read the current time |
| `what's the date` | Read today's date |
| `volume up` / `volume down` | Adjust system volume |
| `mute` / `unmute` | Toggle system mute |
| `pause` / `play` / `resume` | Media play/pause |
| `next song` / `previous song` | Media next/prev track |
| `take a screenshot` | Save screenshot to desktop |
| `sleep` | Put the PC to sleep |
| `shut down` | Shutdown in 30 seconds |
| `cancel shutdown` | Abort a pending shutdown |

### Reminders

| Say | Action |
|-----|--------|
| `remind me in 10 minutes to check the oven` | Set a reminder |
| `set a timer for 5 minutes` | Set a nameless timer |
| `what are my reminders` | List pending reminders |
| `cancel reminders` | Cancel all reminders |

### Visual Overlay / HUD

| Say | Action |
|-----|--------|
| `show overlay` / `hide overlay` | Toggle the HUD panel |
| `show hud` / `close hud` | Same |
| `show log` / `hide log` | Same |
| `open routing directory` | Open the Routing Directory window |
| `open app manager` | Open the App Manager |
| `open window manager` | Open the Window Manager |
| `open voice settings` | Open Voice Settings |
| `open command editor` | Open the Command Editor |

### TTS Control

| Say | Action |
|-----|--------|
| `silence` / `mute eve` / `disable tts` | Stop Eve from speaking |
| `enable voice` / `unmute eve` | Re-enable speech |
| `toggle voice` | Toggle TTS on/off |

---

## Configuration

Edit `config.py` to adjust core settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `WAKE_WORD` | `hey_jarvis` | Pre-trained wake word model name |
| `WHISPER_MODEL` | `small.en` | STT model: `tiny.en` (fastest) → `small.en` (most accurate) |
| `TTS_DEFAULT_VOICE` | `en_US-lessac-medium` | Voice model filename stem (without `.onnx`) |
| `TTS_SPEED` | `1.0` | Speech rate multiplier (0.8 = slower, 1.2 = faster) |
| `SILENCE_THRESHOLD` | `800` | Mic amplitude treated as silence (0–32768); raise if recording runs long |
| `SILENCE_DURATION_S` | `1.5` | Seconds of silence before recording stops |

### Feature toggles

Individual capabilities can be toggled at runtime from the **Routing Directory** without restarting Eve. Toggles are saved to `features.json` and persist across restarts:

- **Text-to-Speech** — disable if you want silent responses
- **YouTube** — disable to free up processing / avoid media commands
- **Web Search** — disable to prevent web lookups
- **Reminders & Timers** — disable if unused
- **App Launcher** — disable if unused
- **Window Tiling** — disable if unused

### Custom commands

Say `"open command editor"` to add custom voice triggers that run any shell command, and aliases that map phrases to built-in actions.

---

## Hot reload

Command files in `commands/` and `core/dispatcher.py` are watched for changes while Eve runs. Save a file and the new logic takes effect immediately — no restart needed.

---

## Project structure

```
Eve/
├── main.py                  # Entry point
├── config.py                # Core settings
├── features.json            # Feature toggle state (auto-created)
├── apps.json                # App launcher config
├── tiling_layouts.json      # Window tiling zone definitions
├── settings.json            # Voice/UI settings
├── requirements.txt         # Python dependencies
├── commands/                # Voice command handlers
│   ├── apps.py
│   ├── search.py
│   ├── system.py
│   ├── tiling.py
│   ├── reminders.py
│   └── youtube.py
├── core/                    # Core infrastructure
│   ├── dispatcher.py        # Intent routing
│   ├── display.py           # WebSocket server + state broadcast
│   ├── features.py          # Feature toggle management
│   ├── listener.py          # Wake word + audio recording
│   ├── transcriber.py       # Whisper STT
│   ├── speaker.py           # Piper TTS
│   └── session.py           # Conversation state
├── models/
│   └── voices/              # Piper .onnx voice model files go here
└── ui/                      # Electron frontend
    ├── main.js
    ├── preload.js
    ├── package.json
    └── src/
        ├── index.html       # Orb overlay
        ├── directory/       # Routing Directory window
        └── app-manager/     # App Manager window
```
