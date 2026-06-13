// ── WebSocket bridge ───────────────────────────────────────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'state')                  applyState(msg)
      else if (msg.type === 'open_app_manager')  window.eve.openAppManager()
      else if (msg.type === 'open_window_manager')  window.eve.openWindowManager()
      else if (msg.type === 'close_app_manager')    window.eve.closeAppManager()
      else if (msg.type === 'close_window_manager') window.eve.closeWindowManager()
    } catch (_) {}
  }
  ws.onclose = () => setTimeout(connect, 500)
  ws.onerror = () => {}
}

function send(action, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action, ...data }))
  }
}

connect()

// ── Canvas orb ─────────────────────────────────────────────────────────────
const canvas = document.getElementById('orb')
const ctx    = canvas.getContext('2d')
const W = canvas.width, H = canvas.height, CX = W / 2, CY = H / 2

const MODE_COLORS = {
  idle:       [74,  158, 255],
  listening:  [255, 140, 0  ],
  processing: [0,   212, 255],
  playing:    [0,   232, 122],
}
const ALWAYS_RGB = [255, 51, 85]

let evMode     = 'idle'
let evAlwaysOn = false
let evHudOpen  = false

function orbColor(alpha) {
  const [r, g, b] = (evAlwaysOn && evMode === 'idle')
    ? ALWAYS_RGB
    : (MODE_COLORS[evMode] || MODE_COLORS.idle)
  return alpha === undefined
    ? `rgb(${r},${g},${b})`
    : `rgba(${r},${g},${b},${alpha})`
}

function drawOrb(ts) {
  const t        = ts * 0.001
  const isActive = evMode === 'listening' || evAlwaysOn
  const pulse    = (Math.sin(t * (isActive ? 4.5 : 1.6)) + 1) / 2

  ctx.clearRect(0, 0, W, H)

  // Ambient aura
  const aura = ctx.createRadialGradient(CX, CY, 0, CX, CY, 68)
  aura.addColorStop(0, orbColor(0.10 + pulse * 0.08))
  aura.addColorStop(1, 'transparent')
  ctx.fillStyle = aura
  ctx.fillRect(0, 0, W, H)

  // Outer dim ring
  _ring(62, 0, Math.PI * 2, orbColor(0.06 + pulse * 0.04), 1, 0)

  // Slow-rotating segmented ring
  ctx.save()
  _pivot(t * 0.06)
  ctx.setLineDash([12, 9])
  _ring(56, 0, Math.PI * 2, orbColor(0.09), 1, 0)
  ctx.setLineDash([])
  ctx.restore()

  // Primary mode arc
  {
    const spd = { idle: 0.10, listening: 0.65, processing: 2.0, playing: 0.22 }[evMode] || 0.10
    const ext = evMode === 'processing' ? Math.PI * 0.55 : Math.PI * 2
    const lw  = evMode === 'idle' ? 1.5 : 2.5
    ctx.save()
    _pivot(t * spd - Math.PI / 2)
    _ring(48, -Math.PI / 2, -Math.PI / 2 + ext, orbColor(0.55 + pulse * 0.3), lw, isActive ? 14 : 7)
    ctx.restore()
  }

  // Inner depth ring
  _ring(36, 0, Math.PI * 2, orbColor(0.06 + pulse * 0.04), 1, 0)

  // Core body
  ctx.beginPath(); ctx.arc(CX, CY, 24, 0, Math.PI * 2)
  ctx.fillStyle = '#020810'; ctx.fill()
  ctx.strokeStyle = orbColor(0.18 + pulse * 0.18)
  ctx.lineWidth = 1.5
  ctx.shadowBlur = 9; ctx.shadowColor = orbColor()
  ctx.stroke(); ctx.shadowBlur = 0

  // Core radial glow
  const cg = ctx.createRadialGradient(CX, CY, 0, CX, CY, 24)
  cg.addColorStop(0, orbColor(0.20 + pulse * 0.16))
  cg.addColorStop(1, 'transparent')
  ctx.fillStyle = cg
  ctx.beginPath(); ctx.arc(CX, CY, 24, 0, Math.PI * 2); ctx.fill()

  // Hot center dot
  ctx.beginPath(); ctx.arc(CX, CY, 5, 0, Math.PI * 2)
  ctx.fillStyle = orbColor(0.55 + pulse * 0.45)
  ctx.shadowBlur = 8; ctx.shadowColor = orbColor()
  ctx.fill(); ctx.shadowBlur = 0

  // Waveform bars inside orb when active
  if (evMode === 'listening' || evAlwaysOn) {
    const n = 7, bw = 2.5, gap = 2
    const sx = CX - (n * (bw + gap) - gap) / 2
    for (let i = 0; i < n; i++) {
      const ph = t * 5.5 + i * 0.65
      const bh = 4 + Math.abs(Math.sin(ph)) * 14
      ctx.fillStyle = orbColor(0.5 + Math.abs(Math.sin(ph)) * 0.5)
      ctx.shadowBlur = 5; ctx.shadowColor = orbColor()
      _roundRect(sx + i * (bw + gap), CY - bh / 2, bw, bh, 1.5)
      ctx.fill(); ctx.shadowBlur = 0
    }
  }

  // Processing scan line
  if (evMode === 'processing') {
    const sy = CY - 36 + ((t * 38) % 72)
    const d  = Math.abs(sy - CY) / 36
    const sa = (1 - d * 0.5) * 0.55
    ctx.strokeStyle = orbColor(sa * 0.3); ctx.lineWidth = 3
    ctx.beginPath(); ctx.moveTo(CX - 40, sy); ctx.lineTo(CX + 40, sy); ctx.stroke()
    ctx.strokeStyle = orbColor(sa * 0.8); ctx.lineWidth = 0.8
    ctx.beginPath(); ctx.moveTo(CX - 32, sy); ctx.lineTo(CX + 32, sy); ctx.stroke()
  }

  requestAnimationFrame(drawOrb)
}

function _ring(r, s, e, color, lw, glow) {
  ctx.save()
  if (glow > 0) { ctx.shadowBlur = glow; ctx.shadowColor = color }
  ctx.strokeStyle = color; ctx.lineWidth = lw
  ctx.beginPath(); ctx.arc(CX, CY, r, s, e); ctx.stroke()
  ctx.restore()
}

function _pivot(a) {
  ctx.translate(CX, CY); ctx.rotate(a); ctx.translate(-CX, -CY)
}

function _roundRect(x, y, w, h, r) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y);   ctx.quadraticCurveTo(x + w, y,     x + w, y + r)
  ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  ctx.lineTo(x + r, y + h);   ctx.quadraticCurveTo(x,     y + h, x,     y + h - r)
  ctx.lineTo(x, y + r);       ctx.quadraticCurveTo(x,     y,     x + r, y)
  ctx.closePath()
}

requestAnimationFrame(drawOrb)

// ── Clock ──────────────────────────────────────────────────────────────────
;(function tickClock() {
  const n = new Date(), p = v => String(v).padStart(2, '0')
  const el = document.getElementById('clock')
  if (el) el.textContent = `${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`
  setTimeout(tickClock, 1000)
})()

// ── History feed ───────────────────────────────────────────────────────────
const KIND = { heard: 'You', action: 'Eve', error: 'Error', system: 'Sys' }
let entryCount = 0

function appendEntry(e) {
  const feed  = document.getElementById('feed')
  const empty = document.getElementById('feed-empty')
  if (empty) empty.style.display = 'none'

  if (entryCount >= 200) {
    const first = feed.querySelector('.entry')
    if (first) { feed.removeChild(first); entryCount-- }
  }

  const div  = document.createElement('div')
  div.className = `entry ${e.kind}`

  const meta = document.createElement('div'); meta.className = 'e-meta'
  const tag  = document.createElement('span'); tag.className = 'e-tag'
  tag.textContent = KIND[e.kind] || e.kind

  const ts   = document.createElement('span'); ts.className = 'e-ts'
  ts.textContent = e.ts

  meta.append(tag, ts)

  const txt = document.createElement('div'); txt.className = 'e-text'
  txt.textContent = e.text

  div.append(meta, txt)
  feed.appendChild(div)
  entryCount++
  feed.scrollTop = feed.scrollHeight
}

function clearFeed() {
  const feed = document.getElementById('feed')
  feed.querySelectorAll('.entry').forEach(el => el.remove())
  entryCount = 0
  const empty = document.getElementById('feed-empty')
  if (empty) empty.style.display = ''
}

// ── Apply state ────────────────────────────────────────────────────────────
let prev = {}

function applyState(s) {
  const mode     = s.mode || 'idle'
  const alwaysOn = !!s.active_listening
  const hudOpen  = !!s.hud_visible

  evMode     = mode
  evAlwaysOn = alwaysOn

  // Resize Electron window when HUD visibility changes
  if (hudOpen !== evHudOpen) {
    evHudOpen = hudOpen
    window.eve.setSize(!hudOpen)
  }

  // Drive all CSS state via body classes
  const cls = []
  if (mode === 'listening')  cls.push('listening')
  if (mode === 'processing') cls.push('processing')
  if (alwaysOn)              cls.push('always-on')
  if (hudOpen)               cls.push('hud-open')
  document.body.className = cls.join(' ')

  // State label in HUD header
  if (mode !== prev.mode || s.status_text !== prev.status_text) {
    const labels = { idle: 'Idle', listening: 'Listening', processing: 'Thinking', playing: 'Playing' }
    document.getElementById('state-label').textContent =
      s.status_text || labels[mode] || 'Idle'
  }

  // Live area label (always-on vs triggered)
  document.getElementById('live-label').textContent =
    (alwaysOn && mode !== 'listening') ? 'Always On' : 'Listening'

  // Live transcript
  if (s.main_text !== prev.main_text) {
    document.getElementById('transcript').textContent = s.main_text || ''
  }

  // History entries (consumed server-side, arrive as a batch)
  for (const e of (s.log_entries || [])) appendEntry(e)

  // Video / result list
  const listKey = JSON.stringify(s.list_items)
  if (listKey !== prev.listKey) {
    const vlist = document.getElementById('vlist')
    if (s.list_items && s.list_items.length > 0) {
      document.getElementById('vlist-hdr').textContent = s.list_status || 'Results'
      const container = document.getElementById('vlist-items')
      container.innerHTML = ''
      s.list_items.forEach(item => {
        const d = document.createElement('div')
        d.className = 'vlist-item'
        d.textContent = item
        container.appendChild(d)
      })
      vlist.classList.add('visible')
    } else {
      vlist.classList.remove('visible')
    }
    prev.listKey = listKey
  }

  prev = {
    mode:        s.mode,
    status_text: s.status_text,
    main_text:   s.main_text,
    listKey:     prev.listKey,
  }
}

// ── User actions ───────────────────────────────────────────────────────────
document.getElementById('orb-wrap').addEventListener('click', () => send('toggle_hud'))
document.getElementById('clr-btn').addEventListener('click', clearFeed)
