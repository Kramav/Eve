# Eve — Feature Roadmap

Priority tiers: **P0** (public release blockers) → **P1** (next up) → **P2** (soon) → **P3** (future consideration)

---

## P0 — Public Release Blockers

> These must be resolved before Eve can be shared publicly or accept outside contributors.

### 1. Platform scope decision — *DECIDED: own Windows*
**Decision (2026-06-24): Eve is a Windows-first voice assistant — "the best voice assistant for Windows."** Rationale: the entire value layer is already Win32-native and hard to abstract without gutting it — DPI-aware tiling, `raise_to_top_no_focus`/foreground-lock z-order tricks, `EnumWindows` identification, the HKCU autostart key, WinRT toasts, `.lnk` app scanning, Discord global-keybind injection, borderless-fullscreen game detection. Cross-platform would mean reimplementing all of it per-OS (or dropping it), turning Eve's differentiator into its weakest feature. Going deep on Windows is the higher-leverage bet.

**Consequences / guardrails:**
- New Win32 usage is fine and encouraged; no need to gate it behind a platform abstraction.
- Keep OS calls funneled through `core/` helpers (`window_ops`, `key_ops`, `monitor`, `autostart`, `notify`) rather than scattering `ctypes` across `commands/` — so *if* a port is ever wanted, the surface is contained. (This is good hygiene, not a cross-platform promise.)
- README/marketing should say "Windows 10/11" plainly so nobody expects macOS/Linux.
- `setup.py` already checks for Windows-only deps; no change needed.

### 2. Test suite — *expanded*
`tests/test_dispatch.py` covers INTENTS routing (protected-programs, workspace presets, monitor naming, per-zone HUD targeting, auto-snap, consolidated panel routing), BROWSING mode, feature-gating, mishear subs, **skill loading + integration**, and an `INTENTS`-compile/handler guard. **Execution tests added:** `tests/test_timeparse.py` (12 deterministic cases for relative/absolute/recurring/split parsing via injected `now` — no mocks); Discord focus-deferral (`test_discord_navigation_defers_when_protected` / `…_proceeds_when_unprotected`, light monkeypatch of `essential.active` + `_discord_hwnd`). Tiling is covered by routing tests + the `_best_open_window` self-check (`python commands/tiling.py`). Both files run under `pytest tests/` or standalone. **Remaining:** reminders *scheduler* re-arm logic (recurring re-fire) still untested — needs a fake clock around `commands.reminders` checker.

### 3. Plugin/skill system — *DONE*
~~Adding a new command required editing `core/dispatcher.py`.~~ **Done:** [core/skills.py](core/skills.py) imports every `skills/*.py` at startup (`skills.load(display)` from main.py) and collects each file's module-level `INTENTS` list (same `(regex, handler)` shape as built-ins). Optional `PRIORITY` (ordering among skills), `FEATURE` (gate on features.json), and `setup(display)` hook. Skill intents are tried in `dispatch()` after built-ins and before the fuzzy/LLM fallback, so they extend without overriding. Import/handler errors are caught + logged so one bad skill can't crash Eve. Ships with [skills/example_dice.py](skills/example_dice.py) (roll/flip) + [skills/README.md](skills/README.md). Tests: `test_skill_loading`, `test_skill_integration_through_dispatch`. This also supersedes P3 "Skill entry points" (file-drop is simpler than pyproject entry points for this use case).

**Related smell — the dispatcher is a hand-ordered ~350-line regex wall.** Still true for *built-in* intents (skills don't need it — they're priority-ordered). Interim guard added: `test_all_intents_compile_and_are_callable` fails on a typo'd pattern or misreferenced handler. A full "no two patterns both match canonical phrase X" self-check is still worth adding if the built-in table keeps growing.

### 3a. Delete the duplicate pre-dispatch routing in `main.py` — *DONE*
~~`main.py`'s `on_command` ran 7 hardcoded regex blocks before `dispatch()`, shadowing INTENTS.~~ **Done:** the 7 blocks + regex constants are deleted; `dispatch()` is now the single routing authority. New `core.response.Panel` (subclass of `Silent`) marks panel open/close/toggle actions so main.py hides the HUD immediately and doesn't speak — preserving the former silent + delay-0 UX. New `system.toggle_overlay` + a top-of-INTENTS toggle intent preserve the "show/hide/bare hud toggles the overlay" behavior; voice-settings + app-manager intents broadened to absorb the bare "voice settings" / "manage apps" / "open apps" forms. Routing tests updated (`test_directory_and_identify`, `test_consolidated_panel_routing`) — they now assert the *real* app behavior instead of the old dead-code path. Residual intentional in-dispatcher overlap (toggle above show/hide for hud) is by design and is what the #3 startup self-check would whitelist.

### 3b. Verify-by-running gap
Recent features (focus-essential, workspace presets, monitor naming, Ollama fallback, auto-snap on launch) were verified by tracing regex + signatures, **not** by executing `tests/test_dispatch.py` or launching the app (run prompts were declined). "Done" currently carries an asterisk until the self-checks (`python core/essential.py`, `python commands/tiling.py`) and `python tests/test_dispatch.py` are run green, and a manual smoke of one snap / one protect command confirms the Win32 paths actually fire. Low effort, removes the asterisk.

### 4. Remove hardcoded assumptions — *mostly done*
**Done:** WS port/host centralized to `config.WS_PORT`/`WS_HOST` (display.py imports them; env-overridable via `EVE_WS_PORT`/`EVE_WS_HOST`). Wake word already lived in `config.WAKE_WORD`; the dispatcher's hardcoded spoken-prefix tuple is now `config.WAKE_PREFIXES` (`EVE_WAKE_PREFIXES`). Screenshot dir → `config.SCREENSHOT_DIR` (`EVE_SCREENSHOT_DIR`). **Remaining/deferred:** the Electron renderer files still hardcode `ws://127.0.0.1:7734` in their CSP `connect-src` + `WS_URL` (8 files) — a build-time inject is only worth it if a configurable port is ever actually needed (ponytail). Data-file paths (`apps.json`, `settings.json`, `tiling_layouts.json`, `eve_memory.json`) are repo-relative and deterministic, not user-facing settings — intentionally left as-is.

### 5. Distribution — *script ready, build step pending*
**Done:** [installer/eve.iss](installer/eve.iss) — an Inno Setup 6 script that wraps a pre-built `dist\Eve\` folder into `Eve-Setup-<ver>.exe` with Start Menu shortcut, optional desktop icon, optional run-at-login (writes the same HKCU Run key as `core/autostart.py`, removed on uninstall), and an uninstaller. Per-user install (no admin prompt). [installer/README.md](installer/README.md) documents the two-stage build (Electron UI → PyInstaller onedir freeze of `main.py` with the right `--add-data`/`--collect-all` flags) and a winget follow-up. **Remaining:** actually run the build toolchain (PyInstaller + npm) to produce `dist\Eve\` and `iscc` it — needs a Windows box with the toolchain; expect to iterate the PyInstaller hidden-imports for openwakeword/piper/sounddevice the first time.

---

## P1 — High Priority

### TTS
- **Change TTS Tone** — *addressed via the Kokoro engine* (see Completed). Piper voice swap is
  still wired for the lightweight path; for a big quality jump set `TTS_ENGINE=kokoro` and drop the
  two Kokoro model files in `models/kokoro/`. **Remaining (user step):** `pip install kokoro-onnx`
  + download the model files, then pick a voice. No code left.

### Visual Navigation skill — *Phase 1 done; Phase 2 mostly done (2d ONNX detector pending)*
Hands-free mouse control was refactored out of core into an optional, feature-gated drop-in skill
([skills/visual_nav.py](skills/visual_nav.py), `visual_nav` flag, default off, Alpha group). It grew
from "nudge the mouse" into voice-driven element navigation: enumerate the interactive elements of
the focused window and pick them by number ("what can I click" → "open number 2"). Cheapest-method
priority order, **no continuous computer vision**.

Provider-based, planner-selected:
- **`AccessibilityProvider`** *(Phase 1, done)* — Windows UI Automation control tree of the
  foreground window (Hyperlink/Button/ListItem/TabItem/…), via the new `uiautomation` dep. Lazy +
  guarded; feature shows "unavailable — pip install uiautomation" when absent.
- **`InputController`** *(Phase 1, done)* — native pyautogui move/click/double/right/scroll + key_ops
  type/hotkey (absorbed the old `commands/handsfree.py` mouse logic).
- **`NavigationPlanner`** *(Phase 1, done)* — accessibility → vision fallback; numbers elements,
  drives the cursor to a chosen one. Light cache keyed by foreground (hwnd, title).
- **Modal capture** *(Phase 1, done)* — via the existing Converse layer (no `Mode.HANDSFREE` hook in
  core), so the skill is fully decoupled. Declines fall through so normal commands still work mid-mode.
- **`VisionProvider`** *(Phase 2, done)* — a **cascade of backends** in [commands/vision.py](commands/vision.py),
  tried cheapest-first via `config.VISION_BACKENDS` (default `["rapidocr"]`). On-demand screenshot only,
  **no continuous CV**; result cached by an 8×8 perceptual hash so an unchanged screen isn't re-scanned.
  Backends: **`OcrBackend`** (RapidOCR, CPU, no GPU/key — the low-end default) · **`CloudVisionBackend`**
  (Claude `claude-haiku-4-5` / GPT `gpt-4o-mini` via stdlib urllib, set-of-marks-friendly, off-machine
  compute for weak hardware) · **`OllamaVisionBackend`** (local `moondream`/llava, needs a GPU) ·
  **`OnnxUiBackend`** *(2d, dormant — `available()=False` until a detector model is dropped at
  `models/ui_detector.onnx`)*. Cloud/Ollama add **no dependency** (urllib).
- **Select-by-description** *(Phase 2, done)* — "open the tutorial video" fuzzy-matches the spoken phrase
  to an element label via `rapidfuzz` (cutoff 70); declines on no match so "open firefox" still launches.
- **Numbered coordinate overlay** *(Phase 2, done)* — "what can I click" tags each element on screen,
  reusing `display.identify_windows` (generic `{index,label,x,y,w,h}` payload).
- **Cloud-key UI** *(Phase 2b, done)* — the API-Keys panel ([ui/src/integrations](ui/src/integrations))
  is now **data-driven** (one card per service from a `SERVICES` list); Anthropic + OpenAI keys added.
  `core/display._test_api_key` routes `integrations:test_<service>` to `vision.test_key` (minimal
  `max_tokens:1` auth ping). Keys resolve via `vision.vision_key()` (settings.json → env), masked in the UI.
- **`BrowserProvider`** *(Phase 2, deferred)* — documented provider slot; no automation dep today.
- **Phase 2 remaining** — `OnnxUiBackend` model + setup download (2d); drag-and-drop; set-of-marks
  prompt mode for cloud/ollama to harden click accuracy on coordinate hallucination.

Tests: [tests/test_visual_nav.py](tests/test_visual_nav.py) (parser/planner/handler/select-by-desc) +
[tests/test_vision.py](tests/test_vision.py) (cascade order, key resolution, JSON parse + scaling,
phash, key-tester) — all against fakes, no UIA/OCR/network/mouse. **Known minor gap:** "start/launch
hands free mode" routes to app-launch (skill intents run after `apps.open_app`); "hands free mode" /
"mouse mode" / "enter hands-free mode" work. **Remaining (user steps):** `pip install uiautomation`
(accessibility tier) and optionally `pip install rapidocr-onnxruntime` (OCR vision tier, no GPU); enable
"Hands-free Visual Navigation" in the App Manager. Cloud vision: add an Anthropic/OpenAI key in the
API-Keys panel and set `EVE_VISION_BACKENDS=rapidocr,claude` (or `gpt`/`ollama`).

---

## P2 — Medium Priority

_All P2 items done — see Completed table (LLM fallback via Ollama, Auto-snap on launch)._

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
  **Deferred (YAGNI):** there is exactly one STT implementation today; build the interface when a
  second engine is actually wanted, not before. Adding it now is a speculative abstraction.
- ~~**Skill entry points**~~ — superseded by the P0 #3 drop-in skill loader (`skills/*.py`,
  [core/skills.py](core/skills.py)). `pyproject.toml` entry points would only be needed if skills
  ship as installable PyPI packages; the file-drop covers the stated use case.
- ~~**Testing framework**~~ — covered by P0 #2: `tests/test_dispatch.py` + `tests/test_timeparse.py`
  (routing, execution, skills, compile guard); zero-dep runners + pytest.

### Voice / UX
- **Wake word customization** — *backend done*: `core/listener.resolve_wake_word()` prefers
  `settings.json` `wake_word` over `config.WAKE_WORD`, so the wake word is overridable without a
  code edit (takes effect on next launch — the model loads once at startup). **Remaining:** an App
  Manager UI field to pick from available openwakeword models + write `settings.json`.
- **Confidence scores** — return confidence alongside responses; surface low-confidence matches
  with a confirmation prompt rather than executing blindly. (Partly done — see `intent_match.py`
  tiered confidence; could be extended to in-pipeline intents.)

### Platform
- ~~**Windows notification integration**~~ — *done*: `core/notify.toast(title, body)` fires a native
  WinRT toast via PowerShell (no new dependency, runs under the built-in PowerShell AppUserModelID,
  title/body passed as env vars so no quoting/injection). Wired into the reminder callback in
  `main.py` alongside TTS; best-effort (any failure swallowed). Persists in the Action Center.
- ~~**Startup on login**~~ — *done*: `core/autostart.py` registers `"<pythonw>" "<repo>/main.py"`
  in the HKCU `…\CurrentVersion\Run` key (no admin, reversible). Voice: "add eve to startup" /
  "start eve when I log in" / "remove eve from startup" / "don't start eve at login" → dispatcher
  `_autostart_enable`/`_disable`/`_status` shims (placed above apps-open so "start eve on login"
  isn't read as launching an app). Tests: `test_autostart_routing`.

---

## Completed (reference)

| Feature | Notes |
|---------|-------|
| Visual Navigation Phase 2 — vision cascade | [commands/vision.py](commands/vision.py): VisionProvider is a cheapest-first cascade (OCR/RapidOCR → ONNX-stub → cloud Claude/GPT → Ollama) over one on-demand screenshot, phash-cached. Adds select-by-description (rapidfuzz), numbered coordinate overlay (reuses `identify_windows`), and a data-driven API-Keys panel with Anthropic/OpenAI keys (`vision.test_key` auth ping). Cloud/Ollama need no new dep (urllib). Default backend OCR-only — no GPU. Tests: [tests/test_vision.py](tests/test_vision.py). 2d ONNX detector still pending |
| Hands-free → Visual Navigation skill (P1, Phase 1) | Moved hands-free mouse control out of core (`commands/handsfree.py` + `Mode.HANDSFREE` + dispatcher hooks all deleted) into optional gated skill [skills/visual_nav.py](skills/visual_nav.py). Adds UIA accessibility tier (`uiautomation`) + numbered element selection ("open number 2") + native input + planner + VisionProvider stub. Modal capture via the Converse layer (no core hook). See the P1 "Visual Navigation skill" entry |
| Off-switches for auto-behaviors | Added `notifications` + `game_protection` feature flags ([core/features.py](core/features.py) DEFAULTS+LABELS → auto-render as App Manager toggles). `notifications` off → reminders skip the Windows toast (gated in main.py `on_reminder`); `game_protection` off → `essential.active()` returns None so fullscreen auto-detect + protect-list stop deferring focus. Both default on |
| STT speed+accuracy | `WHISPER_MODEL="auto"` ([config.py](config.py)) → `transcriber._resolve_model()` picks `distil-large-v3` on GPU / `distil-small.en` on CPU (both faster *and* more accurate than the old `small.en`). transcribe() tuned for short commands: `condition_on_previous_text=False` (no cross-command hallucination), `temperature=0.0` (deterministic, fastest decode), VAD filter. Pairs with the GPU device option |
| Kokoro TTS engine | `core/speaker.py` refactored to a small engine interface (`synth`/`set_params`/`voice_id`); `TTS_ENGINE=piper`(default)/`kokoro`/`auto`. `_KokoroEngine` uses `kokoro-onnx` (no torch) + models in `models/kokoro/`; `_make_engine()` falls back to Piper if Kokoro is requested but unavailable, so TTS never dies. `list_voices()` + `features._compute_status` are engine-aware. Answers the P1 "change TTS tone" item — far more natural than Piper lessac. User step: `pip install kokoro-onnx` + download `kokoro-v1.0.onnx`/`voices-v1.0.bin` |
| Tool-calling LLM fallback | `commands/fallback.py` upgraded from plain Q&A to Ollama **function calling**: 9 tools map to real handlers (open/close app, snap window, bring-to-front, web search, go-to-site, play YouTube, set timer/reminder), each feature-gated. `dispatch()` → `fallback.answer()` now executes weird-phrasing commands the regex misses ("could you throw firefox on my left screen"). Graceful degradation: chat/tools → plain `/api/generate` → None. Needs a tool-capable Ollama model (llama3.1+/qwen3/mistral-nemo) + `FALLBACK_LLM=ollama` |
| Whisper GPU option | `config.WHISPER_DEVICE` (`auto`/`cuda`/`cpu`) + `WHISPER_COMPUTE` (env `EVE_WHISPER_DEVICE`/`_COMPUTE`). `transcriber._resolve_device_compute()` auto-probes for an NVIDIA GPU via `ctranslate2.get_cuda_device_count()` and falls back to CPU; defaults float16 on GPU / int8 on CPU. Runtime safety net: if the GPU model load throws (cuDNN/driver), it degrades to CPU instead of failing to start. Was hardcoded `cpu`/`int8` |
| Drop-in skill system (P0 #3) | `core/skills.py` loads `skills/*.py` at startup; each defines `INTENTS` (+ optional `PRIORITY`/`FEATURE`/`setup`). Tried in `dispatch()` after built-ins, before fuzzy/LLM. `skills/example_dice.py` + `skills/README.md`. Supersedes "skill entry points" |
| Single-router consolidation (P0 #3a) | Deleted main.py's 7 pre-dispatch regex blocks; `dispatch()` is the only router. `core.response.Panel` marks panel actions (silent + delay-0); `system.toggle_overlay` + top-of-INTENTS toggle intent preserve HUD-toggle behavior |
| Config centralization (P0 #4) | `config.WS_PORT`/`WS_HOST`/`WAKE_PREFIXES`/`SCREENSHOT_DIR` (all env-overridable); display/dispatcher/system import them. Renderer WS port + repo-relative data paths intentionally left |
| Startup on login (P3) | `core/autostart.py` HKCU Run-key register/unregister; voice "add eve to startup" / "don't start eve at login" |
| Windows toast notifications (P3) | `core/notify.toast()` WinRT toast via PowerShell (no new dep, env-var args); fires on reminders alongside TTS, best-effort |
| Wake-word override (P3, backend) | `core/listener.resolve_wake_word()` prefers `settings.json` `wake_word` over `config.WAKE_WORD`; UI picker still TODO |
| Auto-snap on launch | Per-app default zone in `tiling_layouts.json` → `app_zones` `{app: {zone, monitor?}}`. "always open firefox in top-left" / "auto-snap discord to right [of monitor 2]" → `tiling.set_app_zone` (validates the zone resolves before saving); "stop auto-snapping firefox" → `tiling.clear_app_zone`; intents placed at the top of the tiling block (before snap patterns + apps-open). `apps.open_app()` calls `tiling.zone_rect_for_app(name)` when launched with no explicit rect and snaps there instead of centering. 5 dispatcher intents |
| LLM fallback via Ollama | `commands/fallback.answer(text)` POSTs to a local Ollama server (`/api/generate`, stdlib `urllib` — no new dep) with a "one or two short spoken sentences" system prompt; wired at the very bottom of `dispatch()` after the fuzzy guess, before "Not recognized". Opt-in: `config.FALLBACK_LLM` (`"ollama"`/`"none"`, default none) + `OLLAMA_HOST`/`OLLAMA_MODEL` (all env-overridable). Any failure (server down, model missing, 20s timeout) returns None → plain not-recognized reply, never hangs |
| Custom monitor naming + per-zone targeting | (1) **Naming** — "name monitor 2 primary display" / "label display 1 gaming" / "name the left monitor coding" saves a display-only label in `tiling_layouts.json` → `monitor_names` (keyed by stable saved monitor id when resolvable, else the spoken ref). `commands/window_manager.name_monitor`; two intents placed *before* Identify Monitors so the shared "label" verb routes to naming when a name tail is present, else falls through to identify. ponytail: name is saved + spoken back, not yet rendered live in the WM panel. (2) **Per-zone HUD targeting** — fixed `tiling._snap_panel` which ignored the monitor qualifier (always primary/first-match); it now resolves an explicit monitor and passes `monitor_id` to `_resolve_zone`. "move/snap hud to top-left of monitor 1" snaps the directory panel into that zone on that monitor (`dispatcher._snap_hud_zone_monitor` shim, intent before move_orb_corner/move_hud); bare "move hud to monitor 2" still relocates the orb, "move hud to top-left" still pins the orb corner |
| Workspace presets | "save layout as work" snapshots every open window's {exe,title,x,y,w,h} into `tiling_layouts.json` → `workspaces` → name; "restore work layout" greedily re-matches each saved window to an open one (exe then closest title via `_best_open_window`) and moves it with `_snap_hwnd_to_rect` (no focus steal); "what layouts do I have" lists them. `commands/tiling.save_workspace`/`restore_workspace`/`list_workspaces` + 4 dispatcher intents placed above snap/open_app. Built on `tiling.enumerate_windows()` (used over `window_manager.enumerate_windows()` since it already carries exe basename) |
| Focus & front essential programs | `core/essential.py` keeps a dynamic protected set in `settings.json` (`essential_programs`). `active()` matches the foreground window's exe basename/title against the list AND auto-protects any borderless/exclusive-fullscreen foreground app (reuses `window_ops.fullscreen_app_running`); `should_defer()` is the gate. Voice: "protect X" / "treat X as essential" / "this is my game" (no name → current foreground), "stop protecting X" / "that's not essential", "what's protected" → `commands/context.protect_program`/`unprotect_program`/`list_protected`, intents placed high so they beat snap/open_app. Gating: Discord nav + send-message (`commands/discord._deferred`) decline with a "X is protected" message instead of stealing focus via `with_window_focused`. App-launch/panel-open focus left ungated (panels already `showInactive`; launching is user-requested) — `ponytail:` extend if a game still loses focus on open |
| License | AGPL-3.0 `LICENSE` present at repo root (was P0 #1) |
| First-run setup + hardening | `setup.py` does the full one-command flow (Python check, pip, wake-word + Piper voice download, npm, mpv/Firefox checks, default config) and is idempotent. Hardened: `create_defaults` imports `core.features.DEFAULTS` (no drift); downloads go to `.part` temp + atomic rename; `check_firefox()` + shared `apps.find_firefox()` (PATH→registry→Program Files); search fallback degrades to default browser if Firefox absent; final core-import smoke test; seeds `discord_keys.json` from example; warns on Node < 18 |
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
| Mixed-DPI tiling | Single `scaleFactor` multiplication is wrong when monitors have *different* scales (preceding higher-DPI monitor shifts later ones' physical x). Fix: WM saves the physical work-area rect at save time via Electron's `screen.dipToScreenRect`, which knows every monitor's individual DPI. `commands/tiling._zone_pixel_rect(physical=True)` prefers `physX/Y/Width/Height` over `workX*scaleFactor`. Re-save layouts in the WM panel (or re-trigger "set monitor N to ...") to populate the new fields |
| Command Editor inline UI | Replaces the tkinter `editor.py` subprocess with an Eve-themed Electron panel: tabs for Commands / Apps / Aliases / Raw JSON, inline editing (no modal dialogs), 500 ms debounced auto-save, native file picker for app paths, built-in JSON syntax highlighter (textarea + tokenized `<pre>` overlay with synced scroll, Ctrl+S save), live-reload notification across windows, duplicate-phrase and invalid-row indicators. `commands.system.open_editor` is now a thin wrapper around `display.open_command_editor()`. Legacy `editor.py` left in place for one release; can be removed once stable. `ui/src/command-editor/` + 9 IPC handlers in `ui/main.js` |
| Discord voice control | Three-mode hybrid: (1) **In-call essentials** (`mute me` / `deafen me` / `disconnect from voice`) send Discord's user-configured global keybinds via `pyautogui` — no focus theft, game/browser keeps focus; (2) **Navigation** (`next channel` / `previous server` / `open discord search`) briefly focuses Discord via `AttachThreadInput` + `SetForegroundWindow`, sends the in-app shortcut, restores previous foreground; (3) **Send message** (`tell <name> <msg>` / `dm <name> <msg>` / `send <msg> to <name> on discord`) opens quick switcher, fuzzy-finds recipient, types message, sends. Keybinds in `discord_keys.json` (user mirrors them in Discord Settings → Keybinds). `core/key_ops.py` + `commands/discord.py` + 12 INTENTS patterns positioned high so they beat snap/open-app. Bare `mute`/`unmute` still toggles system mute |
| Persistent memory + pronoun follow-ups | (1) **Memory** — `remember my X is Y` saves to `eve_memory.json` (case-folded keys); `what is my X` recalls; `forget X` removes; `what do you remember` lists. Editable from a new **Memory** tile in the routing directory (key/value rows with 500ms debounced auto-save via `memory:set`/`delete` WS actions). (2) **Pronoun follow-ups** — `go back`/`undo that` reverts the last snap (window restored to its pre-snap rect, captured with `GetWindowRect` before `_snap_hwnd_to_rect`); `close that window` posts WM_CLOSE to the last targeted hwnd; `cancel it` invokes the last `cancelable` (currently wired for reminders/timers). Side-effecting handlers register a `LastAction` in `session.last_action` so the pronouns resolve. `core/memory.py` + `core/session.py LastAction` + `commands/context.py` + `ui/src/memory/` |
| Multiple TTS voices | `models/voices/` directory scanned at startup; Voice Settings dropdown lists all available; live swap via speaker sentinel queue preserves speed/noise tuning across switch |
| Filler-word tolerance | Overlay regex allows up to 2 filler words via `(?:\w+\s+){0,2}?`; `re.I` + `\s+` make it forgiving of NBSP and case |
| Multi-aliased "hud" command | "hud", "show hud", "hide hud" all route to overlay toggle |
| Tiered fuzzy guess pipeline | `core/intent_match.py` builds 60+ phrase catalog (apps + panels + aliases); `rapidfuzz.token_set_ratio` scoring; HIGH ≥ 88 silent-exec, MED ≥ 68 "did you mean?", below MED no-match |
| Single-turn confirmation | `Session.pending_confirm` stashes a callable + args; next utterance checked for yes/no; "did you mean" prompts auto-resolve |
| Near-miss intent suggestion | Phrase similarity against intent catalog; was P2, now done as part of the tiered guess pipeline |
| Utterance preprocessing | Centralized `_apply_mishear_subs()`; whitespace collapse + filler removal happens before regex + before catalog score |
| Identify Monitors (visual) | "identify monitors" / WM Identify button briefly flashes a big numbered card in the bottom-left of each display's work area (~3.5s); primary monitor styled green. `ui/src/monitor-id/` + `identifyMonitors()` in `ui/main.js` + WS `identify_monitors` action |
| Identify Zones (visual) | "identify zones" / "show tiling layouts" / "identify tiles" / "show segments" overlays each monitor's saved tiling layout — translucent zone boxes + zone names + layout-name tag in the top-left corner. Auto-dismiss after 6s; click any overlay to dismiss that monitor's overlay early. `ui/src/zone-id/` + `identifyZones()` in `ui/main.js` + WS `identify_zones` action |
| Voice-config WM (preset + HUD) | "set monitor 1 to two by two grid" / "make monitor two top and bottom" / "monitor 2 grid" applies preset (full, top-bottom, left-right, main-right, main-stack, grid-4) and writes to `tiling_layouts.json`. "move HUD to monitor 3" / "set HUD to primary" / "pin orb to monitor 1" repositions the orb + persists `overlayDisplayId`. WM UI auto-refreshes if open (via `layouts-changed` IPC). `commands/window_manager.py` (resolves spoken monitor refs + preset aliases) → WS `wm_apply_preset` / `wm_move_hud` → `ui/main.js` IPC handlers |
| Identify Windows (visual) | "identify windows" / "what's open" / "list open windows" / "show open windows" enumerates all visible top-level windows via Win32 `EnumWindows` (filtered: title, size > 200×200, exclude Eve panels + shell windows) and overlays a small numbered + labeled tag at each window's top-left corner. Auto-dismiss after 6s; click any tag to dismiss. `commands/tiling.enumerate_windows()` + `commands/windows.py` + `ui/src/window-id/` + WS `identify_windows` action |
| Snap fallback (open-window match) | "snap discord to top" now works without Discord being in `apps.json` — `commands/tiling.snap_app` falls back to `find_window_by_spoken_name()` which scores all open windows by exe basename + window title and snaps the best match. apps.json is still required to LAUNCH apps that aren't open; the fallback covers the common case of "this app is already running" |
| Converse pattern (multi-turn) | Generalized the single-turn `pending_confirm` into a full converse layer modeled on OVOS ConverseService. `core/session.py` gains a `Converse` dataclass (handler + `turns` budget + `ttl` decay) + `start_converse()`/`clear_converse()`; `core/dispatcher._handle_converse()` gives an active context first crack at each utterance (after the yes/no confirm check, before normal routing) — a clean decline falls through and leaves the context for a later, clearer follow-up. Demonstrated on timers: `set timer 5 minutes` claims follow-ups so `cancel it` / `add 2 minutes` / `make it 10 minutes` / `how long left` route back to the timer handler. Reminders gained id-targeted `cancel_one` / `_reschedule` / `_remaining_minutes`. Also widened timer-create phrasing ("set timer 5 minutes", "start a 5 minute timer") and moved those intents above the apps-open intent so "start" isn't read as an app launch; broadened the time intent to accept "what time is it" |
| Web search fix + clickable results | `html.duckduckgo.com/html` GET scraper was returning an anomaly page with zero `result__a` results (DDG blocked it). `commands/search.py` rewritten to hit `lite.duckduckgo.com/lite` first (parses `//duckduckgo.com/l/?uddg=` redirect links), fall back to an `html` POST, filter out DDG's own ad/help links, decode HTML entities, and dedupe; uses a full Chrome UA (the bare AppleWebKit string tripped bot detection). When both endpoints are throttled it returns [], and `web_search_list` falls back to the **Brave Search API** (`_fetch_brave_results`, JSON) — Brave is fallback-only to conserve the free tier's 2k/month quota; if it has no key or also fails, the browser opens. Key is set from a no-code **API Keys** UI panel (`ui/src/integrations/`, directory tile, voice "open API keys"): masked password input, Save/Test/Clear, "get a free key" link. `search.brave_key()` resolves settings.json `api_keys.brave` first, then the `BRAVE_API_KEY` env var. `display._save_api_key` / `_integrations_state` (returns only a masked `…1234` hint, never the full key) / `test_brave_key` reports invalid/quota/HTTP errors back to the panel. When DDG returns nothing AND no Brave key is configured, `web_search_list` opens the browser and speaks a one-line nudge pointing the user to "open API keys" to add a free Brave key (only nags when no key is set; stays neutral once one exists). Result rows in the routing directory are now **clickable** to open in the default browser: `display.show_list(..., links=[urls])` → `state.list_links` → `ui/src/directory` renders `.result-item.clickable` → `window.eve.openExternal` → `ipcMain 'open-external'` → `shell.openExternal`. Note: DDG aggressively rate-limits scraping; for high reliability consider a JSON search API (e.g. Brave Search free tier) |
| Bring-to-front reliability fix | `core/window_ops.raise_to_top_no_focus` rewritten. Old version's `HWND_TOPMOST→HWND_NOTOPMOST` flip dropped the window back below the active one (looked like a no-op). New deterministic approach: **lower the foreground window to just beneath the target** via `SetWindowPos(fg, hwnd, SWP_NOACTIVATE)` (lowering the foreground is not blocked by the foreground lock and keeps its focus), then lift the target to top of the non-topmost band with AttachThreadInput + `BringWindowToTop`. Also added proper ctypes `argtypes`/`restype` (handles were being truncated to 32-bit `c_int` on 64-bit Windows, corrupting handle comparisons). Still never calls `SetForegroundWindow` — no focus steal. Used by voice "bring X to front" and by snap placement |
| Reminders: absolute / recurring / multi-turn / UI | New `core/timeparse.py` (dependency-free) parses absolute ("at 3pm", "tomorrow at 9", "monday at 8"), relative ("in 5 minutes"), and recurring ("every weekday at 7am", "every 30 minutes", "every morning") phrases, plus `split()` to separate a task from its time tail. `commands/reminders.py` rewritten: entries gain `id` + `recurrence`; checker re-arms recurring reminders (daily/weekly/interval) and marks one-shots done; `schedule()` / `panel_set()` / `cancel_one()` / `get_panel_payload()`. Multi-turn: bare "remind me to X" asks "When?" and the converse layer captures the time answer (`commands/context.remind`). New intents: `remind me to … (at/every/tomorrow …)` → `ctx.remind`, `open/show reminders` → panel. New Electron **Reminders** panel (`ui/src/reminders/`, directory tile, `display.open_reminders` + `reminders:get_all/set/delete/cancel_all` WS actions, live refresh via `display.reminders_changed` from the checker `on_change` hook) — Task + natural-language When fields, debounced auto-save, recurring ↻ badge, mirrors the Memory panel |
