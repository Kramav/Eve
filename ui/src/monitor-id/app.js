// Pull index/label/primary flags from the URL hash that main.js builds when
// it creates this window. Format: #index=2&label=DELL%20G2724D&primary=1
const params = new URLSearchParams(location.hash.slice(1))

const idx     = params.get('index')   || '?'
const label   = params.get('label')   || ''
const primary = params.get('primary') === '1'
const meta    = params.get('meta')    || ''

document.getElementById('num').textContent   = idx
document.getElementById('label').textContent = label
document.getElementById('meta').textContent  = primary ? 'PRIMARY' : meta

if (primary) document.getElementById('card').classList.add('primary')

// Trigger CSS transition on next frame
requestAnimationFrame(() => {
  document.getElementById('card').classList.add('show')
})
