// ── WebSocket ────────────────────────────────────────────────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onopen    = () => wsSend('get_voices')
  ws.onclose   = () => setTimeout(connect, 500)
  ws.onerror   = () => {}
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'voices_list') applyVoices(msg.voices, msg.current)
    } catch (_) {}
  }
}

function wsSend(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...data }))
}

connect()

// ── Sliders ───────────────────────────────────────────────────────────────────
const SLIDERS  = ['speed', 'noise_scale', 'noise_w']
const DEFAULTS = { speed: 1.0, noise_scale: 0.667, noise_w: 0.8 }

function fmt(id, v) {
  return id === 'speed' ? `${parseFloat(v).toFixed(2)}×` : parseFloat(v).toFixed(2)
}

function setSlider(id, v) {
  const el = document.getElementById(id)
  const vl = document.getElementById(`${id}-val`)
  if (el) el.value = v
  if (vl) vl.textContent = fmt(id, v)
}

for (const id of SLIDERS) {
  document.getElementById(id).addEventListener('input', e => {
    document.getElementById(`${id}-val`).textContent = fmt(id, e.target.value)
  })
}

function getParams() {
  const p = Object.fromEntries(SLIDERS.map(id => [id, parseFloat(document.getElementById(id).value)]))
  const voiceId = document.getElementById('voice_id').value
  if (voiceId) p.voice_id = voiceId
  return p
}

// ── Voice dropdown ────────────────────────────────────────────────────────────
let _voices = []

function applyVoices(voices, current) {
  _voices = voices || []
  const sel = document.getElementById('voice_id')
  const cnt = document.getElementById('voice-count')
  sel.innerHTML = ''
  if (!_voices.length) {
    const opt = document.createElement('option')
    opt.value = ''; opt.textContent = 'No voices found'
    sel.appendChild(opt)
    cnt.textContent = '0'
    return
  }
  for (const v of _voices) {
    const opt = document.createElement('option')
    opt.value = v.id
    opt.textContent = v.label
    sel.appendChild(opt)
  }
  cnt.textContent = `${_voices.length} available`
  // Prefer saved voice from settings, fall back to whatever speaker currently has loaded
  if (_pendingVoiceId && _voices.find(v => v.id === _pendingVoiceId)) {
    sel.value = _pendingVoiceId
  } else if (current && _voices.find(v => v.id === current)) {
    sel.value = current
  }
  _pendingVoiceId = null
}

// ── Load saved settings ───────────────────────────────────────────────────────
let _pendingVoiceId = null
;(async () => {
  const [params, presets] = await Promise.all([
    window.eve.getVoiceSettings(),
    window.eve.getVoicePresets(),
  ])
  for (const id of SLIDERS) setSlider(id, params[id] ?? DEFAULTS[id])
  if (params.voice_id) _pendingVoiceId = params.voice_id
  // If voices already arrived, apply now
  if (_voices.length) {
    const sel = document.getElementById('voice_id')
    if (params.voice_id && _voices.find(v => v.id === params.voice_id)) sel.value = params.voice_id
  }
  renderPresets(presets)
})()

// ── Status helper ─────────────────────────────────────────────────────────────
function showStatus(msg, ok = true) {
  const el = document.getElementById('status-msg')
  el.textContent = msg
  el.className = `status-msg ${ok ? 'ok' : 'error'}`
  setTimeout(() => { el.textContent = ''; el.className = 'status-msg' }, 3000)
}

// ── Presets ───────────────────────────────────────────────────────────────────
function renderPresets(presets) {
  const container = document.getElementById('preset-chips')
  const empty     = document.getElementById('preset-empty')
  const names     = Object.keys(presets)
  container.querySelectorAll('.preset-chip').forEach(el => el.remove())
  empty.style.display = names.length ? 'none' : ''
  for (const name of names) {
    const chip = document.createElement('div')
    chip.className = 'preset-chip'

    const btn = document.createElement('button')
    btn.className   = 'chip-name'
    btn.textContent = name
    btn.addEventListener('click', () => {
      const p = presets[name]
      for (const id of SLIDERS) setSlider(id, p[id] ?? DEFAULTS[id])
      if (p.voice_id) {
        const sel = document.getElementById('voice_id')
        if (_voices.find(v => v.id === p.voice_id)) sel.value = p.voice_id
      }
      showStatus(`Loaded "${name}"`)
    })

    const del = document.createElement('button')
    del.className   = 'chip-del'
    del.textContent = '×'
    del.title       = 'Delete preset'
    del.addEventListener('click', async () => {
      const updated = await window.eve.deleteVoicePreset(name)
      renderPresets(updated)
      showStatus(`Deleted "${name}"`)
    })

    chip.append(btn, del)
    container.appendChild(chip)
  }
}

document.getElementById('preset-save-btn').addEventListener('click', async () => {
  const name = document.getElementById('preset-name').value.trim()
  if (!name) { showStatus('Enter a preset name', false); return }
  const updated = await window.eve.saveVoicePreset(name, getParams())
  renderPresets(updated)
  document.getElementById('preset-name').value = ''
  showStatus(`Saved "${name}"`)
})

document.getElementById('preset-name').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('preset-save-btn').click()
})

// ── Footer buttons ────────────────────────────────────────────────────────────
document.getElementById('defaults-btn').addEventListener('click', () => {
  for (const id of SLIDERS) setSlider(id, DEFAULTS[id])
  showStatus('Defaults restored')
})

document.getElementById('test-btn').addEventListener('click', () => {
  wsSend('test_voice', { params: getParams() })
  showStatus('Playing test…')
})

document.getElementById('save-btn').addEventListener('click', () => {
  wsSend('set_voice_settings', { params: getParams() })
  showStatus('Saved')
})
