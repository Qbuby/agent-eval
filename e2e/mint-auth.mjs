// Mint a fresh storageState for verify-* probes without touching the UI.
//
// Why not refresh-auth.mjs: registration now requires an entry code (the
// second user onward), so its register-then-login flow 403s. This script
// talks to the API directly, then writes the zustand-persist localStorage
// blob that the app reads on boot ('agent-eval-auth').
//
// ASCII-only on purpose: this box has a CJK phantom-read problem, so keeping
// the source plain avoids re-reading garbage back.
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const ENTRY_CODE = process.env.ENTRY_CODE || ''
const OUT = path.resolve(process.env.OUT || 'auth.json')
const TS = Date.now()
const USER = {
  username: `probe_${TS}`,
  email: `probe_${TS}@example.com`,
  password: 'Password123!',
}

function log(...a) { console.log(`[${new Date().toISOString().slice(11, 23)}]`, ...a) }

async function jsonPost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const text = await r.text()
  if (!r.ok) throw new Error(`POST ${url} -> ${r.status} ${text.slice(0, 400)}`)
  return text ? JSON.parse(text) : null
}

async function main() {
  if (!ENTRY_CODE) {
    throw new Error('set ENTRY_CODE in the environment; do not hardcode it (repo policy)')
  }
  log('register', USER.username)
  const reg = await jsonPost(`${BASE}/api/auth/register`, { ...USER, entry_code: ENTRY_CODE })
  log('registered role=', reg.role)

  log('login')
  const tok = await jsonPost(`${BASE}/api/auth/login`, {
    username: USER.username,
    password: USER.password,
  })
  if (!tok.access_token) throw new Error('no access_token in login response')

  log('getMe')
  const meRes = await fetch(`${BASE}/api/auth/me`, {
    headers: { Authorization: `Bearer ${tok.access_token}` },
  })
  if (!meRes.ok) throw new Error(`GET /api/auth/me -> ${meRes.status}`)
  const me = await meRes.json()
  log('me.role=', me.role, 'tenant=', me.tenant_id)

  const persisted = {
    state: {
      accessToken: tok.access_token,
      refreshToken: tok.refresh_token,
      user: me,
    },
    version: 0,
  }

  const origin = new URL(BASE).origin
  const storageState = {
    cookies: [],
    origins: [
      {
        origin,
        localStorage: [
          { name: 'agent-eval-auth', value: JSON.stringify(persisted) },
        ],
      },
    ],
  }

  fs.writeFileSync(OUT, JSON.stringify(storageState, null, 2), 'utf8')
  fs.writeFileSync(path.resolve('probe-user.json'), JSON.stringify(USER, null, 2), 'utf8')
  log('wrote', OUT, 'bytes=', fs.statSync(OUT).size)
}

main().catch(err => { console.error('FATAL:', err.message); process.exit(1) })
