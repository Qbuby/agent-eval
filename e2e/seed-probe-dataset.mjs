// Seed a dataset owned by the probe account itself, so verify-* runs against
// data the probe can actually see (no cross-tenant / role issues).
//
// IMPORTANT: DatasetDetailPage renders candidates (candidate_cases local table)
// via GET /api/candidates?dataset_name=..., NOT the provider-backed
// /api/datasets/{name}/cases. So cases must be written through
// POST /api/candidates/batch or the table renders empty (no row checkboxes).
//
// Also refreshes auth.json (login again) so a stale token can't fail the probe.
// ASCII-only on purpose: this box has a CJK phantom-read problem.
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\//, ''))
const USER = JSON.parse(fs.readFileSync(path.join(HERE, 'probe-user.json'), 'utf8'))
const DS = process.env.DS || `probe-ds-${Date.now()}`

function log(...a) { console.log(`[${new Date().toISOString().slice(11, 23)}]`, ...a) }

async function api(method, url, body, token) {
  const r = await fetch(`${BASE}${url}`, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  })
  const text = await r.text()
  if (!r.ok) throw new Error(`${method} ${url} -> ${r.status} ${text.slice(0, 500)}`)
  return text ? JSON.parse(text) : null
}

log('login as', USER.username)
const tok = await api('POST', '/api/auth/login', {
  username: USER.username,
  password: USER.password,
})
const T = tok.access_token
const me = await api('GET', '/api/auth/me', null, T)
log('me.role=', me.role, 'tenant=', me.tenant_id)

log('create dataset', DS)
const created = await api('POST', '/api/datasets', {
  name: DS,
  description: 'probe seed for #114 acceptance',
  dataset_type: 'candidate',
}, T)
log('dataset id=', created.id)

const CASES = [
  { question: 'What is 2 + 2?', answer: 'It is 4.', tags: ['probe'], category: 'normal' },
  { question: 'Name the capital of France.', answer: 'Paris.', tags: ['probe'], category: 'normal' },
  { question: 'Summarize what HTTP 404 means.', answer: null, tags: ['probe'], category: 'normal' },
]

log('add', CASES.length, 'candidates via /api/candidates/batch')
const added = await api('POST', '/api/candidates/batch', {
  dataset_name: DS,
  source: 'manual',
  cases: CASES,
}, T)
log('batch result=', JSON.stringify(added).slice(0, 300))

// Verify through the exact endpoint the page uses.
const listed = await api('GET', `/api/candidates?dataset_name=${encodeURIComponent(DS)}&page=1&page_size=20`, null, T)
const items = listed.items || listed.data || listed
const count = Array.isArray(items) ? items.length : (listed.total ?? 0)
log('candidates visible to page =', count)

const detail = await api('GET', `/api/datasets/${encodeURIComponent(DS)}`, null, T)
log('detail example_count=', detail.example_count, 'type=', detail.dataset_type)

// Refresh storageState with the fresh token.
const persisted = {
  state: { accessToken: T, refreshToken: tok.refresh_token, user: me },
  version: 0,
}
const out = path.join(HERE, 'auth.json')
fs.writeFileSync(out, JSON.stringify({
  cookies: [],
  origins: [{
    origin: new URL(BASE).origin,
    localStorage: [{ name: 'agent-eval-auth', value: JSON.stringify(persisted) }],
  }],
}, null, 2), 'utf8')
log('rewrote', out)

console.log('SEED_DATASET=' + DS)
console.log('SEED_COUNT=' + count)
console.log('SEED=' + (count === CASES.length ? 'OK' : `PARTIAL(${count}/${CASES.length})`))
process.exitCode = count === CASES.length ? 0 : 1
