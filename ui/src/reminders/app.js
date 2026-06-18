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
      if (msg.type === 'reminders_all')        applyList(msg.items || [])
      else if (msg.type === 'reminders_set_result' && !msg.ok)
        flash(msg.error || 'Could not save', 'error')
    } catch (_) {}
  }
}
function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...data }))
}
function requestList() { send('reminders:get_all') }
connect()

// ── Render ────────────────────────────────────────────────────────────────────
function applyList(list) {
  items = Array.isArray(list) ? list : []
  document.getElementById('count-label').textContent =
    items.length ? `${items.length} scheduled` : 'What Eve will remind you'
  const rows = document.getElementById('rows')
  rows.innerHTML = ''
  if (!items.length) {
    rows.innerHTML = '<div class="rows-empty">Tell Eve "remind me to … at …" to add one</div>'
    return
  }
  for (const it of items) rows.appendChild(renderRow(it))
}

function renderRow(item) {
  const row = document.createElement('div')
  row.className = 'row'
  row.dataset.id = item.id || ''

  const msgField  = mkField('TASK', item.message || '', 'call mom')
  const whenField = mkField('WHEN', item.when || '', 'tomorrow at 9am')

  let saveTimer
  const scheduleSave = () => {
    row.classList.remove('saved', 'error')
    row.classList.add('dirty')
    clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      const message = msgField.input.value.trim()
      const when    = whenField.input.value.trim()
      if (!when) return  // need a time before we can schedule
      send('reminders:set', { id: row.dataset.id, message, when })
      row.classList.remove('dirty')
      row.classList.add('saved')
      flash('Saved', 'ok')
      setTimeout(() => row.classList.remove('saved'), 1500)
    }, 600)
  }
  msgField.input.addEventListener('input', scheduleSave)
  whenField.input.addEventListener('input', scheduleSave)

  const repeat = document.createElement('div')
  repeat.className = 'repeat' + (item.recurring ? ' on' : '')
  repeat.textContent = '↻'
  repeat.title = item.recurring ? 'Repeats' : 'One-time'

  const del = document.createElement('button')
  del.className = 'del-btn'
  del.title = 'Delete reminder'
  del.textContent = '✕'
  del.addEventListener('click', () => {
    if (!row.dataset.id) { row.remove(); return }  // unsaved blank row
    if (confirm(`Delete reminder "${msgField.input.value.trim() || 'this'}"?`)) {
      send('reminders:delete', { id: row.dataset.id })
      flash('Deleted', 'ok')
    }
  })

  row.append(msgField.wrap, whenField.wrap, repeat, del)
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
  const row = renderRow({ id: '', message: '', when: '', recurring: false })
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
  _statusTimer = setTimeout(() => { el.textContent = ''; el.className = 'status-msg' }, 2600)
}

document.getElementById('reload-btn').addEventListener('click', () => {
  flash('Refreshing…')
  requestList()
})
