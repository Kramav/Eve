# Eve — Feature Roadmap

Priority tiers: **P1** (next up) → **P2** (soon) → **P3** (future consideration)

---

## P1 — High Priority

### Tiling / Window Management
- **Snap + open** — "snap firefox to top" when Firefox is closed should open it *and* snap it.
  Currently returns "Firefox isn't open." Fix: combine `apps.open_app()` + `monitor.move_new_window()`
  but pass zone rect instead of centering. Lives in `commands/tiling.py`.
- **Workspace presets** — save all current window positions as a named preset, restore by voice.
  "save layout as work", "restore work layout". Store in `tiling_layouts.json` under a `workspaces` key.
  Python: `core/window_manager.py` already has `enumerate_windows()` as the foundation.
- **Identify Monitors** - display numbers on each monitor for UX. This should streamline the Window Manager process.  Window manager should be configurable with voice commands. "Set monitor one to 2x2 grid"  "Name monitor 2 Primary display" "Set HUD display to primary display"  "Move hud to monitor three" "Move hud window 1 top-left"

### Voice / UI Commands
- **Fix routing directory voice commands** — "open directory" / "close directory" not reliably
  triggering show/hide. Audit `_OPEN_DIRECTORY` regex in `main.py` and the `toggle_overlay()`
  path in `core/display.py`; ensure open and close are separate intents, not a single toggle.
- **Voice labels must match UI labels** — rule: any string visible on a tile or button must work
  as a spoken command (with or without "open"). Enforce by keeping `MODULES` labels in sync with
  the regex patterns in `main.py` whenever either side changes.

### Dialogue
- **Converse pattern** — generalize `core/session.py` beyond YouTube/Playing mode so any command
  handler can claim follow-up utterances. Example: "set timer 5 minutes" → "cancel it" should route
  back to the timer handler without re-matching. Pattern modeled on OVOS ConverseService.

### UI / UX
- **Command Editor inline UI** — replace the current external editor (`open_editor()` opens a file
  in Notepad/VS Code) with a first-class BrowserWindow panel styled to match App Manager and
  Window Manager. Inline editor with syntax highlighting for the custom commands JSON/YAML,
  save button, live reload on save. Lives in `ui/src/command-editor/`.

### Change TTS Tone
- **Tone is bad** - I hate the default Piper-TTS voice
---

## P2 — Medium Priority

### Voice Understanding
- **Near-miss intent suggestion** — when a phrase doesn't match any intent but is close to a
  known command label or panel name, speak a confirmation prompt instead of "not recognized."
  Example: "voice manager" → "Did you mean voice settings?" User says "yes" / "no" to confirm.
  Different from prefix-retry (which guesses the command) — this guesses the *meaning* using
  fuzzy string similarity (`rapidfuzz.fuzz.partial_ratio`) against a flat list of labeled intents
  and UI panel names. Requires the converse pattern (P1) to handle the yes/no follow-up.
  Lives in `core/dispatcher.py` as a third-pass after prefix-retry, before Silent fallback.
- **Padatious-style intent matching** — add an example-based intent layer alongside regex.
  Write phrase examples instead of hand-tuned patterns; handles natural paraphrasing.
  Could use `padatious` pip package or `rapidfuzz` for lightweight fuzzy matching.
  Add as a second-pass in `_guess_dispatch()` before the prefix-retry.
- **Utterance transformer pipeline** — formalize `_MISHEAR_SUBS` into a configurable list of
  pre-processing functions (spell correction, abbreviation expansion, filler word removal).
  Lives in `core/dispatcher.py`. Makes mishear handling easier to extend.

### Fallback
- **LLM fallback via Ollama** — when no intent matches, route to a local Ollama model instead of
  returning "not recognized." Config: `FALLBACK_LLM = "ollama"` / `"none"`. Model: `llama3` or
  `mistral`. Keeps Eve useful for general questions without cloud dependency.
  Add at bottom of `dispatch()` in `core/dispatcher.py`.

### Tiling / Window Management
- **Auto-snap on launch** — if an app has a saved zone assignment, auto-snap it when opened via
  voice rather than centering on monitor. Requires a zone-per-app mapping in `tiling_layouts.json`.
  `commands/apps.py` open_app() checks tiling config and calls `move_new_window` with zone rect.

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
  converse pattern (P1) first.
- **Confidence scores** — return confidence alongside responses; surface low-confidence matches
  with a confirmation prompt rather than executing blindly.

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
| Mishear substitutions | "at manager" → "app manager" etc. |
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
