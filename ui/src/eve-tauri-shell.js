// Tauri native shell — the migration replacement for Electron's ui/main.js.
// Runs ONLY in the orb window ('main') under Tauri; a near 1:1 port of the
// window-management logic, using the injected __TAURI__ global API.
// Loaded by ui/src/index.html after eve-tauri-shim.js (whose helpers it reuses).
//
// Focus invariant (see ROADMAP §1): tao shows a window created with
// focus:false via SW_SHOWNOACTIVATE — but only on its FIRST show (the marker
// is consumed). So focus-sensitive surfaces (directory, overlays) are created
// fresh on open and destroyed on close, never hidden/re-shown; raises use
// setAlwaysOnTop which is SWP_NOACTIVATE underneath.
(function () {
  if (!window.__TAURI__) return
  const T = window.__TAURI__
  if (T.window.getCurrentWindow().label !== 'main') return

  const { WebviewWindow } = T.webviewWindow
  const { getCurrentWindow, availableMonitors, primaryMonitor } = T.window
  const { PhysicalPosition, PhysicalSize, LogicalSize } = T.dpi
  const { listen, emit } = T.event
  const { invoke, readJson, readSettings, writeSettings, computeUiScale } = window.__eveShimUtil

  const P = (x, y) => new PhysicalPosition(Math.round(x), Math.round(y))
  const S = (w, h) => new PhysicalSize(Math.round(w), Math.round(h))

  const orb = getCurrentWindow()
  const _wins = new Map()          // label -> WebviewWindow created by this shell
  let _uiScale = 1.0
  let _dirExpanded = false
  let _savedDirBounds = null

  function registerWin(label, win) {
    _wins.set(label, win)
    win.once('tauri://destroyed', () => { if (_wins.get(label) === win) _wins.delete(label) })
    win.once('tauri://error', (e) => {
      console.error('[eve-shell] window error:', label, e.payload)
      if (_wins.get(label) === win) _wins.delete(label)
    })
  }

  const saveSettings = async (patch) => {
    const s = await readSettings()
    await writeSettings({ ...s, ...patch })
  }

  // ── geometry (all physical px — Tauri's native space, same as Win32) ───────

  const ORB_SIZE = 96, ORB_MARGIN = 10
  const DIR_W = 700, DIR_H = 520, DIR_GAP = 10
  const VALID_CORNERS = ['top-right', 'top-left', 'bottom-right', 'bottom-left']

  async function orbMonitor() {
    const s = await readSettings()
    const mons = await availableMonitors()
    return mons.find((m) => m.name === s.overlayDisplayId) || (await primaryMonitor())
  }

  // Electron sized in DIPs; multiply the same numbers by the monitor's scale
  // factor so windows are visually identical on 125%/150% displays.
  async function cornerLayout() {
    const s = await readSettings()
    const m = await orbMonitor()
    const sf = m.scaleFactor || 1
    const corner = VALID_CORNERS.includes(s.overlayCorner) ? s.overlayCorner : 'top-right'
    const orbPx = Math.round(ORB_SIZE * _uiScale * sf)
    const margin = Math.round(ORB_MARGIN * sf)
    const dirW = Math.round(DIR_W * sf), dirH = Math.round(DIR_H * sf), gap = Math.round(DIR_GAP * sf)
    const { x, y } = m.position
    const { width, height } = m.size
    const [v, h] = corner.split('-')
    const orbX = h === 'right' ? x + width - orbPx - margin : x + margin
    const orbY = v === 'bottom' ? y + height - orbPx - margin : y + margin
    const dirX = h === 'right' ? x + width - dirW - margin : x + margin
    // Top corners: directory below the orb; bottom corners: above.
    const dirY = v === 'bottom' ? orbY - dirH - gap : orbY + orbPx + gap
    return { orbX, orbY, orbPx, dirX, dirY, dirW, dirH, monitor: m, sf }
  }

  async function positionOrb() {
    const L = await cornerLayout()
    await orb.setSize(S(L.orbPx, L.orbPx))
    await orb.setPosition(P(L.orbX, L.orbY))
    T.webview.getCurrentWebview().setZoom(_uiScale).catch(() => {})
  }

  async function positionDirectory() {
    const w = _wins.get('directory')
    if (!w || _dirExpanded) return
    const L = await cornerLayout()
    await w.setPosition(P(L.dirX, L.dirY))
  }

  function monitorAt(mons, prim, x, y) {
    return mons.find((m) =>
      x >= m.position.x && x < m.position.x + m.size.width &&
      y >= m.position.y && y < m.position.y + m.size.height) || prim
  }

  // ── directory window ────────────────────────────────────────────────────────

  async function showDirectory() {
    _dirExpanded = false
    _savedDirBounds = null
    const L = await cornerLayout()
    const existing = _wins.get('directory')
    if (existing) {
      await existing.setPosition(P(L.dirX, L.dirY))
      await existing.setSize(S(L.dirW, L.dirH))
      // moveTop equivalent: topmost pulse raises without activating (SWP_NOACTIVATE)
      await existing.setAlwaysOnTop(true); await existing.setAlwaysOnTop(false)
      return
    }
    const win = new WebviewWindow('directory', {
      url: 'directory/index.html',
      width: DIR_W, height: DIR_H, visible: false,
      transparent: true, decorations: false, skipTaskbar: true,
      resizable: false, focus: false, shadow: false,
    })
    registerWin('directory', win)
    win.once('tauri://created', async () => {
      await win.setSize(S(L.dirW, L.dirH))
      await win.setPosition(P(L.dirX, L.dirY))
      await win.show()               // first show after focus:false → no activation
      // moveTop: SW_SHOWNOACTIVATE alone leaves the new window BEHIND a
      // foreground borderless-fullscreen game — pulse topmost (SWP_NOACTIVATE)
      // to surface it above the game without taking focus.
      await win.setAlwaysOnTop(true); await win.setAlwaysOnTop(false)
    })
  }

  // ponytail: destroy on hide (not hide()) so the next show is again a
  // focus-free FIRST show; recreation is cheap, the page is light.
  function hideDirectory() {
    _dirExpanded = false
    _savedDirBounds = null
    const w = _wins.get('directory')
    if (w) w.close().catch(() => {})
  }

  function toggleDirectory() {
    _wins.get('directory') ? hideDirectory() : showDirectory()
  }

  async function toggleDirectorySize() {
    const w = _wins.get('directory')
    if (!w) return
    if (_dirExpanded) {
      _dirExpanded = false
      if (_savedDirBounds) {
        await w.setPosition(P(_savedDirBounds.x, _savedDirBounds.y))
        await w.setSize(S(_savedDirBounds.w, _savedDirBounds.h))
        _savedDirBounds = null
      } else {
        const L = await cornerLayout()
        await w.setPosition(P(L.dirX, L.dirY))
        await w.setSize(S(L.dirW, L.dirH))
      }
    } else {
      const pos = await w.outerPosition()
      const size = await w.innerSize()
      _savedDirBounds = { x: pos.x, y: pos.y, w: size.width, h: size.height }
      _dirExpanded = true
      const wa = monitorAt(await availableMonitors(), await primaryMonitor(), pos.x, pos.y).workArea
      await w.setPosition(P(wa.position.x, wa.position.y))
      await w.setSize(S(wa.size.width, wa.size.height))
    }
    emit('eve-directory-size-changed', { expanded: _dirExpanded })
  }

  // ── managed panels (size restore/persist like createManagedWindow) ─────────

  const PANELS = {
    'app-manager':    { url: 'app-manager/index.html',    width: 860, height: 620, minWidth: 640, minHeight: 460, title: 'Eve — App Manager' },
    'window-manager': { url: 'window-manager/index.html', width: 860, height: 680, minWidth: 640, minHeight: 520, title: 'Eve — Window Manager' },
    'voice-settings': { url: 'voice-settings/index.html', width: 500, height: 540, minWidth: 420, minHeight: 460, title: 'Eve — Voice Settings', resizable: false },
    'command-editor': { url: 'command-editor/index.html', width: 920, height: 680, minWidth: 720, minHeight: 520, title: 'Eve — Command Editor' },
    'programs':       { url: 'programs/index.html',       width: 640, height: 600, minWidth: 480, minHeight: 380, title: 'Eve — Running Programs' },
    'memory':         { url: 'memory/index.html',         width: 560, height: 520, minWidth: 460, minHeight: 360, title: 'Eve — Memory' },
    'reminders':      { url: 'reminders/index.html',      width: 600, height: 520, minWidth: 500, minHeight: 360, title: 'Eve — Reminders' },
    'integrations':   { url: 'integrations/index.html',   width: 520, height: 380, minWidth: 460, minHeight: 320, title: 'Eve — Integrations' },
  }

  // Saved sizes are LOGICAL px (settings.json windowState — same unit Electron
  // saved, so existing values carry over).
  async function restoreSize(def, name) {
    const st = ((await readSettings()).windowState || {})[name]
    if (st && Number.isFinite(st.width) && Number.isFinite(st.height)) {
      let { width, height } = st
      try {
        const p = await primaryMonitor()
        width = Math.min(Math.round(width), Math.round(p.workArea.size.width / p.scaleFactor))
        height = Math.min(Math.round(height), Math.round(p.workArea.size.height / p.scaleFactor))
      } catch {}
      if (width >= def.minWidth && height >= def.minHeight) return { width, height }
    }
    return { width: def.width, height: def.height }
  }

  async function saveWindowState(name, win) {
    try {
      if (await win.isMaximized()) return
      const size = await win.innerSize()
      const sf = await win.scaleFactor()
      const s = await readSettings()
      if (!s.windowState) s.windowState = {}
      s.windowState[name] = { width: Math.round(size.width / sf), height: Math.round(size.height / sf) }
      await writeSettings(s)
    } catch {}
  }

  async function openPanel(name, hash) {
    const def = PANELS[name]
    if (!def) return
    const existing = _wins.get(name)
    if (existing) {
      existing.setFocus().catch(() => {})
      if (name === 'integrations' && hash) emit('eve-scroll-to', hash)
      return
    }
    const resizable = def.resizable !== false
    const dims = resizable ? await restoreSize(def, name) : { width: def.width, height: def.height }
    const win = new WebviewWindow(name, {
      url: def.url + (hash ? '#' + hash : ''),
      width: dims.width, height: dims.height,
      minWidth: def.minWidth, minHeight: def.minHeight,
      title: def.title, decorations: false, resizable,
      backgroundColor: '#080e18', center: true,
    })
    registerWin(name, win)
    if (resizable) {
      let t = null
      // ponytail: persisted on debounced resize only (no close-time save —
      // a resize finished <400ms before closing is lost, acceptable).
      win.onResized(() => {
        clearTimeout(t)
        t = setTimeout(() => saveWindowState(name, win), 400)
      })
    }
  }

  function closePanel(name) {
    const w = _wins.get(name)
    if (w) w.close().catch(() => {})
  }

  async function resetWindowLayout() {
    const s = await readSettings()
    if (s.windowState) { delete s.windowState; await writeSettings(s) }
    for (const [name, def] of Object.entries(PANELS)) {
      const w = _wins.get(name)
      if (!w || def.resizable === false) continue
      try {
        if (await w.isMaximized()) await w.unmaximize()
        await w.setSize(new LogicalSize(def.width, def.height))
        await w.center()
      } catch {}
    }
  }

  // ── snap panel (voice: "snap window manager to top-left") ───────────────────

  const SNAP_LABELS = {
    directory:      'directory',
    window_manager: 'window-manager',
    app_manager:    'app-manager',
    voice_settings: 'voice-settings',
  }

  function snapPanel(panel, bounds) {
    const label = SNAP_LABELS[panel]
    if (!label || !bounds) return
    if (!_wins.get(label)) {
      label === 'directory' ? showDirectory() : openPanel(label)
    }
    // Wait for any just-created window to finish its initial placement.
    setTimeout(async () => {
      const win = _wins.get(label)
      if (!win) return
      try {
        await win.setPosition(P(bounds.x, bounds.y))
        await win.setSize(S(bounds.width, bounds.height))
        if (label === 'directory') { await win.setAlwaysOnTop(true); await win.setAlwaysOnTop(false) }
      } catch {}
    }, 300)
  }

  // ── identify monitors / zones / windows overlays ────────────────────────────

  function closeGroup(prefix) {
    for (const [label, w] of _wins) if (label.startsWith(prefix)) w.close().catch(() => {})
  }

  function spawnOverlay(label, url, extra) {
    const win = new WebviewWindow(label, {
      url,
      width: 100, height: 100, visible: false,
      transparent: true, decorations: false, alwaysOnTop: true, skipTaskbar: true,
      resizable: false, focus: false, shadow: false, visibleOnAllWorkspaces: true,
      ...extra,
    })
    registerWin(label, win)
    return win
  }

  async function identifyMonitors(durationMs = 3500) {
    closeGroup('monitor-id-')
    const mons = await availableMonitors()
    const prim = await primaryMonitor()
    mons.forEach((d, i) => {
      const sf = d.scaleFactor || 1
      const CARD = Math.round(340 * sf), MARGIN = Math.round(20 * sf)
      const wa = d.workArea
      const hash = new URLSearchParams({
        index:   String(i + 1),
        label:   d.name || `Display ${i + 1}`,
        primary: d.name === prim.name ? '1' : '0',
        meta:    `${d.size.width}x${d.size.height}`,
      }).toString()
      const win = spawnOverlay(`monitor-id-${i + 1}`, `monitor-id/index.html#${hash}`, { focusable: false })
      win.once('tauri://created', async () => {
        await win.setSize(S(CARD, CARD))
        // Bottom-left of workArea (excludes taskbar)
        await win.setPosition(P(wa.position.x + MARGIN, wa.position.y + wa.size.height - CARD - MARGIN))
        await win.show()
      })
    })
    setTimeout(() => closeGroup('monitor-id-'), durationMs)
  }

  async function identifyZones(durationMs = 6000) {
    closeGroup('zone-id-')
    const layouts = await readJson('tiling', {})
    const monitors = (layouts && layouts.monitors) || {}
    const mons = await availableMonitors()
    mons.forEach((d, i) => {
      const saved = monitors[String(d.name)]
      if (!saved || !saved.zones || !saved.zones.length) return
      const wa = d.workArea
      const hash = new URLSearchParams({
        zones:        JSON.stringify(saved.zones),
        layout:       saved.layout || '',
        monitorIndex: String(i + 1),
        monitorLabel: d.name || `Display ${i + 1}`,
      }).toString()
      // focusable (default) so clicks route through to the page
      const win = spawnOverlay(`zone-id-${i + 1}`, `zone-id/index.html#${hash}`)
      win.once('tauri://created', async () => {
        await win.setPosition(P(wa.position.x, wa.position.y))
        await win.setSize(S(wa.size.width, wa.size.height))
        await win.show()
      })
    })
    setTimeout(() => closeGroup('zone-id-'), durationMs)
  }

  async function identifyWindows(payload, durationMs = 6000) {
    closeGroup('window-tag-')
    const list = (payload && payload.windows) || []
    if (!list.length) return
    const mons = await availableMonitors()
    const prim = await primaryMonitor()
    list.forEach((item, n) => {
      const sf = monitorAt(mons, prim, item.x, item.y).scaleFactor || 1
      const hash = new URLSearchParams({
        index: String(item.index),
        label: String(item.label || ''),
      }).toString()
      const win = spawnOverlay(`window-tag-${n + 1}`, `window-id/index.html#${hash}`)
      win.once('tauri://created', async () => {
        await win.setSize(S(180 * sf, 36 * sf))
        await win.setPosition(P(item.x + 6 * sf, item.y + 6 * sf))
        await win.show()
      })
    })
    setTimeout(() => closeGroup('window-tag-'), durationMs)
  }

  // ── window-manager voice control ────────────────────────────────────────────

  // Mirror of the renderer's PRESETS — keep in sync with ui/src/window-manager/app.js
  const WM_PRESETS = {
    'full':       [{ name: 'full', x_pct: 0, y_pct: 0, w_pct: 1, h_pct: 1 }],
    'top-bottom': [
      { name: 'top',    x_pct: 0, y_pct: 0,   w_pct: 1, h_pct: 0.5 },
      { name: 'bottom', x_pct: 0, y_pct: 0.5, w_pct: 1, h_pct: 0.5 },
    ],
    'left-right': [
      { name: 'left',  x_pct: 0,   y_pct: 0, w_pct: 0.5, h_pct: 1 },
      { name: 'right', x_pct: 0.5, y_pct: 0, w_pct: 0.5, h_pct: 1 },
    ],
    'main-right': [
      { name: 'main',  x_pct: 0,    y_pct: 0, w_pct: 0.67, h_pct: 1 },
      { name: 'right', x_pct: 0.67, y_pct: 0, w_pct: 0.33, h_pct: 1 },
    ],
    'main-stack': [
      { name: 'main',         x_pct: 0,   y_pct: 0,   w_pct: 0.5, h_pct: 1   },
      { name: 'top-right',    x_pct: 0.5, y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-right', x_pct: 0.5, y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
    ],
    'grid-4': [
      { name: 'top-left',     x_pct: 0,   y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'top-right',    x_pct: 0.5, y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-left',  x_pct: 0,   y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-right', x_pct: 0.5, y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
    ],
  }

  // ref: 'primary', 'hud', or a 1-based index string
  async function resolveDisplay(ref) {
    const mons = await availableMonitors()
    const prim = await primaryMonitor()
    const r = String(ref || '').toLowerCase().trim()
    if (r === 'primary') return prim
    if (r === 'hud') return orbMonitor()
    const n = parseInt(r, 10)
    if (!isNaN(n) && n >= 1 && n <= mons.length) return mons[n - 1]
    return null
  }

  async function wmApplyPreset(monitorRef, presetKey) {
    const d = await resolveDisplay(monitorRef)
    const zones = WM_PRESETS[presetKey]
    if (!d || !zones) return
    const wa = d.workArea
    const monitorData = {
      label:       d.name || 'Display',
      workX:       wa.position.x,
      workY:       wa.position.y,
      workWidth:   wa.size.width,
      workHeight:  wa.size.height,
      scaleFactor: d.scaleFactor || 1.0,
      layout:      presetKey,
      zones,
      // Tauri work areas are already physical px
      physX: wa.position.x, physY: wa.position.y,
      physWidth: wa.size.width, physHeight: wa.size.height,
    }
    const layouts = await readJson('tiling', { monitors: {} })
    if (!layouts.monitors) layouts.monitors = {}
    layouts.monitors[String(d.name)] = monitorData
    await invoke('file_set', { key: 'tiling', text: JSON.stringify(layouts, null, 2) })
    emit('eve-layouts-changed', null)
  }

  async function wmMoveHud(monitorRef) {
    const d = await resolveDisplay(monitorRef)
    if (!d) return
    await saveSettings({ overlayDisplayId: d.name })
    await positionOrb()
    await positionDirectory()
    emit('eve-displays-changed', null)   // WM panel refreshes its HUD pin badge
  }

  async function wmSetOrbCorner(corner) {
    if (!VALID_CORNERS.includes(corner)) return
    await saveSettings({ overlayCorner: corner })
    await positionOrb()
    await positionDirectory()
  }

  async function setOverlayDisplay(displayId) {
    await saveSettings({ overlayDisplayId: displayId })
    await positionOrb()
    await positionDirectory()
  }

  // ── UI scale ────────────────────────────────────────────────────────────────

  async function setUiScale(v) {
    _uiScale = Math.min(2.0, Math.max(0.8, Number(v) || 1.0))
    await saveSettings({ ui_scale: _uiScale })
    await positionOrb()                    // orb tracks the scale (size + zoom)
    await positionDirectory()
    for (const name of Object.keys(PANELS)) {
      const w = _wins.get(name)
      if (w) w.setZoom(_uiScale).catch(() => {})
    }
  }

  // ── YouTube HUD browser ─────────────────────────────────────────────────────
  // A real webview showing youtube.com, floated over (borderless) games with
  // the no-focus topmost recipe. It's a remote page with no IPC — control is
  // injected from Rust via eval_in. Session persists in the app's WebView2
  // profile (Electron used a dedicated partition).

  const YT_W = 480, YT_H = 320, YT_MARGIN = 12

  async function openYoutube(url) {
    const existing = _wins.get('youtube')
    if (existing) { existing.setAlwaysOnTop(true).catch(() => {}); return }
    const m = await orbMonitor()
    const sf = m.scaleFactor || 1
    const w = Math.round(YT_W * sf), h = Math.round(YT_H * sf), margin = Math.round(YT_MARGIN * sf)
    const win = new WebviewWindow('youtube', {
      url: url || 'https://www.youtube.com',
      width: YT_W, height: YT_H, visible: false,
      decorations: false, skipTaskbar: true, resizable: true,
      focus: false, alwaysOnTop: true, backgroundColor: '#0f0f0f',
      visibleOnAllWorkspaces: true,
    })
    registerWin('youtube', win)
    win.once('tauri://created', async () => {
      await win.setSize(S(w, h))
      await win.setPosition(P(m.position.x + m.size.width - w - margin,
                              m.position.y + m.size.height - h - margin))
      await win.show()                     // first show, focus:false → no activation
    })
  }

  // Run JS in the page, then re-assert topmost (raise without focus).
  function ytRun(js) {
    if (!_wins.get('youtube')) return
    invoke('eval_in', { label: 'youtube', js }).catch(() => {})
    _wins.get('youtube').setAlwaysOnTop(true).catch(() => {})
  }

  function youtubeScroll(dir) {
    ytRun(dir === 'up' ? 'window.scrollBy(0,-800)'
        : dir === 'top' ? 'window.scrollTo(0,0)'
        : 'window.scrollBy(0,800)')
  }

  // Badge the in-viewport video tiles 1..N and stash their click targets.
  // ponytail: selectors track YouTube's current DOM; retarget here if layout shifts.
  const YT_NUMBER_JS = `(() => {
  document.querySelectorAll('.eve-badge').forEach(b => b.remove());
  const sel = 'ytd-rich-item-renderer, ytd-video-renderer';
  const vh = window.innerHeight;
  const tiles = [...document.querySelectorAll(sel)].filter(t => {
    const r = t.getBoundingClientRect();
    return r.top >= -40 && r.top < vh && r.height > 40;
  }).slice(0, 9);
  window.__eveTiles = tiles.map(t => t.querySelector('a#thumbnail, a#video-title-link, a#video-title') || t);
  tiles.forEach((t, i) => {
    const b = document.createElement('div');
    b.className = 'eve-badge';
    b.textContent = i + 1;
    b.style.cssText = 'position:absolute;z-index:9999;top:6px;left:6px;background:#4a9eff;color:#000;font:bold 16px sans-serif;width:26px;height:26px;border-radius:4px;display:flex;align-items:center;justify-content:center;box-shadow:0 0 6px rgba(0,0,0,.6)';
    t.style.position = 'relative';
    t.appendChild(b);
  });
})()`

  function youtubeOpen(n) {
    const i = Math.max(1, parseInt(n, 10) || 1) - 1
    ytRun(`(window.__eveTiles && window.__eveTiles[${i}]) && window.__eveTiles[${i}].click()`)
  }

  function youtubeSearch(query) {
    const url = 'https://www.youtube.com/results?search_query=' + encodeURIComponent(query || '')
    if (_wins.get('youtube')) ytRun(`location.href = ${JSON.stringify(url)}`)
    else openYoutube(url)
  }

  // ── dispatch (eve-cmd events from the shim in any window + the Rust tray) ──

  const HANDLERS = {
    'show-directory':        () => showDirectory(),
    'hide-directory':        () => hideDirectory(),
    'toggle-directory':      () => toggleDirectory(),
    'toggle-directory-size': () => toggleDirectorySize(),
    'set-overlay-display':   (a) => setOverlayDisplay(a.displayId),
    'open-app-manager':      () => openPanel('app-manager'),
    'close-app-manager':     () => closePanel('app-manager'),
    'open-window-manager':   () => openPanel('window-manager'),
    'close-window-manager':  () => closePanel('window-manager'),
    'open-voice-settings':   () => openPanel('voice-settings'),
    'close-voice-settings':  () => closePanel('voice-settings'),
    'open-command-editor':   () => openPanel('command-editor'),
    'close-command-editor':  () => closePanel('command-editor'),
    'open-programs':         () => openPanel('programs'),
    'close-programs':        () => closePanel('programs'),
    'open-memory':           () => openPanel('memory'),
    'close-memory':          () => closePanel('memory'),
    'open-reminders':        () => openPanel('reminders'),
    'close-reminders':       () => closePanel('reminders'),
    'open-integrations':     (a) => openPanel('integrations', a || undefined),
    'close-integrations':    () => closePanel('integrations'),
    'snap-panel':            (a) => snapPanel(a.panel, a.bounds),
    'reset-window-layout':   () => resetWindowLayout(),
    'identify-monitors':     () => identifyMonitors(),
    'identify-zones':        () => identifyZones(),
    'identify-windows':      (a) => identifyWindows(a),
    'wm-apply-preset':       (a) => wmApplyPreset(a.monitorRef, a.presetKey),
    'wm-move-hud':           (a) => wmMoveHud(a.monitorRef),
    'wm-set-orb-corner':     (a) => wmSetOrbCorner(a.corner),
    'set-ui-scale':          (v) => setUiScale(v),
    'open-youtube':          () => openYoutube(),
    'youtube-scroll':        (dir) => youtubeScroll(dir),
    'youtube-number':        () => ytRun(YT_NUMBER_JS),
    'youtube-open':          (n) => youtubeOpen(n),
    'youtube-search':        (q) => youtubeSearch(q),
    'youtube-playpause':     () => ytRun(`(() => { const v = document.querySelector('video'); if (v) v.paused ? v.play() : v.pause(); })()`),
    'close-youtube':         () => closePanel('youtube'),
  }

  // ── startup ─────────────────────────────────────────────────────────────────

  async function init() {
    try {
      _uiScale = await computeUiScale()
      await positionOrb()
    } catch (e) {
      console.error('[eve-shell] init failed:', e)
    }
    await orb.show()                       // orb starts hidden (tauri.conf.json)

    listen('eve-cmd', (e) => {
      const { ch, args } = e.payload || {}
      const h = HANDLERS[ch]
      if (h) Promise.resolve(h(args)).catch((err) => console.error('[eve-shell]', ch, err))
      else console.warn('[eve-shell] unknown channel:', ch)
    })

    // Windows demotes topmost when a fullscreen app takes focus — re-assert
    // every 2s so orb + YouTube stay above borderless fullscreen games.
    setInterval(() => {
      orb.setAlwaysOnTop(true).catch(() => {})
      const yt = _wins.get('youtube')
      if (yt) yt.setAlwaysOnTop(true).catch(() => {})
    }, 2000)

    // ponytail: Tauri has no displays-changed event — poll a cheap signature
    // every 5s; upgrade to a Rust-side WM_DISPLAYCHANGE hook if polling shows.
    let sig = ''
    setInterval(async () => {
      try {
        const mons = await availableMonitors()
        const now = mons.map((m) => `${m.name}:${m.position.x},${m.position.y},${m.size.width}x${m.size.height}`).join('|')
        if (sig && now !== sig) {
          await positionOrb()
          await positionDirectory()
          emit('eve-displays-changed', null)
        }
        sig = now
      } catch {}
    }, 5000)
  }

  init()
})();
