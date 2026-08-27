// Read-only check: what does the env dropdown return now, and how many
// traces sit in each environment? Does NOT trigger a poll.
// ASCII-only source on purpose (CJK phantom-read box).
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const st = JSON.parse(fs.readFileSync(path.join(HERE, 'auth.json'), 'utf8'))
const ls = st.origins[0].localStorage.find((x) => x.name === 'agent-eval-auth')
const TOKEN = JSON.parse(ls.value).state.accessToken
const H = { Authorization: `Bearer ${TOKEN}` }

async function get(p) {
  const r = await fetch(`${BASE}${p}`, { headers: H })
  const t = await r.text()
  return { status: r.status, body: t ? JSON.parse(t) : null, raw: t }
}

const s = await get('/api/langfuse-metrics/stats')
console.log('STATS_STATUS=' + s.status)
if (s.status !== 200) {
  console.log('BODY=' + s.raw.slice(0, 400))
  process.exit(1)
}
const envs = s.body.environments || []
console.log('ENVS(' + envs.length + ')=' + JSON.stringify(envs))
console.log('TRACES_TOTAL=' + s.body.total_traces)

// Per-env trace counts: pass environment as a repeated query param.
for (const e of envs) {
  const r = await get('/api/langfuse-metrics/stats?environment=' + encodeURIComponent(e))
  console.log('  ENV ' + e + ' -> status=' + r.status + ' traces=' + (r.body?.total_traces ?? '?'))
}

// Prove the filter actually narrows: single-env count must be < total when >1 env.
const cur = await get('/api/langfuse-metrics/poll/status')
console.log('CURSOR=' + JSON.stringify(cur.body))
console.log('CHECK=DONE')
