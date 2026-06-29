const { app, BrowserWindow, ipcMain, dialog, screen, Tray, Menu, nativeImage, shell } = require('electron')
const fs   = require('fs')
const path = require('path')

const SETTINGS_FILE  = path.join(__dirname, '..', 'settings.json')
const TILING_FILE    = path.join(__dirname, '..', 'tiling_layouts.json')
const COMMANDS_FILE  = path.join(__dirname, '..', 'custom_commands.json')
const APPS_FILE      = path.join(__dirname, '..', 'apps.json')
const ALIASES_FILE   = path.join(__dirname, '..', 'aliases.json')

let orbWin            = null
let dirWin            = null
let appManagerWin     = null
let windowManagerWin  = null
let voiceSettingsWin  = null
let commandEditorWin  = null
let programsWin       = null
let memoryWin         = null
let remindersWin      = null
let integrationsWin   = null
let youtubeWin        = null
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

const ORB_SIZE = 96
const ORB_MARGIN = 10
const DIR_W = 700, DIR_H = 520
const DIR_GAP = 10  // gap between orb and directory

function getOrbCorner() {
  const { overlayCorner } = loadSettings()
  const valid = ['top-right', 'top-left', 'bottom-right', 'bottom-left']
  return valid.includes(overlayCorner) ? overlayCorner : 'top-right'
}

// Returns { orbX, orbY, dirX, dirY } anchored at the corner of the current
// HUD-monitor's full bounds (not work area — orb sits over taskbar too).
function computeCornerLayout() {
  const corner = getOrbCorner()
  const { x, y, width, height } = getOrbDisplay().bounds
  const [v, h] = corner.split('-')

  const orbX = h === 'right'
    ? x + width  - ORB_SIZE - ORB_MARGIN
    : x + ORB_MARGIN
  const orbY = v === 'bottom'
    ? y + height - ORB_SIZE - ORB_MARGIN
    : y + ORB_MARGIN

  const dirX = h === 'right'
    ? x + width  - DIR_W - ORB_MARGIN
    : x + ORB_MARGIN
  // For top corners directory sits BELOW the orb; for bottom corners ABOVE.
  const dirY = v === 'bottom'
    ? orbY - DIR_H - DIR_GAP
    : orbY + ORB_SIZE + DIR_GAP

  return { orbX, orbY, dirX, dirY }
}

function positionOrb() {
  if (!orbWin || orbWin.isDestroyed()) return
  const { orbX, orbY } = computeCornerLayout()
  orbWin.setPosition(orbX, orbY)
}

function positionDirectory() {
  if (!dirWin || dirWin.isDestroyed()) return
  const { dirX, dirY } = computeCornerLayout()
  dirWin.setPosition(dirX, dirY)
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
    skipTaskbar: true, resizable: false, show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  })
  // ponytail: a normal, coverable window — NOT always-on-top and NOT
  // visible-over-fullscreen (that's the orb's job). So tabbing away lets other
  // windows cover it, and a panel opened from a tile stacks in front.
  dirWin.loadFile(path.join(__dirname, 'src', 'directory', 'index.html'))
  dirWin._ready = false
  dirWin.once('ready-to-show', () => { dirWin._ready = true })
  dirWin.on('close', e => {
    if (!app.isQuitting) { e.preventDefault(); hideDirectory() }
  })
}

function showDirectory() {
  if (!dirWin || dirWin.isDestroyed()) createDirWin()
  dirWin._expanded = false
  _savedDirBounds  = null
  const { dirX, dirY } = computeCornerLayout()
  dirWin.setBounds({ x: dirX, y: dirY, width: DIR_W, height: DIR_H })
  const present = () => {
    dirWin.show()
    dirWin.moveTop()   // pop to front on open, but not a sticky top-most pin
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
  if (!dirWin || dirWin.isDestroyed() || !dirWin.isVisible()) {
    showDirectory()                 // closed → open
  } else if (dirWin.isFocused()) {
    hideDirectory()                 // open & front → close
  } else {
    // open but covered/backgrounded → raise it instead of closing (no reposition)
    dirWin.show(); dirWin.moveTop(); dirWin.focus()
  }
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
      const { dirX, dirY } = computeCornerLayout()
      dirWin.setBounds({ x: dirX, y: dirY, width: DIR_W, height: DIR_H })
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

// Augment a monitorData with screen-physical bounds for Python's Win32
// SetWindowPos. Electron saves work areas in DIPs, but in a per-monitor
// DPI-aware Windows process the desktop coordinate space is in physical
// pixels — for example DELL at 125% spans 0..2560 physically (0..2048
// in DIPs), so a portrait monitor that Electron reports starting at DIP
// x=2048 actually starts at physical x=2560. `dipToScreenRect` does the
// conversion accounting for every preceding monitor's scale.
function augmentWithPhysBounds(monitorData, displayId) {
  try {
    const d = screen.getAllDisplays().find(x => String(x.id) === String(displayId))
    if (!d) return monitorData
    const dipRect  = { x: d.workArea.x, y: d.workArea.y, width: d.workArea.width, height: d.workArea.height }
    const physRect = screen.dipToScreenRect(null, dipRect)
    monitorData.physX      = physRect.x
    monitorData.physY      = physRect.y
    monitorData.physWidth  = physRect.width
    monitorData.physHeight = physRect.height
  } catch {}
  return monitorData
}

ipcMain.handle('set-tiling-layout', (_, { monitorId, monitorData }) => {
  augmentWithPhysBounds(monitorData, monitorId)
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
    title: 'Eve — App Manager', backgroundColor: '#080e18', frame: false, resizable: true,
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
    title: 'Eve — Window Manager', backgroundColor: '#080e18', frame: false, resizable: true,
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
    title: 'Eve — Voice Settings', backgroundColor: '#080e18', frame: false, resizable: false,
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

// ── Command Editor (replaces tkinter editor.py) ──────────────────────────────

// Keep this in sync with BUILTIN_MAP in core/dispatcher.py
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

function openCommandEditor() {
  if (commandEditorWin && !commandEditorWin.isDestroyed()) {
    commandEditorWin.focus(); return
  }
  commandEditorWin = new BrowserWindow({
    width: 920, height: 680, minWidth: 720, minHeight: 520,
    title: 'Eve — Command Editor',
    backgroundColor: '#080e18',
    frame: false, resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })
  commandEditorWin.setMenuBarVisibility(false)
  commandEditorWin.loadFile(path.join(__dirname, 'src', 'command-editor', 'index.html'))
  commandEditorWin.on('closed', () => { commandEditorWin = null })
}

ipcMain.on('open-command-editor',  openCommandEditor)
ipcMain.on('close-command-editor', () => {
  if (commandEditorWin && !commandEditorWin.isDestroyed()) commandEditorWin.close()
})

// ── Running Programs panel ───────────────────────────────────────────────────

function openPrograms() {
  if (programsWin && !programsWin.isDestroyed()) { programsWin.focus(); return }
  programsWin = new BrowserWindow({
    width: 640, height: 600, minWidth: 480, minHeight: 380,
    title: 'Eve — Running Programs',
    backgroundColor: '#080e18',
    frame: false, resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })
  programsWin.setMenuBarVisibility(false)
  programsWin.loadFile(path.join(__dirname, 'src', 'programs', 'index.html'))
  programsWin.on('closed', () => { programsWin = null })
}

ipcMain.on('open-programs',  openPrograms)
ipcMain.on('close-programs', () => {
  if (programsWin && !programsWin.isDestroyed()) programsWin.close()
})

// ── Memory panel ─────────────────────────────────────────────────────────────

function openMemory() {
  if (memoryWin && !memoryWin.isDestroyed()) { memoryWin.focus(); return }
  memoryWin = new BrowserWindow({
    width: 560, height: 520, minWidth: 460, minHeight: 360,
    title: 'Eve — Memory',
    backgroundColor: '#080e18',
    frame: false, resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })
  memoryWin.setMenuBarVisibility(false)
  memoryWin.loadFile(path.join(__dirname, 'src', 'memory', 'index.html'))
  memoryWin.on('closed', () => { memoryWin = null })
}

ipcMain.on('open-memory',  openMemory)
ipcMain.on('close-memory', () => {
  if (memoryWin && !memoryWin.isDestroyed()) memoryWin.close()
})

// ── Reminders panel ───────────────────────────────────────────────────────────

function openReminders() {
  if (remindersWin && !remindersWin.isDestroyed()) { remindersWin.focus(); return }
  remindersWin = new BrowserWindow({
    width: 600, height: 520, minWidth: 500, minHeight: 360,
    title: 'Eve — Reminders',
    backgroundColor: '#080e18',
    frame: false, resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })
  remindersWin.setMenuBarVisibility(false)
  remindersWin.loadFile(path.join(__dirname, 'src', 'reminders', 'index.html'))
  remindersWin.on('closed', () => { remindersWin = null })
}

ipcMain.on('open-reminders',  openReminders)
ipcMain.on('close-reminders', () => {
  if (remindersWin && !remindersWin.isDestroyed()) remindersWin.close()
})

// Open a search result (or any URL) in the user's default browser.
ipcMain.on('open-external', (_, url) => {
  if (typeof url === 'string' && /^https?:\/\//i.test(url)) shell.openExternal(url)
})

// Generic window controls for the frameless panel headers (titlebar.js).
ipcMain.on('close-self', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender)
  if (w && !w.isDestroyed()) w.close()
})
ipcMain.on('maximize-self', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender)
  if (!w || w.isDestroyed()) return
  w.isMaximized() ? w.unmaximize() : w.maximize()
})

// ── YouTube HUD browser ─────────────────────────────────────────────────────
// A real Chromium window showing youtube.com, floated over (borderless) games
// with the orb's no-focus topmost recipe. Always presented with showInactive()
// so voice navigation never steals focus from the game. Logged-in session
// persists via the dedicated partition.

const YT_W = 480, YT_H = 320, YT_MARGIN = 12

function youtubeCornerPos() {
  const { x, y, width, height } = getOrbDisplay().bounds
  return { x: x + width - YT_W - YT_MARGIN, y: y + height - YT_H - YT_MARGIN }
}

function createYoutubeWin() {
  const pos = youtubeCornerPos()
  youtubeWin = new BrowserWindow({
    width: YT_W, height: YT_H, x: pos.x, y: pos.y,
    frame: false, skipTaskbar: true, resizable: true, show: false,
    backgroundColor: '#0f0f0f',
    webPreferences: { partition: 'persist:youtube', contextIsolation: true },
  })
  youtubeWin.setAlwaysOnTop(true, 'screen-saver', 1)
  youtubeWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  youtubeWin.loadURL('https://www.youtube.com')
  youtubeWin.on('closed', () => { youtubeWin = null })
}

// Show without activating, then re-assert topmost so it surfaces over the game.
function ytSurface() {
  if (!youtubeWin || youtubeWin.isDestroyed()) return
  youtubeWin.showInactive()
  youtubeWin.setAlwaysOnTop(true, 'screen-saver', 1)
}

// Run JS in the page, then re-surface. Swallows errors (page may be mid-load).
function ytRun(js) {
  if (!youtubeWin || youtubeWin.isDestroyed()) return
  youtubeWin.webContents.executeJavaScript(js, true).catch(() => {})
  ytSurface()
}

function openYoutube() {
  if (!youtubeWin || youtubeWin.isDestroyed()) createYoutubeWin()
  ytSurface()
}

ipcMain.on('open-youtube', openYoutube)

ipcMain.on('youtube-scroll', (_, dir) => {
  const js = dir === 'up'   ? 'window.scrollBy(0,-800)'
           : dir === 'top'  ? 'window.scrollTo(0,0)'
           :                  'window.scrollBy(0,800)'
  ytRun(js)
})

// Badge the in-viewport video tiles 1..N and stash their click targets so
// "open video N" can act on them.
// ponytail: selectors track YouTube's current DOM; retarget here if layout shifts.
ipcMain.on('youtube-number', () => ytRun(`(() => {
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
})()`))

ipcMain.on('youtube-open', (_, n) => {
  const i = Math.max(1, parseInt(n, 10) || 1) - 1
  ytRun(`(window.__eveTiles && window.__eveTiles[${i}]) && window.__eveTiles[${i}].click()`)
})

ipcMain.on('youtube-search', (_, query) => {
  if (!youtubeWin || youtubeWin.isDestroyed()) createYoutubeWin()
  const url = 'https://www.youtube.com/results?search_query=' + encodeURIComponent(query || '')
  youtubeWin.loadURL(url)
  ytSurface()
})

ipcMain.on('youtube-playpause', () => ytRun(
  `(() => { const v = document.querySelector('video'); if (v) v.paused ? v.play() : v.pause(); })()`
))

ipcMain.on('close-youtube', () => {
  if (youtubeWin && !youtubeWin.isDestroyed()) youtubeWin.close()
})

// ── API Keys / Integrations panel ──────────────────────────────────────────────

function openIntegrations(target) {
  // `target` (optional) deep-links to a specific integration card via #hash.
  if (integrationsWin && !integrationsWin.isDestroyed()) {
    integrationsWin.focus()
    if (target) integrationsWin.webContents.send('scroll-to', target)
    return
  }
  integrationsWin = new BrowserWindow({
    width: 520, height: 380, minWidth: 460, minHeight: 320,
    title: 'Eve — Integrations',
    backgroundColor: '#080e18',
    frame: false, resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  })
  integrationsWin.setMenuBarVisibility(false)
  integrationsWin.loadFile(path.join(__dirname, 'src', 'integrations', 'index.html'),
                           target ? { hash: target } : undefined)
  integrationsWin.on('closed', () => { integrationsWin = null })
}

ipcMain.on('open-integrations',  (_e, target) => openIntegrations(target))
ipcMain.on('close-integrations', () => {
  if (integrationsWin && !integrationsWin.isDestroyed()) integrationsWin.close()
})

// Read/write helpers for the three editor files
const _readJsonList = (p) => {
  try {
    const v = JSON.parse(fs.readFileSync(p, 'utf8'))
    return Array.isArray(v) ? v : []
  } catch { return [] }
}
const _writeJsonList = (p, data) => {
  if (!Array.isArray(data)) return { ok: false, error: 'not an array' }
  try {
    fs.writeFileSync(p, JSON.stringify(data, null, 2))
    // Notify any open window so it can refresh
    BrowserWindow.getAllWindows().forEach(w => {
      if (!w.isDestroyed()) w.webContents.send('commands-changed', { file: path.basename(p) })
    })
    return { ok: true }
  } catch (e) { return { ok: false, error: e.message } }
}

ipcMain.handle('command-editor:get-commands', () => _readJsonList(COMMANDS_FILE))
ipcMain.handle('command-editor:set-commands', (_, data) => _writeJsonList(COMMANDS_FILE, data))
ipcMain.handle('command-editor:get-apps',     () => _readJsonList(APPS_FILE))
ipcMain.handle('command-editor:set-apps',     (_, data) => _writeJsonList(APPS_FILE, data))
ipcMain.handle('command-editor:get-aliases',  () => _readJsonList(ALIASES_FILE))
ipcMain.handle('command-editor:set-aliases',  (_, data) => _writeJsonList(ALIASES_FILE, data))
ipcMain.handle('command-editor:get-builtins', () => BUILTIN_REFERENCE)

ipcMain.handle('command-editor:browse-exe', async () => {
  const win = commandEditorWin && !commandEditorWin.isDestroyed() ? commandEditorWin : null
  const r = await dialog.showOpenDialog(win, {
    title: 'Pick executable',
    filters: [
      { name: 'Executables', extensions: ['exe', 'lnk', 'bat', 'cmd'] },
      { name: 'All files',   extensions: ['*'] },
    ],
    properties: ['openFile'],
  })
  return r.canceled ? null : r.filePaths[0]
})

// Raw JSON: get a file by short name
ipcMain.handle('command-editor:get-raw', (_, fileKey) => {
  const map = { commands: COMMANDS_FILE, apps: APPS_FILE, aliases: ALIASES_FILE }
  const p = map[fileKey]; if (!p) return ''
  try { return fs.readFileSync(p, 'utf8') } catch { return '[]' }
})
ipcMain.handle('command-editor:set-raw', (_, { fileKey, text }) => {
  const map = { commands: COMMANDS_FILE, apps: APPS_FILE, aliases: ALIASES_FILE }
  const p = map[fileKey]; if (!p) return { ok: false, error: 'unknown file' }
  // Validate JSON before writing
  let parsed
  try { parsed = JSON.parse(text) }
  catch (e) { return { ok: false, error: 'Invalid JSON: ' + e.message } }
  if (!Array.isArray(parsed)) return { ok: false, error: 'Top level must be an array' }
  return _writeJsonList(p, parsed)
})

// ── Identify Monitors (voice: "identify monitors") ───────────────────────────

let _monitorIdWins = []

function identifyMonitors(durationMs = 3500) {
  // Close any prior identify session immediately so re-triggers don't stack
  for (const w of _monitorIdWins) {
    if (w && !w.isDestroyed()) w.close()
  }
  _monitorIdWins = []

  const primary  = screen.getPrimaryDisplay()
  const displays = screen.getAllDisplays()
  const CARD     = 340
  const MARGIN   = 20
  const HTML     = path.join(__dirname, 'src', 'monitor-id', 'index.html')

  displays.forEach((d, i) => {
    // Bottom-left of workArea (excludes taskbar)
    const wa = d.workArea
    const win = new BrowserWindow({
      width: CARD, height: CARD,
      x: Math.round(wa.x + MARGIN),
      y: Math.round(wa.y + wa.height - CARD - MARGIN),
      frame: false, transparent: true,
      alwaysOnTop: true, skipTaskbar: true, resizable: false,
      focusable: false, hasShadow: false,
      webPreferences: { contextIsolation: true },
    })
    win.setAlwaysOnTop(true, 'screen-saver', 1)
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })

    const hash = new URLSearchParams({
      index:   String(i + 1),
      label:   d.label || `Display ${i + 1}`,
      primary: d.id === primary.id ? '1' : '0',
      meta:    `${d.bounds.width}x${d.bounds.height}`,
    }).toString()
    win.loadURL(`file://${HTML.replace(/\\/g, '/')}#${hash}`)

    win.on('closed', () => {
      _monitorIdWins = _monitorIdWins.filter(w => w !== win)
    })
    _monitorIdWins.push(win)
  })

  setTimeout(() => {
    for (const w of _monitorIdWins) {
      if (w && !w.isDestroyed()) w.close()
    }
    _monitorIdWins = []
  }, durationMs)
}

ipcMain.on('identify-monitors', () => identifyMonitors())

// ── Window-Manager voice control ─────────────────────────────────────────────

// Mirror of the renderer's PRESETS so voice changes can be applied without
// the WM panel being open. Keep in sync with ui/src/window-manager/app.js
// (or factor out into a shared file later).
const WM_PRESETS = {
  'full': {
    label: 'Full',
    zones: [{ name: 'full', x_pct: 0, y_pct: 0, w_pct: 1, h_pct: 1 }],
  },
  'top-bottom': {
    label: 'Top / Bot',
    zones: [
      { name: 'top',    x_pct: 0, y_pct: 0,   w_pct: 1, h_pct: 0.5 },
      { name: 'bottom', x_pct: 0, y_pct: 0.5, w_pct: 1, h_pct: 0.5 },
    ],
  },
  'left-right': {
    label: 'Left / Right',
    zones: [
      { name: 'left',  x_pct: 0,   y_pct: 0, w_pct: 0.5, h_pct: 1 },
      { name: 'right', x_pct: 0.5, y_pct: 0, w_pct: 0.5, h_pct: 1 },
    ],
  },
  'main-right': {
    label: 'Main + Right',
    zones: [
      { name: 'main',  x_pct: 0,    y_pct: 0, w_pct: 0.67, h_pct: 1 },
      { name: 'right', x_pct: 0.67, y_pct: 0, w_pct: 0.33, h_pct: 1 },
    ],
  },
  'main-stack': {
    label: '1 + 2 Stack',
    zones: [
      { name: 'main',         x_pct: 0,   y_pct: 0,   w_pct: 0.5, h_pct: 1   },
      { name: 'top-right',    x_pct: 0.5, y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-right', x_pct: 0.5, y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
    ],
  },
  'grid-4': {
    label: 'Grid 2x2',
    zones: [
      { name: 'top-left',     x_pct: 0,   y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'top-right',    x_pct: 0.5, y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-left',  x_pct: 0,   y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-right', x_pct: 0.5, y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
    ],
  },
}

function resolveDisplay(ref) {
  // ref can be: 'primary', a 1-based index string ('1'), or 'hud'.
  const all = screen.getAllDisplays()
  const r   = String(ref || '').toLowerCase().trim()
  if (r === 'primary')  return screen.getPrimaryDisplay()
  if (r === 'hud') {
    const { overlayDisplayId } = loadSettings()
    if (overlayDisplayId) {
      const f = all.find(d => d.id === overlayDisplayId)
      if (f) return f
    }
    return screen.getPrimaryDisplay()
  }
  const n = parseInt(r, 10)
  if (!isNaN(n) && n >= 1 && n <= all.length) return all[n - 1]
  return null
}

// ── Identify Zones — overlay saved tiling layouts on each monitor ────────────

let _zoneIdWins = []

function identifyZones(durationMs = 6000) {
  // Close any prior session immediately
  for (const w of _zoneIdWins) if (w && !w.isDestroyed()) w.close()
  _zoneIdWins = []

  let layouts = {}
  try { layouts = JSON.parse(fs.readFileSync(TILING_FILE, 'utf8')) } catch {}
  const monitors = (layouts && layouts.monitors) || {}

  const displays = screen.getAllDisplays()
  const HTML     = path.join(__dirname, 'src', 'zone-id', 'index.html')

  displays.forEach((d, i) => {
    const saved = monitors[String(d.id)]
    if (!saved || !saved.zones || !saved.zones.length) return
    const wa = d.workArea
    const win = new BrowserWindow({
      width: wa.width, height: wa.height, x: wa.x, y: wa.y,
      frame: false, transparent: true,
      alwaysOnTop: true, skipTaskbar: true, resizable: false,
      // focusable: true so click events route through to the renderer
      focusable: true, hasShadow: false,
      webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
    })
    win.setAlwaysOnTop(true, 'screen-saver', 1)
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })

    const hash = new URLSearchParams({
      zones:        JSON.stringify(saved.zones),
      layout:       saved.layout || '',
      monitorIndex: String(i + 1),
      monitorLabel: d.label || `Display ${i + 1}`,
    }).toString()
    win.loadURL(`file://${HTML.replace(/\\/g, '/')}#${hash}`)

    win.on('closed', () => { _zoneIdWins = _zoneIdWins.filter(w => w !== win) })
    _zoneIdWins.push(win)
  })

  setTimeout(() => {
    for (const w of _zoneIdWins) if (w && !w.isDestroyed()) w.close()
    _zoneIdWins = []
  }, durationMs)
}

ipcMain.on('identify-zones',       () => identifyZones())
ipcMain.on('dismiss-zone-overlay', (e) => {
  const win = BrowserWindow.fromWebContents(e.sender)
  if (win && !win.isDestroyed()) win.close()
})

// ── Identify Windows — numbered label tags on each open window ───────────────

let _windowIdWins = []

function identifyWindows(payload, durationMs = 6000) {
  for (const w of _windowIdWins) if (w && !w.isDestroyed()) w.close()
  _windowIdWins = []

  const list = (payload && payload.windows) || []
  if (!list.length) return
  const HTML = path.join(__dirname, 'src', 'window-id', 'index.html')

  for (const item of list) {
    const TAG_W = 180
    const TAG_H = 36
    // Anchor at the window's top-left, but keep it clamped on-screen.
    const x = Math.round(item.x + 6)
    const y = Math.round(item.y + 6)
    const win = new BrowserWindow({
      width: TAG_W, height: TAG_H, x, y,
      frame: false, transparent: true,
      alwaysOnTop: true, skipTaskbar: true, resizable: false,
      focusable: true, hasShadow: false,
      webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
    })
    win.setAlwaysOnTop(true, 'screen-saver', 1)
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
    const hash = new URLSearchParams({
      index: String(item.index),
      label: String(item.label || ''),
    }).toString()
    win.loadURL(`file://${HTML.replace(/\\/g, '/')}#${hash}`)
    win.on('closed', () => { _windowIdWins = _windowIdWins.filter(w => w !== win) })
    _windowIdWins.push(win)
  }

  setTimeout(() => {
    for (const w of _windowIdWins) if (w && !w.isDestroyed()) w.close()
    _windowIdWins = []
  }, durationMs)
}

ipcMain.on('identify-windows',        (_, payload) => identifyWindows(payload))
ipcMain.on('dismiss-window-overlay',  (e) => {
  const win = BrowserWindow.fromWebContents(e.sender)
  if (win && !win.isDestroyed()) win.close()
})

// ── Voice WM mutation: apply preset + move HUD ───────────────────────────────

function notifyLayoutsChanged() {
  if (windowManagerWin && !windowManagerWin.isDestroyed()) {
    windowManagerWin.webContents.send('layouts-changed')
  }
}

ipcMain.on('wm-apply-preset', (_, { monitorRef, presetKey }) => {
  const d      = resolveDisplay(monitorRef)
  const preset = WM_PRESETS[presetKey]
  if (!d || !preset) return

  const monitorData = {
    label:       d.label || `Display ${d.id}`,
    workX:       d.workArea.x,
    workY:       d.workArea.y,
    workWidth:   d.workArea.width,
    workHeight:  d.workArea.height,
    scaleFactor: d.scaleFactor || 1.0,
    layout:      presetKey,
    zones:       preset.zones,
  }
  augmentWithPhysBounds(monitorData, d.id)

  let layouts = { monitors: {} }
  try { layouts = JSON.parse(fs.readFileSync(TILING_FILE, 'utf8')) } catch {}
  if (!layouts.monitors) layouts.monitors = {}
  layouts.monitors[String(d.id)] = monitorData

  try {
    fs.writeFileSync(TILING_FILE, JSON.stringify(layouts, null, 2))
    notifyLayoutsChanged()
  } catch (e) {
    console.error('wm-apply-preset write failed:', e)
  }
})

ipcMain.on('wm-move-hud', (_, { monitorRef }) => {
  const d = resolveDisplay(monitorRef)
  if (!d) return
  saveSettings({ overlayDisplayId: d.id })
  positionOrb()
  if (dirWin && !dirWin.isDestroyed() && dirWin.isVisible()) positionDirectory()
  // If WM is open, refresh so the HUD pin badge updates
  if (windowManagerWin && !windowManagerWin.isDestroyed()) {
    windowManagerWin.webContents.send('displays-changed')
  }
})

ipcMain.on('wm-set-orb-corner', (_, { corner }) => {
  const valid = ['top-right', 'top-left', 'bottom-right', 'bottom-left']
  if (!valid.includes(corner)) return
  saveSettings({ overlayCorner: corner })
  positionOrb()
  if (dirWin && !dirWin.isDestroyed() && dirWin.isVisible()) positionDirectory()
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
    if (panel === 'directory') win.moveTop()
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
