// Task #119 acceptance: the three dataset exports (candidate / benchmark /
// multi-turn conversation) must each carry the "Agent reply" + "Agent reply
// version" columns, populated from agent_reply_case_states.current_version_id.
//
// Real HTTP against the deployed stack on :80 with an internal-role token, so
// this exercises the exact path the browser download takes.
//
// ASCII-only source on purpose: this box has a CJK phantom-read problem, so the
// CJK header names are matched via \uXXXX escapes rather than literals.
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const OUTDIR = path.resolve(process.env.OUTDIR || 'export-out-119')

// "Agent 回复" = Agent reply ; "Agent 回复版本" = Agent reply version
const COL_REPLY = 'Agent 回复'
const COL_VERSION = 'Agent 回复版本'

function log(...a) { console.log(`[${new Date().toISOString().slice(11, 23)}]`, ...a) }

function loadToken() {
  const raw = JSON.parse(fs.readFileSync(path.resolve('auth.json'), 'utf8'))
  const ls = raw.origins[0].localStorage.find(e => e.name === 'agent-eval-auth')
  return JSON.parse(ls.value).state.accessToken
}

const TOKEN = loadToken()
const AUTH = { Authorization: `Bearer ${TOKEN}` }

async function getJson(pathname) {
  const r = await fetch(`${BASE}${pathname}`, { headers: AUTH })
  const t = await r.text()
  if (!r.ok) throw new Error(`GET ${pathname} -> ${r.status} ${t.slice(0, 300)}`)
  return JSON.parse(t)
}

// Fetch and save; report the facts that prove the bytes are real.
async function download(label, pathname) {
  const r = await fetch(`${BASE}${pathname}`, { headers: AUTH })
  const buf = Buffer.from(await r.arrayBuffer())
  const ct = r.headers.get('content-type') || '-'
  const cd = r.headers.get('content-disposition') || '-'
  log(`${r.ok ? 'HTTP OK  ' : 'HTTP FAIL'} ${label} status=${r.status} bytes=${buf.length} ct=${ct}`)
  if (!r.ok) {
    log(`      body=${buf.toString('utf8').slice(0, 300)}`)
    return { ok: false, label, status: r.status }
  }
  fs.mkdirSync(OUTDIR, { recursive: true })
  const fname = (cd.match(/filename="?([^";]+)"?/) || [null, `${label}.bin`])[1]
  fs.writeFileSync(path.join(OUTDIR, fname), buf)
  return { ok: true, label, status: r.status, buf, ct, cd, file: fname }
}

// JSON export is the machine-checkable one: keys are the display labels.
function checkJsonExport(label, res) {
  const parsed = JSON.parse(res.buf.toString('utf8'))
  const rows = Array.isArray(parsed) ? parsed : []
  if (!rows.length) {
    log(`      rows=0 -- columns unverifiable from an empty export`)
    return { label, rows: 0, hasCols: null, filled: 0 }
  }
  const keys = Object.keys(rows[0])
  const hasReply = keys.includes(COL_REPLY)
  const hasVersion = keys.includes(COL_VERSION)
  const filled = rows.filter(r => String(r[COL_REPLY] || '').length > 0).length
  log(`      rows=${rows.length} has_reply_col=${hasReply} has_version_col=${hasVersion} rows_with_reply=${filled}`)
  log(`      col_index reply=${keys.indexOf(COL_REPLY)} version=${keys.indexOf(COL_VERSION)} of ${keys.length} cols`)
  const sample = rows.find(r => String(r[COL_REPLY] || '').length > 0)
  if (sample) {
    log(`      sample id=${String(sample.ID || sample.id || '').slice(0, 36)}`)
    log(`      sample version=${JSON.stringify(sample[COL_VERSION])}`)
    log(`      sample reply[0:120]=${JSON.stringify(String(sample[COL_REPLY]).slice(0, 120))}`)
  }
  return { label, rows: rows.length, hasCols: hasReply && hasVersion, filled }
}

// CSV export: confirm the two headers survive the csv writer too.
function checkCsvExport(label, res) {
  const text = res.buf.toString('utf8').replace(/^﻿/, '')
  const header = text.split(/\r?\n/)[0] || ''
  const cols = header.split(',')
  const hasReply = cols.includes(COL_REPLY)
  const hasVersion = cols.includes(COL_VERSION)
  const dataLines = text.split(/\r?\n/).filter(Boolean).length - 1
  log(`      csv cols=${cols.length} data_lines=${dataLines} has_reply_col=${hasReply} has_version_col=${hasVersion}`)
  log(`      csv header=${header.slice(0, 260)}`)
  return { label, hasCols: hasReply && hasVersion, rows: dataLines }
}

function checkXlsx(label, res) {
  const isZip = res.buf.slice(0, 4).toString('hex') === '504b0304'
  log(`      xlsx zip_magic=${isZip} file=${res.file}`)
  return { label, hasCols: null, xlsxOk: isZip }
}

async function main() {
  const verdicts = []

  // ---- 1. candidate dataset export --------------------------------------
  log('=== candidate export ===')
  // Prefer a dataset_name that actually has agent replies. Discover candidates
  // first, then check the agent-reply case-state listing for coverage.
  let candQuery = ''
  try {
    const list = await getJson('/api/candidates?page=1&page_size=5')
    const items = list.items || []
    log(`candidates total=${list.total ?? '?'} sampled=${items.length}`)
    if (items[0]) log(`  first candidate id=${items[0].id} dataset=${items[0].dataset_name || '-'}`)
  } catch (e) { log(`candidates list failed: ${e.message.slice(0, 200)}`) }

  for (const fmt of ['json', 'csv', 'xlsx']) {
    const res = await download(`candidates-${fmt}`, `/api/candidates/export?format=${fmt}${candQuery}`)
    if (!res.ok) { verdicts.push({ label: res.label, fail: true, status: res.status }); continue }
    if (fmt === 'json') verdicts.push(checkJsonExport(res.label, res))
    if (fmt === 'csv') verdicts.push(checkCsvExport(res.label, res))
    if (fmt === 'xlsx') verdicts.push(checkXlsx(res.label, res))
  }

  // ---- 2. benchmark cases export ---------------------------------------
  log('=== benchmark export ===')
  let projectId = null
  try {
    const projects = await getJson('/api/projects')
    const parr = Array.isArray(projects) ? projects : (projects.items || [])
    log(`projects visible=${parr.length}`)
    // Pick the first project that actually has active benchmark cases.
    for (const p of parr) {
      try {
        const cases = await getJson(`/api/benchmark/${p.id}/cases?page=1&page_size=1`)
        const total = cases.total ?? (cases.items || []).length
        log(`  project ${p.id} name=${p.name || '-'} benchmark_cases=${total}`)
        if (total > 0 && !projectId) projectId = p.id
      } catch (e) { log(`  cases probe failed for ${p.id}: ${e.message.slice(0, 120)}`) }
    }
  } catch (e) { log(`projects list failed: ${e.message.slice(0, 200)}`) }
  log(`benchmark target project = ${projectId || 'NONE'}`)

  if (projectId) {
    for (const fmt of ['json', 'csv', 'xlsx']) {
      const res = await download(`benchmark-${fmt}`, `/api/benchmark/${projectId}/cases/export?format=${fmt}`)
      if (!res.ok) { verdicts.push({ label: res.label, fail: true, status: res.status }); continue }
      if (fmt === 'json') verdicts.push(checkJsonExport(res.label, res))
      if (fmt === 'csv') verdicts.push(checkCsvExport(res.label, res))
      if (fmt === 'xlsx') verdicts.push(checkXlsx(res.label, res))
    }
  } else {
    log('SKIP benchmark export: no project with active cases')
  }

  // ---- 3. multi-turn conversation export -------------------------------
  log('=== conversation export ===')
  let convDs = null
  try {
    const ds = await getJson('/api/datasets')
    const dsArr = Array.isArray(ds) ? ds : (ds.items || [])
    log(`datasets visible=${dsArr.length}`)
    // Probe each dataset's conversation export (json, cheap) and keep the first
    // one that yields rows -- that is the only way to know it holds multi-turn
    // cases without duplicating the backend's isConversation filter here.
    for (const d of dsArr) {
      const name = d.name || d.dataset_name
      if (!name) continue
      const r = await fetch(
        `${BASE}/api/datasets/${encodeURIComponent(name)}/cases/export-conversations?format=json`,
        { headers: AUTH },
      )
      if (!r.ok) { log(`  ${name}: status=${r.status}`); continue }
      const rows = JSON.parse(await r.text())
      const n = Array.isArray(rows) ? rows.length : 0
      const withReply = Array.isArray(rows)
        ? rows.filter(x => String(x[COL_REPLY] || '').length > 0).length : 0
      log(`  ${name}: conversation_rows=${n} with_reply=${withReply}`)
      // Prefer a dataset that has replies; fall back to any non-empty one.
      if (withReply > 0) { convDs = name; break }
      if (n > 0 && !convDs) convDs = name
    }
  } catch (e) { log(`datasets list failed: ${e.message.slice(0, 200)}`) }
  log(`conversation target dataset = ${convDs || 'NONE'}`)

  if (convDs) {
    for (const fmt of ['json', 'csv', 'xlsx']) {
      const res = await download(
        `conversations-${fmt}`,
        `/api/datasets/${encodeURIComponent(convDs)}/cases/export-conversations?format=${fmt}`,
      )
      if (!res.ok) { verdicts.push({ label: res.label, fail: true, status: res.status }); continue }
      if (fmt === 'json') verdicts.push(checkJsonExport(res.label, res))
      if (fmt === 'csv') verdicts.push(checkCsvExport(res.label, res))
      if (fmt === 'xlsx') verdicts.push(checkXlsx(res.label, res))
    }
  } else {
    log('SKIP conversation export: no dataset with multi-turn cases')
  }

  log('--- SUMMARY (task #119) ---')
  for (const v of verdicts) {
    if (v.fail) { log(`FAIL          ${v.label} status=${v.status}`); continue }
    if (v.hasCols === true) log(`COLS OK       ${v.label} rows=${v.rows} rows_with_reply=${v.filled ?? '-'}`)
    else if (v.hasCols === null) log(`NOT CHECKED   ${v.label} (${v.xlsxOk !== undefined ? `xlsx zip=${v.xlsxOk}` : `rows=${v.rows}`})`)
    else log(`COLS MISSING  ${v.label} rows=${v.rows}`)
  }
  log(`files written to ${OUTDIR}`)
}

main().catch(err => { console.error('FATAL:', err.stack || err.message); process.exit(1) })
