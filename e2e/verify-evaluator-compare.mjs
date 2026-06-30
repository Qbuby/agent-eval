// Headed Playwright probe for /evaluators/compare:
// 1) navigate, 2) pick A & B evaluators, 3) add manual sample, 4) run, 5) verify both score cells.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'

function ts() { return new Date().toISOString().slice(11, 23) }
function log(...a) { console.log(`[${ts()}]`, ...a) }

async function main() {
  if (!fs.existsSync(STORAGE)) throw new Error(`auth.json not found at ${STORAGE}`)
  const browser = await chromium.launch({ headless: false, slowMo: 60 })
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1500, height: 950 },
    storageState: STORAGE,
  })
  const page = await ctx.newPage()

  const consoleErrors = []
  const pageErrors = []
  const dryRunCalls = []

  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', e => pageErrors.push(e.message))
  page.on('response', async r => {
    const url = r.url()
    if (url.includes('/api/eval/evaluators') && url.includes('/dry-run')) {
      let body = null
      try { body = await r.json() } catch {}
      dryRunCalls.push({ status: r.status(), body })
      log('DRY-RUN:', r.status(), 'score:', body?.scores?.[0]?.value, 'error:', body?.error)
    }
  })

  log('navigating to /evaluators/compare')
  await page.goto('/evaluators/compare', { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('h1', { timeout: 15_000 })

  // 选 A / B（两个 select）
  const selects = page.locator('select')
  await selects.nth(0).waitFor({ timeout: 10_000 })
  // 等下拉框加载完（直到至少 3 个 option：placeholder + 至少 2 个 evaluator）
  await page.waitForFunction(() => {
    const s = document.querySelectorAll('select')
    return s.length >= 2 && s[0].options.length >= 3
  }, { timeout: 15_000 })
  // 取所有 evaluator option（跳过第一个 placeholder）
  const optsA = await selects.nth(0).locator('option').allTextContents()
  log('evaluator options:', optsA)
  if (optsA.length < 3) {  // 1 placeholder + 至少 2 evaluator
    throw new Error(`need at least 2 LLM judge evaluators, got ${optsA.length - 1}`)
  }
  await selects.nth(0).selectOption({ index: 1 })
  // B 的 select 排除了 A，所以只剩 [placeholder, 1 候选]
  await selects.nth(1).selectOption({ index: 1 })
  log('selected evaluators A & B')

  // 在第一个 sample 卡片里填 input/output/expected（新 UI：卡片化）
  const rowTextareas = page.locator('.rounded-lg').filter({ hasText: '用户输入' }).first().locator('textarea')
  await rowTextareas.nth(0).fill('我的CPD15L1叉车的轴承损坏了，这是质量问题吗？')
  await rowTextareas.nth(1).fill('建议停止使用并联系售后检查，需结合保养记录判断是否为质量问题。')
  await rowTextareas.nth(2).fill('建议点击"转人工"按钮，由专业售后人员为您提供详细处理建议。')
  log('sample filled')

  await page.screenshot({ path: 'evaluator-compare-before.png', fullPage: true })

  // 点跑全部
  const runBtn = page.getByRole('button', { name: /跑全部/ })
  await runBtn.click()
  log('clicked run-all')

  // 等两次 dry-run 全部回来（最多 90s）
  const t0 = Date.now()
  while (Date.now() - t0 < 90_000) {
    if (dryRunCalls.length >= 2) break
    await page.waitForTimeout(500)
  }

  await page.waitForTimeout(800)
  await page.screenshot({ path: 'evaluator-compare-after.png', fullPage: true })

  log('--- summary ---')
  log('dry-run calls:', dryRunCalls.length)
  for (const c of dryRunCalls) {
    log('  status:', c.status, 'value:', c.body?.scores?.[0]?.value, 'error:', c.body?.error)
  }
  log('console errors:', consoleErrors.length)
  log('page errors:', pageErrors.length)

  fs.writeFileSync('evaluator-compare-summary.json', JSON.stringify({ dryRunCalls, consoleErrors, pageErrors }, null, 2))

  await page.waitForTimeout(2000)
  await browser.close()

  if (dryRunCalls.length < 2) { log('FAIL: only got', dryRunCalls.length, 'dry-run calls'); process.exit(2) }
  const ok = dryRunCalls.every(c => c.status === 200 && !c.body?.error && typeof c.body?.scores?.[0]?.value === 'number')
  if (!ok) { log('FAIL: at least one dry-run did not score cleanly'); process.exit(3) }
  log('PASS: both evaluators scored', dryRunCalls.map(c => c.body.scores[0].value))
}

main().catch(e => { console.error('FATAL:', e); process.exit(1) })
