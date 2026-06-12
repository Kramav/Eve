const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('eve', {
  // Overlay: tell main process to resize/reposition the window
  setSize: (compact) => ipcRenderer.send('set-size', { compact }),

  // App Manager: open (or focus) the app manager window
  openAppManager: () => ipcRenderer.send('open-app-manager'),
})
