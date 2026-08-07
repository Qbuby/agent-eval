// Probe the #158 fixture run state before the headed UI acceptance.
// ASCII-only source on purpose (CJK phantom-read box).
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const RUN_ID = process.env.RUN_ID || '5238a8b6-d75c-447f-84e0-4543353e8f71'

const auth = JSON.parse(fs.readFileSync(path.join(HERE, 'auth.json'), 'utf8'))
const raw = auth.origins
  ?.find(o => o.origin === new URL(BASE).origin)
  ?.localStorage?.find(x => x.name === 'agent-eval-auth')?.value
if (!raw) throw new Error('auth.json missing agent-eval-auth')
const token = JSON.parse(raw).state.accessToken

async function api(p) {
  const r = await fetch(`${BASE}${p}`, { headers: { Authorization: `Bearer ${token}` } })
  const t = await r.text()
  return { status: r.status, body: t }
}

const detail = await api(`/api/eval/runs/${RUN_ID}`)
console.log('DETAIL_STATUS=' + detail.status)
if (detail.status === 200) {
  const d = JSON.parse(detail.body)
  console.log('RUN_STATUS=' + d.status, 'total=' + (d.total_cases ?? '?'))
}
const st = await api(`/api/eval/runs/${RUN_ID}/rescore-status`)
console.log('RESCORE_STATUS=' + st.status, st.body.slice(0, 500))
console.log('PROBE_158_DONE')
