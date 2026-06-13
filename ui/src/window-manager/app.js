// ── State ──────────────────────────────────────────────────────────────────
let displays    = []
let hoveredId   = null

const CANVAS_W  = 700
const CANVAS_H  = 280

// ── Load & render ──────────────────────────────────────────────────────────
async function loadAndRender() {
  if (!window.eve) return
  try {
    displays = await window.eve.getDisplays()
    renderLayout()
    clearStatus()
  } catch (e) {
    setStatus('Failed to load display info', 'error')
  }
}

// ── Layout calculation ─────────────────────────────────────────────────────
function buildLayout(dsps) {
  if (!dsps.length) return []

  const minX = Math.min(...dsps.map(d => d.x))
  const minY = Math.min(...dsps.map(d => d.y))
  const maxX = Math.max(...dsps.map(d => d.x + d.width))
  const maxY = Math.max(...dsps.map(d => d.y + d.height))

  const vw    = maxX - minX || 1
  const vh    = maxY - minY || 1
  const scale = Math.min(CANVAS_W / vw, CANVAS_H / vh) * 0.82

  return dsps.map(d => ({
    ...d,
    cx: (d.x - minX) * scale,
    cy: (d.y - minY) * scale,
    cw: d.width  * scale,
    ch: d.height * scale,
  }))
}

// ── Render ─────────────────────────────────────────────────────────────────
function renderLayout() {
  const canvas = document.getElementById('vm-canvas')
  canvas.innerHTML = ''

  const ph = document.getElementById('vm-placeholder')
  if (ph) ph.remove()

  if (!displays.length) {
    canvas.innerHTML = '<div class="vm-placeholder"><div class="placeholder-icon">⬡</div><div class="placeholder-text">No displays detected</div></div>'
    return
  }

  const layout = buildLayout(displays)

  // Size the container to the virtual desktop bounding box (scaled)
  const maxCX = Math.max(...layout.map(d => d.cx + d.cw))
  const maxCY = Math.max(...layout.map(d => d.cy + d.ch))
  canvas.style.width  = Math.ceil(maxCX) + 'px'
  canvas.style.height = Math.ceil(maxCY) + 'px'

  for (const d of layout) {
    const card = document.createElement('div')
    card.className = 'monitor-card' + (d.isPinned ? ' hud-pinned' : '')
    card.style.left   = d.cx + 'px'
    card.style.top    = d.cy + 'px'
    card.style.width  = d.cw + 'px'
    card.style.height = d.ch + 'px'

    const idx = document.createElement('span')
    idx.className   = 'card-index'
    idx.textContent = d.index

    const res = document.createElement('div')
    res.className   = 'card-res'
    res.textContent = `${d.width}×${d.height}`

    const meta = document.createElement('div')
    meta.className   = 'card-meta'
    meta.textContent = `${d.refreshRate} Hz · ${Math.round(d.scaleFactor * 100)}%`

    card.append(idx, res, meta)

    if (d.isPrimary) {
      const badge = document.createElement('span')
      badge.className   = 'card-badge primary-badge'
      badge.textContent = 'Primary'
      card.appendChild(badge)
    }

    if (d.isPinned) {
      const badge = document.createElement('span')
      badge.className   = 'card-badge hud-badge'
      badge.textContent = 'HUD'
      card.appendChild(badge)
    }

    card.addEventListener('mouseenter', () => showInfo(d))
    card.addEventListener('mouseleave', () => clearInfo())
    card.addEventListener('click',      () => pinHud(d))

    canvas.appendChild(card)
  }
}

// ── HUD pinning ────────────────────────────────────────────────────────────
function pinHud(d) {
  window.eve.setOverlayDisplay(d.id)

  // Optimistically update isPinned in local state and re-render
  displays = displays.map(x => ({ ...x, isPinned: x.id === d.id }))
  renderLayout()

  const label = d.label || `Display ${d.index}`
  setStatus(`HUD pinned to ${label}`, 'ok')
}

// ── Info bar ───────────────────────────────────────────────────────────────
function showInfo(d) {
  const bar = document.getElementById('info-bar')
  const label = d.label || `Display ${d.index}`

  const items = [
    ['Display',   label],
    ['Position',  `${d.x >= 0 ? '+' : ''}${d.x}, ${d.y >= 0 ? '+' : ''}${d.y}`],
    ['Work area', `${d.workWidth}×${d.workHeight}`],
    ['Scale',     `${Math.round(d.scaleFactor * 100)}%`],
    ['Refresh',   `${d.refreshRate} Hz`],
  ]

  bar.innerHTML = items.map(([k, v]) =>
    `<span class="info-item"><span class="info-label">${k}</span><span class="info-val">${v}</span></span>`
  ).join('')
}

function clearInfo() {
  document.getElementById('info-bar').innerHTML = ''
}

// ── Status ─────────────────────────────────────────────────────────────────
let statusTimer = null

function setStatus(msg, cls = '') {
  const el = document.getElementById('status-msg')
  el.textContent = msg
  el.className   = `status-msg${cls ? ' ' + cls : ''}`
  if (cls) {
    clearTimeout(statusTimer)
    statusTimer = setTimeout(() => { el.textContent = ''; el.className = 'status-msg' }, 3000)
  }
}

function clearStatus() {
  document.getElementById('status-msg').textContent = ''
}

// ── Display change events (hotplug) ────────────────────────────────────────
if (window.eve && window.eve.onDisplaysChanged) {
  window.eve.onDisplaysChanged(loadAndRender)
}

// ── Refresh button ─────────────────────────────────────────────────────────
document.getElementById('refresh-btn').addEventListener('click', () => {
  setStatus('Refreshing…')
  loadAndRender()
})

// ── Init ───────────────────────────────────────────────────────────────────
loadAndRender()
