const { app, BrowserWindow, ipcMain, screen } = require('electron')
const fs   = require('fs')
const path = require('path')

const COMPACT  = { w: 96,  h: 96  }
const EXPANDED = { w: 380, h: 620 }

const SETTINGS_FILE = path.join(__dirname, '..', 'settings.json')
const TILING_FILE   = path.join(__dirname, '..', 'tiling_layouts.json')

let win              = null
let appManagerWin    = null
let windowManagerWin = null

// ── Persistent settings ────────────────────────────────────────────────────

function loadSettings() {
  try { return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')) } catch { return {} }
}

function saveSettings(patch) {
  const s = loadSettings()
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify({ ...s, ...patch }, null, 2))
}

// ── Monitor helpers ────────────────────────────────────────────────────────

function getOverlayDisplay() {
  const { overlayDisplayId } = loadSettings()
  const all = screen.getAllDisplays()
  if (overlayDisplayId) {
    const found = all.find(d => d.id === overlayDisplayId)
    if (found) return found
  }
  return screen.getPrimaryDisplay()
}

// Top-right anchor for a given display (absolute screen coordinates)
function anchorFor(display) {
  const { x, y, width } = display.bounds
  return { right: x + width, top: y }
}

// ── Overlay window ─────────────────────────────────────────────────────────

function createWindow() {
  const { right, top } = anchorFor(getOverlayDisplay())

  win = new BrowserWindow({
    width:       COMPACT.w,
    height:      COMPACT.h,
    x:           right - COMPACT.w - 10,
    y:           top   + 10,
    frame:       false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable:   false,
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })

  win.loadFile(path.join(__dirname, 'src', 'index.html'))
}

// Resize/reposition overlay when HUD is toggled.
// Always uses the SAVED overlay display preference — never dynamic window-center
// detection, which causes monitor drift on multi-monitor setups.
ipcMain.on('set-size', (_, { compact }) => {
  if (!win) return
  const { right, top } = anchorFor(getOverlayDisplay())
  if (compact) {
    win.setSize(COMPACT.w, COMPACT.h)
    win.setPosition(right - COMPACT.w - 10, top + 10)
  } else {
    win.setSize(EXPANDED.w, EXPANDED.h)
    win.setPosition(right - EXPANDED.w - 10, top + 10)
  }
})

// Move overlay to a specific display and persist the choice
ipcMain.on('set-overlay-display', (_, { displayId }) => {
  saveSettings({ overlayDisplayId: displayId })
  const display = screen.getAllDisplays().find(d => d.id === displayId) || screen.getPrimaryDisplay()
  const { right, top } = anchorFor(display)
  if (win) {
    win.setSize(COMPACT.w, COMPACT.h)
    win.setPosition(right - COMPACT.w - 10, top + 10)
  }
})

// ── Tiling layouts ─────────────────────────────────────────────────────────

ipcMain.handle('get-tiling-layouts', () => {
  try   { return JSON.parse(fs.readFileSync(TILING_FILE, 'utf8')) }
  catch { return { monitors: {} } }
})

ipcMain.handle('set-tiling-layout', (_, { monitorId, monitorData }) => {
  let layouts = { monitors: {} }
  try { layouts = JSON.parse(fs.readFileSync(TILING_FILE, 'utf8')) } catch {}
  if (!layouts.monitors) layouts.monitors = {}
  layouts.monitors[String(monitorId)] = monitorData
  try {
    fs.writeFileSync(TILING_FILE, JSON.stringify(layouts, null, 2))
    return { success: true }
  } catch (e) { return { success: false, error: e.message } }
})

// Return all displays with full metadata for UI consumers
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

// ── Display change events → notify Window Manager window ──────────────────

function broadcastDisplayChange() {
  if (windowManagerWin && !windowManagerWin.isDestroyed()) {
    windowManagerWin.webContents.send('displays-changed')
  }
}

// ── App Manager window ─────────────────────────────────────────────────────

function openAppManager() {
  if (appManagerWin && !appManagerWin.isDestroyed()) {
    appManagerWin.focus()
    return
  }

  appManagerWin = new BrowserWindow({
    width:           860,
    height:          620,
    minWidth:        640,
    minHeight:       460,
    title:           'Eve — App Manager',
    backgroundColor: '#080e18',
    frame:           true,
    resizable:       true,
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })

  appManagerWin.setMenuBarVisibility(false)
  appManagerWin.loadFile(path.join(__dirname, 'src', 'app-manager', 'index.html'))
  appManagerWin.on('closed', () => { appManagerWin = null })
}

ipcMain.on('open-app-manager',  openAppManager)
ipcMain.on('close-app-manager', () => {
  if (appManagerWin && !appManagerWin.isDestroyed()) appManagerWin.close()
})

// ── Window Manager window ──────────────────────────────────────────────────

function openWindowManager() {
  if (windowManagerWin && !windowManagerWin.isDestroyed()) {
    windowManagerWin.focus()
    return
  }

  windowManagerWin = new BrowserWindow({
    width:           860,
    height:          680,
    minWidth:        640,
    minHeight:       520,
    title:           'Eve — Window Manager',
    backgroundColor: '#080e18',
    frame:           true,
    resizable:       true,
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })

  windowManagerWin.setMenuBarVisibility(false)
  windowManagerWin.loadFile(path.join(__dirname, 'src', 'window-manager', 'index.html'))
  windowManagerWin.on('closed', () => { windowManagerWin = null })
}

ipcMain.on('open-window-manager',  openWindowManager)
ipcMain.on('close-window-manager', () => {
  if (windowManagerWin && !windowManagerWin.isDestroyed()) windowManagerWin.close()
})

// ── Lifecycle ──────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  createWindow()
  screen.on('display-added',           broadcastDisplayChange)
  screen.on('display-removed',         broadcastDisplayChange)
  screen.on('display-metrics-changed', broadcastDisplayChange)
})

app.on('window-all-closed', () => app.quit())
