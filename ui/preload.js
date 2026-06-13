const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('eve', {
  // Overlay: tell main process to resize/reposition the window
  setSize: (compact) => ipcRenderer.send('set-size', { compact }),

  // Tool windows
  openAppManager:     () => ipcRenderer.send('open-app-manager'),
  closeAppManager:    () => ipcRenderer.send('close-app-manager'),
  openWindowManager:  () => ipcRenderer.send('open-window-manager'),
  closeWindowManager: () => ipcRenderer.send('close-window-manager'),

  // Monitor settings
  getDisplays:       ()          => ipcRenderer.invoke('get-displays'),
  setOverlayDisplay: (displayId) => ipcRenderer.send('set-overlay-display', { displayId }),

  // Display change notifications (Window Manager window subscribes to this)
  onDisplaysChanged: (cb) => ipcRenderer.on('displays-changed', cb),

  // Tiling layouts
  getTilingLayouts:  ()                       => ipcRenderer.invoke('get-tiling-layouts'),
  setTilingLayout:   (monitorId, monitorData) => ipcRenderer.invoke('set-tiling-layout', { monitorId, monitorData }),
})
