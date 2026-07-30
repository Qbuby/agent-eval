// Measure list endpoint latency + status for task #115.
// Candidate + conversation dataset lists, cold then hot (x3 each).
// ASCII-only on purpose (CJK phantom-read box).
import fs from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const auth = JSON.parse(fs.readFileSync(process.env.AUTH || 'auth.json', 'utf8'))
const blob = auth.origins[0].localStorage.find(x => x.name === 'agent-eval-auth').value
const token = JSON.parse(blob).state.accessToken

async function hit(url) {
  const t0 = performance.now()
  const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
  const body = await r.text()
  const ms = (performance.now() - t0).toFixed(0)
  let count = '?'
  try {
    const j = JSON.parse(body)
    count = Array.isArray(j) ? j.length : (j.total ?? j.items?.length ?? '?')
  } catch { /* non-json */ }
  return { status: r.status, ms, count, bytes: body.length }
}

async function series(label, url, n = 3) {
  console.log(`\n=== ${label} :: ${url}`)
  for (let i = 0; i < n; i++) {
    const r = await hit(url)
    console.log(`  [${i === 0 ? 'cold' : 'hot '}] ${r.status}  ${r.ms}ms  count=${r.count}  bytes=${r.bytes}`)
  }
}

await series('candidate', `${BASE}/api/datasets?type=candidate`)
await series('conversation', `${BASE}/api/datasets?type=conversation`)
console.log('\n=== done ===')
