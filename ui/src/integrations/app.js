// ── WebSocket bridge to Python ───────────────────────────────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onopen    = () => send('integrations:get')
  ws.onclose   = () => setTimeout(connect, 500)
  ws.onerror   = () => {}
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'integrations_state')        applyState(msg.services || {})
      else if (msg.type === 'integrations_test_result') {
        flash(msg.message, msg.ok ? 'ok' : 'error')
      }
    } catch (_) {}
  }
}
function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...data }))
}
connect()

// ── Render ────────────────────────────────────────────────────────────────────
function applyState(services) {
  const brave = services.brave || { set: false, hint: '' }
  const status = document.getElementById('brave-status')
  if (brave.set) {
    status.textContent = brave.hint ? `Set ${brave.hint}` : 'Set'
    status.classList.add('set')
    document.getElementById('brave-key').placeholder = brave.hint
      ? `Saved (${brave.hint}) — paste to replace`
      : 'Saved — paste to replace'
  } else {
    status.textContent = 'Not set'
    status.classList.remove('set')
  }
}

// ── Brave card actions ─────────────────────────────────────────────────────────
const keyInput = document.getElementById('brave-key')

document.getElementById('brave-reveal').addEventListener('click', () => {
  keyInput.type = keyInput.type === 'password' ? 'text' : 'password'
})

document.getElementById('brave-get').addEventListener('click', (e) => {
  e.preventDefault()
  window.eve.openExternal('https://brave.com/search/api/')
})

document.getElementById('brave-save').addEventListener('click', () => {
  const key = keyInput.value.trim()
  if (!key) { flash('Paste a key first.', 'error'); return }
  send('integrations:set_brave', { key })
  keyInput.value = ''
  flash('Saved.', 'ok')
})

document.getElementById('brave-test').addEventListener('click', () => {
  const key = keyInput.value.trim()  // test the typed key, else the saved one
  flash('Testing…', 'busy')
  send('integrations:test_brave', key ? { key } : {})
})

document.getElementById('brave-clear').addEventListener('click', () => {
  if (!confirm('Remove the saved Brave API key?')) return
  send('integrations:set_brave', { key: '' })
  keyInput.value = ''
  flash('Cleared.', 'ok')
})

// ── Status flash ─────────────────────────────────────────────────────────────
let _t = null
function flash(msg, cls = '') {
  const el = document.getElementById('status-msg')
  el.textContent = msg
  el.className = `status-msg${cls ? ' ' + cls : ''}`
  clearTimeout(_t)
  if (cls !== 'busy') _t = setTimeout(() => { el.textContent = ''; el.className = 'status-msg' }, 4000)
}
