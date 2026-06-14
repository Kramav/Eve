# Eve — Feature Roadmap

Priority tiers: **P1** (next up) → **P2** (soon) → **P3** (future consideration)

---

## P1 — High Priority

### Tiling / Window Management
- **Workspace presets** — save all current window positions as a named preset, restore by voice.
  "save layout as work", "restore work layout". Store in `tiling_layouts.json` under a `workspaces` key.
  Python: `core/window_manager.py` already has `enumerate_windows()` as the foundation.
- **Voice-config Window Manager** — Window Manager should be configurable by voice now that monitor
  numbers are visible. Examples: "Set monitor one to 2x2 grid", "Name monitor 2 Primary display",
  "Set HUD display to primary display", "Move HUD to monitor three", "Move HUD window 1 top-left".
  Phase 1 (visual identify) is done; this is phase 2 — voice → WM state mutation.

### Dialogue
- **Generalize Converse pattern** — extend the single-turn `pending_confirm` mechanic into a full
  converse layer so any command handler (timers, reminders, video playback) can claim follow-up
  utterances. Example: "set timer 5 minutes" → "cancel it" routes back to the timer handler without
  re-matching from scratch. Pattern modeled on OVOS ConverseService. Lives in `core/session.py`
  alongside the existing `Mode` and `pending_confirm`.

### UI / UX
- **Command Editor inline UI** — replace the current external editor (`open_editor()` opens a file
  in Notepad/VS Code) with a first-class BrowserWindow panel styled to match App Manager and
  Window Manager. Inline editor with syntax highlighting for the custom commands JSON/YAML,
  save button, live reload on save. Lives in `ui/src/command-editor/`.

### TTS
- **Change TTS Tone** — default Piper voice (`en_US-lessac-medium`) sounds bad. Voice swap is now
  fully wired (drop `.onnx` + `.onnx.json` pair into `models/voices/`, pick from Voice Settings
  dropdown), so this is just a matter of grabbing a better voice from
  https://huggingface.co/rhasspy/piper-voices and selecting it.

---

## P2 — Medium Priority

### Fallback
- **LLM fallback via Ollama** — when no intent matches and the catalog score is below MED threshold,
  route to a local Ollama model instead of returning "not recognized." Config: `FALLBACK_LLM = "ollama"`
  / `"none"`. Model: `llama3` or `mistral`. Keeps Eve useful for general questions without cloud
  dependency. Add at bottom of `dispatch()` in `core/dispatcher.py`, just before the final
  "Not recognized" return.

### Tiling / Window Management
- **Auto-snap on launch** — if an app has a saved zone assignment, auto-snap it when opened via
  voice rather than centering on monitor. Requires a zone-per-app mapping in `tiling_layouts.json`.
  `commands/apps.py` open_app() checks tiling config and calls `move_new_window_to_rect` with the
  saved zone rect.

---

## P3 — Future Consideration

### Automation
- **Scheduled research agent** — Claude Code remote routine (claude.ai/code/routines) that runs every
  5 hours, searches for new voice assistant / TTS / STT / window management developments, and pushes
  a `RESEARCH.md` update directly to the GitHub repo. Requires a GitHub PAT with repo write access
  set as `GITHUB_TOKEN` in the CCR environment. A second agent (manually triggered) reviews
  `RESEARCH.md` and promotes findings to ROADMAP.md. Blocked on: setting up GitHub PAT in cloud env.

### Architecture
- **STT abstraction layer** — abstract `core/transcriber.py` behind an `STTEngine` interface.
  Allows swapping Whisper for Vosk (faster/smaller) or cloud STT via config, no code change.
- **Skill entry points** — dynamic plugin loading via `pyproject.toml` entry points instead of
  hardcoded imports. Enables external custom skill packages. Only relevant if Eve becomes
  multi-user or shareable.
- **Testing framework** — pytest suite for dispatcher regex patterns, integration tests for
  multi-step commands. Catch regressions when adding new intents.

### Voice / UX
- **Wake word customization** — allow users to set a custom wake word via the App Manager UI
  rather than editing `config.py`. Store in `settings.json`.
- **Multi-turn reminders** — "remind me to check email" → "when?" → "at 3pm". Requires
  generalized Converse pattern (P1) first.
- **Confidence scores** — return confidence alongside responses; surface low-confidence matches
  with a confirmation prompt rather than executing blindly. (Partly done — see `intent_match.py`
  tiered confidence; could be extended to in-pipeline intents.)

### Platform
- **Windows notification integration** — use Windows toast notifications for reminders instead of
  (or in addition to) TTS. `winotify` or `plyer` library.
- **Startup on login** — register Eve in Windows startup via registry or Task Scheduler.
  Currently requires manual launch.

---

## Completed (reference)

| Feature | Notes |
|---------|-------|
| Window Manager UI | Monitor cards, display picker, HUD pinning |
| Tiling WM | Zone presets, voice snap, layout panel in WM UI |
| HUD drift fix | `set-size` uses `getOverlayDisplay()` not dynamic window center |
| App close/kill | Graceful `close` vs force `kill` distinction |
| Prefix retry | Unrecognized "firefox" → tries "open firefox" |
| Mishear substitutions | Expanded set: "at manager" → "app manager", "hood" → "hud", "voice manor" → "voice manager", filler-word strip ("show me", "please"), verb mishears, etc. |
| TTS gate on listener | Wake word suppressed while Eve is speaking |
| Silence threshold fix | Raised 400 → 800 to stop 30-second recording timeouts |
| Firefox maximize | `ShowWindow(SW_MAXIMIZE)` after placement in monitor.py |
| Open app manager intent | Added to dispatcher INTENTS so close/kill work via voice |
| Piper TTS | `core/speaker.py` rewritten; `PiperVoice.synthesize()` → `audio_float_array` → sounddevice |
| Routing Directory UI | Separate `dirWin` (700×520) + `orbWin` (96×96) + system tray; module tiles, activity feed, status strip, result list |
| Orb toggle behavior | Orb click toggles directory open/close via `toggle-directory` IPC |
| X button / tray hide | Close button always hides to tray; never kills process; `hideDirectory()` resets expanded state |
| Fullscreen bounds save/restore | `_savedDirBounds` saved before expand; `setBounds()` restores atomically on collapse |
| DWM white border fix | `dirWin` uses `transparent: true` to eliminate NC area border on focus transfer |
| Monitor-aware fullscreen | `screen.getDisplayMatching(dirWin.getBounds())` expands to window's current display |
| Blink-on-open fix | `dirWin` pre-warmed at startup; `ready-to-show` guard ensures `show()` only fires after first paint |
| NC resize handle fix | Removed all `setResizable(true/false)` calls; `dirWin` stays `resizable: false` always |
| Expand button state | `directory-size-changed` IPC event syncs button icon (⛶ / ❐) to expanded state |
| Voice Settings panel | Sliders for speed/expressiveness/pitch, presets w/ save+delete, Test/Save/Defaults, persisted in `settings.json` |
| Voice commands for every panel | "app manager", "window manager", "voice manager"/"voice settings", "command editor", "routing directory" all open via voice |
| Open/close routing directory split | Separate `_OPEN_DIRECTORY` and `_CLOSE_DIRECTORY` regexes; `show_directory()`/`hide_directory()` are state-checked no-ops if already in target state |
| Orb above fullscreen games | `setAlwaysOnTop(true, 'screen-saver', 1)` + `setVisibleOnAllWorkspaces` + 2s periodic re-assert defeats Windows' demotion of topmost flags when fullscreen apps grab focus |
| Routing directory above fullscreen | Same z-order treatment as orb; `present()` pre- and post-asserts topmost around `show()` |
| Online/Offline listener toggle | Clickable state pill in directory titlebar; toggles `listener.enabled`; offline state shown via red dot + dim orb |
| Snap + open | "snap firefox to top" now launches Firefox AND places it in the top zone via `apps.open_app(snap_rect=...)` + `monitor.move_new_window_to_rect` |
| Snap UI panels to zones | "snap window manager to top-left" works for routing directory, app/window/voice managers via WS `snap_panel` → IPC → `setBounds` |
| DPI-aware tiling | Python set to PROCESS_PER_MONITOR_DPI_AWARE; Window Manager saves per-monitor `scaleFactor`; `_zone_pixel_rect(physical=...)` converts DIPs → physical px for Win32 |
| Multiple TTS voices | `models/voices/` directory scanned at startup; Voice Settings dropdown lists all available; live swap via speaker sentinel queue preserves speed/noise tuning across switch |
| Filler-word tolerance | Overlay regex allows up to 2 filler words via `(?:\w+\s+){0,2}?`; `re.I` + `\s+` make it forgiving of NBSP and case |
| Multi-aliased "hud" command | "hud", "show hud", "hide hud" all route to overlay toggle |
| Tiered fuzzy guess pipeline | `core/intent_match.py` builds 60+ phrase catalog (apps + panels + aliases); `rapidfuzz.token_set_ratio` scoring; HIGH ≥ 88 silent-exec, MED ≥ 68 "did you mean?", below MED no-match |
| Single-turn confirmation | `Session.pending_confirm` stashes a callable + args; next utterance checked for yes/no; "did you mean" prompts auto-resolve |
| Near-miss intent suggestion | Phrase similarity against intent catalog; was P2, now done as part of the tiered guess pipeline |
| Utterance preprocessing | Centralized `_apply_mishear_subs()`; whitespace collapse + filler removal happens before regex + before catalog score |
| Identify Monitors (visual) | "identify monitors" / WM Identify button briefly flashes a big numbered card on each display (~3.5s); primary monitor styled green. `ui/src/monitor-id/` + `identifyMonitors()` in `ui/main.js` + WS `identify_monitors` action |
