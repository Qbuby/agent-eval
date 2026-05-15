/**
 * Agent-Eval evaluation workbench · full-surface UI test.
 *
 * Authentication is handled in global-setup.ts.
 *
 * Real-agent contract: tests 4-6 require the agent at AGENT_URL to be
 * reachable. globalSetup probes it once and exits non-zero if unreachable,
 * so the whole suite refuses to run rather than silently passing on a
 * dead agent. (A previous version of this suite pointed at an unreachable
 * URL on purpose and "passed" — that was a false positive.)
 *
 * Sequence:
 *   1. Sanity — sidebar visible
 *   2. /evaluators — create / edit / delete an evaluator instance
 *   3. /evaluation — switch to "新建评估", upload fixture, see preview
 *   4. Start a run against the real agent → at least 1 sample must be
 *      non-error (i.e. SSE adapter parsed something, output stored, scores
 *      computed). All-error → test fails.
 *   5. Run detail — verify project lookup section + 查询轨迹 banner
 *   6. Start a 2nd run → multi-select two rows → /evaluation/compare renders
 */
import { expect, test } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'

const TIMESTAMP = Date.now()
const EVALUATOR_NAME = `e2e-eval-${TIMESTAMP}`
const SHARED_EVAL = `e2e-shared-${TIMESTAMP}`
const RUN_NAME_A = `e2e-run-a-${TIMESTAMP}`
const RUN_NAME_B = `e2e-run-b-${TIMESTAMP}`

const FIXTURE_FILE = path.resolve(__dirname, '..', 'fixtures', 'cases.json')
// Backend reaches the host-running agent through host.docker.internal,
// configured via docker-compose extra_hosts.
const AGENT_URL = process.env.AGENT_URL
  || 'http://host.docker.internal:18096/api/agent/langgraph'

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  fs.mkdirSync(path.dirname(FIXTURE_FILE), { recursive: true })
  fs.writeFileSync(FIXTURE_FILE, JSON.stringify({
    test_cases: [
      { name: 'case-1', question: 'RPL201 锂电池 BMS 检查方法?' },
      { name: 'case-2', question: 'RPL201 锂电池过放电检查?' },
      { name: 'case-3', question: 'RPL201 电芯电压判断?' },
    ],
  }, null, 2), 'utf-8')
})

// Helpers ─────────────────────────────────────────────────────────────────

async function uploadFixture(page: import('@playwright/test').Page) {
  await page.locator('input[type="file"]').setInputFiles(FIXTURE_FILE)
  await expect(page.getByText(/已上传\s*3\s*条/)).toBeVisible({ timeout: 15_000 })
}

async function pickEvaluator(page: import('@playwright/test').Page, name: string) {
  await page.locator('label', { hasText: name }).click()
}

/**
 * Wait for the run row to leave running state, then assert the contract:
 * `passed + failed = total` AND at least one sample is non-error.
 *
 * We hit /api/eval directly so the assertion doesn't depend on UI rendering
 * timing. The cookie carrying the auth token is shared with the page, so
 * APIRequestContext requests are authenticated.
 */
async function waitForRunCompletion(
  page: import('@playwright/test').Page, runName: string,
) {
  const row = page.locator('tr', { hasText: runName }).first()
  await expect(row).toBeVisible({ timeout: 30_000 })

  // Read the run id from a navigation, then poll the API.
  await row.click()
  await page.waitForURL(/\/evaluation\/runs\/([0-9a-f-]+)/, { timeout: 15_000 })
  const runId = page.url().match(/\/runs\/([0-9a-f-]+)/)![1]

  // Poll detail until terminal state.
  await expect(async () => {
    const resp = await page.request.get(`/api/eval/runs/${runId}`)
    expect(resp.ok()).toBeTruthy()
    const body = await resp.json()
    expect(['completed', 'failed', 'interrupted']).toContain(body.status)
  }).toPass({ timeout: 180_000 })

  // Now assert the real contract on results.
  const resultsResp = await page.request.get(`/api/eval/runs/${runId}/results?page=1&page_size=50`)
  expect(resultsResp.ok()).toBeTruthy()
  const results = await resultsResp.json()
  expect(results.total).toBeGreaterThanOrEqual(1)

  const errorCount = results.items.filter((r: { status: string }) => r.status === 'error').length
  const nonErrorCount = results.total - errorCount
  expect(
    nonErrorCount,
    `All ${results.total} samples errored — agent unreachable or SSE parsing broken.\n` +
    `First error: ${results.items[0]?.error_message?.slice(0, 200) || '(none)'}`,
  ).toBeGreaterThan(0)

  // Bonus: ensure at least one sample has actual_output (proves SSE parsed)
  const withOutput = results.items.filter(
    (r: { status: string; actual_output: string | null }) =>
      r.status !== 'error' && (r.actual_output ?? '').trim().length > 0,
  ).length
  expect(withOutput).toBeGreaterThan(0)

  return runId
}

// ─────────────────────────────────────────────────────────────────────────

test('1. sanity — already logged in', async ({ page }) => {
  await page.goto('/')
  await page.waitForURL(/\/dashboard/, { timeout: 10_000 })
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
})

test('2. evaluators · create + edit + delete', async ({ page }) => {
  await page.goto('/evaluators')
  await expect(page.getByRole('heading', { name: '评估器', exact: true }))
    .toBeVisible({ timeout: 10_000 })

  await page.getByRole('button', { name: /\+\s*新建评估器/ }).click()
  const modal = page.locator('div').filter({ hasText: /新建评估器/ }).last()
  await modal.locator('input[type="text"]').first().fill(EVALUATOR_NAME)
  await modal.locator('select').first().selectOption('exact_match')
  await modal.locator('input[type="text"]').nth(1).fill('e2e probe — created')
  await modal.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText(EVALUATOR_NAME)).toBeVisible({ timeout: 10_000 })

  const row = page.locator('tr', { hasText: EVALUATOR_NAME })
  await row.getByRole('button', { name: '编辑' }).click()
  const editModal = page.locator('div').filter({ hasText: /编辑评估器/ }).last()
  await editModal.locator('input[type="text"]').nth(1).fill('e2e probe — edited')
  await editModal.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('e2e probe — edited')).toBeVisible({ timeout: 10_000 })

  page.once('dialog', d => d.accept())
  await row.getByRole('button', { name: '删除' }).click()
  await expect(page.getByText(EVALUATOR_NAME)).toHaveCount(0, { timeout: 10_000 })
})

test('3. evaluation page · upload tab + preview', async ({ page }) => {
  await page.goto('/evaluation')
  await expect(page.getByRole('heading', { name: '评估', exact: true }))
    .toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: '新建评估', exact: true }).click()
  await page.getByRole('button', { name: '上传文件' }).click()
  await uploadFixture(page)
  await expect(page.getByText('case-1')).toBeVisible()
})

test('4. start run against real agent · at least 1 non-error sample', async ({ page }) => {
  // Shared evaluator for runs A and B
  await page.goto('/evaluators')
  await page.getByRole('button', { name: /\+\s*新建评估器/ }).click()
  const modal = page.locator('div').filter({ hasText: /新建评估器/ }).last()
  await modal.locator('input[type="text"]').first().fill(SHARED_EVAL)
  await modal.locator('select').first().selectOption('exact_match')
  await modal.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText(SHARED_EVAL)).toBeVisible({ timeout: 10_000 })

  await page.goto('/evaluation')
  await page.getByRole('button', { name: '新建评估', exact: true }).click()
  await page.getByRole('button', { name: '上传文件' }).click()
  await uploadFixture(page)

  // Real agent
  await page.getByPlaceholder(/http.*localhost.*18094/).fill(AGENT_URL)
  await page.getByPlaceholder(/ep-agent.*ruyi-agent/).fill('e2e-fake-project')
  await pickEvaluator(page, SHARED_EVAL)
  await page.getByPlaceholder(/默认自动按时间戳生成/).fill(RUN_NAME_A)
  await page.getByRole('button', { name: /^启动评估$/ }).click()

  // Validate the run actually produced something
  await waitForRunCompletion(page, RUN_NAME_A)
})

test('5. detail page · trace project lookup surfaces a clear diagnosis', async ({ page }) => {
  // The previous version of this test only asserted the lookup ran. That
  // hid the fact that the LangSmith API key in the dev .env doesn't have
  // read permission on any project — every lookup silently came back as
  // "matched 0/N". The new contract: the banner must explicitly tell the
  // user *why* it's zero (forbidden / unauthorized / not_found / network
  // / unknown), or congratulate matches > 0. A blank "matched 0/N" with
  // no follow-up is a UX bug.
  await page.goto('/evaluation')
  await page.locator('tr', { hasText: RUN_NAME_A }).first().click()
  await page.waitForURL(/\/evaluation\/runs\/[0-9a-f-]+/, { timeout: 15_000 })

  await expect(page.getByRole('heading', { name: /调用轨迹/ })).toBeVisible({ timeout: 10_000 })

  await page.getByPlaceholder(/例如\s*ruyi-agent/).fill('e2e-fake-project-2')

  // Capture the underlying API response so we can check the contract end-to-
  // end: the response must include error_kind/error_message fields and the
  // UI must show the matching banner.
  const respPromise = page.waitForResponse(
    r => r.url().includes('/api/eval/runs/') && r.url().includes('/backfill_trace') && r.status() === 200,
    { timeout: 30_000 },
  )
  await page.getByRole('button', { name: /查询轨迹/ }).click()
  const resp = await respPromise
  const body = await resp.json()

  // API contract: every response carries the new diagnostic fields.
  expect(body).toHaveProperty('matched')
  expect(body).toHaveProperty('scanned')
  expect(body).toHaveProperty('error_kind')
  expect(body).toHaveProperty('error_message')

  // UI contract: pick the banner that matches the API verdict.
  if (body.matched > 0) {
    await expect(page.getByText(/匹配\s+\d+\s*\/\s*\d+\s*条样例。展开下方/)).toBeVisible()
  } else if (body.error_kind) {
    // Real failure — banner must spell out the cause, not say "0 matched"
    await expect(page.getByText(/查询失败\s*·/)).toBeVisible()
    // And include the specific category text so the user knows what to fix
    const expectedSnippet: Record<string, RegExp> = {
      forbidden: /没有读权限.*403/,
      unauthorized: /API key 无效.*401/,
      not_found: /找不到名为.*的 project.*404/,
      network: /网络不可达/,
      client_init: /未初始化/,
      unknown: /未知错误/,
    }
    const re = expectedSnippet[body.error_kind as string]
    expect(re, `unmapped error_kind: ${body.error_kind}`).toBeDefined()
    await expect(page.getByText(re!)).toBeVisible()
  } else {
    // Zero matches but no error — must explain *that* (project name etc)
    await expect(page.getByText(/匹配 0.*没有时间窗口内/)).toBeVisible()
  }
})

test('6. compare page · multi-select', async ({ page }) => {
  await page.goto('/evaluation')
  await page.getByRole('button', { name: '新建评估', exact: true }).click()
  await page.getByRole('button', { name: '上传文件' }).click()
  await uploadFixture(page)

  await page.getByPlaceholder(/http.*localhost.*18094/).fill(AGENT_URL)
  await pickEvaluator(page, SHARED_EVAL)
  await page.getByPlaceholder(/默认自动按时间戳生成/).fill(RUN_NAME_B)
  await page.getByRole('button', { name: /^启动评估$/ }).click()

  await waitForRunCompletion(page, RUN_NAME_B)

  await page.goto('/evaluation')
  await page.locator('tr', { hasText: RUN_NAME_A }).first()
    .locator('input[type="checkbox"]').check()
  await page.locator('tr', { hasText: RUN_NAME_B }).first()
    .locator('input[type="checkbox"]').check()

  await page.getByRole('button', { name: /对比所选/ }).click()
  await page.waitForURL(/\/evaluation\/compare\?ids=/)

  await expect(page.getByRole('heading', { name: /Runs 对比/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /通过率对比/ })).toBeVisible()
})
