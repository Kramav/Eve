// ── WebSocket bridge to Python ───────────────────────────────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null
let items = {}

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onopen    = () => requestList()
  ws.onclose   = () => setTimeout(connect, 500)
  ws.onerror   = () => {}
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'memory_all') applyList(msg.items || {})
    } catch (_) {}
  }
}
function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...data }))
}
function requestList() { send('memory:get_all') }
connect()

// ── Render ────────────────────────────────────────────────────────────────────
function applyList(dict) {
  items = dict
  const keys = Object.keys(dict).sort()
  document.getElementById('count-label').textContent =
    keys.length ? `${keys.length} remembered` : 'What Eve remembers'
  const rows = document.getElementById('rows')
  rows.innerHTML = ''
  if (!keys.length) {
    rows.innerHTML = '<div class="rows-empty">Tell Eve "remember my X is Y" to add memories</div>'
    return
  }
  for (const k of keys) {
    rows.appendChild(renderRow(k, dict[k]))
  }
}

function renderRow(key, value) {
  const row = document.createElement('div')
  row.className = 'row'
  row.dataset.origKey = key

  const keyField = mkField('KEY', key, 'work monitor')
  const valField = mkField('VALUE', value, 'monitor 3')

  // Debounced save on input
  let saveTimer
  const scheduleSave = () => {
    row.classList.remove('saved')
    row.classList.add('dirty')
    clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      const newKey = keyField.input.value.trim().toLowerCase()
      const newVal = valField.input.value.trim()
      if (!newKey) return
      // If user renamed the key, drop the old one first
      const origKey = row.dataset.origKey
      if (newKey !== origKey) send('memory:delete', { key: origKey })
      send('memory:set', { key: newKey, value: newVal })
      row.dataset.origKey = newKey
      row.classList.remove('dirty')
      row.classList.add('saved')
      flash(`Saved "${newKey}"`, 'ok')
      setTimeout(() => row.classList.remove('saved'), 1500)
    }, 500)
  }
  keyField.input.addEventListener('input', scheduleSave)
  valField.input.addEventListener('input', scheduleSave)

  const del = document.createElement('button')
  del.className = 'del-btn'
  del.title = 'Forget this'
  del.textContent = '✕'
  del.addEventListener('click', () => {
    if (confirm(`Forget "${row.dataset.origKey}"?`)) {
      send('memory:delete', { key: row.dataset.origKey })
      flash(`Forgot "${row.dataset.origKey}"`, 'ok')
    }
  })

  row.append(keyField.wrap, valField.wrap, del)
  return row
}

function mkField(labelText, value, placeholder) {
  const wrap = document.createElement('div'); wrap.className = 'field'
  const lbl = document.createElement('label'); lbl.textContent = labelText
  const input = document.createElement('input'); input.type = 'text'
  input.value = value || ''
  input.placeholder = placeholder
  input.spellcheck = false
  wrap.append(lbl, input)
  return { wrap, input }
}

// ── Add button ───────────────────────────────────────────────────────────────
document.getElementById('add-btn').addEventListener('click', () => {
  const rows = document.getElementById('rows')
  if (rows.querySelector('.rows-empty')) rows.innerHTML = ''
  // Insert a blank row and focus the key field
  const row = renderRow('', '')
  rows.appendChild(row)
  const input = row.querySelector('input')
  if (input) input.focus()
})

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
