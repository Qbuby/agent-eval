// Headed Playwright probe: exercise the new "导出" (export) feature end-to-end
// through the real browser. For each page that mounts an <ExportMenu>, open the
// popover, click CSV / Excel / JSON in turn, and assert the resulting export
// request returns 200 with a Content-Disposition attachment header — all while
// watching for console / page errors.
//
// Pages covered (browser-reachable, DB-backed → deterministic):
//   1. /datasets/:name           → GET  /api/candidates/export
//   2. /benchmark/:projectId      → GET  /api/benchmark/{pid}/cases/export
//   3. /evaluation/runs/:runId    → GET  /api/eval/runs/{id}/results/export
//   4. /evaluation/compare?ids=   → POST /api/eval/runs/export-compare
//   5. /traces (best-effort)      → POST /api/traces/runs/export  (needs LangSmith)
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const DATASET = process.env.DATASET || 'ep-agent'
const PROJECT_ID = process.env.PROJECT_ID || 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'
const RUN1 = process.env.RUN1 || 'd88c3f61-b50a-4b03-91a3-f5c58c90a528'
const RUN2 = process.env.RUN2 || 'e1dba573-dfa7-4c14-aa20-b9f8f24330fe'
const TRACES_PROJECT = process.env.TRACES_PROJECT || 'ep-agent'

function ts() { return new Date().toISOString().slice(11, 23) }
function log(...a) { console.log(`[${ts()}]`, ...a) }

const results = []          // { page, format, status, disposition, ok }
const consoleErrors = []
const pageErrors = []

async function main() {
  if (!fs.existsSync(STORAGE)) throw new Error(`auth.json not found at ${STORAGE}`)

  const browser = await chromium.launch({ headless: false, slowMo: 120 })
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1500, height: 950 },
    storageState: STORAGE,
    acceptDownloads: true,
  })
  const page = await ctx.newPage()

  page.on('console', msg => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text())
      log('CONSOLE ERROR:', msg.text())
    }
  })
  page.on('pageerror', err => {
    pageErrors.push(err.message)
    log('PAGE ERROR:', err.message)
  })

  // Record every export response we see (status + disposition header).
  page.on('response', async resp => {
    const url = resp.url()
    if (url.includes('/export') && url.includes('/api/')) {
      const cd = resp.headers()['content-disposition'] || ''
      log('EXPORT RESP', resp.status(), url.split('/api/')[1]?.slice(0, 50), '| cd:', cd.slice(0, 50))
    }
  })

  // Helper: open the ExportMenu whose label matches `labelRe`, then click the
  // menuitem for `optLabel`, awaiting the export network response. Returns
  // { status, disposition }.
  async function exportOnce(labelRe, optLabel, urlMatch) {
    // Find the export trigger button. There may be several "导出"-ish buttons;
    // pick the first that opens a role=menu containing our option.
    const btns = page.getByRole('button', { name: labelRe })
    const n = await btns.count()
    let opened = false
    for (let i = 0; i < n; i++) {
      await btns.nth(i).click()
      const menuItem = page.getByRole('menuitem', { name: optLabel })
      try {
        await menuItem.waitFor({ state: 'visible', timeout: 1500 })
        opened = true
        const [resp] = await Promise.all([
          page.waitForResponse(r => r.url().includes(urlMatch) && r.request().method() !== 'OPTIONS', { timeout: 20000 }),
          menuItem.click(),
        ])
        return { status: resp.status(), disposition: resp.headers()['content-disposition'] || '' }
      } catch {
        // Not this button (or no menu). Close any stray popover and try next.
        await page.keyboard.press('Escape').catch(() => {})
      }
    }
    if (!opened) throw new Error(`could not open ExportMenu for ${labelRe}`)
    throw new Error('export response not captured')
  }

  async function testPage({ name, url, ready, labelRe, urlMatch }) {
    log(`\n=== ${name} → ${url}`)
    await page.goto(url, { waitUntil: 'domcontentloaded' })
    if (ready) {
      try { await ready() } catch (e) { log(`  ready() warn: ${e.message}`) }
    }
    for (const [fmt, optLabel] of [['csv', 'CSV (.csv)'], ['xlsx', 'Excel (.xlsx)'], ['json', 'JSON (.json)']]) {
      try {
        const { status, disposition } = await exportOnce(labelRe, optLabel, urlMatch)
        const ok = status === 200 && /attachment/i.test(disposition)
        results.push({ page: name, format: fmt, status, disposition, ok })
        log(`  [${fmt}] status=${status} ok=${ok}`)
      } catch (e) {
        results.push({ page: name, format: fmt, status: 'ERR', disposition: '', ok: false, err: e.message })
        log(`  [${fmt}] FAILED: ${e.message}`)
      }
    }
  }

  // 1. Dataset detail → candidates export
  await testPage({
    name: 'DatasetDetail',
    url: `/datasets/${encodeURIComponent(DATASET)}`,
    ready: async () => { await page.getByRole('button', { name: /导出/ }).first().waitFor({ timeout: 10000 }) },
    labelRe: /^导出$/,
    urlMatch: '/api/candidates/export',
  })

  // 2. Benchmark → benchmark cases export
  await testPage({
    name: 'Benchmark',
    url: `/benchmark/${PROJECT_ID}`,
    ready: async () => { await page.getByRole('button', { name: /导出/ }).first().waitFor({ timeout: 10000 }) },
    labelRe: /^导出$/,
    urlMatch: '/cases/export',
  })

  // 3. Eval run detail → results export
  await testPage({
    name: 'EvalRunDetail',
    url: `/evaluation/runs/${RUN1}`,
    ready: async () => { await page.getByRole('button', { name: /导出/ }).first().waitFor({ timeout: 10000 }) },
    labelRe: /^导出$/,
    urlMatch: '/results/export',
  })

  // 4. Eval compare → compare export
  await testPage({
    name: 'EvalCompare',
    url: `/evaluation/compare?ids=${RUN1},${RUN2}`,
    ready: async () => { await page.getByRole('button', { name: /导出对比/ }).first().waitFor({ timeout: 10000 }) },
    labelRe: /导出对比/,
    urlMatch: '/eval/runs/export-compare',
  })

  // 5. Traces (best-effort; depends on live LangSmith connectivity)
  log('\n=== Traces (best-effort) → /traces')
  try {
    await page.goto('/traces', { waitUntil: 'domcontentloaded' })
    // Fill project name and trigger a query so rows (and the ExportMenu) appear.
    const input = page.getByPlaceholder(/项目|project/i).first()
    await input.fill(TRACES_PROJECT, { timeout: 5000 }).catch(() => {})
    await page.getByRole('button', { name: /查询|搜索|加载/ }).first().click({ timeout: 5000 }).catch(() => {})
    const exportBtn = page.getByRole('button', { name: /^导出$/ })
    await exportBtn.first().waitFor({ state: 'visible', timeout: 30000 })
    for (const [fmt, optLabel] of [['csv', 'CSV (.csv)'], ['xlsx', 'Excel (.xlsx)'], ['json', 'JSON (.json)']]) {
      try {
        const { status, disposition } = await exportOnce(/^导出$/, optLabel, '/api/traces/runs/export')
        const ok = status === 200 && /attachment/i.test(disposition)
        results.push({ page: 'Traces', format: fmt, status, disposition, ok })
        log(`  [${fmt}] status=${status} ok=${ok}`)
      } catch (e) {
        results.push({ page: 'Traces', format: fmt, status: 'ERR', ok: false, err: e.message })
        log(`  [${fmt}] FAILED: ${e.message}`)
      }
    }
  } catch (e) {
    log(`  Traces skipped: ${e.message} (likely LangSmith unreachable from this host)`)
    results.push({ page: 'Traces', format: '-', status: 'SKIP', ok: null, err: e.message })
  }

  await page.screenshot({ path: 'export-verify.png', fullPage: false }).catch(() => {})
  await browser.close()

  // ---- summary ----
  console.log('\n================ EXPORT VERIFY SUMMARY ================')
  for (const r of results) {
    const flag = r.ok === true ? 'PASS' : r.ok === null ? 'SKIP' : 'FAIL'
    console.log(`  ${flag.padEnd(4)} ${r.page.padEnd(14)} ${String(r.format).padEnd(5)} status=${r.status}${r.err ? ' err=' + r.err : ''}`)
  }
  const pass = results.filter(r => r.ok === true).length
  const fail = results.filter(r => r.ok === false).length
  const skip = results.filter(r => r.ok === null).length
  console.log(`  ---- ${pass} pass / ${fail} fail / ${skip} skip ----`)
  console.log(`  console errors: ${consoleErrors.length}, page errors: ${pageErrors.length}`)
  console.log('======================================================')
  if (fail > 0) process.exitCode = 1
}

main().catch(err => { console.error('FATAL:', err); process.exit(1) })
