// Probe: does the metrics sync now pull ALL Langfuse environments?
// 1) read env dropdown from /stats BEFORE poll
// 2) trigger POST /poll (one incremental round, no env filter now)
// 3) read env dropdown AFTER poll and diff
// ASCII-only source on purpose (CJK phantom-read box).
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const AUTH = path.join(HERE, 'auth.json')

const st = JSON.parse(fs.readFileSync(AUTH, 'utf8'))
const ls = st.origins[0].localStorage.find((x) => x.name === 'agent-eval-auth')
const TOKEN = JSON.parse(ls.value).state.accessToken
const H = { Authorization: `Bearer ${TOKEN}` }

async function get(p) {
  const r = await fetch(`${BASE}${p}`, { headers: H })
  const t = await r.text()
  return { status: r.status, body: t ? JSON.parse(t) : null, raw: t }
}

// POST /poll runs a FULL sync round server-side and regularly outlives
// undici's 5min headers timeout (UND_ERR_HEADERS_TIMEOUT). Fire it with
// node:http (no built-in header deadline) and do not await the body: we
// track completion via the poll/status cursor instead.
function fire(p) {
  return new Promise((resolve, reject) => {
    const u = new URL(BASE + p)
    const req = http.request(
      { hostname: u.hostname, port: u.port || 80, path: u.pathname, method: 'POST', headers: H },
      (res) => { res.resume(); resolve(res.statusCode) },
    )
    req.on('error', reject)
    req.end()
  })
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Wait until the cursor leaves 'running'. Returns the final cursor.
async function waitIdle(maxMs = 40 * 60 * 1000) {
  const t0 = Date.now()
  while (Date.now() - t0 < maxMs) {
    await sleep(20000)
    const s = await get('/api/langfuse-metrics/poll/status')
    if (s.body && s.body.status !== 'running') return s.body
  }
  return null
}

const before = await get('/api/langfuse-metrics/stats')
console.log('STATS_BEFORE_STATUS=' + before.status)
if (before.status !== 200) {
  console.log('BODY=' + before.raw.slice(0, 400))
  process.exit(1)
}
const envsBefore = before.body.environments || []
console.log('ENVS_BEFORE(' + envsBefore.length + ')=' + JSON.stringify(envsBefore))
console.log('TRACES_BEFORE=' + before.body.total_traces)

const cur = await get('/api/langfuse-metrics/poll/status')
console.log('CURSOR_BEFORE=' + JSON.stringify(cur.body))

console.log('--- triggering POST /poll (may take a while) ---')
const t0 = Date.now()
const poll = await post('/api/langfuse-metrics/poll')
console.log('POLL_STATUS=' + poll.status, 'elapsed=' + Math.round((Date.now() - t0) / 1000) + 's')
console.log('POLL_BODY=' + poll.raw.slice(0, 600))

const after = await get('/api/langfuse-metrics/stats')
console.log('STATS_AFTER_STATUS=' + after.status)
const envsAfter = after.body?.environments || []
console.log('ENVS_AFTER(' + envsAfter.length + ')=' + JSON.stringify(envsAfter))
console.log('TRACES_AFTER=' + after.body?.total_traces)

const added = envsAfter.filter((e) => !envsBefore.includes(e))
console.log('ENVS_ADDED=' + JSON.stringify(added))
console.log('PROBE=DONE')
