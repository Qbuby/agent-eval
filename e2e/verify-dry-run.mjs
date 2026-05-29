// Headed Playwright probe: open Evaluators page, edit the
// "agent-eval-correctness/kiro-aidong-claude-opus-4-6" evaluator,
// fill dry-run sample inputs, click "试跑一次" and capture the
// /api/eval/evaluators/{id}/dry-run response. Verifies that the
// DNS-fix in docker-compose let the judge actually call out and
// score the sample after the variable_mapping refactor.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const EVAL_ID = process.env.EVAL_ID || 'acaf3ec0-86e2-4010-b2b9-7b35b6f7a29b'
const DRY_QUERY = process.env.DRY_QUERY || '我的CPD15L1叉车的轴承损坏了，这是质量问题吗？'
const DRY_OUTPUT = process.env.DRY_OUTPUT || '建议停止使用并联系售后检查，需结合保养记录判断是否为质量问题。'
const DRY_EXPECTED = process.env.DRY_EXPECTED || '建议点击"转人工"按钮，由专业售后人员为您提供详细处理建议。'

function ts() {
  return new Date().toISOString().slice(11, 23)
}
function log(...a) { console.log(`[${ts()}]`, ...a) }

async function main() {
  if (!fs.existsSync(STORAGE)) throw new Error(`auth.json not found at ${STORAGE}`)

  const browser = await chromium.launch({ headless: false, slowMo: 100 })
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1500, height: 950 },
    storageState: STORAGE,
  })
  const page = await ctx.newPage()

  const consoleErrors = []
  const pageErrors = []
  const dryRunCalls = []

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
  page.on('response', async resp => {
    const url = resp.url()
    if (url.includes('/api/eval/evaluators') && url.includes('/dry-run')) {
      let body = null
      try { body = await resp.json() } catch {}
      dryRunCalls.push({ status: resp.status(), body })
      log('DRY-RUN API:', resp.status())
    }
  })

  log('navigating to /evaluators')
  await page.goto('/evaluators', { waitUntil: 'domcontentloaded' })

  await page.waitForSelector('table tbody tr', { timeout: 15_000 })
  log('table rendered')

  const editorRow = page.locator(`tr:has-text("${EVAL_ID.slice(0, 8)}"), tr:has-text("agent-eval-correctness/kiro-aidong-claude-opus-4-6")`).first()
  if (!(await editorRow.count())) {
    const all = await page.locator('table tbody tr').allTextContents()
    log('no row matched; first row text:', all[0])
    throw new Error('target evaluator row not found')
  }

  log('clicking 编辑 on target row')
  await editorRow.locator('button', { hasText: '编辑' }).click()

  // Drawer opens; wait for the "试跑一次" button to appear
  const dryBtn = page.getByRole('button', { name: '试跑一次' })

  // Legacy evaluators with evaluator_type=null used to default the drawer
  // to "tag" mode and hide the judge config + dry-run section. Newer code
  // also infers judge mode from params.provider_id; click the chip
  // explicitly as a belt-and-suspenders fallback so the test still works
  // even if a frontend that lacks the inference fix is running.
  const judgeChip = page.getByRole('button', { name: '可配置 LLM Judge' })
  if (await judgeChip.count()) {
    await judgeChip.first().click().catch(() => {})
  }

  await dryBtn.waitFor({ state: 'visible', timeout: 10_000 })
  log('drawer opened')

  // Fill the three textareas. Drawer labels are 输入 / AI 输出 / 期望答案（可选）
  await page.getByLabel('输入', { exact: true }).first().fill(DRY_QUERY)
  await page.getByLabel('AI 输出').first().fill(DRY_OUTPUT)
  await page.getByLabel('期望答案（可选）').first().fill(DRY_EXPECTED)
  log('sample inputs filled')

  // Capture screenshot before triggering
  await page.screenshot({ path: 'dry-run-before.png', fullPage: true })

  // Click 试跑一次 and wait for response
  await Promise.all([
    page.waitForResponse(r => r.url().includes('/dry-run') && r.request().method() === 'POST', { timeout: 90_000 }),
    dryBtn.click(),
  ])
  log('dry-run request returned')

  // Give UI a beat to reconcile, then snapshot result
  await page.waitForTimeout(800)
  await page.screenshot({ path: 'dry-run-after.png', fullPage: true })

  const summary = {
    dryRunCalls,
    consoleErrors,
    pageErrors,
  }
  fs.writeFileSync('dry-run-summary.json', JSON.stringify(summary, null, 2))
  log('--- summary ---')
  log('dry-run calls:', dryRunCalls.length)
  for (const c of dryRunCalls) {
    log('  status:', c.status,
      'error:', c.body?.error,
      'score[0].value:', c.body?.scores?.[0]?.value,
      'model:', c.body?.model,
      'tokens:', c.body?.usage?.total_tokens)
  }
  log('console errors:', consoleErrors.length)
  log('page errors:', pageErrors.length)

  // Hold open briefly so the human reviewer can see the result on screen
  await page.waitForTimeout(3000)
  await browser.close()

  const last = dryRunCalls[dryRunCalls.length - 1]
  if (!last) throw new Error('no dry-run call observed')
  if (last.body?.error) {
    log('FAIL: dry-run still errored:', last.body.error)
    process.exit(2)
  }
  if (typeof last.body?.scores?.[0]?.value !== 'number') {
    log('FAIL: dry-run did not return a numeric score')
    process.exit(3)
  }
  log('PASS: dry-run scored', last.body.scores[0].value)
}

main().catch(err => {
  console.error('FATAL:', err)
  process.exit(1)
})
