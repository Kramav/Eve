// ── WebSocket bridge ─────────────────────────────────────────────────────────
const WS_URL = 'ws://127.0.0.1:7734'
let ws = null

function connect() {
  ws = new WebSocket(WS_URL)
  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if      (msg.type === 'state')               applyState(msg)
      else if (msg.type === 'show_directory')       window.eve.showDirectory()
      else if (msg.type === 'hide_directory')       window.eve.hideDirectory()
      else if (msg.type === 'open_app_manager')     window.eve.openAppManager()
      else if (msg.type === 'close_app_manager')    window.eve.closeAppManager()
      else if (msg.type === 'open_window_manager')  window.eve.openWindowManager()
      else if (msg.type === 'close_window_manager') window.eve.closeWindowManager()
      else if (msg.type === 'open_voice_settings')  window.eve.openVoiceSettings()
      else if (msg.type === 'close_voice_settings') window.eve.closeVoiceSettings()
      else if (msg.type === 'snap_panel')           window.eve.snapPanel(msg.panel, msg.bounds)
    } catch (_) {}
  }
  ws.onclose = () => setTimeout(connect, 500)
  ws.onerror = () => {}
}

connect()

// ── Canvas orb ───────────────────────────────────────────────────────────────
const canvas = document.getElementById('orb')
const ctx    = canvas.getContext('2d')
const W = canvas.width, H = canvas.height, CX = W / 2, CY = H / 2

const MODE_COLORS = {
  idle:       [74,  158, 255],
  listening:  [255, 140, 0  ],
  processing: [0,   212, 255],
  playing:    [0,   232, 122],
}
const ALWAYS_RGB  = [255, 51, 85]
const OFFLINE_RGB = [120, 30, 40]

let evMode     = 'idle'
let evAlwaysOn = false
let evEnabled  = true

function orbColor(alpha) {
  let rgb
  if (!evEnabled)                              rgb = OFFLINE_RGB
  else if (evAlwaysOn && evMode === 'idle')    rgb = ALWAYS_RGB
  else                                         rgb = MODE_COLORS[evMode] || MODE_COLORS.idle
  const [r, g, b] = rgb
  return alpha === undefined
    ? `rgb(${r},${g},${b})`
    : `rgba(${r},${g},${b},${alpha})`
}

function drawOrb(ts) {
  const t        = ts * 0.001
  const isActive = evEnabled && (evMode === 'listening' || evAlwaysOn)
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
  if (evEnabled && (evMode === 'listening' || evAlwaysOn)) {
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
  ctx.lineTo(x + w - r, y);     ctx.quadraticCurveTo(x + w, y,     x + w, y + r)
  ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  ctx.lineTo(x + r, y + h);     ctx.quadraticCurveTo(x,     y + h, x,     y + h - r)
  ctx.lineTo(x, y + r);         ctx.quadraticCurveTo(x,     y,     x + r, y)
  ctx.closePath()
}

requestAnimationFrame(drawOrb)

// ── Apply state ───────────────────────────────────────────────────────────────
function applyState(s) {
  evMode     = s.mode || 'idle'
  evAlwaysOn = !!s.active_listening
  evEnabled  = s.listener_enabled !== false
  const cls  = []
  if (!evEnabled) cls.push('offline')
  if (evAlwaysOn) cls.push('always-on')
  document.body.className = cls.join(' ')
}

// ── Orb click opens directory ─────────────────────────────────────────────────
document.getElementById('orb-wrap').addEventListener('click', () => window.eve.toggleDirectory())
