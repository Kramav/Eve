const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('eve', {
  showDirectory:       () => ipcRenderer.send('show-directory'),
  hideDirectory:       () => ipcRenderer.send('hide-directory'),
  toggleDirectory:     () => ipcRenderer.send('toggle-directory'),
  toggleDirectorySize: () => ipcRenderer.send('toggle-directory-size'),

  openAppManager:     () => ipcRenderer.send('open-app-manager'),
  closeAppManager:    () => ipcRenderer.send('close-app-manager'),
  openWindowManager:  () => ipcRenderer.send('open-window-manager'),
  closeWindowManager: () => ipcRenderer.send('close-window-manager'),

  getDisplays:       ()          => ipcRenderer.invoke('get-displays'),
  setOverlayDisplay: (displayId) => ipcRenderer.send('set-overlay-display', { displayId }),

  onDisplaysChanged:      (cb) => ipcRenderer.on('displays-changed',       cb),
  onDirectorySizeChanged: (cb) => ipcRenderer.on('directory-size-changed', cb),

  getTilingLayouts:  ()                       => ipcRenderer.invoke('get-tiling-layouts'),
  setTilingLayout:   (monitorId, monitorData) => ipcRenderer.invoke('set-tiling-layout', { monitorId, monitorData }),
})
