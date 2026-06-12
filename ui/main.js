const { app, BrowserWindow, ipcMain, screen } = require('electron')
const path = require('path')

const COMPACT  = { w: 96,  h: 96  }
const EXPANDED = { w: 380, h: 620 }

let win           = null
let appManagerWin = null

// ── Overlay window ─────────────────────────────────────────────────────────
function createWindow() {
  const { width: sw } = screen.getPrimaryDisplay().bounds

  win = new BrowserWindow({
    width:       COMPACT.w,
    height:      COMPACT.h,
    x:           sw - COMPACT.w - 10,
    y:           10,
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

// Resize/reposition overlay when HUD is toggled
ipcMain.on('set-size', (_, { compact }) => {
  if (!win) return
  const { width: sw } = screen.getPrimaryDisplay().bounds
  if (compact) {
    win.setSize(COMPACT.w, COMPACT.h)
    win.setPosition(sw - COMPACT.w - 10, 10)
  } else {
    win.setSize(EXPANDED.w, EXPANDED.h)
    win.setPosition(sw - EXPANDED.w - 10, 10)
  }
})

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

ipcMain.on('open-app-manager', openAppManager)

// ── Lifecycle ──────────────────────────────────────────────────────────────
app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())
