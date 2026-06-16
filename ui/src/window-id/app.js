// Hash format: #index=3&label=Discord
const params = new URLSearchParams(location.hash.slice(1))
const idx   = params.get('index') || '?'
const label = params.get('label') || ''

document.getElementById('num').textContent   = idx
document.getElementById('label').textContent = label

requestAnimationFrame(() => {
  document.getElementById('tag').classList.add('show')
})

document.body.addEventListener('click', () => {
  if (window.eve && window.eve.dismissWindowOverlay) window.eve.dismissWindowOverlay()
})
