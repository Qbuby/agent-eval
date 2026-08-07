// #168 probe: dump the raw /eval/runs response so we can tell whether the
// empty list is a permission issue (fresh probe tenant sees no data) or a
// response-shape mismatch in our earlier probe.
//
// Reads the token from the live browser session's localStorage (key
// agent-eval-auth) rather than auth.json, since the shape there is nested.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const AUTH = path.join(HERE, 'auth.json')
const OUT = path.join(HERE, 'result-168-raw.json')
const BASE = process.env.BASE_URL || 'http://localhost'

const out = {}
let browser = null
try {
  browser = await chromium.launch({ headless: true })
  const ctx = await browser.newContext({ baseURL: BASE, storageState: AUTH })
  const page = await ctx.newPage()
  await page.goto('/evaluation', { waitUntil: 'networkidle' })

  const probe = await page.evaluate(async () => {
    const raw = localStorage.getItem('agent-eval-auth')
    let token = null
    const hunt = (o, d = 0) => {
      if (!o || d > 6 || token) return
      if (typeof o === 'string') { if (o.split('.').length === 3 && o.length > 60) token = o; return }
      if (typeof o !== 'object') return
      for (const v of Object.values(o)) hunt(v, d + 1)
    }
    try { hunt(JSON.parse(raw)) } catch { /* ignore */ }

    const hdr = token ? { Authorization: `Bearer ${token}` } : {}
    const get = async (u) => {
      const r = await fetch(u, { headers: hdr })
      let body = null
      try { body = await r.json() } catch { body = '<non-json>' }
      return { url: u, status: r.status, body }
    }
    return {
      token_present: !!token,
      me: await get('/api/auth/me'),
      runs: await get('/api/eval/runs?page=1&page_size=5'),
    }
  })

  out.token_present = probe.token_present
  out.me_status = probe.me.status
  out.me_body = probe.me.body
  out.runs_status = probe.runs.status
  // Keep the dump small but structural: top-level keys + first item's keys.
  const rb = probe.runs.body
  out.runs_toplevel_keys = rb && typeof rb === 'object' ? Object.keys(rb) : String(rb).slice(0, 200)
  out.runs_total = rb?.total ?? null
  out.runs_items_len = Array.isArray(rb?.items) ? rb.items.length : null
  out.runs_first_item = Array.isArray(rb?.items) && rb.items[0]
    ? { id: rb.items[0].id, status: rb.items[0].status, name: rb.items[0].langfuse_run_name }
    : null
  out.runs_body_snippet = JSON.stringify(rb).slice(0, 600)

  console.log('token_present=' + out.token_present)
  console.log('me_status=' + out.me_status + ' me=' + JSON.stringify(out.me_body).slice(0, 300))
  console.log('runs_status=' + out.runs_status)
  console.log('runs_toplevel_keys=' + JSON.stringify(out.runs_toplevel_keys))
  console.log('runs_total=' + out.runs_total + ' items_len=' + out.runs_items_len)
  console.log('runs_first_item=' + JSON.stringify(out.runs_first_item))
  console.log('snippet=' + out.runs_body_snippet)
} catch (e) {
  out.error = String(e && e.stack || e)
  console.log('ERROR ' + out.error)
} finally {
  if (browser) await browser.close()
  fs.writeFileSync(OUT, JSON.stringify(out, null, 2), 'utf8')
  console.log('wrote ' + OUT)
}
