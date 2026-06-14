const { app, BrowserWindow, ipcMain, screen, Tray, Menu, nativeImage } = require('electron')
const fs   = require('fs')
const path = require('path')

const SETTINGS_FILE = path.join(__dirname, '..', 'settings.json')
const TILING_FILE   = path.join(__dirname, '..', 'tiling_layouts.json')

let orbWin           = null
let dirWin           = null
let appManagerWin    = null
let windowManagerWin = null
let voiceSettingsWin = null
let tray             = null
let _savedDirBounds  = null

// ── Settings ─────────────────────────────────────────────────────────────────

function loadSettings() {
  try { return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')) } catch { return {} }
}

function saveSettings(patch) {
  const s = loadSettings()
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify({ ...s, ...patch }, null, 2))
}

// ── Display helpers ───────────────────────────────────────────────────────────

function getOrbDisplay() {
  const { overlayDisplayId } = loadSettings()
  const all = screen.getAllDisplays()
  if (overlayDisplayId) {
    const found = all.find(d => d.id === overlayDisplayId)
    if (found) return found
  }
  return screen.getPrimaryDisplay()
}

function positionOrb() {
  if (!orbWin || orbWin.isDestroyed()) return
  const { x, y, width } = getOrbDisplay().bounds
  orbWin.setPosition(x + width - 96 - 10, y + 10)
}

function positionDirectory() {
  if (!dirWin || dirWin.isDestroyed()) return
  const { x, y, width } = getOrbDisplay().bounds
  dirWin.setPosition(x + width - 700 - 10, y + 116)  // below orb: 96 + 10 + 10
}

// ── Tray icon (programmatic 16×16 blue circle) ────────────────────────────────

function buildTrayIcon() {
  const size = 16
  const buf  = Buffer.alloc(size * size * 4)
  const cx = size / 2, cy = size / 2, r = size / 2 - 1
  for (let row = 0; row < size; row++) {
    for (let col = 0; col < size; col++) {
      const inside = Math.sqrt((col - cx) ** 2 + (row - cy) ** 2) <= r
      const i = (row * size + col) * 4
      buf[i] = 74; buf[i+1] = 158; buf[i+2] = 255
      buf[i+3] = inside ? 220 : 0
    }
  }
  return nativeImage.createFromBuffer(buf, { width: size, height: size })
}

function createTray() {
  tray = new Tray(buildTrayIcon())
  tray.setToolTip('Eve')
  tray.on('click', () => toggleDirectory())
  const menu = Menu.buildFromTemplate([
    { label: 'Open Directory',  click: () => showDirectory()     },
    { label: 'Window Manager',  click: () => openWindowManager() },
    { label: 'App Manager',     click: () => openAppManager()    },
    { type: 'separator' },
    { label: 'Quit Eve',        click: () => app.quit()          },
  ])
  tray.setContextMenu(menu)
}

// ── Orb window ────────────────────────────────────────────────────────────────

function createOrbWin() {
  orbWin = new BrowserWindow({
    width: 96, height: 96,
    frame: false, transparent: true,
    alwaysOnTop: true, skipTaskbar: true, resizable: false,
    focusable: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  })
  // 'screen-saver' is the highest standard z-level — beats borderless fullscreen games.
  // visibleOnFullScreen ensures the orb stays drawn when another window goes fullscreen.
  orbWin.setAlwaysOnTop(true, 'screen-saver', 1)
  orbWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  orbWin.loadFile(path.join(__dirname, 'src', 'index.html'))
  positionOrb()
  orbWin.on('close', e => { if (!app.isQuitting) e.preventDefault() })
  // Windows demotes topmost flags when a fullscreen app takes focus.
  // Re-assert every 2s so the orb stays above borderless fullscreen games.
  setInterval(() => {
    if (orbWin && !orbWin.isDestroyed()) {
      orbWin.setAlwaysOnTop(true, 'screen-saver', 1)
    }
  }, 2000)
}

// ── Directory window ──────────────────────────────────────────────────────────

function createDirWin() {
  dirWin = new BrowserWindow({
    width: 700, height: 520,
    frame: false, transparent: true,
    alwaysOnTop: true, skipTaskbar: true, resizable: false, show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  })
  dirWin.setAlwaysOnTop(true, 'screen-saver', 1)
  dirWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  dirWin.loadFile(path.join(__dirname, 'src', 'directory', 'index.html'))
  dirWin._ready = false
  dirWin.once('ready-to-show', () => { dirWin._ready = true })
  dirWin.on('close', e => {
    if (!app.isQuitting) { e.preventDefault(); hideDirectory() }
  })
  // Same periodic re-assert as the orb. Windows clears topmost flags on hidden
  // windows when a fullscreen app takes focus; this keeps the flag alive so
  // show() pops above the game instead of behind it.
  setInterval(() => {
    if (dirWin && !dirWin.isDestroyed()) {
      dirWin.setAlwaysOnTop(true, 'screen-saver', 1)
    }
  }, 2000)
}

function showDirectory() {
  if (!dirWin || dirWin.isDestroyed()) createDirWin()
  dirWin._expanded = false
  _savedDirBounds  = null
  const { x, y, width } = getOrbDisplay().bounds
  dirWin.setBounds({ x: x + width - 700 - 10, y: y + 116, width: 700, height: 520 })
  const present = () => {
    // Pre-assert topmost so the OS orders the window above the fullscreen app
    // at the moment show() takes effect, not after.
    dirWin.setAlwaysOnTop(true, 'screen-saver', 1)
    dirWin.show()
    // Re-assert after — Windows sometimes processes the show() event before
    // applying the new z-order; this catches that race.
    dirWin.setAlwaysOnTop(true, 'screen-saver', 1)
    dirWin.moveTop()
    dirWin.focus()
  }
  if (dirWin._ready) present()
  else                dirWin.once('ready-to-show', present)
}

function hideDirectory() {
  if (!dirWin || dirWin.isDestroyed() || !dirWin.isVisible()) return
  dirWin._expanded = false
  _savedDirBounds  = null
  dirWin.hide()
}

function toggleDirectory() {
  if (dirWin && !dirWin.isDestroyed() && dirWin.isVisible()) hideDirectory()
  else showDirectory()
}

// ── IPC ───────────────────────────────────────────────────────────────────────

ipcMain.on('show-directory',   () => showDirectory())
ipcMain.on('hide-directory',   () => hideDirectory())
ipcMain.on('toggle-directory', () => toggleDirectory())

ipcMain.on('toggle-directory-size', () => {
  if (!dirWin || dirWin.isDestroyed()) return
  if (dirWin._expanded) {
    dirWin._expanded = false
    if (_savedDirBounds) {
      dirWin.setBounds(_savedDirBounds)
      _savedDirBounds = null
    } else {
      const { x, y, width } = getOrbDisplay().bounds
      dirWin.setBounds({ x: x + width - 700 - 10, y: y + 116, width: 700, height: 520 })
    }
  } else {
    _savedDirBounds  = dirWin.getBounds()
    dirWin._expanded = true
    const wa = screen.getDisplayMatching(_savedDirBounds).workArea
    dirWin.setBounds({ x: wa.x, y: wa.y, width: wa.width, height: wa.height })
  }
  dirWin.webContents.send('directory-size-changed', { expanded: !!dirWin._expanded })
})

ipcMain.on('set-overlay-display', (_, { displayId }) => {
  saveSettings({ overlayDisplayId: displayId })
  positionOrb()
  if (dirWin && !dirWin.isDestroyed() && dirWin.isVisible()) positionDirectory()
})

ipcMain.handle('get-tiling-layouts', () => {
  try   { return JSON.parse(fs.readFileSync(TILING_FILE, 'utf8')) }
  catch { return { monitors: {} } }
})

ipcMain.handle('set-tiling-layout', (_, { monitorId, monitorData }) => {
  let layouts = { monitors: {} }
  try { layouts = JSON.parse(fs.readFileSync(TILING_FILE, 'utf8')) } catch {}
  if (!layouts.monitors) layouts.monitors = {}
  layouts.monitors[String(monitorId)] = monitorData
  try { fs.writeFileSync(TILING_FILE, JSON.stringify(layouts, null, 2)); return { success: true } }
  catch (e) { return { success: false, error: e.message } }
})

ipcMain.handle('get-displays', () => {
  const primary              = screen.getPrimaryDisplay()
  const { overlayDisplayId } = loadSettings()
  const pinnedId             = overlayDisplayId || primary.id
  return screen.getAllDisplays().map((d, i) => ({
    id:          d.id,
    index:       i + 1,
    label:       d.label || `Display ${i + 1}`,
    x:           d.bounds.x,
    y:           d.bounds.y,
    width:       d.bounds.width,
    height:      d.bounds.height,
    workX:       d.workArea.x,
    workY:       d.workArea.y,
    workWidth:   d.workArea.width,
    workHeight:  d.workArea.height,
    scaleFactor: d.scaleFactor,
    refreshRate: d.displayFrequency,
    rotation:    d.rotation,
    isPrimary:   d.id === primary.id,
    isPinned:    d.id === pinnedId,
  }))
})

function openAppManager() {
  if (appManagerWin && !appManagerWin.isDestroyed()) { appManagerWin.focus(); return }
  appManagerWin = new BrowserWindow({
    width: 860, height: 620, minWidth: 640, minHeight: 460,
    title: 'Eve — App Manager', backgroundColor: '#080e18', frame: true, resizable: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  })
  appManagerWin.setMenuBarVisibility(false)
  appManagerWin.loadFile(path.join(__dirname, 'src', 'app-manager', 'index.html'))
  appManagerWin.on('closed', () => { appManagerWin = null })
}

ipcMain.on('open-app-manager',  openAppManager)
ipcMain.on('close-app-manager', () => {
  if (appManagerWin && !appManagerWin.isDestroyed()) appManagerWin.close()
})

function openWindowManager() {
  if (windowManagerWin && !windowManagerWin.isDestroyed()) { windowManagerWin.focus(); return }
  windowManagerWin = new BrowserWindow({
    width: 860, height: 680, minWidth: 640, minHeight: 520,
    title: 'Eve — Window Manager', backgroundColor: '#080e18', frame: true, resizable: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  })
  windowManagerWin.setMenuBarVisibility(false)
  windowManagerWin.loadFile(path.join(__dirname, 'src', 'window-manager', 'index.html'))
  windowManagerWin.on('closed', () => { windowManagerWin = null })
}

ipcMain.on('open-window-manager',  openWindowManager)
ipcMain.on('close-window-manager', () => {
  if (windowManagerWin && !windowManagerWin.isDestroyed()) windowManagerWin.close()
})

function openVoiceSettings() {
  if (voiceSettingsWin && !voiceSettingsWin.isDestroyed()) { voiceSettingsWin.focus(); return }
  voiceSettingsWin = new BrowserWindow({
    width: 500, height: 540, minWidth: 420, minHeight: 460,
    title: 'Eve — Voice Settings', backgroundColor: '#080e18', frame: true, resizable: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  })
  voiceSettingsWin.setMenuBarVisibility(false)
  voiceSettingsWin.loadFile(path.join(__dirname, 'src', 'voice-settings', 'index.html'))
  voiceSettingsWin.on('closed', () => { voiceSettingsWin = null })
}

ipcMain.on('open-voice-settings',  openVoiceSettings)
ipcMain.on('close-voice-settings', () => {
  if (voiceSettingsWin && !voiceSettingsWin.isDestroyed()) voiceSettingsWin.close()
})

// ── Snap panel (voice: "snap window manager to top-left") ────────────────────

const _panelGetters = {
  directory:      () => dirWin,
  window_manager: () => windowManagerWin,
  app_manager:    () => appManagerWin,
  voice_settings: () => voiceSettingsWin,
}

const _panelOpeners = {
  directory:      () => showDirectory(),
  window_manager: () => openWindowManager(),
  app_manager:    () => openAppManager(),
  voice_settings: () => openVoiceSettings(),
}

ipcMain.on('snap-panel', (_, { panel, bounds }) => {
  const opener = _panelOpeners[panel]
  const getter = _panelGetters[panel]
  if (!opener || !getter) return

  // Make sure the panel exists and is visible
  const existing = getter()
  if (!existing || existing.isDestroyed() || !existing.isVisible()) opener()

  const place = () => {
    const win = getter()
    if (!win || win.isDestroyed()) return
    win.setBounds(bounds, true)
    win.show()
    if (panel === 'directory') {
      // dirWin is transparent/topmost — re-assert after move so it stays on top
      win.setAlwaysOnTop(true, 'screen-saver', 1)
      win.moveTop()
    }
  }
  // Wait one tick so any just-created BrowserWindow finishes its initial setBounds
  setTimeout(place, 50)
})

ipcMain.handle('get-voice-settings', () => {
  let s = {}
  try { s = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')) } catch {}
  return s.voice || { speed: 1.0, noise_scale: 0.667, noise_w: 0.8 }
})

ipcMain.handle('get-voice-presets', () => {
  let s = {}
  try { s = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')) } catch {}
  return s.voice_presets || {}
})

ipcMain.handle('save-voice-preset', (_, { name, params }) => {
  let s = {}
  try { s = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')) } catch {}
  if (!s.voice_presets) s.voice_presets = {}
  s.voice_presets[name] = params
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(s, null, 2))
  return s.voice_presets
})

ipcMain.handle('delete-voice-preset', (_, { name }) => {
  let s = {}
  try { s = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')) } catch {}
  if (s.voice_presets) delete s.voice_presets[name]
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(s, null, 2))
  return s.voice_presets || {}
})

function broadcastDisplayChange() {
  positionOrb()
  if (dirWin && !dirWin.isDestroyed() && dirWin.isVisible()) positionDirectory()
  if (windowManagerWin && !windowManagerWin.isDestroyed())
    windowManagerWin.webContents.send('displays-changed')
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  createOrbWin()
  createDirWin()   // pre-warm: content loads in background before first open
  createTray()
  screen.on('display-added',           broadcastDisplayChange)
  screen.on('display-removed',         broadcastDisplayChange)
  screen.on('display-metrics-changed', broadcastDisplayChange)
})

app.on('window-all-closed', () => {})       // app lives in tray
app.on('before-quit', () => { app.isQuitting = true })
