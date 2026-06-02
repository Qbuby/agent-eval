// Headed probe for the eval-history BATCH export. Drives the real UI:
// open /evaluation (history tab), tick 2 run checkboxes, then use the
// "导出所选" ExportMenu for each format and assert the export request body
// carries exactly the checked run ids — and the response is a 200 attachment.
import { chromium } from '@playwright/test'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = 'http://localhost'

function log(...a) { console.log(`[${new Date().toISOString().slice(11,23)}]`, ...a) }

const consoleErrors = [], pageErrors = []
const exportReqs = []   // { format, runIds }
const exportResps = []  // { status, cd }

async function main() {
  const browser = await chromium.launch({ headless: false, slowMo: 120 })
  const ctx = await browser.newContext({ baseURL: BASE, viewport: { width: 1500, height: 950 }, storageState: STORAGE, acceptDownloads: true })
  const page = await ctx.newPage()
  page.on('console', m => { if (m.type() === 'error') { consoleErrors.push(m.text()); log('CONSOLE ERR:', m.text()) } })
  page.on('pageerror', e => { pageErrors.push(e.message); log('PAGE ERR:', e.message) })
  page.on('request', req => {
    if (req.url().includes('/api/eval/runs/export-summary') && req.method() === 'POST') {
      let b = {}; try { b = JSON.parse(req.postData() || '{}') } catch {}
      exportReqs.push({ format: b.format, runIds: b.run_ids || [] })
      log(`EXPORT REQ format=${b.format} run_ids=${(b.run_ids||[]).join(',')}`)
    }
  })
  page.on('response', async r => {
    if (r.url().includes('/api/eval/runs/export-summary') && r.request().method() === 'POST') {
      exportResps.push({ status: r.status(), cd: r.headers()['content-disposition'] || '' })
    }
  })

  log('goto /evaluation')
  await page.goto('/evaluation', { waitUntil: 'domcontentloaded' })

  // Wait for the history table to have data rows (checkboxes in the first col).
  const rowCheckboxes = page.locator('table tbody tr input[type="checkbox"]')
  await rowCheckboxes.first().waitFor({ state: 'visible', timeout: 15000 })
  const n = await rowCheckboxes.count()
  log(`history rows with checkbox: ${n}`)
  if (n < 2) throw new Error(`need >=2 history rows to test batch export, found ${n}`)

  // Tick the first two rows.
  await rowCheckboxes.nth(0).check()
  await rowCheckboxes.nth(1).check()
  log('checked 2 rows')

  // The "导出所选（2）" ExportMenu should now be visible.
  const exportBtn = page.getByRole('button', { name: /导出所选/ }).first()
  await exportBtn.waitFor({ state: 'visible', timeout: 5000 })

  for (const [fmt, opt] of [['csv','CSV (.csv)'],['xlsx','Excel (.xlsx)'],['json','JSON (.json)']]) {
    await exportBtn.click()
    const item = page.getByRole('menuitem', { name: opt })
    await item.waitFor({ state: 'visible', timeout: 3000 })
    const [resp] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/api/eval/runs/export-summary') && r.request().method() === 'POST', { timeout: 20000 }),
      item.click(),
    ])
    log(`  [${fmt}] status=${resp.status()}`)
    await page.waitForTimeout(400)
  }

  await browser.close()

  console.log('\n========== EVAL HISTORY BATCH EXPORT ==========')
  let pass = 0, fail = 0
  for (let i = 0; i < exportReqs.length; i++) {
    const req = exportReqs[i], resp = exportResps[i]
    const ok = req.runIds.length === 2 && resp && resp.status === 200 && /attachment/i.test(resp.cd)
    console.log(`  ${ok ? 'PASS' : 'FAIL'} ${req.format.padEnd(5)} run_ids=${req.runIds.length} status=${resp?.status}`)
    ok ? pass++ : fail++
  }
  if (exportReqs.length !== 3) { console.log(`  FAIL: expected 3 export requests, got ${exportReqs.length}`); fail++ }
  console.log(`  ---- ${pass} pass / ${fail} fail ----`)
  console.log(`  console errors: ${consoleErrors.length}, page errors: ${pageErrors.length}`)
  console.log('===============================================')
  if (fail > 0 || consoleErrors.length > 0 || pageErrors.length > 0) process.exitCode = 1
}
main().catch(e => { console.error('FATAL:', e); process.exit(1) })
