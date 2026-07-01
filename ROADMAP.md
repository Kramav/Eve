# Eve — Feature Roadmap

Priority tiers: **P0** (public release blockers) → **P1** (next up) → **P2** (soon) → **P3** (future consideration)

> **Note on tiers:** the P0/P1/P2/P3 labels below are *historical* — they were written when
> "ship publicly" was the goal. The **current** ordering is the North Star section directly below;
> read that first. The tier labels are kept only so old entries stay findable.

---

## North Star — Engine-first (current direction, 2026-07-01)

> **The goal right now is an exceptional *engine*, not a shipped product.** Build something that
> feels incredibly fast, polished, and intuitive to use day-to-day. Distribution comes *after* the
> engine is mature — not before. The maintainer is entering a hands-on daily-use testing period and
> expects it to surface missing skills, UX gaps, edge cases, and perf opportunities; iterate against
> that lived experience rather than a release checklist.

**Order of priority (higher beats lower when time is scarce):**
1. **Engine capability — UI/UX automation skills.** Expand the skill system with *broadly useful*
   capabilities, especially controlling and interacting with arbitrary desktop application windows.
   Push reliability until Eve can confidently operate *most* app interfaces. Prioritize skills that
   make the assistant feel **autonomous and seamless**; avoid niche one-offs. → see **Skill Library**
   and the **Visual Navigation** entry.
2. **Performance & efficiency.** A leaner, faster engine beats a bigger one. If a change meaningfully
   improves responsiveness or resource use, it outranks a new feature. → see **Performance & Efficiency**.
3. **Architecture & maintainability.** Pay down technical debt; keep the system modular, extensible,
   and scalable to many future skills. Favor long-term maintainability over short-term convenience.
   → see **Architecture**.
4. **Foundation reconsideration (Electron → ?).** Open to replacing Electron *if* it's a clear
   long-term win (startup, memory, responsiveness, UI/animation smoothness, maintainability) — but
   **only after** a written tradeoff/effort/risk/migration analysis is reviewed. No rewrite for its own
   sake. → see **Foundation — desktop shell**.

**Design principles for every decision:** speed · responsiveness · smooth UX · elegant UI · low
resource use · clean architecture · maintainability · extensibility. **Every new feature must justify
its cost in complexity and performance** — prefer a fast, polished, intuitive engine over a
feature-packed one that's slower or harder to maintain. Avoid complexity and feature bloat.

**Explicitly deprioritized:** installers, packaging, release automation, public docs, deployment,
distribution (former P0 #5). Do not spend time here until the engine feels production-ready. See the
parked note under P0 #5.

---

## P0 — (historical) Release Blockers

> Written when public release was the goal. **Public release is now deferred** (see North Star).
> Items here that also improve the *engine* (tests, dead-code removal, hardcode cleanup) still count;
> the *distribution* item (#5) is parked.

### 1. Platform scope decision — *DECIDED: own Windows*
**Decision (2026-06-24): Eve is a Windows-first voice assistant — "the best voice assistant for Windows."** Rationale: the entire value layer is already Win32-native and hard to abstract without gutting it — DPI-aware tiling, `raise_to_top_no_focus`/foreground-lock z-order tricks, `EnumWindows` identification, the HKCU autostart key, WinRT toasts, `.lnk` app scanning, Discord global-keybind injection, borderless-fullscreen game detection. Cross-platform would mean reimplementing all of it per-OS (or dropping it), turning Eve's differentiator into its weakest feature. Going deep on Windows is the higher-leverage bet.

**Consequences / guardrails:**
- New Win32 usage is fine and encouraged; no need to gate it behind a platform abstraction.
- Keep OS calls funneled through `core/` helpers (`window_ops`, `key_ops`, `monitor`, `autostart`, `notify`) rather than scattering `ctypes` across `commands/` — so *if* a port is ever wanted, the surface is contained. (This is good hygiene, not a cross-platform promise.)
- README/marketing should say "Windows 10/11" plainly so nobody expects macOS/Linux.
- `setup.py` already checks for Windows-only deps; no change needed.

### 2. Test suite — *expanded*
`tests/test_dispatch.py` covers INTENTS routing (protected-programs, workspace presets, monitor naming, per-zone HUD targeting, auto-snap, consolidated panel routing), BROWSING mode, feature-gating, mishear subs, **skill loading + integration**, and an `INTENTS`-compile/handler guard. **Execution tests added:** `tests/test_timeparse.py` (12 deterministic cases for relative/absolute/recurring/split parsing via injected `now` — no mocks); Discord focus-deferral (`test_discord_navigation_defers_when_protected` / `…_proceeds_when_unprotected`, light monkeypatch of `essential.active` + `_discord_hwnd`). Tiling is covered by routing tests + the `_best_open_window` self-check (`python commands/tiling.py`). Both files run under `pytest tests/` or standalone. **Reminders scheduler now covered:** [tests/test_reminders.py](tests/test_reminders.py) (7 cases — recurring re-arm / recurring re-fire / weekly roll-a-week via injected clock) closes the former gap. **Suite status (2026-07-01): fully green** — `tests/test_dispatch.py` 34/34, `tests/test_visual_nav.py` 13/13, plus registry/vision/verify/timeparse/reminders all passing.

### 3. Plugin/skill system — *DONE*
~~Adding a new command required editing `core/dispatcher.py`.~~ **Done:** [core/skills.py](core/skills.py) imports every `skills/*.py` at startup (`skills.load(display)` from main.py) and collects each file's module-level `INTENTS` list (same `(regex, handler)` shape as built-ins). Optional `PRIORITY` (ordering among skills), `FEATURE` (gate on features.json), and `setup(display)` hook. Skill intents are tried in `dispatch()` after built-ins and before the fuzzy/LLM fallback, so they extend without overriding. Import/handler errors are caught + logged so one bad skill can't crash Eve. Ships with [skills/example_dice.py](skills/example_dice.py) (roll/flip) + [skills/README.md](skills/README.md). Tests: `test_skill_loading`, `test_skill_integration_through_dispatch`. This also supersedes P3 "Skill entry points" (file-drop is simpler than pyproject entry points for this use case).

**Related smell — the dispatcher is a hand-ordered ~350-line regex wall.** Still true for *built-in* intents (skills don't need it — they're priority-ordered). Interim guards: `test_all_intents_compile_and_are_callable` fails on a typo'd pattern or misreferenced handler; **[tests/test_intent_audit.py](tests/test_intent_audit.py)** is the "no two patterns both match phrase X" self-check — it asserts each canonical phrase's intended handler wins *first*, and fails when a **new** order-dependent phrase appears (multiple handlers match, only ordering saves it). It already documents the current fragility: all five "open &lt;panel&gt;" commands also match `apps.open_app` (reorder them below it and "open app manager" tries to launch an app), plus "hide interface" (toggle vs hide_directory) and "snap X to top" (snap vs bring-to-front). This is the groundwork/evidence base for the Tier-A registry rework (P3 Architecture) — you now know exactly which overlaps a scored registry has to preserve.

### 3a. Delete the duplicate pre-dispatch routing in `main.py` — *DONE*
~~`main.py`'s `on_command` ran 7 hardcoded regex blocks before `dispatch()`, shadowing INTENTS.~~ **Done:** the 7 blocks + regex constants are deleted; `dispatch()` is now the single routing authority. New `core.response.Panel` (subclass of `Silent`) marks panel open/close/toggle actions so main.py hides the HUD immediately and doesn't speak — preserving the former silent + delay-0 UX. New `system.toggle_overlay` + a top-of-INTENTS toggle intent preserve the "show/hide/bare hud toggles the overlay" behavior; voice-settings + app-manager intents broadened to absorb the bare "voice settings" / "manage apps" / "open apps" forms. Routing tests updated (`test_directory_and_identify`, `test_consolidated_panel_routing`) — they now assert the *real* app behavior instead of the old dead-code path. Residual intentional in-dispatcher overlap (toggle above show/hide for hud) is by design and is what the #3 startup self-check would whitelist.

### 3b. Verify-by-running gap
Recent features (focus-essential, workspace presets, monitor naming, Ollama fallback, auto-snap on launch) were verified by tracing regex + signatures, **not** by executing `tests/test_dispatch.py` or launching the app (run prompts were declined). "Done" currently carries an asterisk until the self-checks (`python core/essential.py`, `python commands/tiling.py`) and `python tests/test_dispatch.py` are run green, and a manual smoke of one snap / one protect command confirms the Win32 paths actually fire. Low effort, removes the asterisk.

### 4. Remove hardcoded assumptions — *mostly done*
**Done:** WS port/host centralized to `config.WS_PORT`/`WS_HOST` (display.py imports them; env-overridable via `EVE_WS_PORT`/`EVE_WS_HOST`). Wake word already lived in `config.WAKE_WORD`; the dispatcher's hardcoded spoken-prefix tuple is now `config.WAKE_PREFIXES` (`EVE_WAKE_PREFIXES`). Screenshot dir → `config.SCREENSHOT_DIR` (`EVE_SCREENSHOT_DIR`). **Remaining/deferred:** the Electron renderer files still hardcode `ws://127.0.0.1:7734` in their CSP `connect-src` + `WS_URL` (8 files) — a build-time inject is only worth it if a configurable port is ever actually needed (ponytail). Data-file paths (`apps.json`, `settings.json`, `tiling_layouts.json`, `eve_memory.json`) are repo-relative and deterministic, not user-facing settings — intentionally left as-is.

### 5. Distribution — *PARKED (deprioritized 2026-07-01)*
> **Do not work on this until the engine is mature.** Public release is deferred; installers,
> packaging, release automation, deployment, and public docs are explicitly off the table for now
> (see North Star). The script below already exists, so there is nothing to *do* here — it waits.

**Done (kept for whenever release becomes a goal):** [installer/eve.iss](installer/eve.iss) — an Inno Setup 6 script that wraps a pre-built `dist\Eve\` folder into `Eve-Setup-<ver>.exe` with Start Menu shortcut, optional desktop icon, optional run-at-login (writes the same HKCU Run key as `core/autostart.py`, removed on uninstall), and an uninstaller. Per-user install (no admin prompt). [installer/README.md](installer/README.md) documents the two-stage build (Electron UI → PyInstaller onedir freeze of `main.py`). **When resumed:** run the build toolchain (PyInstaller + npm) to produce `dist\Eve\` and `iscc` it; expect to iterate the PyInstaller hidden-imports for openwakeword/piper/sounddevice the first time.

---

## P1 — High Priority

### TTS
- **Change TTS Tone** — *addressed via the Kokoro engine* (see Completed). Piper voice swap is
  still wired for the lightweight path; for a big quality jump set `TTS_ENGINE=kokoro` and drop the
  two Kokoro model files in `models/kokoro/`. **Remaining (user step):** `pip install kokoro-onnx`
  + download the model files, then pick a voice. No code left.

### Visual Navigation skill — *Phase 1 done; Phase 2 mostly done (2d ONNX detector pending)*
> **★ Headline priority (North Star #1).** This is the skill that makes Eve feel autonomous — driving
> arbitrary app UIs by voice. The next work here is **reliability across real apps**, not new backends:
> daily-drive it, log where element detection misses (which apps/controls the UIA tree or OCR fails to
> surface), and close those gaps. Treat the "known minor gap" and detection misses as the priority
> queue. A bigger set-of-marks font and drag-by-description are quick UX wins; the ONNX detector is only
> worth it if UIA+OCR leave a real coverage hole after daily use proves it.

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
- **Integrations & Setup hub** *(Phase 2b, done)* — the old "API Keys" panel
  ([ui/src/integrations](ui/src/integrations)) became a **data-driven setup hub** (one card per
  integration from a `SERVICES` list). **Key cards** (Brave, Anthropic, OpenAI) keep the key field +
  Save/Test/Clear (`core/display._test_api_key` → `vision.test_key`, a `max_tokens:1` auth ping; keys
  via `vision.vision_key()`, masked). **Tool cards** (Ollama, OCR/RapidOCR, UI Automation) show a live
  status pill, numbered setup steps, a copyable install command, and a "Setup guide ↗" link.
  `display._setup_status()` reports readiness (import checks + an off-loop Ollama `/api/tags` ping).
  **One-click Install** *(done)* — pip-based tiers (RapidOCR, UI Automation) install from the panel via
  `integrations:install_<id>` → `display._install_integration` (subprocess pip, then `features.refresh_status`
  + pill/snapshot refresh); `_INSTALLERS` map gates which get a button. System installers (Ollama) keep the
  guide + command. **Extensible:** add a `SERVICES` entry (UI) + `_INSTALLERS` entry (pip) for Kokoro TTS,
  mpv, etc.
- **Feature "Set up ↗" links** *(done)* — directory feature toggles deep-link to the relevant
  Integrations card (`FEATURE_SETUP` map; `visual_nav` → accessibility card). `window.eve.openIntegrations(target)`
  → `main.js` loads with `#hash` or messages the live window (`scroll-to`) → panel scrolls + highlights.
- **`BrowserProvider`** *(Phase 2, deferred)* — documented provider slot; no automation dep today.
- **Set-of-marks** *(done)* — cloud/Ollama backends route through `vision._model_detect`: OCR provides
  pixel-accurate candidate boxes, `_mark_image` draws numbered marks, the model picks/labels **by number**
  (`_SOM_PROMPT`) and `_elements_from_marks` maps back to the OCR geometry — no coordinate hallucination.
  Falls back to direct box-detection when OCR yields nothing. Use via `EVE_VISION_BACKENDS=claude` (OCR runs
  inside the cloud tier). Tests in [tests/test_vision.py](tests/test_vision.py).
- **Drag-and-drop** *(done)* — `InputController.drag` (pyautogui moveTo→dragTo) + `NavigationPlanner.drag(n1,n2)`
  + parser `("drag", n1, n2)` ("drag number 2 to number 5"). Tests in [tests/test_visual_nav.py](tests/test_visual_nav.py).
- **Phase 2 remaining** — `OnnxUiBackend` model + setup download (2d); larger marker font for set-of-marks
  readability at downscaled sizes; drag-by-description (numeric/ordinal only today).

Tests: [tests/test_visual_nav.py](tests/test_visual_nav.py) (parser/planner/handler/select-by-desc) +
[tests/test_vision.py](tests/test_vision.py) (cascade order, key resolution, JSON parse + scaling,
phash, key-tester) — all against fakes, no UIA/OCR/network/mouse. **Known minor gap:** "start/launch
hands free mode" routes to app-launch (skill intents run after `apps.open_app`); "hands free mode" /
"mouse mode" / "enter hands-free mode" work. **Remaining (user steps):** `pip install uiautomation`
(accessibility tier) and optionally `pip install rapidocr-onnxruntime` (OCR vision tier, no GPU); enable
"Hands-free Visual Navigation" in the App Manager. Cloud vision: add an Anthropic/OpenAI key in the
API-Keys panel and set `EVE_VISION_BACKENDS=rapidocr,claude` (or `gpt`/`ollama`).

---

## Performance & Efficiency (North Star #2)

> **Goal: the engine should *feel* instant and sip resources.** A meaningful responsiveness or
> resource win outranks a new feature. This section is an **audit backlog**, not a list of confirmed
> bugs — every candidate below must be *measured before it's touched*. Encode findings as tests where
> possible (per the "verify via tests" rule) and record before/after numbers.

**Method:** [tools/profile_baseline.py](tools/profile_baseline.py) is the profiling harness (zero deps —
stdlib + PowerShell). *Part A* (headless micro-benchmarks — routing, catalog build, feature-snapshot
cost) runs any time with no side effects; *Part B* (idle CPU%/RSS/threads per Eve process) runs while
Eve is up and idle. Both write a timestamped report to `profiling/`. Run Part B **before** optimizing so
fixes 1/4/6 below can be ranked by real cost. Optimize against numbers, not intuition. **Guardrail:**
never trade capability or determinism for speed without saying so explicitly. *(Part A already run
2026-07-01: routing 0.057ms/phrase, feature-snapshot 0.007ms — confirms the understanding path is not a
bottleneck; the wins are in the UI render loop + polling, below.)*

**Static audit findings (2026-07-01) — ranked by expected idle-cost impact. Confirm magnitudes with the
profiler; the *diagnosis* is code-confirmed at the cited lines.**

1. **★ DONE — Orb canvas rendered at ~60fps forever, even at idle.** `drawOrb` in
   [ui/src/app.js](ui/src/app.js) used multiple `shadowBlur` passes + radial gradients every frame and
   re-scheduled itself unconditionally, so the orb burned CPU/GPU at 60fps while Eve did nothing.
   **Fixed:** the rAF self-schedule was pulled out into a `frame(ts)` pump that keeps full frame rate
   for active states (listening/processing/playing/always-on) but throttles to ~18fps (`IDLE_FRAME_MS`)
   when idle — the idle orb only slow-pulses, which 18fps renders fine. Expected the biggest idle-CPU
   drop; confirm magnitude with the profiler (Part B before/after).
2. **★ DONE — Hot-reload polled the filesystem every 1s in production.**
   [core/hot_reload.py](core/hot_reload.py) `while True: time.sleep(1)` stat'd 5 module files every
   second, forever — a dev-only convenience with zero user value and the most frequent Python poll.
   **Fixed:** `start()` is now a no-op unless `EVE_DEV=1` / `EVE_HOT_RELOAD=1` is set, so the poll is
   gone in normal use. Verified off-by-default / on-with-flag.
3. **DONE — Per-command disk I/O on the dispatch hot path.**
   [core/dispatcher.py](core/dispatcher.py) `_load_custom()` / `_load_aliases()` re-read **and**
   re-parsed `custom_commands.json` + `aliases.json` from disk on **every** `dispatch()` call.
   **Fixed:** `_load_json_cached()` caches the parsed list keyed on the file's mtime (the command editor
   bumps mtime on save → cache invalidates), so a steady-state command does a single `stat` instead of
   read+parse×2. Verified cache-hit on present files; absent files stay a cheap `stat`. *(Routing itself
   was already 0.057ms/phrase, so this I/O — not matching — was the per-command cost.)*
4. **HUD broadcast fan-out: ~6–8 full-state serializations per command** —
   [core/display.py](core/display.py) `_broadcast()` ([:285](core/display.py#L285)) rebuilds a full
   `_snapshot()` (state + features + status + labels + alpha + reasons) and pushes it to *every*
   connected client on each of `show`/`update`/`set_mode`/`log`/`hide_list` — a single `on_command`
   fires this many times. Every open panel receives every `state` message even though most filter it
   out. Snapshot build itself is cheap (measured 0.007ms), so this is about **message volume/chatter**,
   not CPU — lower priority than 1–3, but worth coalescing (batch rapid updates in a command, or only
   send `state` to the orb/directory that consume it). Note: `_compute_status()` (the expensive
   import-probe) correctly runs only at startup / `refresh_status()`, *not* per broadcast — good.
5. **Orb topmost re-assert every 2s** — [ui/main.js:163-167](ui/main.js#L163-L167). Low absolute cost
   (one `setAlwaysOnTop` call/2s). Candidate for event-driven replacement via a foreground-change hook,
   but minor. **Roadmap correction:** only the *orb* re-asserts; the directory window is intentionally
   *not* always-on-top ([ui/main.js:179-181](ui/main.js#L179)), so the earlier "orb *and* directory"
   note was wrong.
6. **Directory clock ticks every 1s on a hidden window** —
   [ui/src/directory/app.js:57-62](ui/src/directory/app.js#L57-L62). The directory is pre-warmed and
   hidden (not destroyed), so its clock `setTimeout` keeps updating DOM every second even while hidden.
   Tiny, but a clean "pause work when not visible" fix (gate on `document.visibilityState`).
7. **Reminder scheduler re-reads its JSON file every 15s** —
   [commands/reminders.py:352-386](commands/reminders.py#L352-L386). Light (15s interval, `on_change`
   only fires on real change — good), but it does disk read + parse each tick even with zero reminders.
   Low priority; could compute sleep-until-next-due and re-read only on change.

**Confirmed NON-issues (checked, leave alone):** routing/registry matching is microseconds
([profiler](tools/profile_baseline.py) Part A); heavy optional deps (`uiautomation`, `rapidocr`,
`onnxruntime`, `paho-mqtt`, `kokoro`) are all lazy-imported inside functions, not at startup; the LLM
fallback is genuinely last-resort in `dispatch()` (never speculative); panels are *destroyed* on close
(`'closed' → null`), so only orb+directory hold renderer memory persistently; `features._compute_status`
(import probing) runs only at startup/on-demand, not per broadcast. The Programs panel's 3s poll
([ui/src/programs/app.js:27](ui/src/programs/app.js#L27)) only runs while that panel is open (destroyed
on close), so it's acceptable.

**Still to measure (needs the live profiler — Part B — while Eve runs idle):** actual idle CPU% and RSS
per process (to *rank* fixes 1/4/6 by real cost), total thread count, and Electron renderer RSS per panel
(feeds the Foundation question).

**Simplify execution paths (without losing capability):** the dispatcher is now registry-driven; continue
flattening intent priorities cluster-by-cluster (see Architecture → Tier A "Remaining") so literal-match
specificity carries routing and the order-dependent set shrinks. Fewer hand-tuned priorities = fewer
places to reason about latency and correctness.

---

## Foundation — desktop shell (North Star #4)

> **Open to replacing Electron, but not before a written analysis is reviewed.** No rewrite for its own
> sake. This entry is the *placeholder for that analysis*, not a decision.

**When to seriously evaluate:** after the performance audit above produces real numbers. If Electron
renderer memory / startup / animation smoothness turn out to be a material drag on the "feels instant"
goal, that's the trigger. If they don't, this stays parked.

**What a proposal must contain before any migration work begins** (per maintainer's instruction —
present tradeoffs, effort, risks, and strategy *first*):
- **Candidates & tradeoffs.** Tauri (Rust core + system WebView — big memory/startup win, but the UI is
  still web tech so panels port with moderate effort; Rust learning curve, smaller ecosystem, per-OS
  WebView quirks). Native (WinUI 3 / Win32 + Direct2D — best smoothness/footprint, but a *full* UI
  rewrite and loses the web-panel authoring speed). Others (Flutter desktop, Avalonia) noted for
  completeness. Weigh each against: startup, idle RSS, responsiveness, animation, and — heavily —
  **maintainability + how easily new skill-owned panels are added**.
- **Effort & blast radius.** Eve's Python core (`main.py`, dispatch, Win32 funnels, WS bridge) is
  **shell-agnostic** — the migration surface is the `ui/` layer (~15 panels + orb + overlays + `main.js`
  window factory + the WS/IPC contract). Estimate panel-by-panel; the WS protocol is the natural seam
  (a new shell speaks the same WS messages, so Python barely changes). Tauri reuses the existing
  HTML/CSS/JS panels (lower effort); native does not (highest effort).
- **Risks.** Losing the hard-won Win32 z-order tricks (topmost-above-fullscreen for orb/directory,
  `showInactive`, transparent NC-area border fix, per-monitor DPI `dipToScreenRect`) — these are
  Electron-`BrowserWindow`-specific and must be re-proven on any new shell *before* committing.
- **Strategy.** If it goes ahead: keep the WS contract fixed, port one non-critical panel first as a
  spike to validate z-order + transparency + DPI on the new shell, measure it against Electron, and only
  then commit to porting the rest. Never a big-bang rewrite.

**Recommendation until then:** *don't migrate yet.* Get the numbers first; Tauri is the most likely
target if the numbers justify a move (it preserves the web-panel investment), but the audit decides.

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
- **Intent engine rework** — replace the hand-ordered `INTENTS` regex wall (the P0 #3 smell) with a
  proper local intent engine, in two tiers. Reframe: runtime isn't the problem (50 regex scans =
  microseconds) — the pain is *order-encodes-priority fragility* ("protect X" must sit above "snap X"
  above "open X") and paraphrase misses.
  - **Tier A — declarative intent registry (zero new deps, do first).** Each intent declares its own
    `priority`/specificity, patterns, slots, and feature gate as data; the matcher scores all and picks
    the best instead of first-match-wins. Kills the fragile ordering, adds a startup "no two intents
    claim the same canonical phrase" self-check (the guard #3 already wants). Slots stay regex (what
    regex is good at). Deterministic; guarded by the existing `tests/test_dispatch.py`. **Caveat:**
    highest blast-radius change in the repo — do it incrementally behind the routing tests.
    **Core landed (standalone, not yet wired):** [core/intent_registry.py](core/intent_registry.py) —
    `Intent` dataclass (patterns + priority + feature + slots + learning metadata: source/confidence/
    successes/failures/provenance) and `IntentRegistry` with a scored matcher (priority desc → fewest
    wildcard-captured chars, i.e. most literal → stable order). [tests/test_intent_registry.py](tests/test_intent_registry.py)
    proves routing is **registration-order-independent** (panel beats `open_app` first- or last-added),
    resolves the audit's "snap X to top" case by literal-match specificity, and covers feature gating +
    the learning counters. **Migration bridge landed:** `intent_registry.from_intents(INTENTS)` builds a
    registry from the live list with position→priority (semantics-preserving), and
    [tests/test_intent_registry_parity.py](tests/test_intent_registry_parity.py) **proves it routes
    identically to the current first-match loop across 60 phrases** — so swapping `dispatch()`'s
    `for … in INTENTS` loop for `registry.resolve()` is now a small, provably behaviour-preserving diff.
    **WIRED INTO dispatch():** the old `for ... in INTENTS` first-match loop is replaced by
    `_registry().best(text)` (lazy module-level registry from `from_intents(INTENTS, _HANDLER_FEATURE)`).
    Behaviour-identical (no built-in feature gating, matching prior behaviour) — full suite green incl.
    `test_dispatch`'s 32 routing assertions. `INTENTS` stays the source of truth (patterns unchanged); only
    the *matching strategy* changed from list position to priority + literal-match specificity.
    **Remaining (polish, optional):** (1) flatten priorities cluster-by-cluster so literal specificity
    takes over and the audit's order-dependent set shrinks toward empty; (2) optionally enable built-in
    feature gating by passing `feature_get=_features.get` to `.best()` (a deliberate behaviour change —
    built-ins don't currently gate at the dispatch loop).
  - **Tier B — local semantic fallback (opt-in, CPU).** Swap the fuzzy tier (`core/intent_match.py`,
    lexical rapidfuzz `token_set_ratio`) for a local sentence-embedding classifier (MiniLM via
    `onnxruntime`, ~80MB, CPU-ms — reuses the vision stack's optional onnxruntime dep). Embeds the
    utterance → cosine-sim vs. a few example phrases per intent → runs that intent's regex slot
    extractor. Real paraphrase handling ("could you throw firefox on my left screen") while staying
    local and only firing when regex misses. Keep Ollama tool-calling as the last resort. **Caveat:**
    trades a little determinism for coverage; it's an addition, not a replacement, and only classifies
    intent (slots stay regex). Gate behind a feature flag + optional dep like the vision tiers.
  - Not chosen: Snips NLU (purpose-built for offline assistants but abandoned ~2020, painful install),
    Rasa (heavy server + TF), spaCy Matcher/textcat (clean but a real dep + training data for marginal
    gain over Tier A). Start with Tier A; add Tier B only if daily-use paraphrase misses justify it.
- **Dynamic Intent Learning — verified adaptive training** *(big bet; builds directly on the Intent
  engine rework above — Tier A is its prerequisite).* Eve learns from **verified successful** LLM-fallback
  interactions so the local engine becomes the primary path and LLM inference grows rare over time.
  **Core principle: never learn from an LLM response alone — only from verified outcomes.** The LLM is a
  *teacher* that interprets unfamiliar requests; verified execution + user feedback are the real learning.
  - **Pipeline:** manual matcher → learned intents → learned aliases → LLM fallback (last resort); first
    success wins. Inserts *persisted, per-user learned tiers* between today's regex/skills and the Ollama fallback.
  - **Promotion pipeline (not "auto-learning") — nothing jumps from an LLM response into the primary registry.**
    Each mapping climbs a ladder with explicit entry criteria per stage:
    `Unknown → LLM Candidate → Verified Candidate → Trusted Candidate → Primary Intent`.
    LLM output is a *candidate interpretation*; only accumulated verified evidence advances a stage.
  - **Verification (3 tiers — reuse existing infra):** T1 system verification = extend the existing
    `verify_commands` / `core.response.Verified` side-effect checks (highest confidence). T2 explicit feedback
    = a "yes / that worked / 👍" vs "no / undo / 👎" vocabulary on the existing confirm+converse layer
    (negative feedback blocks learning). T3 implicit = user continues without correcting (moderate).
  - **Multi-metric confidence (not one score).** Track independent signals per mapping — interpretation
    confidence, execution-success rate, explicit-confirmation count, frequency, recency, failure rate — and
    make **promotion decisions weigh all of them**, never a single percentage. (A high one-off confidence with
    a recent failure must not promote.)
  - **Two separate learning tracks, different promotion rules:**
    - **Language learning** — aliases / phrase variations / STT & spelling corrections that map to an
      **existing** intent ("nuke chrome" → close chrome). Low-risk, faster promotion; this is the common case.
    - **Capability / workflow learning (Teach Mode)** — recording a **new multi-step workflow** ("prepare my
      morning workspace") into a reusable callable intent. Higher bar: explicit build + confirmation, and each
      constituent action still obeys its own class guardrails.
  - **Interactive confirmation:** below the promotion bar, Eve states its interpretation and asks before
    permanently learning ("I read that as warm-white @35% — correct?"). Reject → don't learn, record the miss, clarify.
  - **Capability awareness:** distinguish (1) unknown intent, (2) known intent / unsupported capability,
    (3) known intent / missing integration — every failure says *why* (understanding vs capability vs
    integration), never a dead end. When it's (2)/(3), offer Teach Mode instead of a dead end.
  - **Provenance + Intent Explanation.** Every learned mapping must answer **"Why does Eve believe this?"** —
    origin (LLM fallback / Teach Mode / user-created), successful-execution count, confirmations, promotion
    date, last failure, current pipeline stage. Surfaced as an **Intent Explanation** feature: "why did you do
    that?" / "why do you think X means Y" → what matched, the metric breakdown, #successful executions, and
    origin. Non-negotiable for a self-modifying system — it's what makes drift debuggable and trustworthy.
    **Engine landed:** `IntentRegistry.explain()` / `explain_str()` report the winning intent, WHY it won
    (priority vs literal-match vs sole match), every shadowed candidate, and each intent's provenance/learning
    metadata; `dispatcher.explain_last()` records the last built-in-routed text so a spoken "why did you do
    that" can explain it. Tests in [tests/test_intent_registry.py](tests/test_intent_registry.py) +
    an integration check in [tests/test_intent_registry_parity.py](tests/test_intent_registry_parity.py).
    **Voice command landed:** "why did you do that" / "explain that" / "how did you interpret that" →
    `_explain_last_intent` → `explain_last()`, explaining the *previous* routing (guarded so the meta-query
    never overwrites the command it asks about). **Remaining:** extend explanation to learned mappings once they exist.
  - **Reuse anchors:** Tier-A registry (**learned intents are just data added at runtime — this is why Tier A
    is a hard prerequisite**); Tier-B embeddings (the Intent Clustering engine that dedupes semantically
    identical learned phrases); [commands/fallback.py](commands/fallback.py) (LLM Fallback Engine — already
    Ollama tool-calling; needs structured candidate output); `verify_commands`/`Verified` (Execution Verifier);
    converse/`pending_confirm` (Feedback Manager); Ollama idle/nightly/on-demand for background promotion
    (must never block command responsiveness).
  - **Guardrails (the "implications" — this converts a deterministic table into a living, self-modifying dataset):**
    - **Safety is class-based, not confidence-based.** Destructive / sensitive intent *classes* — delete,
      shutdown, kill/close, messaging, purchases, financial actions — are **never** auto-promoted and **always**
      require explicit confirmation, *even at 99% confidence*. **Critically: an LLM interpretation alone never
      executes a destructive action** — when the LLM fallback resolves a destructive command, Eve confirms with
      the user before executing, and keeps confirming until that intent has been safely learned + promoted
      through the normal verified pipeline. Confidence gates *language* learning; it never overrides *class* safety.
    - **Privacy:** stores every utterance + the user's profile — local-only (fits the Windows-first/offline
      ethos) but persistent PII; needs retention/redaction (ties to the Discord-redaction rule) + a forget/reset
      path (extends the Memory panel's edit/reset patterns).
    - **Single-user, firm.** No cross-user sharing — language is highly personal, and the model must be proven
      locally first. Multi-user (`user_id`, shared generic improvements) is explicitly deferred, not planned;
      keeping it out now drops profiles/sharing and much of the module count.
  - **Phasing (each ships value; stop where payoff plateaus):** (0) Tier-A registry [prereq] → (1) structured
    LLM candidate + Execution Verifier on the existing Ollama fallback → (2) training store + multi-metric
    confidence + interactive confirmation + provenance → (3) **language-track** promotion of *safe* intent
    classes + learned aliases → (4) Intent Explanation feature → (5) **capability-track** Teach Mode workflows →
    (6) clustering/dedup via Tier-B embeddings → (7) background Ollama promotion (idle/nightly).
  - **Modules (independently replaceable):** Manual Matcher · Language Learner · Alias Generator · LLM Fallback
    Engine · Execution Verifier · Feedback Manager · Multi-metric Confidence Engine · Promotion Manager ·
    Provenance / Intent Explainer · Intent Clustering · Training Dataset Manager · Workflow Recorder (Teach
    Mode) · Capability Registry · Ollama Training Service.
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
- **Larger UI on high-res displays** — *DONE: panels auto-zoom on 1440p (1.25×) / 4K (1.5×) via
  `webContents.setZoomFactor` (global `web-contents-created` hook gated to panel folders; directory/
  tag overlays excluded). The **orb** now also scales — `_orbSize = ORB_SIZE × _uiScale` + content
  zoom (`_applyOrbScale`), live-updating with the slider. App Manager **UI SIZE** slider (0.8–2.0×)
  live-applies + persists to `settings.json` `ui_scale`. `ui/main.js`, `ui/preload.js`, `ui/src/app-manager`.*
  Remaining: the routing directory panel overlay still uses fixed geometry (scale it if 4K users report it small).
  Original note: increase UI text size and maybe general UI size for 2560×1440p
  (and higher). The Electron panels use fixed `px` font sizes/dimensions tuned for ~1080p, so they
  read small on 1440p/4K. Options: a UI-scale setting (App Manager slider → CSS root font-size /
  zoom factor), or auto-scale off `screen` DPI/resolution. Affects every `ui/src/*` panel + the
  overlay; consider `rem`-based sizing or an Electron `webContents.setZoomFactor`.

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

## Architecture — core vs. skill (where to draw the line)

The repeated refactors this cycle (YouTube → skill, hands-free → `visual_nav`
skill) converged on a rule worth stating outright:

> **Core is the engine and the OS-integration primitives. A skill is a feature
> built on top of them. If something *can* be a skill, it should not be woven
> into core.**

**Core = the kernel everything else stands on.** A capability belongs in core
only if it meets one of:
1. **It's the always-on assistant loop** — wake-word listener, STT
   (`transcriber`), the dispatch pipeline (`dispatcher`), session/converse state
   (`session`), TTS (`speaker`), the Display/overlay + WS bridge, feature flags
   (`features`), and the skill loader itself.
2. **It's an OS-integration primitive reused by many features** — the `core/`
   Win32 funnels: `window_ops`, `key_ops`, `monitor`, `notify`, `autostart`. Per
   the P0 platform decision, new Win32 lives here so skills call helpers, not raw
   `ctypes`.
3. **It defines the UX contract** — the response types
   (`Silent`/`Panel`/`VideoList`/`SiteList`/`Verified`), the HUD/orb, the
   directory panels.

**Skill = a self-contained capability** that maps phrases → actions, that not
every user needs, that toggles off via a `FEATURE` flag, and that integrates with
exactly one thing (an app, a device, a service, a domain). YouTube, the 3D
printer, smart lights, weather, media control — all skills. The test: *if I
deleted this file, would the rest of Eve still make sense?* If yes, it's a skill.

**Litmus questions for a new capability:**
- Do other features depend on it as infrastructure? → core.
- Is it part of the irreducible "listen → understand → act → respond" loop? → core.
- Is it one domain/integration a user might never touch? → skill.
- Could a contributor ship it as a single dropped-in file? → skill.

**Grey areas (currently core, but skill *candidates* for extraction):**
- **Web search** (`commands/search.py`) — self-contained (DDG/Brave + clickable
  results + site-list converse); it's core only because it predates the loader.
  The site-list converse moves with it (web search is its sole user now that
  YouTube left).
- **Discord** (`commands/discord.py`) — very self-contained (keybind injection,
  nav, messaging). Prime extraction candidate.
- **Memory store** (`remember`/`forget`/recall) — the *store* is a skill
  candidate; the **pronoun follow-ups** (`go back`/`cancel it`, on
  `session.last_action`) are cross-cutting and stay core.
- **Tiling voice grammar** — the snap/enumerate *primitives* are core and heavily
  reused (auto-snap on launch, workspaces, HUD positioning); the *voice grammar*
  over them could be a skill layer. Lower priority — tiling is the headline
  Windows differentiator.

**What keeps a feature in core today — skill-API gaps to close first.** These are
*intentionally* core until the skill contract grows, not neglect:
- **Background services** — reminders/timers run a long-lived scheduler thread.
  Needs a `register_service()` skill hook (+ an `on_change` UI hook) before it can
  be a drop-in.
- **Pre-dispatch text shaping** — mishear substitutions and the fuzzy catalog live
  in core; a skill can't contribute either yet. Needs skills to export optional
  `MISHEARS` / `CATALOG` entries the loader merges.
- **New HUD panels** — a skill can drive *existing* panels via the Display and
  return existing response types, but a genuinely new panel still needs `ui/` +
  `display` work. Needs a generic skill-owned list/detail panel.

Closing those three gaps is what lets reminders (then web search, Discord) become
clean drop-ins. Until then they stay core by design.

---

## Skill Library — candidate drop-in skills

> **North Star #1 emphasis (2026-07-01):** the top skill priority is **operating arbitrary desktop
> application UIs reliably** — making Eve feel autonomous and seamless, not accumulating niche skills.
> That means the **Visual Navigation** entry (P1) and the Tier-A **Window quick-actions** below are the
> headline work: push accessibility/vision element-detection reliability until Eve can confidently drive
> *most* app interfaces by voice ("what can I click" → act on it) across the apps used daily. New skills
> should be **broadly useful** and justify their complexity/perf cost (see North Star). Tiers below stay
> as-is for reference, but weight selection toward things that make Eve more autonomous over one-off toys.

Day-to-day skills worth shipping. **Every item below is implementable as a
`skills/*.py` drop-in** (module-level `INTENTS`, optional `PRIORITY`/`FEATURE`/
`setup`) with **no core changes** — same shape as [skills/example_dice.py](skills/example_dice.py)
and [skills/3dprinter.py](skills/3dprinter.py). Conventions for all of them:
gate anything heavy or keyed on a `FEATURE` flag; resolve API keys via the
existing API-Keys UI (`settings.json` `api_keys` → env-var fallback, masked hint
back to the panel); keep network calls stdlib `urllib` + bounded timeouts; for
local-hardware integrations mirror the 3D-printer **backend abstraction**
(normalized dicts, graceful "not configured" speech, lazy-import optional deps).
Side-effecting ones should return `core.response.Verified` so the new command
verification confirms they actually took. Tiered by daily value × low friction.

### Tier A — high daily value, no new dependencies (build first)
- **Media & volume control** — "pause" / "next track" / "previous" / "volume up" /
  "set volume to 30" / "mute". `keybd_event` media keys + `IAudioEndpointVolume`
  (ctypes COM). Bare "mute" already exists for system mute — extend, don't clash.
- **System power / session** — "lock my screen" / "sleep" / "restart [in 10 minutes]" /
  "sign out" / "shut down". `user32.LockWorkStation`, `SetSuspendState`,
  `shutdown.exe`. Destructive ones return `Verified`/confirm first.
- **Calculator + unit/currency/tip convert** — "what's 15% of 240" / "convert 5
  miles to km" / "how many cups in 2 liters" / "tip on 80 dollars". Local parse,
  no deps (currency rates optional via a free API, cached).
- **Window quick-actions** — "minimize all" / "show desktop" / "minimize this" /
  "maximize this" / "always on top". Complements tiling; `ShowWindow` + shell COM.
- **Virtual desktops** — "new desktop" / "switch to desktop 2" / "move this to
  desktop 2". `IVirtualDesktopManager` COM (or `Win+Ctrl+←/→` key injection).
- **Battery & system status** — "how much battery" / "am I charging" / "how's my
  memory" / "disk space" / "how long has my PC been on". `GetSystemPowerStatus`,
  `GlobalMemoryStatusEx`, `GetDiskFreeSpaceEx`, `GetTickCount64` — all ctypes.
- **Open Windows settings** — "open bluetooth settings" / "display settings" /
  "sound settings". `ms-settings:` URIs via `ShellExecute`; natural extension of
  the app launcher.
- **Quick notes + read clipboard** — "take a note: buy milk" / "read my clipboard" /
  "what's on my clipboard". Append to a notes file; speak clipboard via existing
  TTS (`win32clipboard`/ctypes).
- **Screenshot / window capture** — "take a screenshot" / "screenshot this window".
  Saves to `config.SCREENSHOT_DIR` (already defined); `PrintWindow`/`BitBlt` or a
  PowerShell one-liner like the toast helper.

### Tier B — high value, free API or no key
- **Weather** ⭐ — "what's the weather" / "will it rain today" / "forecast for
  tomorrow". **Open-Meteo needs no key**; geocode once from a configured city.
  Highest value-per-effort of the keyed-ish group.
- **Dictionary / spell / thesaurus** — "define ephemeral" / "spell accommodate" /
  "synonym for happy". `dictionaryapi.dev`, no key.
- **World clock / countdown** — "what time is it in Tokyo" / "how many days until
  Christmas". Local tz math (`zoneinfo`), no deps.
- **News headlines** — "what's in the news" / "tech headlines". RSS pull (stdlib),
  no key; reuses the clickable-results list UI.
- **Translate** — "how do you say good morning in Spanish". LibreTranslate
  (self-host/free) or local Argos; gate on `FEATURE`.

### Tier C — local smart-home / IoT (mirror the 3D-printer backend pattern)
- **Smart lights** — "turn off the living room lights" / "dim to 40%". Philips Hue
  (local bridge REST), LIFX (LAN UDP), Nanoleaf — each a backend behind one
  abstraction, like the printer's Prusa/Bambu split.
- **Home Assistant bridge** — "turn on the fan" / "is the front door locked".
  Local HA REST API + long-lived token; one skill covers any HA-exposed device.
- **Spotify control** — "play my Discover Weekly" / "what's this song". Spotify Web
  API (needs OAuth/key via the API-Keys panel); generic media keys cover basic
  play/pause without it.

### Tier D — productivity & lists (build on reminders/converse/memory)
- **TODO / shopping list** — "add milk to my shopping list" / "what's on my list" /
  "clear my list". Mirrors the Memory panel (list-shaped store + directory tile).
- **Pomodoro / focus sessions** — "start a pomodoro" / "start a 25-minute focus
  session". Cycles work/break on the existing reminder scheduler.
- **Daily briefing** — "good morning" → composite: weather + today's reminders +
  headlines in one spoken summary.
- **Habit / streak tracker** — "did I work out today" / "mark meditation done".
- **Email compose** — "email John about the meeting". `mailto:` opens the default
  client prefilled (no OAuth, light); full send is out of scope.
- **Do-not-disturb / Focus Assist** — "turn on do not disturb". Toggles Windows
  Focus Assist; pairs with the existing `notifications` flag.

### Tier E — utilities & fun (small, delightful, cheap)
- **Random / decisions** — "flip a coin" (example covers dice) / "pick a number 1
  to 100" / "pick between pizza and sushi" / "magic 8 ball".
- **Password / QR generator** — "generate a password" / "make a QR code for this
  link" (QR needs a tiny lib or a no-dep PNG writer).
- **Clipboard transforms** — "uppercase my clipboard" / "pretty-print this JSON" /
  "base64 encode this".
- **Phonetic spelling** — "spell that in the NATO alphabet".
- **Network utilities** — "what's my IP" / "am I online" / "flush DNS" / "toggle
  wifi" / "toggle bluetooth". stdlib + `ipconfig`/`netsh`.
- **App updates** — "update my apps" / "check for updates" via `winget upgrade`.
- **Cleanup** — "empty the recycle bin" / "clear my downloads folder" (confirm
  first; `Verified`).
- **What's using my CPU** — top process by CPU/RAM (`GetProcessTimes` or
  `tasklist`).
- **Find files / recent docs** — "find my resume" / "open my recent documents".
- **Dictation mode** — "start dictation" types spoken words into the focused field
  (uses the existing STT loop + `SendInput`); "stop dictation" ends it.
- **Read selection / summarize** — "read this aloud" / "summarize my clipboard"
  (the latter via the Ollama fallback that already exists).
- **Jokes / quote of the day** — local list or a no-key API; pure delight.

### Tier F — UI/UX & the Eve interface (make Eve itself nicer to use)
These improve the moment-to-moment experience of *using Eve* — its overlay,
feedback, and discoverability — not just external tasks. They drive the existing
Display/HUD via skill `setup(display)`, so they stay skills (no new core panels
needed beyond what already exists; a genuinely new panel is a core gap — see the
Architecture section).
- **Media & volume + now-playing HUD** ⭐ — the recommended **first** skill.
  Controls ("pause" / "next" / "volume to 30" / "mute" via media keys +
  `IAudioEndpointVolume`), *plus* surface the current track (title/artist) in the
  routing directory and a small now-playing badge on the orb, click-to-pause.
  Makes the most-used daily action visible. Bare "mute" already maps to system
  mute — extend, don't clash. Returns `Verified` (re-read volume/transport state).
- **Appearance & orb control** — "dark mode" / "accent color blue" / "make the orb
  bigger" / "move the orb to the top-right" / "more transparent". Voice theming of
  Eve's own UI (writes `settings.json` `ui_scale`/theme, drives the overlay).
  Generalizes the orb-move that already lives in the Window Manager.
- **Live captions / "show me what you heard"** — a translucent strip showing the
  last recognized utterance + Eve's reply. Accessibility + trust (you see exactly
  what STT heard). Drives a small overlay.
- **"Repeat that" / "louder" / "read it again"** — replay the last spoken response,
  bump TTS volume, or re-show the last result list. Tiny, high-frequency win on the
  last response / `session.last_action`.
- **Do Not Disturb (Eve)** — "be quiet" / "stop listening for a bit" / "wake me in
  20" — mute Eve's TTS + dim the orb without fully disabling the listener. Distinct
  from system Focus Assist.
- **Interactive tour** — "what can you do" / "show me around" — a guided walkthrough
  that opens each panel and demos one command. Onboarding for new users +
  contributors.
- **Activity recap** — "what did I miss" / "read the last few things" — read back
  the directory's recent activity feed aloud; turns the existing log into a voice
  surface.
- **Clipboard history panel** — a HUD list of recent clipboard entries, click to
  re-copy (mirrors the Memory/Reminders panels). Pairs with the Tier-A "read
  clipboard."

> **Recommended first build: the Media & volume + now-playing skill** (Tier A
> controls + the Tier F HUD surface) — highest daily use, no new deps, and it
> exercises both the action path (`Verified`) and the Display path, making it the
> ideal template for contributors. After it: **weather**, **system power/lock**,
> **calculator/convert** (the rest of the zero-friction first batch). A
> "recommended skills" index in [skills/README.md](skills/README.md) would help
> seed outside contributors.

---

## Completed (reference)

| Feature | Notes |
|---------|-------|
| 3D printer integration | [skills/3dprinter.py](skills/3dprinter.py) drop-in skill with a `PrinterBackend` abstraction → **Prusa** (PrusaLink HTTP API v1, stdlib) + **Bambu** (local MQTT LAN mode, lazy `paho-mqtt`). Backend-agnostic voice layer (normalized status/temps dicts): "how's my print" / "how long left" / "nozzle temp" / "pause/resume/cancel the print" (cancel routes through the yes/no confirmation) / "preheat for PETG" / "cool down". Config in `settings.json` `printer` block; self-gates with spoken guidance when unconfigured. Adding OctoPrint/Klipper = one more subclass + `_BACKENDS` entry. Tests in `tests/test_dispatch.py` (load, routing, backend selection, unconfigured/unknown-type guidance, cancel-confirm) |
| Command verification (did it actually run?) | `core.response.Verified` wrapper (optimistic message + `check()`/`on_fail`/`retry`/`announce`/`delay`) resolved by [core/verify.py](core/verify.py): waits, checks, **retries once**, reports honestly on failure. Wired into `main.on_command` behind a `verify_commands` feature flag (default on). Verifiers: **app launch** (process appears) with an **adaptive learned per-app delay** in `app_launch_delays.json` — a slow double-launch bumps the wait so it stops double-launching, clean successes trim it, and slow apps get a spoken "this may take a moment"; **app close/kill** (process gone); **window snap** (rect lands in zone ±16px); **printer pause/resume/cancel/preheat/cooldown** (re-query state/targets). Tests: [tests/test_verify.py](tests/test_verify.py) (resolver, delay bump/decay/clamp, printer verifiers with fakes) |
| YouTube/mpv → skill | Moved YouTube/mpv out of core into self-contained [skills/youtube.py](skills/youtube.py). Entry commands are `PREEMPT` `INTENTS` (new skill-system capability: a skill can opt to run *before* the built-in table, so "open youtube" beats `open_app`); the three former `Mode`-gated dispatchers (`_dispatch_listing`/`_playing`/`_browsing`) are reimplemented via the Converse layer (single `_converse()` routing by `feed`/`list`/`play` state). Core no longer imports `commands.youtube`; `_dispatch_listing` is now web-search-only; `fallback`/`intent_match`/`hot_reload` repointed. Tests updated for the new routing |
| Persistent window state (app-wide) | Centralized `ui/main.js` `createManagedWindow(name, options)` factory — restores saved size → creates → persists, with persistence logic fully separate from window creation. All 8 resizable panels (app-manager, window-manager, voice-settings, command-editor, programs, memory, reminders, integrations) route through it; a future window gets persistence by just calling the factory. Stored as `settings.json` `windowState[name] = {width,height}` (object → position/maximized/fullscreen later, no migration). `_restoreSize` validates (clamp to work area, enforce minimums, fall back on corrupt/missing — handles monitor changes); save is debounced 400ms on resize + a final save on `close`, skipping maximized/minimized. Orb / directory / tag overlays / corner YouTube window intentionally excluded (positioned/fixed). "Reset size" button in the routing directory's WINDOW LAYOUT section → `reset-window-layout` IPC clears all `windowState` + snaps every open managed window to its default. Reuses shared `_readSettings`/`_writeSettings` (also ui_scale/api_keys). |
| Integrations & Setup hub (Phase 2b) | "API Keys" panel → data-driven setup hub ([ui/src/integrations](ui/src/integrations)): key cards (Brave/Anthropic/OpenAI, Save/Test/Clear) + tool cards (Ollama/OCR/UIA) with live status pill, setup steps, copyable install command, and guide link. `display._setup_status()` (import checks + off-loop Ollama ping). Directory feature toggles get a "Set up ↗" deep-link (`FEATURE_SETUP` → `openIntegrations(target)` → scroll + highlight). |
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
