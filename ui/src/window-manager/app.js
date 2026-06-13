// ── State ──────────────────────────────────────────────────────────────────
let displays       = []
let hoveredId      = null
let selectedId     = null
let tilingLayouts  = {}
let selectedPreset = null

const CANVAS_W = 700
const CANVAS_H = 280

// ── Preset definitions ─────────────────────────────────────────────────────
const PRESETS = {
  'full': {
    label: 'Full',
    zones: [{ name: 'full', x_pct: 0, y_pct: 0, w_pct: 1, h_pct: 1 }],
  },
  'top-bottom': {
    label: 'Top / Bot',
    zones: [
      { name: 'top',    x_pct: 0, y_pct: 0,   w_pct: 1, h_pct: 0.5 },
      { name: 'bottom', x_pct: 0, y_pct: 0.5, w_pct: 1, h_pct: 0.5 },
    ],
  },
  'left-right': {
    label: 'Left / Right',
    zones: [
      { name: 'left',  x_pct: 0,   y_pct: 0, w_pct: 0.5, h_pct: 1 },
      { name: 'right', x_pct: 0.5, y_pct: 0, w_pct: 0.5, h_pct: 1 },
    ],
  },
  'main-right': {
    label: 'Main + Right',
    zones: [
      { name: 'main',  x_pct: 0,    y_pct: 0, w_pct: 0.67, h_pct: 1 },
      { name: 'right', x_pct: 0.67, y_pct: 0, w_pct: 0.33, h_pct: 1 },
    ],
  },
  'main-stack': {
    label: '1 + 2 Stack',
    zones: [
      { name: 'main',         x_pct: 0,   y_pct: 0,   w_pct: 0.5, h_pct: 1   },
      { name: 'top-right',    x_pct: 0.5, y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-right', x_pct: 0.5, y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
    ],
  },
  'grid-4': {
    label: 'Grid 2×2',
    zones: [
      { name: 'top-left',     x_pct: 0,   y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'top-right',    x_pct: 0.5, y_pct: 0,   w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-left',  x_pct: 0,   y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
      { name: 'bottom-right', x_pct: 0.5, y_pct: 0.5, w_pct: 0.5, h_pct: 0.5 },
    ],
  },
}

// ── Load & render ──────────────────────────────────────────────────────────
async function loadAndRender() {
  if (!window.eve) return
  try {
    ;[displays, tilingLayouts] = await Promise.all([
      window.eve.getDisplays(),
      window.eve.getTilingLayouts(),
    ])
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

  const maxCX = Math.max(...layout.map(d => d.cx + d.cw))
  const maxCY = Math.max(...layout.map(d => d.cy + d.ch))
  canvas.style.width  = Math.ceil(maxCX) + 'px'
  canvas.style.height = Math.ceil(maxCY) + 'px'

  for (const d of layout) {
    const isSelected = d.id === selectedId
    const card = document.createElement('div')
    card.className = 'monitor-card'
      + (d.isPinned   ? ' hud-pinned' : '')
      + (isSelected   ? ' selected'   : '')
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

    // Zone overlays — only on selected card with a preset chosen
    if (isSelected && selectedPreset && PRESETS[selectedPreset]) {
      for (const zone of PRESETS[selectedPreset].zones) {
        const ov = document.createElement('div')
        ov.className    = 'zone-overlay'
        ov.style.left   = (zone.x_pct * 100) + '%'
        ov.style.top    = (zone.y_pct * 100) + '%'
        ov.style.width  = (zone.w_pct * 100) + '%'
        ov.style.height = (zone.h_pct * 100) + '%'
        const lbl = document.createElement('span')
        lbl.className   = 'zone-label'
        lbl.textContent = zone.name
        ov.appendChild(lbl)
        card.appendChild(ov)
      }
    }

    card.addEventListener('mouseenter', () => { hoveredId = d.id; showInfo(d) })
    card.addEventListener('mouseleave', () => { hoveredId = null; clearInfo() })
    card.addEventListener('click',      () => selectMonitor(d))

    canvas.appendChild(card)
  }
}

// ── Monitor selection ──────────────────────────────────────────────────────
function selectMonitor(d) {
  selectedId     = d.id
  selectedPreset = null

  // Restore previously saved layout if any
  const saved = tilingLayouts.monitors && tilingLayouts.monitors[String(d.id)]
  if (saved && saved.layout) selectedPreset = saved.layout

  renderLayout()
  showLayoutPanel(d)
}

// ── Layout panel ───────────────────────────────────────────────────────────
function showLayoutPanel(d) {
  const panel = document.getElementById('layout-panel')
  panel.style.display = ''

  const label = d.label || `Display ${d.index}`
  document.getElementById('layout-panel-title').textContent = 'LAYOUT'
  document.getElementById('layout-panel-sub').textContent   = `— ${label.toUpperCase()}`

  renderPresetGrid()
}

function renderPresetGrid() {
  const grid = document.getElementById('preset-grid')
  grid.innerHTML = ''

  for (const [key, preset] of Object.entries(PRESETS)) {
    const btn = document.createElement('button')
    btn.className        = 'preset-btn' + (selectedPreset === key ? ' active' : '')
    btn.dataset.presetKey = key

    const lbl = document.createElement('div')
    lbl.className   = 'preset-label'
    lbl.textContent = preset.label

    btn.appendChild(lbl)
    btn.addEventListener('click', () => choosePreset(key))
    grid.appendChild(btn)
  }
}

function choosePreset(key) {
  selectedPreset = key
  // Toggle active class on buttons without full re-render
  document.querySelectorAll('.preset-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.presetKey === key)
  })
  // Re-render canvas to show zone overlays on the selected card
  renderLayout()
}

// ── Save layout ────────────────────────────────────────────────────────────
async function saveLayout() {
  if (!selectedId || !selectedPreset) {
    setStatus('Select a monitor and a layout preset first', 'error')
    return
  }
  const d = displays.find(x => x.id === selectedId)
  if (!d) return

  const monitorData = {
    label:      d.label || `Display ${d.index}`,
    workX:      d.workX,
    workY:      d.workY,
    workWidth:  d.workWidth,
    workHeight: d.workHeight,
    layout:     selectedPreset,
    zones:      PRESETS[selectedPreset].zones,
  }

  const result = await window.eve.setTilingLayout(d.id, monitorData)
  if (result && result.success) {
    if (!tilingLayouts.monitors) tilingLayouts.monitors = {}
    tilingLayouts.monitors[String(d.id)] = monitorData
    const label = d.label || `Display ${d.index}`
    setStatus(`Layout saved for ${label}`, 'ok')
  } else {
    setStatus('Save failed', 'error')
  }
}

// ── HUD pinning (moved to button in layout panel) ──────────────────────────
function pinHud() {
  if (!selectedId) return
  const d = displays.find(x => x.id === selectedId)
  if (!d) return

  window.eve.setOverlayDisplay(d.id)
  displays = displays.map(x => ({ ...x, isPinned: x.id === d.id }))
  renderLayout()

  const label = d.label || `Display ${d.index}`
  setStatus(`HUD pinned to ${label}`, 'ok')
}

// ── Info bar ───────────────────────────────────────────────────────────────
function showInfo(d) {
  const bar   = document.getElementById('info-bar')
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

// ── Button wiring ──────────────────────────────────────────────────────────
document.getElementById('set-hud-btn').addEventListener('click',      pinHud)
document.getElementById('save-layout-btn').addEventListener('click',  saveLayout)
document.getElementById('refresh-btn').addEventListener('click', () => {
  setStatus('Refreshing…')
  loadAndRender()
})

// ── Display change events (hotplug) ────────────────────────────────────────
if (window.eve && window.eve.onDisplaysChanged) {
  window.eve.onDisplaysChanged(() => {
    selectedId     = null
    selectedPreset = null
    document.getElementById('layout-panel').style.display = 'none'
    loadAndRender()
  })
}

// ── Init ───────────────────────────────────────────────────────────────────
loadAndRender()
