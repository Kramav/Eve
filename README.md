# Eve — Local Voice Assistant

A fully local, free Windows voice assistant. No cloud, no API keys. Wake word detection, speech recognition, and text-to-speech all run on your machine.

---

## Requirements

- **Windows 10/11**
- **Python 3.10+**
- **[mpv](https://mpv.io/installation/)** — media player used for YouTube playback
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp/releases)** — YouTube search and streaming (also used by mpv)
- **Firefox** — opened by the `open firefox` command like any other app (not used for YouTube)

> **mpv and yt-dlp must both be on your PATH.** The easiest way is to drop their executables into a folder that's already in PATH (e.g. `C:\Windows\System32`) or add their folder to PATH in System Settings.

Verify before running Eve:
```
mpv --version
yt-dlp --version
```

---

## Installation

### 1. Install Python packages

```
pip install -r requirements.txt
```

### 2. Install mpv

**Easiest — one command (Windows 10/11 built-in package manager):**

```
winget install mpv
```

Restart your terminal after so the PATH update takes effect.

**If winget doesn't add mpv to PATH automatically** (installs to `C:\Program Files\MPV Player\`):

```powershell
$old = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", "$old;C:\Program Files\MPV Player", "User")
```

Restart your terminal, then verify: `mpv --version`

### 3. Install yt-dlp

```
pip install yt-dlp
```

Or download the standalone `yt-dlp.exe` from the [releases page](https://github.com/yt-dlp/yt-dlp/releases) and place it on your PATH.

### 4. Add your apps (optional)

Create `apps.json` in the Eve folder to teach Eve which apps to open. Each entry is `["spoken name", "path or command"]`:

```json
[
  ["firefox", "C:\\Program Files\\Mozilla Firefox\\firefox.exe"],
  ["spotify", "C:\\Users\\YOU\\AppData\\Roaming\\Spotify\\Spotify.exe"],
  ["vs code", "C:\\Users\\YOU\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe"],
  ["notepad", "notepad.exe"]
]
```

---

## Running Eve

```
python main.py
```

Say **"Hey Jarvis"** to wake Eve up, then speak your command.

---

## Voice Commands

### YouTube

| Say | Action |
|-----|--------|
| `play lo-fi music` | Search YouTube and show top 5 results |
| `search youtube for cooking` | Same — explicit YouTube search |
| `play number 2` / `play the third one` | Play a result from the list |
| `pause` / `resume` | Toggle playback |
| `skip ahead` / `go back` | Jump ±10 seconds |
| `skip 30 seconds` | Jump a specific amount |
| `go back 15 seconds` | Rewind a specific amount |
| `mute` / `unmute` | Toggle mute |
| `fullscreen` | Toggle fullscreen |
| `close youtube` | Stop playback and clear the overlay thumbnail |
| `youtube` / `browse youtube` | Open youtube.com in your browser |

YouTube plays in a small **mpv** window that appears in the bottom-right corner of your screen. Playback control works without the window having focus — you can type, browse, or do anything else while audio/video plays.

### Apps

| Say | Action |
|-----|--------|
| `open firefox` | Launch an app from apps.json |
| `open spotify` | Launch an app from apps.json |
| `close chrome` | Kill a running app by name |

### System

| Say | Action |
|-----|--------|
| `what time is it` | Read the current time |
| `what's the date` | Read today's date |
| `volume up` / `volume down` | Adjust system volume |
| `mute` / `unmute` | Toggle system mute |
| `take a screenshot` | Save screenshot to desktop |
| `shut down` | Shutdown in 30 seconds |
| `cancel shutdown` | Abort a pending shutdown |
| `sleep` | Put the PC to sleep |

### Reminders

| Say | Action |
|-----|--------|
| `remind me in 10 minutes to check the oven` | Set a reminder |
| `set a timer for 5 minutes` | Set a nameless timer |
| `what are my reminders` | List pending reminders |
| `cancel reminders` | Cancel all reminders |

### Web

| Say | Action |
|-----|--------|
| `search for python tutorials` | Google search |
| `go to github.com` | Open a URL directly |

### Visual Overlay

| Say | Action |
|-----|--------|
| `show overlay` / `open overlay` | Show the HUD panel |
| `hide overlay` / `close overlay` | Hide the HUD panel |
| `overlay` / `overlay on` / `overlay off` | Toggle the HUD panel |
| `show log` / `hide log` | Alias for the above |

The overlay shows the current mode (IDLE / LISTENING / THINKING / PLAYING), a live activity feed, and a YouTube thumbnail when a video is playing. It's draggable — grab the header to reposition it.

---

## Configuration

Edit `config.py` to adjust core settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `WAKE_WORD` | `hey_jarvis` | Pre-trained wake word model name |
| `WHISPER_MODEL` | `small.en` | STT model: `tiny.en` (fastest) → `small.en` (most accurate) |
| `TTS_RATE` | `175` | Speech rate in words per minute |
| `SILENCE_THRESHOLD` | `400` | Mic amplitude treated as silence (0–32768) |
| `SILENCE_DURATION_S` | `1.5` | Seconds of silence before recording stops |

### Custom commands

Say `"open command editor"` or run `python editor.py` to add custom voice commands and aliases through a GUI.

---

## Hot reload

Command files (`commands/apps.py`, `commands/system.py`, etc.) and `core/dispatcher.py` are watched for changes while Eve runs. Save a file and the new logic takes effect immediately — no restart needed.
