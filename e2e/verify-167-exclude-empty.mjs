// #163 / task #167 headed acceptance: the "exclude unusable samples" entry.
//
// What must hold in the real browser against the rebuilt image:
//   1. Switching a ReplySourcePanel to "use existing replies" precheck runs.
//   2. When some picked samples cannot be evaluated (no reply, or an empty
//      reply), the panel reports the blocked count AND renders the one-click
//      exclude button.
//   3. Clicking it calls POST /api/agent-replies/filter-empty and writes the
//      surviving refs back into the selection, so the button disappears and
//      the summary flips to all-usable.
//
// Project ep-agent is used because it has both usable (4) and blocked (46)
// samples in the first page, which is exactly the mixed state the feature is
// for. Data is read live, never assumed.
//
// The needles must be literal CJK to match the DOM, so this file is NOT
// ASCII-only: verify its bytes with node, never with the Read tool.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const AUTH = path.join(HERE, 'auth.json')
const SHOT_BEFORE = path.join(HERE, 'shot-167-before.png')
const SHOT_AFTER = path.join(HERE, 'shot-167-after.png')
const OUT = path.join(HERE, 'result-167-exclude-empty.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const PROJECT = process.env.PROJECT_NAME || 'ep-agent'

const T = {
  newRun: '新建评估',
  projectLabel: '项目',
  usePersisted: '使用已有回复',
  precheckDone: '已检查',
  missingWord: '缺少回复',
  excludePrefix: '排除这',
  excludeSuffix: '条不可评估样例',
  writeBack: '会把剩余',
}

const evidence = { base: BASE, project: PROJECT, checks: {}, shots: [SHOT_BEFORE, SHOT_AFTER] }
const fail = []
let browser = null

try {
  browser = await chromium.launch({ headless: false, slowMo: 50 })
  const ctx = await browser.newContext({
    baseURL: BASE,
    storageState: AUTH,
    viewport: { width: 1440, height: 1200 },
  })
  const page = await ctx.newPage()

  // Capture the real filter-empty exchange so DOM claims can be cross-checked
  // against what the backend actually returned.
  const filterCalls = []
  page.on('response', async res => {
    if (!res.url().includes('/agent-replies/filter-empty')) return
    try {
      filterCalls.push({ status: res.status(), body: await res.json() })
    } catch { filterCalls.push({ status: res.status(), body: null }) }
  })

  await page.goto('/evaluation', { waitUntil: 'networkidle' })

  // The route lands on run history; the config form is behind this button.
  await page.getByRole('button', { name: T.newRun }).first().click()
  await page.waitForTimeout(1200)

  // Pick the project that has a mixed usable/blocked population.
  const projectSelect = page.locator('select').first()
  await projectSelect.waitFor({ state: 'visible', timeout: 20_000 })
  await projectSelect.selectOption({ label: PROJECT })
  await page.waitForTimeout(2000)
  evidence.checks.project_selected = await projectSelect.inputValue()

  // Switch reply source to persisted -> triggers the precheck.
  const persistedBtn = page.getByRole('button', { name: T.usePersisted }).first()
  await persistedBtn.waitFor({ state: 'visible', timeout: 20_000 })
  await persistedBtn.click()

  // Wait for the precheck summary line to land.
  await page.getByText(T.precheckDone, { exact: false }).first()
    .waitFor({ state: 'visible', timeout: 40_000 })
  await page.waitForTimeout(1200)
  await page.screenshot({ path: SHOT_BEFORE, fullPage: true })

  const before = await page.locator('body').innerText()
  evidence.summary_before = (before.match(/已检查[^\n]*/) || [''])[0]

  const sawMissing = before.includes(T.missingWord)
  evidence.checks.blocked_reported = sawMissing
  if (!sawMissing) fail.push('precheck did not report blocked samples')

  // The one-click exclude button: label is "排除这 N 条不可评估样例".
  const excludeBtn = page.getByRole('button', { name: new RegExp(`${T.excludePrefix}.*${T.excludeSuffix}`) }).first()
  const btnVisible = await excludeBtn.isVisible().catch(() => false)
  evidence.checks.exclude_button_visible = btnVisible
  if (!btnVisible) fail.push('exclude button not rendered while blocked > 0')

  if (btnVisible) {
    evidence.exclude_button_label = (await excludeBtn.innerText()).trim()
    const hint = before.includes(T.writeBack)
    evidence.checks.writeback_hint_visible = hint
    if (!hint) fail.push('write-back hint line missing')

    await excludeBtn.click()
    // Give the mutation + refetch time to settle.
    await page.waitForTimeout(6000)
    await page.screenshot({ path: SHOT_AFTER, fullPage: true })

    const after = await page.locator('body').innerText()
    evidence.summary_after = (after.match(/已检查[^\n]*/) || [''])[0]
    evidence.filter_calls = filterCalls

    const called = filterCalls.length > 0
    evidence.checks.filter_empty_called = called
    if (!called) fail.push('POST /agent-replies/filter-empty was never called')

    const ok200 = filterCalls.some(c => c.status === 200)
    evidence.checks.filter_empty_200 = ok200
    if (called && !ok200) fail.push('filter-empty did not return 200')

    // Partition sanity straight from the wire payload.
    const last = filterCalls[filterCalls.length - 1]
    if (last && last.body) {
      const { empty_refs = [], missing_refs = [], kept_refs = [] } = last.body
      evidence.wire_counts = {
        empty: empty_refs.length, missing: missing_refs.length, kept: kept_refs.length,
      }
      if (missing_refs.length === 0 && empty_refs.length === 0) {
        fail.push('filter-empty reported nothing blocked, fixture expectation broken')
      }
    }

    // After the write-back the button must be gone (nothing left to exclude).
    const stillThere = await excludeBtn.isVisible().catch(() => false)
    evidence.checks.exclude_button_gone_after_click = !stillThere
    if (stillThere) fail.push('exclude button still visible after write-back')

    const cleared = !after.includes(T.missingWord)
    evidence.checks.blocked_cleared_after_click = cleared
    if (!cleared) fail.push('summary still reports missing replies after write-back')
  }
} finally {
  if (browser) await browser.close()
}

evidence.failures = fail
fs.writeFileSync(OUT, JSON.stringify(evidence, null, 2), 'utf8')
console.log(JSON.stringify(evidence, null, 2))
if (fail.length) {
  console.log('VERIFY_167_UI_FAIL')
  process.exit(1)
}
console.log('VERIFY_167_UI_OK')
