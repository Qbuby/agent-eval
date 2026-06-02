// Headed probe for the NEW traces-export semantics: export must serialize the
// rows already loaded in the list (after the active filter/sort), NOT re-pull
// from LangSmith. We stub /api/traces/runs (the list query) with fixed rows so
// the test is independent of live LangSmith connectivity, then drive the real
// ExportMenu and assert the export request body carries those exact rows.
import { chromium } from '@playwright/test'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = 'http://localhost'

// Three fake runs the stubbed list endpoint will return.
const FAKE_RUNS = [
  { id: 'run-1', name: 'LangGraph', status: 'success', start_time: '2026-06-01T08:00:00+00:00', latency_s: 12.5, total_tokens: 3200, error: null, tags: ['t1'], input_preview: '问题甲', output_preview: '答案甲', model_name: 'claude-opus-4-6', first_token_s: 1.2, first_tool_call_s: 2.3 },
  { id: 'run-2', name: 'LangGraph', status: 'success', start_time: '2026-06-01T07:00:00+00:00', latency_s: 8.0,  total_tokens: 1500, error: null, tags: [],     input_preview: '问题乙', output_preview: '答案乙', model_name: 'claude-haiku',     first_token_s: 0.9, first_tool_call_s: null },
  { id: 'run-3', name: 'LangGraph', status: 'success', start_time: '2026-06-01T06:00:00+00:00', latency_s: 20.1, total_tokens: 9001, error: null, tags: ['x'],  input_preview: '问题丙', output_preview: '答案丙', model_name: 'claude-opus-4-6', first_token_s: 2.0, first_tool_call_s: 5.5 },
]

function log(...a) { console.log(`[${new Date().toISOString().slice(11,23)}]`, ...a) }

const consoleErrors = [], pageErrors = []
const exportRequests = []   // { format, rowCount, rowIds }

async function main() {
  const browser = await chromium.launch({ headless: false, slowMo: 120 })
  const ctx = await browser.newContext({ baseURL: BASE, viewport: { width: 1500, height: 950 }, storageState: STORAGE, acceptDownloads: true })
  const page = await ctx.newPage()
  page.on('console', m => { if (m.type() === 'error') { consoleErrors.push(m.text()); log('CONSOLE ERR:', m.text()) } })
  page.on('pageerror', e => { pageErrors.push(e.message); log('PAGE ERR:', e.message) })

  // Stub the list query so rows render deterministically (no LangSmith).
  await page.route('**/api/traces/runs', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: FAKE_RUNS, total: FAKE_RUNS.length, page: 1, page_size: 20 }),
    })
  })

  // Capture the export request body (but let it hit the real backend).
  page.on('request', req => {
    if (req.url().includes('/api/traces/runs/export') && req.method() === 'POST') {
      let body = {}
      try { body = JSON.parse(req.postData() || '{}') } catch {}
      const rows = body.rows || []
      exportRequests.push({ format: body.format, rowCount: rows.length, rowIds: rows.map(r => r.id) })
      log(`EXPORT REQ format=${body.format} rows=${rows.length} ids=${rows.map(r => r.id).join(',')}`)
    }
  })

  log('goto /traces')
  await page.goto('/traces', { waitUntil: 'domcontentloaded' })
  const input = page.getByPlaceholder('项目名称…')
  await input.click(); await input.fill('ep-agent'); await input.press('Enter')

  // Export button appears once rows load (allRuns>0).
  const exportBtn = page.getByRole('button', { name: '导出', exact: true }).first()
  await exportBtn.waitFor({ state: 'visible', timeout: 15000 })
  log('rows loaded, export button visible')

  // ---- Case A: no filter → export should carry all 3 rows ----
  for (const [fmt, opt] of [['csv','CSV (.csv)'],['xlsx','Excel (.xlsx)'],['json','JSON (.json)']]) {
    await exportBtn.click()
    const item = page.getByRole('menuitem', { name: opt })
    await item.waitFor({ state: 'visible', timeout: 3000 })
    const [resp] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/api/traces/runs/export') && r.request().method() === 'POST', { timeout: 20000 }),
      item.click(),
    ])
    log(`  [${fmt}] export status=${resp.status()}`)
    await page.waitForTimeout(400)
  }

  await browser.close()

  // ---- assertions ----
  console.log('\n============ TRACES EXPORT SEMANTICS ============')
  let pass = 0, fail = 0
  for (const r of exportRequests) {
    const ok = r.rowCount === FAKE_RUNS.length &&
               JSON.stringify(r.rowIds) === JSON.stringify(FAKE_RUNS.map(x => x.id))
    console.log(`  ${ok ? 'PASS' : 'FAIL'} ${r.format.padEnd(5)} rows=${r.rowCount} ids=[${r.rowIds.join(',')}]`)
    ok ? pass++ : fail++
  }
  if (exportRequests.length !== 3) { console.log(`  FAIL: expected 3 export requests, got ${exportRequests.length}`); fail++ }
  console.log(`  ---- ${pass} pass / ${fail} fail ----`)
  console.log(`  console errors: ${consoleErrors.length}, page errors: ${pageErrors.length}`)
  console.log('=================================================')
  if (fail > 0 || consoleErrors.length > 0 || pageErrors.length > 0) process.exitCode = 1
}
main().catch(e => { console.error('FATAL:', e); process.exit(1) })
