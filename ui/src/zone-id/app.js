// Hash format:
//   #zones=<JSON>&layout=<name>&monitorIndex=<1..N>&monitorLabel=<string>
const params = new URLSearchParams(location.hash.slice(1))

let zones = []
try { zones = JSON.parse(params.get('zones') || '[]') } catch (_) {}

const layoutName = params.get('layout')       || ''
const monIndex   = params.get('monitorIndex') || ''
const monLabel   = params.get('monitorLabel') || ''

// Tag: "M2  —  HP 24f  ·  top-bottom"
const tag = document.getElementById('layout-tag')
const tagParts = []
if (monIndex) tagParts.push(`M${monIndex}`)
if (monLabel) tagParts.push(monLabel)
if (layoutName) tagParts.push(layoutName)
if (tagParts.length) {
  tag.textContent = tagParts.join('  ·  ')   // middle dot
  requestAnimationFrame(() => tag.classList.add('show'))
}

const root = document.getElementById('zones')
for (const z of zones) {
  const el = document.createElement('div')
  el.className = 'zone'
  el.style.left   = (z.x_pct * 100) + '%'
  el.style.top    = (z.y_pct * 100) + '%'
  el.style.width  = (z.w_pct * 100) + '%'
  el.style.height = (z.h_pct * 100) + '%'

  const lbl = document.createElement('span')
  lbl.className   = 'zone-label'
  // Prefix with monitor index so two monitors with a "top" zone are
  // visually distinguishable as "M1 · TOP" / "M2 · TOP".
  lbl.textContent = monIndex ? `M${monIndex}  ·  ${z.name}` : z.name
  el.appendChild(lbl)
  root.appendChild(el)

  requestAnimationFrame(() => el.classList.add('show'))
}

// Click anywhere → dismiss this monitor's overlay.
document.body.addEventListener('click', () => {
  if (window.eve && window.eve.dismissZoneOverlay) window.eve.dismissZoneOverlay()
})
