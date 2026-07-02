// Tauri bridge shim — the migration replacement for Electron's preload.js.
//
// Under ELECTRON, contextBridge already installed window.eve before this runs,
// so we detect that and do nothing (Electron path untouched). Under TAURI we
// install a Tauri-backed window.eve so every page's JS runs UNMODIFIED.
//
// Division of labour:
//   - things any window can do locally (file I/O via Rust commands, monitor
//     queries, self close/maximize, self zoom) are implemented right here;
//   - anything needing central state (orb/directory/panel/overlay window
//     management) is forwarded as an 'eve-cmd' event to the shell running in
//     the orb window (ui/src/eve-tauri-shell.js).
(function () {
  if (window.eve) return                     // Electron preload already provided it

  const stub = (name) => (...args) =>
    console.warn(`[eve-shim] ${name}() not available here`, args)

  if (!window.__TAURI__) {
    // Plain browser (dev preview): no-op everything so pages still render.
    window.eve = new Proxy({}, {
      get: (_t, prop) => (prop === 'then' ? undefined : stub(String(prop))),
    })
    return
  }

  window.__EVE_TAURI__ = true

  const T = window.__TAURI__
  const invoke = T.core.invoke
  const { listen, emit } = T.event
  const { getCurrentWindow, availableMonitors, primaryMonitor } = T.window
  const getCurrentWebview = T.webview.getCurrentWebview

  // Fire-and-forget to the shell in the orb window (emit broadcasts; only the
  // shell registers an 'eve-cmd' listener).
  const cmd = (ch, args) => { emit('eve-cmd', { ch, args: args === undefined ? null : args }) }
  // Electron ipcRenderer.on(cb) invoked cb(event, payload) — keep that shape.
  const on = (event) => (cb) => { listen(event, (e) => cb(e, e.payload)) }

  // ── shared JSON stores (Rust file_get/file_set, same files as ui/main.js) ──
  const readJson = async (key, fallback) => {
    try { return JSON.parse(await invoke('file_get', { key })) } catch { return fallback }
  }
  const readSettings  = () => readJson('settings', {})
  const writeSettings = (s) => invoke('file_set', { key: 'settings', text: JSON.stringify(s, null, 2) })

  const EDITOR_FILES = ['commands', 'apps', 'aliases']
  const readList = async (key) => {
    const v = await readJson(key, [])
    return Array.isArray(v) ? v : []
  }
  const writeList = async (key, data) => {
    if (!Array.isArray(data)) return { ok: false, error: 'not an array' }
    try {
      await invoke('file_set', { key, text: JSON.stringify(data, null, 2) })
      emit('eve-commands-changed', { file: key })
      return { ok: true }
    } catch (e) { return { ok: false, error: String(e) } }
  }

  // Keep this in sync with BUILTIN_MAP in core/dispatcher.py (copied from ui/main.js).
  const BUILTIN_REFERENCE = [
    { key: 'get_time',         label: 'Tell me the time' },
    { key: 'get_date',         label: 'Tell me the date' },
    { key: 'volume_up',        label: 'Volume up' },
    { key: 'volume_down',      label: 'Volume down' },
    { key: 'toggle_mute',      label: 'Mute / unmute' },
    { key: 'play_pause',       label: 'Play / pause media' },
    { key: 'next_track',       label: 'Next track' },
    { key: 'prev_track',       label: 'Previous track' },
    { key: 'screenshot',       label: 'Take a screenshot' },
    { key: 'list_reminders',   label: 'List my reminders' },
    { key: 'cancel_reminders', label: 'Cancel all reminders' },
    { key: 'open_editor',      label: 'Open command editor' },
    { key: 'sleep',            label: 'Put PC to sleep' },
    { key: 'shutdown',         label: 'Shut down PC' },
    { key: 'cancel_shutdown',  label: 'Cancel shutdown' },
  ]

  // ── displays (Tauri coords are PHYSICAL px — matches Python's Win32 space,
  //    so no Electron-style DIP→physical conversion is needed) ────────────────
  async function getDisplays() {
    const [mons, prim, settings] = await Promise.all([
      availableMonitors(), primaryMonitor(), readSettings(),
    ])
    const primName = prim && prim.name
    const pinned = settings.overlayDisplayId || primName
    return mons.map((m, i) => ({
      id:          m.name,           // ponytail: monitor name is the stable id under Tauri;
      index:       i + 1,            // Electron-era numeric ids in saved settings/layouts won't
      label:       m.name || `Display ${i + 1}`,   // match and fall back to primary — re-pin once.
      x:           m.position.x,
      y:           m.position.y,
      width:       m.size.width,
      height:      m.size.height,
      workX:       m.workArea.position.x,
      workY:       m.workArea.position.y,
      workWidth:   m.workArea.size.width,
      workHeight:  m.workArea.size.height,
      scaleFactor: m.scaleFactor,
      refreshRate: null,             // not exposed by Tauri; pages tolerate null
      rotation:    0,
      isPrimary:   m.name === primName,
      isPinned:    m.name === pinned,
    }))
  }

  // ── UI scale (mirrors ui/main.js _readUiScale/_autoUiScale) ────────────────
  async function computeUiScale() {
    const s = await readSettings()
    const v = Number(s.ui_scale)
    if (v && v > 0) return Math.min(2.0, Math.max(0.8, v))
    try {
      // ponytail: physical width, not Electron's DIP width — more honest about
      // "is this panel on a 1440p/4K monitor".
      const p = await primaryMonitor()
      if (p.size.width >= 3840) return 1.5
      if (p.size.width >= 2560) return 1.25
    } catch {}
    return 1.0
  }

  // Panels zoom themselves on load (Electron did this centrally via
  // web-contents-created; self-zoom needs no cross-window hook).
  const PANEL_FOLDERS = new Set([
    'app-manager', 'window-manager', 'voice-settings', 'command-editor',
    'programs', 'memory', 'reminders', 'integrations',
  ])
  const _folder = (location.pathname.match(/\/([^/]+)\/index\.html$/) || [])[1]
  if (PANEL_FOLDERS.has(_folder)) {
    computeUiScale().then((z) => getCurrentWebview().setZoom(z)).catch(() => {})
  }

  // ── tiling layouts (read-modify-write, same shape ui/main.js wrote) ────────
  async function setTilingLayout(monitorId, monitorData) {
    try {
      // Tauri work areas are already physical — phys* mirrors work* directly
      // (Electron needed dipToScreenRect here).
      monitorData.physX      = monitorData.workX
      monitorData.physY      = monitorData.workY
      monitorData.physWidth  = monitorData.workWidth
      monitorData.physHeight = monitorData.workHeight
      const layouts = await readJson('tiling', { monitors: {} })
      if (!layouts.monitors) layouts.monitors = {}
      layouts.monitors[String(monitorId)] = monitorData
      await invoke('file_set', { key: 'tiling', text: JSON.stringify(layouts, null, 2) })
      return { success: true }
    } catch (e) { return { success: false, error: String(e) } }
  }

  const eve = {
    // ── directory / HUD (shell-managed) ──
    showDirectory:       () => cmd('show-directory'),
    hideDirectory:       () => cmd('hide-directory'),
    toggleDirectory:     () => cmd('toggle-directory'),
    toggleDirectorySize: () => cmd('toggle-directory-size'),

    // ── panels (shell-managed) ──
    openAppManager:     () => cmd('open-app-manager'),
    closeAppManager:    () => cmd('close-app-manager'),
    openWindowManager:  () => cmd('open-window-manager'),
    closeWindowManager: () => cmd('close-window-manager'),
    openVoiceSettings:  () => cmd('open-voice-settings'),
    closeVoiceSettings: () => cmd('close-voice-settings'),
    openCommandEditor:  () => cmd('open-command-editor'),
    closeCommandEditor: () => cmd('close-command-editor'),
    openPrograms:       () => cmd('open-programs'),
    closePrograms:      () => cmd('close-programs'),
    openMemory:         () => cmd('open-memory'),
    closeMemory:        () => cmd('close-memory'),
    openReminders:      () => cmd('open-reminders'),
    closeReminders:     () => cmd('close-reminders'),
    openIntegrations:   (target) => cmd('open-integrations', target),
    closeIntegrations:  () => cmd('close-integrations'),
    snapPanel:          (panel, bounds) => cmd('snap-panel', { panel, bounds }),
    resetWindowLayout:  () => cmd('reset-window-layout'),

    // ── displays / overlays ──
    getDisplays:          getDisplays,
    setOverlayDisplay:    (displayId) => cmd('set-overlay-display', { displayId }),
    identifyMonitors:     () => cmd('identify-monitors'),
    identifyZones:        () => cmd('identify-zones'),
    identifyWindows:      (payload) => cmd('identify-windows', payload),
    // Electron closed the *sender* window; under Tauri the overlay just closes itself.
    dismissZoneOverlay:   () => getCurrentWindow().close(),
    dismissWindowOverlay: () => getCurrentWindow().close(),
    wmApplyPreset:        (monitorRef, presetKey) => cmd('wm-apply-preset', { monitorRef, presetKey }),
    wmMoveHud:            (monitorRef) => cmd('wm-move-hud', { monitorRef }),
    wmSetOrbCorner:       (corner) => cmd('wm-set-orb-corner', { corner }),

    // ── events (shell broadcasts; Electron cb shapes preserved) ──
    onDisplaysChanged:      on('eve-displays-changed'),
    onDirectorySizeChanged: on('eve-directory-size-changed'),
    onLayoutsChanged:       on('eve-layouts-changed'),
    onCommandsChanged:      on('eve-commands-changed'),
    onScrollTo:             (cb) => { listen('eve-scroll-to', (e) => cb(e.payload)) },

    // ── UI scale ──
    getUiScale: () => computeUiScale(),
    setUiScale: (v) => cmd('set-ui-scale', v),

    // ── self window controls (titlebar.js) ──
    closeSelf:    () => getCurrentWindow().close(),
    maximizeSelf: async () => {
      const w = getCurrentWindow()
      ;(await w.isMaximized()) ? w.unmaximize() : w.maximize()
    },

    openExternal: (url) => {
      if (typeof url === 'string' && /^https?:\/\//i.test(url)) invoke('open_external', { url })
    },

    // ── YouTube HUD (shell-managed; JS injected via Rust eval_in) ──
    openYoutube:      () => cmd('open-youtube'),
    youtubeScroll:    (dir) => cmd('youtube-scroll', dir),
    youtubeNumber:    () => cmd('youtube-number'),
    youtubeOpen:      (n) => cmd('youtube-open', n),
    youtubeSearch:    (query) => cmd('youtube-search', query),
    youtubePlayPause: () => cmd('youtube-playpause'),
    closeYoutube:     () => cmd('close-youtube'),

    // ── command editor file I/O (Rust commands, local to this window) ──
    ceGetCommands: () => readList('commands'),
    ceSetCommands: (data) => writeList('commands', data),
    ceGetApps:     () => readList('apps'),
    ceSetApps:     (data) => writeList('apps', data),
    ceGetAliases:  () => readList('aliases'),
    ceSetAliases:  (data) => writeList('aliases', data),
    ceGetBuiltins: () => Promise.resolve(BUILTIN_REFERENCE),
    ceBrowseExe:   () => invoke('browse_exe'),
    ceGetRaw:      async (key) => {
      if (!EDITOR_FILES.includes(key)) return ''
      try { return await invoke('file_get', { key }) } catch { return '[]' }
    },
    ceSetRaw:      async (key, text) => {
      if (!EDITOR_FILES.includes(key)) return { ok: false, error: 'unknown file' }
      let parsed
      try { parsed = JSON.parse(text) }
      catch (e) { return { ok: false, error: 'Invalid JSON: ' + e.message } }
      if (!Array.isArray(parsed)) return { ok: false, error: 'Top level must be an array' }
      return writeList(key, parsed)
    },

    // ── voice settings / presets (settings.json, same defaults as main.js) ──
    getVoiceSettings: async () =>
      (await readSettings()).voice || { speed: 1.0, noise_scale: 0.667, noise_w: 0.8 },
    getVoicePresets:  async () => (await readSettings()).voice_presets || {},
    saveVoicePreset:  async (name, params) => {
      const s = await readSettings()
      if (!s.voice_presets) s.voice_presets = {}
      s.voice_presets[name] = params
      await writeSettings(s)
      return s.voice_presets
    },
    deleteVoicePreset: async (name) => {
      const s = await readSettings()
      if (s.voice_presets) delete s.voice_presets[name]
      await writeSettings(s)
      return s.voice_presets || {}
    },

    // ── tiling ──
    getTilingLayouts: () => readJson('tiling', { monitors: {} }),
    setTilingLayout:  setTilingLayout,
  }

  // Anything not listed resolves to a warning no-op instead of throwing.
  window.eve = new Proxy(eve, {
    get: (t, prop) => (prop in t ? t[prop] : prop === 'then' ? undefined : stub(String(prop))),
  })

  // Internals reused by the shell (loaded after this script in the orb window).
  window.__eveShimUtil = { invoke, listen, emit, readJson, readSettings, writeSettings, computeUiScale, getDisplays }
})();
