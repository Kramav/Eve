// Tauri bridge shim — the migration replacement for Electron's preload.js.
//
// Under ELECTRON, contextBridge already installed window.eve before this runs,
// so we detect that and do nothing (Electron path untouched). Under TAURI there
// is no preload, so window.eve is absent and we install a Tauri-backed version.
//
// Phase 1 (orb only): every method is a safe no-op that logs — enough that the
// orb renders, connects to ws://127.0.0.1:7734, and never throws on a click or
// an incoming WS message. Real per-window implementations (open panels, window
// geometry, file I/O) replace these stubs phase by phase. See
// project_tauri_migration.md.
(function () {
  if (window.eve) return                     // Electron preload already provided it

  const stub = (name) => (...args) =>
    console.warn(`[eve-shim] ${name}() not implemented yet`, args)

  // Proxy so any of preload.js's ~60 methods resolves to a no-op until wired,
  // instead of throwing ReferenceError. ponytail: a Proxy beats hand-listing 60
  // stubs; each gets a real impl when its phase lands.
  window.eve = new Proxy({}, {
    get: (_target, prop) => stub(String(prop)),
  })

  window.__EVE_TAURI__ = true               // lets pages branch on the runtime if needed
})();
