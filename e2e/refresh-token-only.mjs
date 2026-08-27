// Re-login the existing probe user and rewrite auth.json in place.
// Unlike mint-auth.mjs (registers a NEW user -> new tenant -> empty data) and
// seed-probe-dataset.mjs (creates a NEW dataset), this only refreshes the token
// so previously seeded datasets stay reachable.
// ASCII-only source on purpose (CJK phantom-read box).
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const USER_FILE = path.join(HERE, 'probe-user.json')
const OUT = path.join(HERE, 'auth.json')

const USER = JSON.parse(fs.readFileSync(USER_FILE, 'utf8'))

async function jsonPost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const text = await r.text()
  if (!r.ok) throw new Error(`POST ${url} -> ${r.status} ${text.slice(0, 300)}`)
  return text ? JSON.parse(text) : null
}

console.log('login as', USER.username)
const tok = await jsonPost(`${BASE}/api/auth/login`, {
  username: USER.username,
  password: USER.password,
})
if (!tok.access_token) throw new Error('no access_token')

const meRes = await fetch(`${BASE}/api/auth/me`, {
  headers: { Authorization: `Bearer ${tok.access_token}` },
})
if (!meRes.ok) throw new Error(`GET /api/auth/me -> ${meRes.status}`)
const me = await meRes.json()
console.log('me.role=', me.role)

const persisted = {
  state: { accessToken: tok.access_token, refreshToken: tok.refresh_token, user: me },
  version: 0,
}
const storageState = {
  cookies: [],
  origins: [{
    origin: new URL(BASE).origin,
    localStorage: [{ name: 'agent-eval-auth', value: JSON.stringify(persisted) }],
  }],
}
fs.writeFileSync(OUT, JSON.stringify(storageState, null, 2), 'utf8')
console.log('rewrote', OUT)

// Prove the token actually works against the page's own API before any UI run.
const ds = process.argv[2]
if (ds) {
  const r = await fetch(`${BASE}/api/candidates?page=1&page_size=20&dataset_name=${encodeURIComponent(ds)}`, {
    headers: { Authorization: `Bearer ${tok.access_token}` },
  })
  const body = r.ok ? await r.json() : null
  console.log('CANDIDATES_STATUS=' + r.status, 'total=' + (body?.total ?? '?'))
}
console.log('REFRESH=OK')
