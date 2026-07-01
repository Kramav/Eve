// ── Module registry — add entries here to register new modules ───────────────
const MODULES = [
  { id: 'app-manager',    label: 'App Manager',       icon: '⬡', action: () => window.eve.openAppManager()    },
  { id: 'window-manager', label: 'Window Manager',    icon: '⬢', action: () => window.eve.openWindowManager() },
  { id: 'programs',       label: 'Running Programs',  icon: '◉', action: () => window.eve.openPrograms()       },
  { id: 'memory',         label: 'Memory',            icon: '✦', action: () => window.eve.openMemory()         },
  { id: 'reminders',      label: 'Reminders',         icon: '⏰', action: () => window.eve.openReminders()      },
  { id: 'integrations',   label: 'API Keys',          icon: '🔑', action: () => window.eve.openIntegrations()   },
  { id: 'commands',       label: 'Command Editor',    icon: '⌨', action: () => send('open_command_editor')    },
  { id: 'voice-settings', label: 'Voice Settings',    icon: '◈', action: () => window.eve.openVoiceSettings() },
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
  // The directory is pre-warmed and hidden (not destroyed), so skip the DOM
  // write while it's not visible — no point re-rendering a clock nobody sees.
  if (document.visibilityState !== 'hidden') {
    const n = new Date(), p = v => String(v).padStart(2, '0')
    const el = document.getElementById('clock')
    if (el) el.textContent = `${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`
  }
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

  const listKey = JSON.stringify([s.list_items, s.list_links])
  if (listKey !== prev.listKey) {
    const rl = document.getElementById('result-list')
    if (s.list_items && s.list_items.length) {
      document.getElementById('result-hdr').textContent = s.list_status || 'Results'
      const items = document.getElementById('result-items')
      const links = s.list_links || []
      items.innerHTML = ''
      s.list_items.forEach((item, i) => {
        const d   = document.createElement('div');  d.className   = 'result-item'
        const num = document.createElement('span'); num.className = 'result-num'; num.textContent = i + 1
        const txt = document.createElement('span'); txt.textContent = item
        d.append(num, txt)
        const url = links[i]
        if (url) {
          d.classList.add('clickable')
          d.title = url
          d.addEventListener('click', () => window.eve.openExternal(url))
        }
        items.appendChild(d)
      })
      rl.style.display = ''
    } else {
      rl.style.display = 'none'
    }
    prev.listKey = listKey
  }

  if (s.features) renderFeatures(s.features, s.feature_labels || {}, s.feature_status || {}, s.feature_reasons || {}, s.feature_alpha || [])

  prev = { mode: s.mode, status_text: s.status_text, main_text: s.main_text, listKey: prev.listKey, enabled: enabled }
}

// ── Feature toggles ──────────────────────────────────────────────────────────
let _featureState   = {}
let _featureStatus  = {}
let _featureReasons = {}

// Features whose setup lives in the Integrations panel. The value is the card to
// deep-link to (Set up ↗ opens Integrations scrolled + highlighting that card).
const FEATURE_SETUP = { visual_nav: 'uiautomation' }

function renderFeatures(features, labels, status, reasons, alpha) {
  const alphaSet  = new Set(alpha || [])
  const keys      = Object.keys(features)
  const stable    = keys.filter(k => !alphaSet.has(k))
  const alphaKeys = keys.filter(k =>  alphaSet.has(k))

  _renderFeatureGroup(document.getElementById('feature-list'),       stable,    features, labels, status || {}, reasons || {})
  _renderFeatureGroup(document.getElementById('alpha-feature-list'), alphaKeys, features, labels, status || {}, reasons || {})

  const hdr = document.getElementById('alpha-hdr')
  if (hdr) hdr.style.display = alphaKeys.length ? '' : 'none'
}

function _renderFeatureGroup(list, keys, features, labels, status, reasons) {
  if (!list) return

  // (Re)build rows when the set changes; clear caches so the update pass repaints.
  if (list.children.length !== keys.length) {
    list.innerHTML = ''
    for (const key of keys) {
      const row = document.createElement('div')
      row.className   = 'feature-row'
      row.dataset.key = key

      const lbl = document.createElement('span')
      lbl.className   = 'feature-label'
      lbl.textContent = labels[key] || key

      const tog = document.createElement('button')
      tog.className = 'feature-toggle'
      tog.addEventListener('click', () => {
        if (tog.dataset.status !== 'unavailable') send('toggle_feature', { key })
      })

      row.append(lbl)
      const target = FEATURE_SETUP[key]
      if (target && window.eve && window.eve.openIntegrations) {
        const setup = document.createElement('a')
        setup.className   = 'feature-setup'
        setup.href        = '#'
        setup.textContent = 'Set up ↗'
        setup.title       = 'Open setup & install guide'
        setup.addEventListener('click', e => { e.preventDefault(); window.eve.openIntegrations(target) })
        row.append(setup)
      }
      row.append(tog)
      list.appendChild(row)
      _featureState[key] = undefined
      _featureStatus[key] = undefined
    }
  }

  for (const key of keys) {
    const avail   = (status[key] || 'ok') === 'ok'
    const enabled = features[key]
    const changed = _featureState[key] !== enabled || _featureStatus[key] !== status[key]
    if (!changed) continue

    _featureState[key]  = enabled
    _featureStatus[key] = status[key]

    const row = list.querySelector(`[data-key="${key}"]`)
    if (!row) continue

    const tog = row.querySelector('.feature-toggle')
    tog.dataset.status = avail ? 'ok' : 'unavailable'

    if (!avail) {
      tog.textContent  = 'UNAVAILABLE'
      tog.className    = 'feature-toggle unavailable'
      tog.title        = reasons[key] || 'Feature unavailable'
      tog.disabled     = true
      row.classList.add('feature-row-unavailable')
    } else {
      tog.textContent  = enabled ? 'ON' : 'OFF'
      tog.className    = `feature-toggle ${enabled ? 'on' : 'off'}`
      tog.title        = ''
      tog.disabled     = false
      row.classList.remove('feature-row-unavailable')
    }
  }
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

// Reset Window Layout — forget saved panel sizes (panels reopen at default).
{
  const resetBtn = document.getElementById('reset-layout-btn')
  if (resetBtn && window.eve && window.eve.resetWindowLayout) {
    resetBtn.addEventListener('click', () => {
      window.eve.resetWindowLayout()
      resetBtn.textContent = 'Reset ✓'
      setTimeout(() => { resetBtn.textContent = 'Reset size' }, 1500)
    })
  }
}

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
