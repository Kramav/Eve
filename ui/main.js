const { app, BrowserWindow, ipcMain, screen } = require('electron')
const path = require('path')

const COMPACT  = { w: 96,  h: 96  }
const EXPANDED = { w: 380, h: 620 }

let win = null

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

// Renderer asks to resize when HUD is toggled
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

app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())
