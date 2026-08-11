// Independent expectation generator for #173 cross-run cost section on the
// COMPARE page (?ids=A,B). Reads each run's /results payload and the unit
// prices, redoes the cost arithmetic here (NOT via the frontend helpers), and
// emits expect_173_crossrun.json for verify-173-crossrun-cost.mjs.
//
// Why separate: if the verifier computed expectations from the same code the
// page uses, a wrong formula would agree with itself and pass.
//
// Pure ASCII on purpose (repo hazard: tools return phantom content for files
// holding literal CJK). Every CJK needle is a \u escape.

import { readFileSync, writeFileSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const DIR = 'D:\\program\\agent_eval\\e2e\\'
const OUT = DIR + 'expect_173_crossrun.json'
const PROBE = JSON.parse(readFileSync(DIR + 'probe-user.json', 'utf8'))

const PRICE_UNIT = 1_000_000

// The two runs from the user's screenshots: blue sonnet-4.6, green kimi-k2.6.
// rgbTriple is what the page's RUN_RGB_VARS[i] resolves to in the light theme:
//   index 0 -> --accent   0 122 255 (systemBlue)
//   index 1 -> --positive 52 199 89 (systemGreen)
const RUNS = [
  { id: '0c48de37-41df-4638-8451-dc5a5d43ed57', model: 'sonnet-4.6', rgb: [0, 122, 255],
    price: { inputHit: 0.3, inputMiss: 3.0, output: 15.0 } },
  { id: '37704110-f0e0-492b-8fe0-f2e8f09345ae', model: 'kimi-k2.6', rgb: [52, 199, 89],
    price: { inputHit: 0.15, inputMiss: 0.6, output: 2.5 } },
]

// Mirrors lib/pricing.ts formatCost.
const fmtCost = (v, currency = '$') => {
  const abs = Math.abs(v)
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6
  return `${currency}${v.toFixed(digits)}`
}

async function login() {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: PROBE.username, password: PROBE.password }),
  })
  if (!r.ok) throw new Error(`login -> ${r.status} ${(await r.text()).slice(0, 200)}`)
  return (await r.json()).access_token
}

// Pull EVERY result row for a run (paginated). Mirrors evaluation.ts getAllResults.
async function allResults(token, runId) {
  const rows = []
  let page = 1
  const size = 200
  for (;;) {
    const r = await fetch(`${BASE}/api/eval/runs/${runId}/results?page=${page}&page_size=${size}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!r.ok) throw new Error(`results ${runId} p${page} -> ${r.status}`)
    const j = await r.json()
    const items = j.items || []
    rows.push(...items)
    if (rows.length >= (j.total ?? rows.length) || items.length === 0) break
    page += 1
  }
  return rows
}

// Mirrors CostPanel usageOfA + pricing.ts splitBillableTokens EXACTLY, against
// the real row field names (probed live): prompt_tokens / completion_tokens /
// cache_read_tokens / cache_creation_tokens. Reimplemented here, not imported.
//   hit  = cache_read_tokens
//   miss = prompt_tokens != null ? max(0, prompt - hit) : (cache_creation ?? 0)
//   out  = completion_tokens
const finite = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
function usageOf(row) {
  const prompt = finite(row.prompt_tokens)
  const hit = finite(row.cache_read_tokens) ?? 0
  const write = finite(row.cache_creation_tokens) ?? 0
  const out = finite(row.completion_tokens) ?? 0
  const miss = prompt == null ? write : Math.max(0, prompt - hit)
  const hasAny = prompt != null || finite(row.completion_tokens) != null
    || finite(row.cache_read_tokens) != null || finite(row.cache_creation_tokens) != null
  return { hitInputTokens: hit, missInputTokens: miss, outputTokens: out, hasAny }
}

function aggregate(rows, price) {
  let hit = 0, miss = 0, out = 0, n = 0
  for (const row of rows) {
    const u = usageOf(row)
    if (!u.hasAny) continue
    hit += u.hitInputTokens
    miss += u.missInputTokens
    out += u.outputTokens
    n += 1
  }
  const tiers = [
    { tok: hit, unit: price.inputHit },
    { tok: miss, unit: price.inputMiss },
    { tok: out, unit: price.output },
  ]
  const costs = tiers.map(t => (t.tok / PRICE_UNIT) * t.unit)
  const total = costs.reduce((s, v) => s + v, 0)
  return { n, total, mean: n > 0 ? total / n : null, tiers, costs }
}

const token = await login()
console.log('LOGIN ok')

const out = { runs: [] }
for (const run of RUNS) {
  const rows = await allResults(token, run.id)
  const agg = aggregate(rows, run.price)
  console.log(`${run.id.slice(0, 6)} ${run.model}: rows=${rows.length} scored=${agg.n} total=${fmtCost(agg.total)} mean=${agg.mean != null ? fmtCost(agg.mean) : '-'}`)
  out.runs.push({
    id: run.id,
    idShort: run.id.slice(0, 6),
    model: run.model,
    rgb: run.rgb,
    price: run.price,
    n: agg.n,
    total: agg.total,
    totalStr: fmtCost(agg.total),
    mean: agg.mean,
    meanStr: agg.mean != null ? fmtCost(agg.mean) : null,
    tierTokens: agg.tiers.map(t => t.tok),
    tierCosts: agg.costs,
  })
}

// Same-scale bars: total bar widths scale off the larger total; mean bar off larger mean.
const totals = out.runs.map(r => r.total)
const means = out.runs.map(r => r.mean).filter(m => m != null)
out.totalMax = Math.max(...totals)
out.meanMax = means.length ? Math.max(...means) : null
out.runs.forEach(r => {
  r.totalWidthPct = out.totalMax > 0 ? (r.total / out.totalMax) * 100 : 0
  r.meanWidthPct = (out.meanMax && r.mean != null) ? (r.mean / out.meanMax) * 100 : 0
})

// Cheapest / dearest by MEAN (per-sample), since run sample counts differ.
const withMean = out.runs.filter(r => r.mean != null)
if (withMean.length >= 2) {
  const cheapest = withMean.reduce((m, r) => (r.mean < m.mean ? r : m))
  const dearest = withMean.reduce((m, r) => (r.mean > m.mean ? r : m))
  out.cheapest = { idShort: cheapest.idShort, model: cheapest.model, meanStr: cheapest.meanStr }
  out.dearest = { idShort: dearest.idShort, model: dearest.model, meanStr: dearest.meanStr }
  out.spreadPct = cheapest.mean > 0 ? ((dearest.mean - cheapest.mean) / cheapest.mean) * 100 : null
}

writeFileSync(OUT, JSON.stringify(out, null, 1), 'utf8')
console.log('WROTE ' + OUT)
for (const r of out.runs) {
  console.log(`  ${r.idShort} ${r.model}: total=${r.totalStr} (w=${r.totalWidthPct.toFixed(1)}%) mean=${r.meanStr} (w=${r.meanWidthPct.toFixed(1)}%) rgb=${r.rgb.join(',')}`)
}
if (out.cheapest) console.log(`  cheapest=${out.cheapest.idShort} dearest=${out.dearest.idShort} spread=${out.spreadPct != null ? out.spreadPct.toFixed(1) + '%' : '-'}`)
