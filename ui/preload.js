const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('eve', {
  // Tell main process to resize/reposition the window
  setSize: (compact) => ipcRenderer.send('set-size', { compact }),
})
