// ── State ─────────────────────────────────────────────────────────────────────
const stores = {
  commands: { data: [], dirty: new Set(), saveTimer: null },
  apps:     { data: [], dirty: new Set(), saveTimer: null },
  aliases:  { data: [], dirty: new Set(), saveTimer: null },
}

const IPC = {
  commands: { get: 'ceGetCommands', set: 'ceSetCommands' },
  apps:     { get: 'ceGetApps',     set: 'ceSetApps'     },
  aliases:  { get: 'ceGetAliases',  set: 'ceSetAliases'  },
}

let builtins = []   // [{key, label}]

// ── Status indicator ─────────────────────────────────────────────────────────
let _statusTimer = null
function setStatus(msg, cls = '') {
  const el = document.getElementById('status-msg')
  el.textContent = msg
  el.className = `status-msg${cls ? ' ' + cls : ''}`
  clearTimeout(_statusTimer)
  if (cls) {
    _statusTimer = setTimeout(() => { el.textContent = ''; el.className = 'status-msg' }, 2500)
  }
}

// ── Tab switching ────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.tab
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn))
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.dataset.tab === key))
    if (key === 'raw') loadRaw()
    if (key === 'learned') learnedRefresh()
  })
})

// ── Reference panel toggle ───────────────────────────────────────────────────
const refToggle = document.getElementById('ref-toggle')
const refBody   = document.getElementById('ref-body')
refToggle.addEventListener('click', () => {
  const open = refBody.classList.toggle('open')
  refToggle.setAttribute('aria-expanded', open ? 'true' : 'false')
})

// ── Debounced save ───────────────────────────────────────────────────────────
async function save(key) {
  const store = stores[key]
  // Skip rows with empty primary field — but keep them in UI state
  const cleaned = (store.data || []).filter(row => Array.isArray(row) && row[0] && row[0].trim())
  const result  = await window.eve[IPC[key].set](cleaned)
  if (result && result.ok) {
    store.dirty.clear()
    document.querySelectorAll(`#rows-${key} .row`).forEach(r => {
      r.classList.remove('dirty')
      r.classList.add('saved')
      r.querySelector('.row-status').textContent = 'SAVED'
      setTimeout(() => r.classList.remove('saved'), 1500)
    })
    setStatus('Saved', 'ok')
  } else {
    setStatus('Save failed: ' + (result?.error || 'unknown'), 'error')
  }
}

function scheduleSave(key) {
  setStatus('Editing…')
  clearTimeout(stores[key].saveTimer)
  stores[key].saveTimer = setTimeout(() => save(key), 500)
}

function markDirty(key, idx) {
  stores[key].dirty.add(idx)
  const row = document.querySelector(`#rows-${key} .row[data-idx="${idx}"]`)
  if (row) {
    row.classList.add('dirty'); row.classList.remove('saved')
    row.querySelector('.row-status').textContent = 'EDITING'
  }
  scheduleSave(key)
}

// ── Validation ──────────────────────────────────────────────────────────────
function validate(key) {
  const data = stores[key].data
  const seen = new Map()
  for (let i = 0; i < data.length; i++) {
    const row = document.querySelector(`#rows-${key} .row[data-idx="${i}"]`)
    if (!row) continue
    row.classList.remove('invalid', 'dup')
    const phrase = (data[i][0] || '').trim().toLowerCase()
    const value  = (data[i][1] || '').trim()
    if (!phrase || !value) {
      // empty rows are tolerated visually but flagged
      if (data[i][0] !== '' || data[i][1] !== '') {
        row.classList.add('invalid')
        row.querySelector('.row-status').textContent = 'INVALID'
      }
    } else if (seen.has(phrase)) {
      row.classList.add('dup')
      row.querySelector('.row-status').textContent = 'DUPLICATE'
    } else {
      seen.set(phrase, i)
    }
  }
}

// ── Row rendering ────────────────────────────────────────────────────────────
function createInput(value, placeholder, onInput) {
  const el = document.createElement('input')
  el.type = 'text'
  el.value = value || ''
  el.placeholder = placeholder
  el.spellcheck = false
  el.addEventListener('input', e => onInput(e.target.value))
  return el
}

function createField(labelText, inputEl) {
  const wrap = document.createElement('div')
  wrap.className = 'field'
  const lbl = document.createElement('label')
  lbl.textContent = labelText
  wrap.append(lbl, inputEl)
  return wrap
}

function createDeleteBtn(key, idx) {
  const btn = document.createElement('button')
  btn.className = 'del-btn'
  btn.title = 'Delete'
  btn.textContent = '✕'
  btn.addEventListener('click', () => {
    stores[key].data.splice(idx, 1)
    render(key)
    scheduleSave(key)
  })
  return btn
}

function createStatusBadge() {
  const s = document.createElement('span')
  s.className = 'row-status'
  s.textContent = 'SAVED'
  return s
}

function renderCommandsRow(idx, row) {
  const r = document.createElement('div')
  r.className = 'row saved'
  r.dataset.idx = idx
  const phraseInput = createInput(row[0], 'open music',
    v => { row[0] = v; markDirty('commands', idx); validate('commands') })
  const cmdInput = createInput(row[1], 'spotify  /  notepad  /  code C:\\path',
    v => { row[1] = v; markDirty('commands', idx) })
  const action = document.createElement('div')
  action.className = 'row-action'
  action.appendChild(createDeleteBtn('commands', idx))
  r.append(
    createField('WHEN I SAY', phraseInput),
    createField('RUNS THIS',  cmdInput),
    createStatusBadge(),
    action,
  )
  return r
}

function renderAppsRow(idx, row) {
  const r = document.createElement('div')
  r.className = 'row saved'
  r.dataset.idx = idx
  const nameInput = createInput(row[0], 'figma',
    v => { row[0] = v; markDirty('apps', idx); validate('apps') })
  const cmdInput  = createInput(row[1], 'C:\\Path\\App.exe  or  spotify',
    v => { row[1] = v; markDirty('apps', idx) })
  const action = document.createElement('div')
  action.className = 'row-action'

  const browse = document.createElement('button')
  browse.className = 'browse-btn'
  browse.title = 'Browse for an executable'
  browse.textContent = 'Browse'
  browse.addEventListener('click', async () => {
    const p = await window.eve.ceBrowseExe()
    if (p) {
      row[1] = p
      cmdInput.value = p
      markDirty('apps', idx)
    }
  })
  action.appendChild(browse)
  action.appendChild(createDeleteBtn('apps', idx))

  r.append(
    createField('APP NAME (say this)', nameInput),
    createField('OPENS THIS',          cmdInput),
    createStatusBadge(),
    action,
  )
  return r
}

function renderAliasesRow(idx, row) {
  const r = document.createElement('div')
  r.className = 'row saved'
  r.dataset.idx = idx
  const phraseInput = createInput(row[0], 'tick tock',
    v => { row[0] = v; markDirty('aliases', idx); validate('aliases') })

  const sel = document.createElement('select')
  for (const b of builtins) {
    const opt = document.createElement('option')
    opt.value = b.key
    opt.textContent = b.label
    if (b.key === row[1]) opt.selected = true
    sel.appendChild(opt)
  }
  sel.addEventListener('change', () => { row[1] = sel.value; markDirty('aliases', idx) })

  const action = document.createElement('div')
  action.className = 'row-action'
  action.appendChild(createDeleteBtn('aliases', idx))

  r.append(
    createField('WHEN I SAY',  phraseInput),
    createField('RUNS BUILT-IN', sel),
    createStatusBadge(),
    action,
  )
  return r
}

const RENDERERS = {
  commands: renderCommandsRow,
  apps:     renderAppsRow,
  aliases:  renderAliasesRow,
}

function render(key) {
  const list = document.getElementById(`rows-${key}`)
  list.innerHTML = ''
  const data = stores[key].data
  if (!data.length) {
    const empty = document.createElement('div')
    empty.className = 'rows-empty'
    const word = key === 'commands' ? 'commands' : key === 'apps' ? 'apps' : 'aliases'
    empty.textContent = `No ${word} yet — click + Add ${word.slice(0, -1)} below`
    list.appendChild(empty)
    return
  }
  data.forEach((row, i) => list.appendChild(RENDERERS[key](i, row)))
  validate(key)
}

// ── Add row ──────────────────────────────────────────────────────────────────
document.querySelectorAll('.add-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.add
    const blanks = {
      commands: ['', ''],
      apps:     ['', ''],
      aliases:  ['', builtins[0]?.key || 'get_time'],
    }
    stores[key].data.push([...blanks[key]])
    render(key)
    // Focus first input of new row
    const inputs = document.querySelectorAll(`#rows-${key} .row:last-child input`)
    if (inputs[0]) inputs[0].focus()
    scheduleSave(key)
  })
})

// ── Initial load ─────────────────────────────────────────────────────────────
async function loadAll() {
  builtins = await window.eve.ceGetBuiltins()
  stores.commands.data = await window.eve.ceGetCommands()
  stores.apps.data     = await window.eve.ceGetApps()
  stores.aliases.data  = await window.eve.ceGetAliases()
  render('commands'); render('apps'); render('aliases')
  setStatus('Loaded', 'ok')
}

document.getElementById('reload-btn').addEventListener('click', async () => {
  setStatus('Reloading…')
  await loadAll()
  if (document.querySelector('.tab.active').dataset.tab === 'raw') loadRaw()
})

// External edits: refresh when main.js notifies via commands-changed
if (window.eve.onCommandsChanged) {
  window.eve.onCommandsChanged(async (_, _info) => {
    // Don't clobber in-flight edits; just refresh if no row is dirty
    const anyDirty = Object.values(stores).some(s => s.dirty.size > 0)
    if (!anyDirty) await loadAll()
  })
}

loadAll()

// ── Raw JSON tab ─────────────────────────────────────────────────────────────
const rawSelect   = document.getElementById('raw-file')
const rawHighlight = document.getElementById('raw-highlight')
const rawText     = document.getElementById('raw-text')
const rawStatus   = document.getElementById('raw-status')
const rawSave     = document.getElementById('raw-save')

// Tokenize JSON into spans. Robust enough for our small files.
function highlightJSON(src) {
  // Order matters: longest first
  const re = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\b(?:true|false)\b)|(\bnull\b)|([{}\[\],:])|(\s+)/g
  let out = ''
  let m
  let lastIndex = 0
  while ((m = re.exec(src)) !== null) {
    if (m.index > lastIndex) out += escapeHtml(src.slice(lastIndex, m.index))
    if      (m[1]) out += `<span class="tok-key">${escapeHtml(m[1])}</span>`
    else if (m[2]) out += `<span class="tok-str">${escapeHtml(m[2])}</span>`
    else if (m[3]) out += `<span class="tok-num">${escapeHtml(m[3])}</span>`
    else if (m[4]) out += `<span class="tok-bool">${escapeHtml(m[4])}</span>`
    else if (m[5]) out += `<span class="tok-null">${escapeHtml(m[5])}</span>`
    else if (m[6]) out += `<span class="tok-punct">${escapeHtml(m[6])}</span>`
    else if (m[7]) out += escapeHtml(m[7])
    lastIndex = re.lastIndex
  }
  if (lastIndex < src.length) out += escapeHtml(src.slice(lastIndex))
  return out + '\n'   // trailing newline ensures last line renders
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
}

function updateRawHighlight() {
  rawHighlight.innerHTML = highlightJSON(rawText.value)
}

function setRawStatus(msg, cls = '') {
  rawStatus.textContent = msg
  rawStatus.className = `raw-status${cls ? ' ' + cls : ''}`
}

function validateRawAndUpdateSave() {
  try {
    const v = JSON.parse(rawText.value || '[]')
    if (!Array.isArray(v)) { setRawStatus('Top level must be an array', 'error'); rawSave.disabled = true; return false }
    setRawStatus('Valid JSON', 'ok')
    rawSave.disabled = false
    return true
  } catch (e) {
    setRawStatus(`Invalid JSON: ${e.message}`, 'error')
    rawSave.disabled = true
    return false
  }
}

async function loadRaw() {
  setRawStatus('Loading…')
  const text = await window.eve.ceGetRaw(rawSelect.value)
  rawText.value = text || '[]'
  updateRawHighlight()
  validateRawAndUpdateSave()
}

rawSelect.addEventListener('change', loadRaw)

rawText.addEventListener('input', () => {
  updateRawHighlight()
  validateRawAndUpdateSave()
})

// Sync scroll between textarea and highlight pre
rawText.addEventListener('scroll', () => {
  rawHighlight.scrollTop  = rawText.scrollTop
  rawHighlight.scrollLeft = rawText.scrollLeft
})

// Tab key inserts 2 spaces in raw editor
rawText.addEventListener('keydown', e => {
  if (e.key === 'Tab') {
    e.preventDefault()
    const start = rawText.selectionStart, end = rawText.selectionEnd
    rawText.value = rawText.value.slice(0, start) + '  ' + rawText.value.slice(end)
    rawText.selectionStart = rawText.selectionEnd = start + 2
    updateRawHighlight(); validateRawAndUpdateSave()
  }
})

rawSave.addEventListener('click', async () => {
  if (!validateRawAndUpdateSave()) return
  setRawStatus('Saving…')
  const r = await window.eve.ceSetRaw(rawSelect.value, rawText.value)
  if (r && r.ok) {
    setRawStatus('Saved', 'ok')
    // Also refresh the in-memory store for that file
    const key = rawSelect.value
    if (stores[key]) {
      stores[key].data = await window.eve[IPC[key].get]()
      render(key)
    }
  } else {
    setRawStatus('Save failed: ' + (r?.error || 'unknown'), 'error')
  }
})

// Save on Ctrl/Cmd+S in any tab
window.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    const tabKey = document.querySelector('.tab.active').dataset.tab
    if (tabKey === 'raw') rawSave.click()
    else if (stores[tabKey]) save(tabKey)
  }
})

// ── Learned intents tab (auto-updating store; view + delete over WS) ────────
// Data lives in learned_intents.json / imported_intents.json (both gitignored,
// owned by core/intent_learning.py). Read-only here except delete — the list
// grows by itself from verified LLM-fallback successes.
const LEARNED_WS_URL = 'ws://127.0.0.1:7734'
let _learnedWs = null

function learnedConnect() {
  _learnedWs = new WebSocket(LEARNED_WS_URL)
  _learnedWs.onopen    = () => learnedRefresh()
  _learnedWs.onclose   = () => setTimeout(learnedConnect, 1000)
  _learnedWs.onerror   = () => {}
  _learnedWs.onmessage = e => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'intents_list') renderLearned(msg)
    } catch (_) {}
  }
}

function learnedSend(action, data = {}) {
  if (_learnedWs && _learnedWs.readyState === WebSocket.OPEN)
    _learnedWs.send(JSON.stringify({ action, ...data }))
}

function learnedRefresh() { learnedSend('intents:list') }

function renderLearned(msg) {
  const root = document.getElementById('rows-learned')
  const rows = [...(msg.imported || []), ...(msg.personal || [])]
  root.innerHTML = ''
  if (!rows.length) {
    root.innerHTML = '<div class="learned-empty">Nothing learned yet — phrases Eve ' +
      'resolves through the LLM fallback (and verifies worked) will appear here.</div>'
    return
  }
  const escL = s => String(s).replace(/[&<>"]/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  for (const r of rows) {
    const row = document.createElement('div')
    row.className = 'row learned-row'
    const argstr = Object.entries(r.args || {})
      .filter(([, v]) => v !== null && v !== '')
      .map(([k, v]) => `${escL(k)}: ${escL(v)}`).join(', ')
    const badges = [
      r.store === 'imported' ? `<span class="lbadge imported" title="From ${escL(r.origin || 'an imported pack')}">imported</span>` : '',
      r.generalizes ? '<span class="lbadge gen" title="Trusted — also matches the same phrasing with different targets">generalizes</span>'
                    : '<span class="lbadge" title="Only this exact phrase">exact</span>',
      r.destructive ? '<span class="lbadge danger" title="Destructive class — never auto-served">held</span>' : '',
    ].join('')
    row.innerHTML = `
      <div class="learned-main">
        <div class="learned-phrase">“${escL(r.phrase)}”</div>
        <div class="learned-action">→ ${escL(r.tool)}(${argstr})</div>
      </div>
      <div class="learned-meta">${badges}
        <span class="learned-ev" title="verified successes / failures — confidence">${r.s}✓ ${r.f}✗ · ${Math.round((r.confidence || 0) * 100)}%</span>
      </div>`
    const del = document.createElement('button')
    del.className = 'del-btn'
    del.title = 'Forget this mapping'
    del.textContent = '✕'
    del.addEventListener('click', () => {
      learnedSend('intents:delete', { store: r.store, tool: r.tool, phrase: r.phrase })
      setStatus('Forgotten.', 'ok')
    })
    row.appendChild(del)
    root.appendChild(row)
  }
}

learnedConnect()
