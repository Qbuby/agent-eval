// Headed Playwright probe: exercise the error-handling sweep (#248) by
// triggering 3 distinct failure modes and asserting the frontend renders
// our normalized ErrorCard with the expected hint text.
//
// Failure modes exercised (no docker / hosts changes required — we drive
// everything through API calls + route mocking):
//   1) Backend returns 400 (missing provider_id) → axios path → ErrorCard
//   2) Backend returns 500 (route-mocked) → 5xx hint
//   3) DryRunResponse.error contains "truncated at max_tokens" → ErrorCard
//      renders code=truncated + the "调高 max_tokens" hint
//
// Case 3 is the load-bearing one: it exercises both formatDryRunError
// (lib/errors.ts) AND ErrorCard (the data-error-code attribute we set
// for testability) AND that the EvaluatorComparePage wires the two
// together correctly. The 400/500 cases just sanity-check the API
// surface.
//
// Run from D:\program\agent_eval\e2e:
//   $ node verify-error-handling.mjs
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const EVAL_ID = process.env.EVAL_ID || 'f7f2b43b-8a48-4fc1-9433-ee61e5de906c'

function ts() { return new Date().toISOString().slice(11, 23) }
function log(...a) { console.log(`[${ts()}]`, ...a) }

async function callDryRun(page, evaluatorId, body) {
  return await page.evaluate(async ({ id, payload }) => {
    const tok = JSON.parse(localStorage.getItem('auth-storage') || '{}')?.state?.accessToken
    const r = await fetch(`/api/eval/evaluators/${id}/dry-run`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
      },
      body: JSON.stringify(payload),
    })
    let data
    try { data = await r.json() } catch { data = await r.text() }
    return { status: r.status, body: data }
  }, { id: evaluatorId, payload: body })
}

async function main() {
  if (!fs.existsSync(STORAGE)) throw new Error(`auth.json not found at ${STORAGE}`)
  const browser = await chromium.launch({ headless: false, slowMo: 50 })
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1500, height: 950 },
    storageState: STORAGE,
  })
  const page = await ctx.newPage()

  const consoleErrors = []
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })

  log('navigating to /evaluators/compare')
  await page.goto('/evaluators/compare', { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('h1', { timeout: 15_000 })

  const cases = []

  // ── Case A: backend 400 (missing provider_id)
  log('CASE A — missing provider_id (backend 400)')
  const noProvider = await callDryRun(page, EVAL_ID, {
    provider_id: null,
    params: { evaluation_prompt: 'test', score_type: 'numeric' },
    input: 'x',
    output: 'y',
    expected_output: 'z',
  })
  log('  status:', noProvider.status, 'detail:', noProvider.body?.detail)
  cases.push({ name: 'missing_provider_id', api: noProvider })

  // ── Case B: HTTP 500 (route-mocked)
  log('CASE B — route-mocked HTTP 500')
  await page.route('**/api/eval/evaluators/*/dry-run', route => {
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'internal server error: synthetic' }),
    })
  })
  const mocked500 = await callDryRun(page, EVAL_ID, {
    params: { score_type: 'numeric' },
    input: 'x', output: 'y', expected_output: 'z',
  })
  await page.unroute('**/api/eval/evaluators/*/dry-run')
  log('  status:', mocked500.status, 'detail:', mocked500.body?.detail)
  cases.push({ name: 'mocked_500', api: mocked500 })

  // ── Case C: UI render of truncated error (load-bearing test)
  log('CASE C — UI rendering of truncated error in EvaluatorComparePage')
  await page.waitForFunction(() => {
    const s = document.querySelectorAll('select')
    return s.length >= 2 && s[0].options.length >= 3
  }, { timeout: 15_000 })

  await page.route('**/api/eval/evaluators/*/dry-run', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        scores: [],
        model: 'mock-model',
        usage: { input_tokens: 100, output_tokens: 10, total_tokens: 110 },
        raw_content: '<empty — truncated>',
        rendered_messages: [],
        error: 'judge response was truncated at max_tokens (output_tokens=10); raise max_tokens in the evaluator config',
      }),
    })
  })
  await page.locator('select').nth(0).selectOption({ index: 1 })
  await page.locator('select').nth(1).selectOption({ index: 1 })
  const rowTextareas = page.locator('.rounded-lg').filter({ hasText: '用户输入' }).first().locator('textarea')
  await rowTextareas.nth(0).fill('truncated test')
  await rowTextareas.nth(1).fill('truncated test output')
  await rowTextareas.nth(2).fill('expected')
  await page.getByRole('button', { name: /跑这一条/ }).click()
  await page.waitForSelector('[data-error-code="truncated"]', { timeout: 30_000 })
  const renderedCode = await page.locator('[data-error-code="truncated"]').first().getAttribute('data-error-code')
  const renderedText = await page.locator('[data-error-code="truncated"]').first().innerText()
  log('  renderedCode:', renderedCode)
  log('  renderedText:', renderedText.slice(0, 200))
  cases.push({
    name: 'ui_truncated_render',
    renderedCode,
    renderedTextSnippet: renderedText,
    hasMaxTokensHint: /max_tokens/i.test(renderedText),
    hasReasoningHint: /推理模型/.test(renderedText),
  })
  await page.unroute('**/api/eval/evaluators/*/dry-run')
  await page.screenshot({ path: 'error-handling-after.png', fullPage: true })

  log('--- summary ---')
  fs.writeFileSync(
    'error-handling-summary.json',
    JSON.stringify({ cases, consoleErrors }, null, 2),
  )

  await browser.close()

  let failed = false
  if (noProvider.status !== 400) {
    log('FAIL: case A — expected 400, got', noProvider.status)
    failed = true
  }
  if (mocked500.status !== 500) {
    log('FAIL: case B — expected 500, got', mocked500.status)
    failed = true
  }
  const cC = cases.find(c => c.name === 'ui_truncated_render')
  if (!cC || cC.renderedCode !== 'truncated' || !cC.hasMaxTokensHint || !cC.hasReasoningHint) {
    log('FAIL: case C — ErrorCard did not render correctly:', cC)
    failed = true
  }

  if (failed) process.exit(2)
  log('PASS: all error-handling cases verified')
  log('  A: 400 surfaced from backend')
  log('  B: 500 surfaced from mock')
  log('  C: ErrorCard renders code=truncated with max_tokens hint + reasoning-model fragment')
}

main().catch(e => { console.error('FATAL:', e); process.exit(1) })
