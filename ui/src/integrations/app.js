// ── Integrations & Setup panel — data-driven (one card per integration) ─────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null
let _setup = {}   // latest tool-readiness map from the backend

// Add an integration here and its card + actions wire up automatically.
//   kind: 'key'  → API-key field + Save/Test/Clear (Python: integrations:set_<id>/test_<id>)
//   kind: 'tool' → status pill + copyable install command + setup guide
const SERVICES = [
  {
    kind: 'llm', id: 'llm', title: 'Local LLM fallback (no key, no cloud)',
    desc: "When no command matches, a local model interprets it — and Eve learns " +
          "verified successes so the model is needed less over time. Runs entirely " +
          "on this machine via llama.cpp + llama-swap. Idle models auto-unload.",
  },
  {
    kind: 'key', id: 'brave', title: 'Brave Search',
    desc: "Fallback web search when DuckDuckGo returns nothing. Free tier: 2,000 searches/month.",
    url: 'https://brave.com/search/api/',
    steps: ["Create a free key at the link below", "Paste it here and press Save"],
  },
  {
    kind: 'key', id: 'anthropic', title: 'Anthropic — Claude vision',
    desc: "Cloud vision for hands-free navigation when an app exposes no accessibility info. Compute runs off-machine — great for low-end PCs. Pay per use.",
    url: 'https://console.anthropic.com/settings/keys',
    steps: ["Create an API key at the link below", "Paste it here and press Save",
            "Turn it on: set EVE_VISION_BACKENDS=rapidocr,claude"],
  },
  {
    kind: 'key', id: 'openai', title: 'OpenAI — GPT vision',
    desc: "Alternative cloud vision backend for hands-free navigation. Pay per use.",
    url: 'https://platform.openai.com/api-keys',
    steps: ["Create an API key at the link below", "Paste it here and press Save",
            "Turn it on: set EVE_VISION_BACKENDS=rapidocr,gpt"],
  },
  {
    kind: 'tool', id: 'ollama', title: 'Ollama — local AI (no key, no cloud)',
    desc: "Runs language + vision models on your own machine. Free and private; best with a GPU.",
    guide: 'https://ollama.com/download',
    install: 'ollama pull moondream',
    steps: ["Install Ollama from the guide below", "Pull a vision model — run: ollama pull moondream",
            "Vision: set EVE_VISION_BACKENDS=rapidocr,ollama", "Q&A fallback: set FALLBACK_LLM=ollama"],
  },
  {
    kind: 'tool', id: 'rapidocr', title: 'OCR vision (no GPU)', installable: true,
    desc: "Reads on-screen text so hands-free navigation works on any app, on any machine. CPU-only — the default vision tier.",
    guide: 'https://github.com/RapidAI/RapidOCR',
    install: 'pip install rapidocr-onnxruntime',
    steps: ["Press Install (or run the command)", "That's it — it's the default vision tier"],
  },
  {
    kind: 'tool', id: 'uiautomation', title: 'Accessibility navigation (most accurate)', installable: true,
    desc: "Reads real links/buttons from apps that expose them (Firefox, Chrome, Electron). The top hands-free tier — used before any screenshot.",
    guide: 'https://github.com/yinkaisheng/Python-UIAutomation-for-Windows',
    install: 'pip install uiautomation',
    steps: ["Press Install (or run the command)", "Enable 'Hands-free Visual Navigation' in the App Manager"],
  },
]

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onopen    = () => { send('integrations:get'); send('llm:get') }
  ws.onclose   = () => setTimeout(connect, 500)
  ws.onerror   = () => {}
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'integrations_state') {
        _setup = msg.setup || _setup
        applyState(msg.services || {})
        // Re-enable any Install button now that fresh status arrived.
        document.querySelectorAll('[data-install-btn]').forEach(b => { b.disabled = false })
      } else if (msg.type === 'llm_intents_result') {
        flash(msg.message, msg.ok ? 'ok' : 'error')
      } else if (msg.type === 'llm_state') {
        _llm = msg
        applyLlmState()
        if (_llmSaving) { _llmSaving = false; flash('LLM options saved.', 'ok') }
      } else if (msg.type === 'integrations_test_result') {
        flash(msg.message, msg.ok ? 'ok' : 'error')
      } else if (msg.type === 'integrations_install_result') {
        flash(msg.message, msg.ok ? 'ok' : 'error')
      }
    } catch (_) {}
  }
}
function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...data }))
}

// ── Render ────────────────────────────────────────────────────────────────────
const esc = s => String(s).replace(/[&<>"]/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

function steps(list) {
  if (!list || !list.length) return ''
  return `<ol class="steps">${list.map(s => `<li>${esc(s)}</li>`).join('')}</ol>`
}

function keyCard(s) {
  return `
    <div class="key-row">
      <input type="password" class="key-input" data-key
             placeholder="Paste your ${esc(s.title)} key" spellcheck="false" autocomplete="off">
      <button class="icon-btn" data-reveal title="Show / hide">👁</button>
    </div>
    ${steps(s.steps)}
    <div class="btn-row">
      <a class="link-btn" data-guide href="#">${s.url ? 'Get a key ↗' : ''}</a>
      <span class="spacer"></span>
      <button class="ghost-btn" data-test>Test</button>
      <button class="ghost-btn danger" data-clear>Clear</button>
      <button class="save-btn" data-save>Save</button>
    </div>`
}

// ── Local LLM options card — every knob user-editable, saved to settings.json
let _llm = null          // latest llm_state from the backend
let _llmSaving = false   // Apply in flight (so llm:get responses don't flash)

const _LLM_FIELDS = [   // [key, label, input type]
  ['enabled',         'Enable LLM fallback',                     'check'],
  ['gpu',             'Use GPU for the main model',              'check'],
  ['preload',         'Preload model at startup (no first-use wait)', 'check'],
  ['swap_when_busy',  'Use small model while gaming / RAM high',  'check'],
  ['gpu_when_busy',   'Run small model on GPU during games (needs VRAM headroom)', 'check'],
  ['busy_ram_pct',    'Busy above RAM %',                        'num'],
  ['main_model_file', 'Main model',                              'model'],
  ['mini_model_file', 'Small model',                             'model'],
  ['ctx_main',        'Main context (tokens)',                   'num'],
  ['ctx_mini',        'Small context (tokens)',                  'num'],
  ['ttl_main',        'Main idle unload (s)',                    'num'],
  ['ttl_mini',        'Small idle unload (s)',                   'num'],
  ['base_url',        'Server URL (any local OpenAI-style host)', 'text'],
]

function llmCard() {
  return `
    <div class="llm-grid">
      ${_LLM_FIELDS.map(([key, label, type]) => `
        <label class="llm-field llm-${type}">
          <span>${esc(label)}</span>
          ${type === 'check' ? `<input type="checkbox" data-llm="${key}">`
          : type === 'model' ? `<select data-llm="${key}"></select>`
          : `<input type="${type === 'num' ? 'number' : 'text'}" data-llm="${key}" spellcheck="false">`}
        </label>`).join('')}
    </div>
    <div class="llm-tip">Tip: <b>Preload</b> + <b>Main idle unload</b> set to 0 keeps the model
      warm — instant answers, always. Gaming is still protected: when a game launches the main
      model is evicted from the GPU right away and the small model takes over (on CPU by default;
      flip <b>Run small model on GPU during games</b> if your card has VRAM to spare — faster, but
      can micro-stutter a maxed-out GPU). When you quit, the main model warms back up.</div>
    <div class="btn-row">
      <button class="ghost-btn" data-llm-export title="Save your learned intents as a shareable file">Export intents</button>
      <button class="ghost-btn" data-llm-import title="Load someone else's exported intents">Import intents</button>
      <span class="spacer"></span>
      <button class="save-btn" data-llm-apply>Apply</button>
    </div>`
}

function applyLlmState() {
  const card = document.querySelector('.card[data-service="llm"]')
  if (!card || !_llm) return
  const pill = card.querySelector('[data-status]')
  const s = _llm.settings || {}
  pill.textContent = !s.enabled ? 'Off'
    : _llm.server_up ? 'Ready' : 'Starts on first use'
  pill.classList.toggle('set', !!s.enabled)
  card.querySelectorAll('[data-llm]').forEach(el => {
    const key = el.dataset.llm
    if (el.tagName === 'SELECT') {
      const models = _llm.models || []
      el.innerHTML = models.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('')
        || '<option value="">(no .gguf files in models/llm)</option>'
      el.value = s[key] || ''
    } else if (el.type === 'checkbox') el.checked = !!s[key]
    else el.value = s[key] ?? ''
  })
}

function readLlmForm(card) {
  const out = {}
  card.querySelectorAll('[data-llm]').forEach(el => {
    const key = el.dataset.llm
    out[key] = el.type === 'checkbox' ? el.checked
             : el.type === 'number'   ? Number(el.value) || 0
             : el.value.trim()
  })
  return out
}

function toolCard(s) {
  return `
    ${s.install ? `<div class="key-row">
      <input class="key-input" data-install readonly value="${esc(s.install)}">
      <button class="icon-btn" data-copy title="Copy">⧉</button>
    </div>` : ''}
    ${steps(s.steps)}
    <div class="btn-row">
      <a class="link-btn" data-guide href="#">Setup guide ↗</a>
      <span class="spacer"></span>
      ${s.installable ? '<button class="save-btn" data-install-btn>Install</button>' : ''}
    </div>`
}

function render() {
  const root = document.getElementById('cards')
  root.innerHTML = SERVICES.map(s => `
    <div class="card" data-service="${s.id}" data-kind="${s.kind}">
      <div class="card-hdr">
        <span class="card-title">${esc(s.title)}</span>
        <span class="card-status" data-status>…</span>
      </div>
      <p class="card-desc">${esc(s.desc)}</p>
      ${s.kind === 'key' ? keyCard(s) : s.kind === 'llm' ? llmCard() : toolCard(s)}
    </div>`).join('')

  root.querySelectorAll('.card').forEach(card => {
    const id = card.dataset.service
    const svc = SERVICES.find(s => s.id === id)
    const guide = card.querySelector('[data-guide]')
    if (guide) guide.onclick = e => { e.preventDefault(); window.eve.openExternal(svc.url || svc.guide) }

    if (svc.kind === 'llm') {
      card.querySelector('[data-llm-apply]').onclick = () => {
        _llmSaving = true
        flash('Applying… (restarts the model host)', 'busy')
        send('llm:set', { settings: readLlmForm(card) })
      }
      card.querySelector('[data-llm-export]').onclick = () => {
        flash('Exporting…', 'busy')
        send('llm:export_intents')
      }
      card.querySelector('[data-llm-import]').onclick = () => {
        if (!confirm(
          'Import someone else\'s trained intents?\n\n' +
          'Warning: imported intents can cause unexpected issues — phrases ' +
          'may trigger actions you don\'t expect, or override how Eve ' +
          'interprets similar commands. They are kept separate from your own ' +
          'learned intents (imported_intents.json) and can be removed by ' +
          'deleting that file.')) return
        flash('Choose a file in the picker…', 'busy')
        send('llm:import_intents')
      }
    } else if (svc.kind === 'key') {
      const input = card.querySelector('[data-key]')
      card.querySelector('[data-reveal]').onclick = () => {
        input.type = input.type === 'password' ? 'text' : 'password'
      }
      card.querySelector('[data-save]').onclick = () => {
        const key = input.value.trim()
        if (!key) { flash('Paste a key first.', 'error'); return }
        send('integrations:set_' + id, { key }); input.value = ''; flash('Saved.', 'ok')
      }
      card.querySelector('[data-test]').onclick = () => {
        const key = input.value.trim()
        flash('Testing…', 'busy')
        send('integrations:test_' + id, key ? { key } : {})
      }
      card.querySelector('[data-clear]').onclick = () => {
        if (!confirm('Remove the saved ' + svc.title + ' key?')) return
        send('integrations:set_' + id, { key: '' }); input.value = ''; flash('Cleared.', 'ok')
      }
    } else {
      const copy = card.querySelector('[data-copy]')
      if (copy) copy.onclick = async () => {
        try { await navigator.clipboard.writeText(svc.install); flash('Command copied.', 'ok') }
        catch (_) { flash('Select the command and copy it.', 'busy') }
      }
      const inst = card.querySelector('[data-install-btn]')
      if (inst) inst.onclick = () => {
        inst.disabled = true
        flash(`Installing ${svc.title}… this can take a minute.`, 'busy')
        send('integrations:install_' + id)
      }
    }
  })
  applyState({})
}

function applyState(services) {
  SERVICES.forEach(s => {
    const card = document.querySelector(`.card[data-service="${s.id}"]`)
    if (!card) return
    if (s.kind === 'llm') { applyLlmState(); return }
    const pill = card.querySelector('[data-status]')
    if (s.kind === 'key') {
      const st = services[s.id] || { set: false, hint: '' }
      const input = card.querySelector('[data-key]')
      if (st.set) {
        pill.textContent = st.hint ? `Set ${st.hint}` : 'Set'
        pill.classList.add('set')
        if (input) input.placeholder = st.hint
          ? `Saved (${st.hint}) — paste to replace` : 'Saved — paste to replace'
      } else {
        pill.textContent = 'Not set'; pill.classList.remove('set')
      }
    } else {
      const st = _setup[s.id] || { ready: false, detail: '' }
      pill.textContent = st.ready ? (st.detail ? `Ready · ${st.detail}` : 'Ready') : 'Not installed'
      pill.classList.toggle('set', !!st.ready)
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

// ── Deep-link: scroll to + highlight a card (from a feature "Set up" link) ──
function scrollToCard(target) {
  if (!target) return
  const card = document.querySelector(`.card[data-service="${target}"]`)
  if (!card) return
  card.scrollIntoView({ behavior: 'smooth', block: 'center' })
  card.classList.add('highlight')
  setTimeout(() => card.classList.remove('highlight'), 2000)
}

render()
connect()

// Opened fresh with #hash, or messaged while already open.
const initial = (location.hash || '').replace('#', '')
if (initial) setTimeout(() => scrollToCard(initial), 120)
if (window.eve && window.eve.onScrollTo) window.eve.onScrollTo(t => scrollToCard(t))
