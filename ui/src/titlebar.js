// Wires the frameless-panel header buttons. Included by each panel's index.html.
addEventListener('DOMContentLoaded', () => {
  document.querySelector('.titlebar-close')?.addEventListener('click', () => window.eve.closeSelf())
  document.querySelector('.titlebar-max')?.addEventListener('click', () => window.eve.maximizeSelf())
})
