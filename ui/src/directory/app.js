// ── Module registry — add entries here to register new modules ───────────────
const MODULES = [
  { id: 'app-manager',    label: 'App Manager',    icon: '⬡', action: () => window.eve.openAppManager()    },
  { id: 'window-manager', label: 'Window Manager', icon: '⬢', action: () => window.eve.openWindowManager() },
  { id: 'commands',       label: 'Command Editor',       icon: '⌨', action: () => send('open_command_editor')    },
  { id: 'voice-settings', label: 'Voice Settings',          icon: '◈', action: () => window.eve.openVoiceSettings() },
]

function renderModules() {
  const grid = document.getElementById('module-grid')
  for (const mod of MODULES) {
    const tile  = document.createElement('button')
    tile.className = 'module-tile'
    const icon  = document.createElement('div'); icon.className  = 'module-icon';  icon.textContent  = mod.icon
    const label = document.createElement('div'); label.className = 'module-label'; label.textContent = mod.label
    tile.append(icon, label)
    tile.addEventListener('click', mod.action)
    grid.appendChild(tile)
  }
}
renderModules()

// ── WebSocket ────────────────────────────────────────────────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onopen  = () => { setConnected(true);  send('directory_opened') }
  ws.onclose = () => { setConnected(false); setTimeout(connect, 500) }
  ws.onerror = () => {}
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'state') applyState(msg)
    } catch (_) {}
  }
}

function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...data }))
}

connect()

// ── Connection indicator ─────────────────────────────────────────────────────
function setConnected(ok) {
  document.getElementById('conn-dot').className    = `conn-dot ${ok ? 'connected' : 'disconnected'}`
  document.getElementById('conn-label').textContent = ok ? 'Connected' : 'Reconnecting…'
}

// ── Clock ────────────────────────────────────────────────────────────────────
;(function tick() {
  const n = new Date(), p = v => String(v).padStart(2, '0')
  const el = document.getElementById('clock')
  if (el) el.textContent = `${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`
  setTimeout(tick, 1000)
})()

// ── State ────────────────────────────────────────────────────────────────────
const KIND = { heard: 'You', action: 'Eve', error: 'Error', system: 'Sys' }
let entryCount = 0, prev = {}

function applyState(s) {
  const mode    = s.mode || 'idle'
  const enabled = s.listener_enabled !== false   // default true
  const cls     = []
  if (!enabled)              cls.push('offline')
  if (mode === 'listening')  cls.push('listening')
  if (mode === 'processing') cls.push('processing')
  if (s.active_listening)    cls.push('always-on')
  document.body.className = cls.join(' ')

  if (mode !== prev.mode || s.status_text !== prev.status_text || enabled !== prev.enabled) {
    const labels   = { idle: 'Online', listening: 'Listening', processing: 'Thinking', playing: 'Playing' }
    const dotMode  = enabled ? mode : 'offline'
    const labelTxt = enabled ? (s.status_text || labels[mode] || 'Online') : 'Offline'
    document.getElementById('state-dot').className     = `state-dot ${dotMode}`
    document.getElementById('state-label').textContent = labelTxt
    const pill = document.getElementById('state-pill')
    if (pill) pill.title = enabled ? 'Click to disable listening' : 'Click to enable listening'
  }

  const st = document.getElementById('status-text')
  const tr = document.getElementById('transcript-text')
  if (s.status_text !== prev.status_text) {
    st.textContent  = s.status_text || ''
    st.style.display = s.status_text ? '' : 'none'
  }
  if (s.main_text !== prev.main_text) {
    tr.textContent  = s.main_text || ''
    tr.style.display = s.main_text ? '' : 'none'
  }

  for (const e of (s.log_entries || [])) appendEntry(e)

  const listKey = JSON.stringify(s.list_items)
  if (listKey !== prev.listKey) {
    const rl = document.getElementById('result-list')
    if (s.list_items && s.list_items.length) {
      document.getElementById('result-hdr').textContent = s.list_status || 'Results'
      const items = document.getElementById('result-items')
      items.innerHTML = ''
      s.list_items.forEach((item, i) => {
        const d   = document.createElement('div');  d.className   = 'result-item'
        const num = document.createElement('span'); num.className = 'result-num'; num.textContent = i + 1
        const txt = document.createElement('span'); txt.textContent = item
        d.append(num, txt); items.appendChild(d)
      })
      rl.style.display = ''
    } else {
      rl.style.display = 'none'
    }
    prev.listKey = listKey
  }

  prev = { mode: s.mode, status_text: s.status_text, main_text: s.main_text, listKey: prev.listKey, enabled: enabled }
}

function appendEntry(e) {
  const feed  = document.getElementById('feed')
  const empty = document.getElementById('feed-empty')
  if (empty) empty.style.display = 'none'
  if (entryCount >= 200) {
    const first = feed.querySelector('.entry')
    if (first) { feed.removeChild(first); entryCount-- }
  }
  const div  = document.createElement('div'); div.className  = `entry ${e.kind}`
  const meta = document.createElement('div'); meta.className = 'e-meta'
  const tag  = document.createElement('span'); tag.className = 'e-tag'; tag.textContent = KIND[e.kind] || e.kind
  const ts   = document.createElement('span'); ts.className  = 'e-ts';  ts.textContent  = e.ts
  const txt  = document.createElement('div'); txt.className  = 'e-text'; txt.textContent = e.text
  meta.append(tag, ts); div.append(meta, txt); feed.appendChild(div); entryCount++
  feed.scrollTop = feed.scrollHeight
}

// ── Buttons ──────────────────────────────────────────────────────────────────
document.getElementById('clr-btn').addEventListener('click', () => {
  document.getElementById('feed').querySelectorAll('.entry').forEach(el => el.remove())
  entryCount = 0
  const empty = document.getElementById('feed-empty')
  if (empty) empty.style.display = ''
})

document.getElementById('expand-btn').addEventListener('click', () => window.eve.toggleDirectorySize())

if (window.eve.onDirectorySizeChanged) {
  const _expandBtn = document.getElementById('expand-btn')
  window.eve.onDirectorySizeChanged((_, { expanded }) => {
    _expandBtn.textContent = expanded ? '❐' : '⛶'
    _expandBtn.title       = expanded ? 'Restore window' : 'Toggle fullscreen'
  })
}

document.getElementById('close-btn').addEventListener('click', () => {
  send('directory_closed')
  window.eve.hideDirectory()
})

document.getElementById('state-pill').addEventListener('click', () => {
  send('toggle_listener')
})
