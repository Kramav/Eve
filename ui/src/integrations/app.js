// ── API Keys panel — data-driven (one card per service) ─────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null

// Add a service here and a card appears + Save/Test/Clear wire up automatically.
// The Python side is generic: integrations:set_<id> / integrations:test_<id>.
const SERVICES = [
  {
    id: 'brave', title: 'Brave Search',
    desc: "Fallback web search when DuckDuckGo returns nothing. Free tier: 2,000 searches/month.",
    url: 'https://brave.com/search/api/',
  },
  {
    id: 'anthropic', title: 'Anthropic (Claude vision)',
    desc: "Cloud vision for hands-free navigation when an app exposes no accessibility info. Compute runs off-machine — good for low-end PCs. Pay per use.",
    url: 'https://console.anthropic.com/settings/keys',
  },
  {
    id: 'openai', title: 'OpenAI (GPT vision)',
    desc: "Alternative cloud vision backend for hands-free navigation. Pay per use.",
    url: 'https://platform.openai.com/api-keys',
  },
]

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onopen    = () => send('integrations:get')
  ws.onclose   = () => setTimeout(connect, 500)
  ws.onerror   = () => {}
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'integrations_state')           applyState(msg.services || {})
      else if (msg.type === 'integrations_test_result') flash(msg.message, msg.ok ? 'ok' : 'error')
    } catch (_) {}
  }
}
function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...data }))
}

// ── Render cards + wire actions ──────────────────────────────────────────────
function render() {
  const root = document.getElementById('cards')
  root.innerHTML = SERVICES.map(s => `
    <div class="card" data-service="${s.id}">
      <div class="card-hdr">
        <span class="card-title">${s.title}</span>
        <span class="card-status" data-status>Not set</span>
      </div>
      <p class="card-desc">${s.desc}</p>
      <div class="key-row">
        <input type="password" class="key-input" data-key
               placeholder="Paste your ${s.title} API key" spellcheck="false" autocomplete="off">
        <button class="icon-btn" data-reveal title="Show / hide">👁</button>
      </div>
      <div class="btn-row">
        <a class="link-btn" data-get href="#">Get a key ↗</a>
        <span class="spacer"></span>
        <button class="ghost-btn" data-test>Test</button>
        <button class="ghost-btn danger" data-clear>Clear</button>
        <button class="save-btn" data-save>Save</button>
      </div>
    </div>`).join('')

  root.querySelectorAll('.card').forEach(card => {
    const id = card.dataset.service
    const svc = SERVICES.find(s => s.id === id)
    const input = card.querySelector('[data-key]')
    card.querySelector('[data-reveal]').onclick = () => {
      input.type = input.type === 'password' ? 'text' : 'password'
    }
    card.querySelector('[data-get]').onclick = e => { e.preventDefault(); window.eve.openExternal(svc.url) }
    card.querySelector('[data-save]').onclick = () => {
      const key = input.value.trim()
      if (!key) { flash('Paste a key first.', 'error'); return }
      send('integrations:set_' + id, { key }); input.value = ''; flash('Saved.', 'ok')
    }
    card.querySelector('[data-test]').onclick = () => {
      const key = input.value.trim()  // test the typed key, else the saved one
      flash('Testing…', 'busy')
      send('integrations:test_' + id, key ? { key } : {})
    }
    card.querySelector('[data-clear]').onclick = () => {
      if (!confirm('Remove the saved ' + svc.title + ' key?')) return
      send('integrations:set_' + id, { key: '' }); input.value = ''; flash('Cleared.', 'ok')
    }
  })
}

function applyState(services) {
  SERVICES.forEach(s => {
    const card = document.querySelector(`.card[data-service="${s.id}"]`)
    if (!card) return
    const st = services[s.id] || { set: false, hint: '' }
    const status = card.querySelector('[data-status]')
    const input = card.querySelector('[data-key]')
    if (st.set) {
      status.textContent = st.hint ? `Set ${st.hint}` : 'Set'
      status.classList.add('set')
      input.placeholder = st.hint ? `Saved (${st.hint}) — paste to replace` : 'Saved — paste to replace'
    } else {
      status.textContent = 'Not set'
      status.classList.remove('set')
    }
  })
}

// ── Status flash ─────────────────────────────────────────────────────────────
let _t = null
function flash(msg, cls = '') {
  const el = document.getElementById('status-msg')
  el.textContent = msg
  el.className = `status-msg${cls ? ' ' + cls : ''}`
  clearTimeout(_t)
  if (cls !== 'busy') _t = setTimeout(() => { el.textContent = ''; el.className = 'status-msg' }, 4000)
}

render()
connect()
