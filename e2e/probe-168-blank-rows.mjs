// #168 probe: find eval runs that contain result rows with a blank agent reply.
//
// Earlier version of this probe reported runs_total=null / candidates=0 because
// it unwrapped the list response wrong. The real shape is confirmed from
// result-168-raw.json: { items, total, page, page_size } and 108 runs exist.
//
// Field names are taken from EvalResultRow in frontend/src/types/index.ts:
//   actual_output : string | null   <- the A-side (or single-mode) reply
//   comparison    : object | null   <- B-side payload for comparative runs
// Nothing here is guessed; anything unexpected is dumped raw for inspection.
import fs from 'node:fs'
import path from 'node:path'

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const AUTH = path.join(HERE, 'auth.json')
const OUT = path.join(HERE, 'result-168-probe.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const API = `${BASE}/api`

// Pull whatever looks like a JWT out of the saved storage state, whichever key
// the app happens to use. Avoids hardcoding a key name that may have drifted.
function tokenFromAuth() {
  const st = JSON.parse(fs.readFileSync(AUTH, 'utf8'))
  const cands = []
  for (const origin of st.origins || []) {
    for (const { name, value } of origin.localStorage || []) {
      if (typeof value !== 'string') continue
      if (/^ey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./.test(value)) cands.push({ name, value })
      else {
        // token may be nested inside a JSON blob (e.g. a zustand store)
        try {
          const found = JSON.stringify(JSON.parse(value)).match(/ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+/)
          if (found) cands.push({ name, value: found[0] })
        } catch { /* not JSON, ignore */ }
      }
    }
  }
  if (!cands.length) throw new Error('no JWT found in auth.json')
  return cands[0]
}

const tok = tokenFromAuth()
const H = { Authorization: `Bearer ${tok.value}`, 'Content-Type': 'application/json' }

async function getJson(url) {
  const res = await fetch(url, { headers: H })
  const text = await res.text()
  let body = null
  try { body = JSON.parse(text) } catch { /* keep null */ }
  return { status: res.status, body, text }
}

// A reply counts as blank when the field is missing, null, or whitespace-only.
const isBlank = v => v === null || v === undefined || (typeof v === 'string' && v.trim() === '')

const out = {
  base: BASE,
  token_key: tok.name,
  runs_total: null,
  runs_listed: 0,
  runs_scanned: 0,
  runs_failed: [],
  rows_scanned: 0,
  candidates: [],
}

// 1. list every run (page_size is capped server-side, so page through).
const runs = []
let page = 1
while (true) {
  const r = await getJson(`${API}/eval/runs?page=${page}&page_size=100`)
  if (r.status !== 200 || !r.body) {
    out.runs_failed.push({ stage: 'list', page, status: r.status, snippet: r.text.slice(0, 300) })
    break
  }
  if (out.runs_total === null) out.runs_total = r.body.total ?? null
  const items = r.body.items || []
  runs.push(...items)
  if (items.length === 0 || runs.length >= (r.body.total ?? runs.length)) break
  page += 1
  if (page > 20) break // safety stop
}
out.runs_listed = runs.length

// 2. for each run, page through its result rows and flag blank replies.
for (const run of runs) {
  let rpage = 1
  let runRows = 0
  const blanks = []
  let ok = true
  while (true) {
    const r = await getJson(`${API}/eval/runs/${run.id}/results?page=${rpage}&page_size=100`)
    if (r.status !== 200 || !r.body) {
      out.runs_failed.push({ stage: 'results', run_id: run.id, status: r.status, snippet: r.text.slice(0, 200) })
      ok = false
      break
    }
    const items = r.body.items || []
    for (const row of items) {
      runRows += 1
      out.rows_scanned += 1
      const aBlank = isBlank(row.actual_output)
      // comparative runs keep the B reply inside `comparison`; the exact inner
      // key is not asserted, so look at every string leaf one level down.
      let bBlank = null
      if (row.comparison && typeof row.comparison === 'object') {
        const bKeys = Object.keys(row.comparison).filter(k => /output_b|reply_b|_b$/.test(k))
        bBlank = bKeys.length ? bKeys.every(k => isBlank(row.comparison[k])) : null
        if (!bKeys.length) out.comparison_keys_sample ||= Object.keys(row.comparison)
      }
      if (aBlank || bBlank === true) {
        blanks.push({
          result_id: row.id,
          status: row.status,
          execution_status: row.execution_status ?? null,
          evaluation_status: row.evaluation_status ?? null,
          a_blank: aBlank,
          b_blank: bBlank,
          error_message: row.error_message ?? null,
          scores_keys: Object.keys(row.scores || {}),
          question_preview: typeof row.question === 'string' ? row.question.slice(0, 60) : null,
        })
      }
    }
    if (items.length === 0 || runRows >= (r.body.total ?? runRows)) break
    rpage += 1
    if (rpage > 20) break
  }
  if (ok) out.runs_scanned += 1
  if (blanks.length) {
    out.candidates.push({
      run_id: run.id,
      run_name: run.langfuse_run_name ?? null,
      run_status: run.status,
      eval_mode: run.eval_mode ?? null,
      rows_total: runRows,
      blank_count: blanks.length,
      blanks: blanks.slice(0, 10),
    })
  }
}

out.candidates.sort((a, b) => b.blank_count - a.blank_count)
fs.writeFileSync(OUT, JSON.stringify(out, null, 2), 'utf8')
console.log(`runs_total=${out.runs_total} runs_listed=${out.runs_listed} runs_scanned=${out.runs_scanned} rows_scanned=${out.rows_scanned}`)
console.log(`list/results failures=${out.runs_failed.length}`)
console.log(`candidate_runs=${out.candidates.length}`)
for (const c of out.candidates.slice(0, 15)) {
  console.log(`  ${c.run_id}  ${c.blank_count}/${c.rows_total} blank  mode=${c.eval_mode}  status=${c.run_status}  ${c.run_name ?? ''}`)
}
console.log(`wrote ${OUT}`)
