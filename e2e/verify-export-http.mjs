// Real-HTTP acceptance for the export endpoints (csv / json / xlsx + bitable-less
// paths). Talks to the deployed stack on :80 with a freshly minted internal-role
// token, so it exercises the same code path the browser download does.
//
// ASCII-only on purpose: this box has a CJK phantom-read problem.
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const OUTDIR = path.resolve(process.env.OUTDIR || 'export-out')

function log(...a) { console.log(`[${new Date().toISOString().slice(11, 23)}]`, ...a) }

function loadToken() {
  const raw = JSON.parse(fs.readFileSync(path.resolve('auth.json'), 'utf8'))
  const ls = raw.origins[0].localStorage.find(e => e.name === 'agent-eval-auth')
  return JSON.parse(ls.value).state.accessToken
}

const TOKEN = loadToken()
const H = { Authorization: `Bearer ${TOKEN}` }

async function getJson(pathname) {
  const r = await fetch(`${BASE}${pathname}`, { headers: H })
  const t = await r.text()
  if (!r.ok) throw new Error(`GET ${pathname} -> ${r.status} ${t.slice(0, 300)}`)
  return JSON.parse(t)
}

// Download a response and report the facts that prove the file is real:
// status, content-type, content-disposition, byte length, magic bytes.
async function probe(label, pathname, init = {}) {
  const url = `${BASE}${pathname}`
  // NOTE: spread init FIRST -- otherwise init.headers clobbers the merged
  // headers object and the Authorization header silently disappears on POST.
  const r = await fetch(url, { ...init, headers: { ...H, ...(init.headers || {}) } })
  const buf = Buffer.from(await r.arrayBuffer())
  const ct = r.headers.get('content-type') || '-'
  const cd = r.headers.get('content-disposition') || '-'
  const magic = buf.slice(0, 4).toString('hex')
  const ok = r.ok
  log(`${ok ? 'OK ' : 'FAIL'} ${label} status=${r.status} bytes=${buf.length} ct=${ct}`)
  log(`      disposition=${cd} magic=${magic}`)
  if (!ok) {
    log(`      body=${buf.toString('utf8').slice(0, 400)}`)
    return { ok: false, label, status: r.status, bytes: buf.length }
  }
  fs.mkdirSync(OUTDIR, { recursive: true })
  const fname = (cd.match(/filename="?([^";]+)"?/) || [null, `${label}.bin`])[1]
  const dest = path.join(OUTDIR, fname)
  fs.writeFileSync(dest, buf)
  return { ok: true, label, status: r.status, bytes: buf.length, ct, cd, magic, dest, buf }
}

function assertCsv(res) {
  const text = res.buf.toString('utf8')
  const hasBom = res.buf[0] === 0xef && res.buf[1] === 0xbb && res.buf[2] === 0xbf
  const lines = text.split(/\r?\n/).filter(Boolean)
  const header = (lines[0] || '').replace(/^﻿/, '')
  const cols = header.split(',').length
  log(`      csv bom=${hasBom} lines=${lines.length} header_cols=${cols}`)
  log(`      header=${header.slice(0, 220)}`)
  if (lines.length > 1) log(`      row1=${lines[1].slice(0, 220)}`)
  return { hasBom, lines: lines.length, cols }
}

function assertXlsx(res) {
  // A real xlsx is a zip: local file header magic 50 4b 03 04.
  const isZip = res.magic.startsWith('504b0304')
  log(`      xlsx zip_magic=${isZip}`)
  return { isZip }
}

function assertJson(res) {
  const parsed = JSON.parse(res.buf.toString('utf8'))
  const n = Array.isArray(parsed) ? parsed.length : Object.keys(parsed).length
  log(`      json ${Array.isArray(parsed) ? 'array' : 'object'} size=${n}`)
  if (Array.isArray(parsed) && parsed[0]) {
    log(`      keys=${Object.keys(parsed[0]).slice(0, 12).join(',')}`)
  }
  return { n, parsed }
}

async function main() {
  const results = []

  log('list runs')
  const runs = await getJson('/api/eval/runs?limit=20')
  const arr = Array.isArray(runs) ? runs : (runs.items || runs.runs || [])
  log(`runs visible=${arr.length}`)
  if (!arr.length) {
    log('NO RUNS VISIBLE to this tenant -- cannot verify run export paths')
  } else {
    for (const r of arr.slice(0, 5)) {
      log(`  run ${r.id} name=${r.name || '-'} status=${r.status || '-'} total=${r.total_cases ?? r.total ?? '?'}`)
    }
  }

  // Pick a run that actually has results so the CSV has data rows.
  let target = null
  for (const r of arr) {
    try {
      const det = await getJson(`/api/eval/runs/${r.id}/results?limit=1`)
      const items = Array.isArray(det) ? det : (det.items || det.results || [])
      if (items.length) { target = r; break }
    } catch (e) { log(`  results probe failed for ${r.id}: ${e.message.slice(0, 120)}`) }
  }
  log(`target run = ${target ? target.id : 'NONE'}`)

  if (target) {
    for (const fmt of ['csv', 'json', 'xlsx']) {
      const res = await probe(`run-export-${fmt}`, `/api/eval/runs/${target.id}/results/export?format=${fmt}`)
      results.push(res)
      if (res.ok && fmt === 'csv') assertCsv(res)
      if (res.ok && fmt === 'xlsx') assertXlsx(res)
      if (res.ok && fmt === 'json') assertJson(res)
    }

    // export-summary (POST, multi-run)
    const ids = arr.slice(0, 2).map(r => r.id)
    for (const fmt of ['csv', 'xlsx']) {
      const res = await probe(`export-summary-${fmt}`, '/api/eval/runs/export-summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_ids: ids, format: fmt }),
      })
      results.push(res)
      if (res.ok && fmt === 'csv') assertCsv(res)
      if (res.ok && fmt === 'xlsx') assertXlsx(res)
    }

    // export-compare (POST, needs >= 2 runs)
    if (arr.length >= 2) {
      for (const fmt of ['csv', 'json']) {
        const res = await probe(`export-compare-${fmt}`, '/api/eval/runs/export-compare', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ run_ids: ids, format: fmt }),
        })
        results.push(res)
        if (res.ok && fmt === 'csv') assertCsv(res)
        if (res.ok && fmt === 'json') assertJson(res)
      }
    } else {
      log('SKIP export-compare: fewer than 2 runs visible')
    }
  }

  // Bad-format guard should be a clean 400, not a 500.
  if (target) {
    const res = await probe('run-export-badformat', `/api/eval/runs/${target.id}/results/export?format=pdf`)
    results.push({ ...res, expectFail: true })
  }

  // Dataset case export (separate router, separate column pipeline).
  log('list datasets')
  let dsName = null
  try {
    const ds = await getJson('/api/datasets')
    const dsArr = Array.isArray(ds) ? ds : (ds.items || [])
    log(`datasets visible=${dsArr.length}`)
    dsName = dsArr[0] && (dsArr[0].name || dsArr[0].dataset_name)
  } catch (e) { log(`datasets list failed: ${e.message.slice(0, 200)}`) }
  if (dsName) {
    const res = await probe('dataset-export', `/api/datasets/${encodeURIComponent(dsName)}/export`)
    results.push(res)
    if (res.ok) assertJson(res)
  }

  log('--- SUMMARY ---')
  for (const r of results) {
    const verdict = r.expectFail ? (r.status === 400 ? 'OK(400 as expected)' : `SUSPECT(status=${r.status})`)
      : (r.ok ? 'OK' : 'FAIL')
    log(`${verdict.padEnd(22)} ${r.label} status=${r.status} bytes=${r.bytes}`)
  }
  log(`files written to ${OUTDIR}`)
}

main().catch(err => { console.error('FATAL:', err.stack || err.message); process.exit(1) })
