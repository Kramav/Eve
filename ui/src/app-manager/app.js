// ── State ──────────────────────────────────────────────────────────────────
let discovered  = []   // [{name, path, spoken}]  from Python scanner
let configured  = []   // [[spoken, path]]         current apps.json
let selectedPaths = new Set()  // paths the user has checked

// ── WebSocket ──────────────────────────────────────────────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null

function connect() {
  ws = new WebSocket(WS_URL)

  ws.onopen = () => {
    setStatus('Connected — loading current config…')
    send('get_apps_config')
  }

  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'apps_config') {
        configured = msg.configured || []
        // Pre-select paths already in apps.json
        selectedPaths = new Set(configured.map(([, p]) => p.toLowerCase()))
        renderConfigured()
        setStatus(`Loaded ${configured.length} configured app${configured.length !== 1 ? 's' : ''}`)
      } else if (msg.type === 'scan_result') {
        discovered = msg.discovered || []
        // Merge configured paths from scan result too
        if (msg.configured) {
          configured = msg.configured
          selectedPaths = new Set(configured.map(([, p]) => p.toLowerCase()))
        }
        renderDiscovered()
        renderConfigured()
        document.getElementById('scan-btn').disabled = false
        document.getElementById('scan-btn').textContent = 'Scan'
        setStatus(`Found ${discovered.length} app${discovered.length !== 1 ? 's' : ''}`)
      } else if (msg.type === 'save_result') {
        if (msg.success) {
          setStatus('Saved!', 'ok')
        } else {
          setStatus('Save failed — check console', 'error')
        }
      }
    } catch (_) {}
  }

  ws.onclose = () => {
    setStatus('Disconnected — reconnecting…')
    setTimeout(connect, 500)
  }
  ws.onerror = () => {}
}

function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action, ...data }))
  }
}

connect()

// ── Render: discovered list ────────────────────────────────────────────────
const AVATAR_COLORS = [
  '#1a4a8a','#1a6a4a','#6a1a4a','#4a3a1a','#1a3a6a',
  '#6a2a1a','#2a1a6a','#1a5a5a','#5a1a2a','#3a5a1a',
]

function avatarColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) & 0xffffffff
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length]
}

function renderDiscovered() {
  const list    = document.getElementById('discovered-list')
  const search  = document.getElementById('search').value.toLowerCase()
  const ph      = document.getElementById('placeholder')
  if (ph) ph.remove()

  // Remove existing rows (keep scanning msg if present)
  list.querySelectorAll('.app-row').forEach(el => el.remove())

  const filtered = discovered.filter(app =>
    !search ||
    app.name.toLowerCase().includes(search) ||
    app.path.toLowerCase().includes(search)
  )

  document.getElementById('found-count').textContent =
    discovered.length ? `${filtered.length} / ${discovered.length}` : ''

  for (const app of filtered) {
    const isSelected = selectedPaths.has(app.path.toLowerCase())
    const row = document.createElement('div')
    row.className = `app-row${isSelected ? ' selected' : ''}`
    row.dataset.path = app.path

    const check = document.createElement('div')
    check.className = 'app-check'
    if (isSelected) check.textContent = '✓'

    const avatar = document.createElement('div')
    avatar.className = 'app-avatar'
    avatar.style.background = avatarColor(app.name)
    avatar.textContent = (app.name[0] || '?').toUpperCase()

    const info = document.createElement('div')
    info.className = 'app-info'

    const name = document.createElement('div')
    name.className = 'app-name'
    name.textContent = app.name

    const path = document.createElement('div')
    path.className = 'app-path'
    path.textContent = app.path

    info.append(name, path)
    row.append(check, avatar, info)

    row.addEventListener('click', () => toggleApp(app))
    list.appendChild(row)
  }
}

// ── Render: configured list ────────────────────────────────────────────────
function renderConfigured() {
  const list  = document.getElementById('configured-list')
  const empty = document.getElementById('configured-empty')

  list.querySelectorAll('.config-row').forEach(el => el.remove())

  document.getElementById('configured-count').textContent =
    `${configured.length} app${configured.length !== 1 ? 's' : ''}`

  if (configured.length === 0) {
    if (empty) empty.style.display = ''
    return
  }
  if (empty) empty.style.display = 'none'

  for (let i = 0; i < configured.length; i++) {
    const [spoken, path] = configured[i]
    const row = document.createElement('div')
    row.className = 'config-row'

    const spokenInput = document.createElement('input')
    spokenInput.className = 'config-spoken'
    spokenInput.type = 'text'
    spokenInput.value = spoken
    spokenInput.title = 'Spoken name (what you say to open this app)'
    spokenInput.addEventListener('input', () => {
      configured[i] = [spokenInput.value, path]
    })

    const pathEl = document.createElement('div')
    pathEl.className = 'config-path'
    pathEl.textContent = path
    pathEl.title = path

    const removeBtn = document.createElement('button')
    removeBtn.className = 'remove-btn'
    removeBtn.textContent = '×'
    removeBtn.title = 'Remove'
    removeBtn.addEventListener('click', () => removeApp(path))

    row.append(spokenInput, pathEl, removeBtn)
    list.appendChild(row)
  }
}

// ── Toggle app selection ───────────────────────────────────────────────────
function toggleApp(app) {
  const pathKey = app.path.toLowerCase()
  if (selectedPaths.has(pathKey)) {
    selectedPaths.delete(pathKey)
    configured = configured.filter(([, p]) => p.toLowerCase() !== pathKey)
  } else {
    selectedPaths.add(pathKey)
    // Use existing spoken name from apps.json if available, else use scanned spoken
    const existing = configured.find(([, p]) => p.toLowerCase() === pathKey)
    if (!existing) {
      configured.push([app.spoken, app.path])
    }
  }
  renderConfigured()
  // Update the checkbox in the discovered list without full re-render
  const row = document.querySelector(`.app-row[data-path="${CSS.escape(app.path)}"]`)
  if (row) {
    const isNowSelected = selectedPaths.has(pathKey)
    row.classList.toggle('selected', isNowSelected)
    row.querySelector('.app-check').textContent = isNowSelected ? '✓' : ''
  }
}

function removeApp(path) {
  const pathKey = path.toLowerCase()
  selectedPaths.delete(pathKey)
  configured = configured.filter(([, p]) => p.toLowerCase() !== pathKey)
  renderConfigured()
  // Uncheck in discovered list
  const row = document.querySelector(`.app-row[data-path="${CSS.escape(path)}"]`)
  if (row) {
    row.classList.remove('selected')
    row.querySelector('.app-check').textContent = ''
  }
}

// ── Status ─────────────────────────────────────────────────────────────────
let statusTimer = null

function setStatus(msg, cls = '') {
  const el = document.getElementById('status-msg')
  el.textContent = msg
  el.className = `status-msg${cls ? ' ' + cls : ''}`
  if (cls) {
    clearTimeout(statusTimer)
    statusTimer = setTimeout(() => { el.textContent = ''; el.className = 'status-msg' }, 3000)
  }
}

// ── Buttons ────────────────────────────────────────────────────────────────
document.getElementById('scan-btn').addEventListener('click', () => {
  const btn = document.getElementById('scan-btn')
  btn.disabled = true
  btn.textContent = 'Scanning…'
  document.getElementById('found-count').textContent = ''

  // Show scanning state in list
  const list = document.getElementById('discovered-list')
  list.innerHTML = `
    <div class="scanning-msg">
      <div>Scanning installed apps<span class="scanning-dots"></span></div>
    </div>`

  setStatus('Scanning system — this may take a few seconds…')
  send('scan_apps')
})

document.getElementById('save-btn').addEventListener('click', () => {
  setStatus('Saving…')
  send('save_apps', { apps: configured })
})

document.getElementById('add-btn').addEventListener('click', addManual)
document.getElementById('manual-path').addEventListener('keydown', e => {
  if (e.key === 'Enter') addManual()
})

function addManual() {
  const spoken = document.getElementById('manual-spoken').value.trim()
  const path   = document.getElementById('manual-path').value.trim()
  if (!spoken || !path) { setStatus('Enter both a spoken name and a path', 'error'); return }

  const pathKey = path.toLowerCase()
  if (!selectedPaths.has(pathKey)) {
    selectedPaths.add(pathKey)
    configured.push([spoken, path])
    renderConfigured()
  }
  document.getElementById('manual-spoken').value = ''
  document.getElementById('manual-path').value   = ''
}

document.getElementById('search').addEventListener('input', renderDiscovered)
