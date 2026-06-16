// ── WebSocket bridge to Python ───────────────────────────────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null
let items = []

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onopen    = () => requestList()
  ws.onclose   = () => setTimeout(connect, 500)
  ws.onerror   = () => {}
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if      (msg.type === 'programs_list')        applyList(msg.items || [])
      else if (msg.type === 'programs_add_result')  applyAddResult(msg)
    } catch (_) {}
  }
}
function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...data }))
}
function requestList() { send('programs:get_list') }
connect()

// ── Render ────────────────────────────────────────────────────────────────────
function applyList(list) {
  items = list
  document.getElementById('count-label').textContent =
    `${list.length} detected`
  const rows = document.getElementById('rows')
  rows.innerHTML = ''
  if (!list.length) {
    rows.innerHTML = '<div class="rows-empty">Nothing running.</div>'
    return
  }
  for (const it of list) {
    rows.appendChild(renderRow(it))
  }
}

function renderRow(it) {
  const row = document.createElement('div')
  row.className = 'row' + (it.minimized ? ' minimized' : '')
  row.dataset.hwnd = it.hwnd

  const glyph = document.createElement('span')
  glyph.className   = 'row-glyph'
  glyph.textContent = '◈'

  const meta = document.createElement('div')
  meta.className = 'row-meta'
  const name = document.createElement('div')
  name.className   = 'row-name'
  name.textContent = it.name || it.exe || 'window'
  const title = document.createElement('div')
  title.className  = 'row-title'
  if (it.minimized) {
    const b = document.createElement('span')
    b.className = 'badge'; b.textContent = 'MIN'
    title.appendChild(b)
  }
  title.appendChild(document.createTextNode(it.title || ''))
  meta.append(name, title)

  const actions = document.createElement('div')
  actions.className = 'row-actions'

  const front = mkBtn('▲', 'Bring to front',
    () => { send('programs:bring_front', { hwnd: it.hwnd }); flash(`Brought ${it.name} to front`, 'ok') })
  const back  = mkBtn('▼', 'Send to back',
    () => { send('programs:send_back',   { hwnd: it.hwnd }); flash(`Sent ${it.name} to back`, 'ok') })

  let add
  if (it.in_apps) {
    add = mkBtn('✓', 'Already in apps', () => {})
    add.classList.add('added')
  } else {
    const addTitle = it.path
      ? `Add to apps.json (${it.path})`
      : 'Add to apps.json'
    add = mkBtn('+', addTitle, () => {
      send('programs:add_to_apps', {
        name: it.name.toLowerCase(),
        // Prefer the full executable path; fall back to bare exe if Win32
        // couldn't read it (e.g., a process we don't have rights to query).
        path: it.path || '',
        exe:  it.exe,
      })
    })
    add.classList.add('good')
  }

  const close = mkBtn('✕', 'Close', () => {
    if (confirm(`Close ${it.name}?`)) {
      send('programs:close', { hwnd: it.hwnd })
      flash(`Closed ${it.name}`, 'ok')
      // optimistic remove
      row.style.opacity = 0.3
      setTimeout(requestList, 400)
    }
  })
  close.classList.add('danger')

  actions.append(front, back, add, close)
  row.append(glyph, meta, actions)
  return row
}

function mkBtn(label, title, onClick) {
  const b = document.createElement('button')
  b.className   = 'action-btn'
  b.textContent = label
  b.title       = title
  b.addEventListener('click', onClick)
  return b
}

function applyAddResult(msg) {
  if (msg.ok) {
    flash(`Added "${msg.name}" to apps.json`, 'ok')
    requestList()
  } else {
    flash(`Couldn't add: ${msg.error}`, 'error')
  }
}

// ── Status flash ─────────────────────────────────────────────────────────────
let _statusTimer = null
function flash(msg, cls = '') {
  const el = document.getElementById('status-msg')
  el.textContent = msg
  el.className   = `status-msg${cls ? ' ' + cls : ''}`
  clearTimeout(_statusTimer)
  _statusTimer = setTimeout(() => { el.textContent = ''; el.className = 'status-msg' }, 2200)
}

document.getElementById('reload-btn').addEventListener('click', () => {
  flash('Refreshing…')
  requestList()
})
