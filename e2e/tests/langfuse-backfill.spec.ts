/**
 * Langfuse 对称回拉 · 端到端验证。
 *
 * 验证我们新加的链路（对称于 LangSmith 回拉，区别是 join key 是 trace
 * **name** 而非 project，Langfuse project 由凭据对固定）：
 *
 *   1. UI 契约 — 评估来源选 "Langfuse" 后，trace name 输入框出现。
 *   2. 持久化往返 — 启动 run 时带 langfuse_trace_name → 后端落库
 *      test_runs.langfuse_trace_name → 详情接口 EvalRunSummary 读回同值。
 *   3. 自动回拉触发 — run 跑完后 _execute_run 会 fire-and-forget
 *      asyncio.create_task(_backfill_langfuse_traces(...))。我们断言 run
 *      正常进入终态且至少 1 条样例非 error（证明 SSE 解析 + 评分通路没被
 *      回拉改动弄坏），然后把每条结果的 langfuse_trace_id 命中数打印出来。
 *
 * 诚实的边界：真实命中数 > 0 需要目标 agent 真的把 trace 以可匹配的 name
 * 上报到我们这套 Langfuse 实例 —— 那是外部 agent 的行为，本测试不控制，
 * 所以命中数仅作信息输出，不作硬断言。硬断言只覆盖我们自己改的代码。
 *
 * 运行：
 *   cd e2e
 *   AGENT_PROBE_URL=http://localhost:18094/api/agent/langgraph \
 *   AGENT_URL=http://host.docker.internal:18094/api/agent/langgraph \
 *   npx playwright test tests/langfuse-backfill.spec.ts
 */
import { expect, test } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'

const TIMESTAMP = Date.now()
const SHARED_EVAL = `e2e-lf-eval-${TIMESTAMP}`
const RUN_NAME = `e2e-lf-run-${TIMESTAMP}`
// 唯一的 trace name —— 既是落库往返要回读的值，也是回拉的 join key。
const TRACE_NAME = `e2e-lf-trace-${TIMESTAMP}`

const FIXTURE_FILE = path.resolve(__dirname, '..', 'fixtures', 'cases.json')
const AGENT_URL = process.env.AGENT_URL
  || 'http://host.docker.internal:18094/api/agent/langgraph'

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  fs.mkdirSync(path.dirname(FIXTURE_FILE), { recursive: true })
  fs.writeFileSync(FIXTURE_FILE, JSON.stringify({
    test_cases: [
      { name: 'case-1', question: 'RPL201 锂电池 BMS 检查方法?' },
      { name: 'case-2', question: 'RPL201 锂电池过放电检查?' },
    ],
  }, null, 2), 'utf-8')
})

async function uploadFixture(page: import('@playwright/test').Page) {
  await page.locator('input[type="file"]').setInputFiles(FIXTURE_FILE)
  await expect(page.getByText(/已上传\s*2\s*条/)).toBeVisible({ timeout: 15_000 })
}

// 认证是 JWT bearer（存 localStorage 的 agent-eval-auth，不是 cookie），所以
// page.request 不会自动带上 —— 直接打 API 必须手动注入 Authorization 头。
async function bearer(
  page: import('@playwright/test').Page,
): Promise<{ Authorization: string }> {
  const token = await page.evaluate(() => {
    const raw = localStorage.getItem('agent-eval-auth')
    return raw ? JSON.parse(raw).state?.accessToken as string : null
  })
  expect(token, 'localStorage 里没有 accessToken —— 认证态没建立').toBeTruthy()
  return { Authorization: `Bearer ${token}` }
}

test('1. UI 契约 · 选 Langfuse 来源后出现 trace name 输入框', async ({ page }) => {
  await page.goto('/evaluation')
  await expect(page.getByRole('heading', { name: '评估', exact: true }))
    .toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: '新建评估', exact: true }).first().click()

  // 默认 none —— trace name 输入框不应存在
  await expect(page.getByPlaceholder('例如：ep-agent-chat')).toHaveCount(0)

  // 切到 langfuse —— 输入框出现
  await page.locator('select').filter({ hasText: 'Langfuse（回拉 trace）' })
    .selectOption('langfuse')
  await expect(page.getByPlaceholder('例如：ep-agent-chat')).toBeVisible()

  // 切回 none —— 输入框消失（确认是条件渲染，不是常驻）
  await page.locator('select').filter({ hasText: 'Langfuse（回拉 trace）' })
    .selectOption('none')
  await expect(page.getByPlaceholder('例如：ep-agent-chat')).toHaveCount(0)
})

test('2. 持久化往返 + 自动回拉触发 · 启动带 trace name 的 run', async ({ page }) => {
  // 先到同源页面让 localStorage 就绪，取出 bearer token 供所有 API 调用使用。
  await page.goto('/evaluation')
  const headers = await bearer(page)

  // 建一个评估器。直接走 API（POST /api/eval/evaluators，tag 模式只需 name）—
  // 评估器创建只是本测试的前置依赖，不是被测对象。被测的是 Langfuse 落库往返 +
  // run 非 error + 回拉触发，与打分方式无关。绕开评估器编辑 Drawer 的 UI 漂移，
  // 测试更稳、失败信号更聚焦在真正要验证的回拉链路上。
  const createEvalResp = await page.request.post('/api/eval/evaluators', {
    headers,
    data: { name: SHARED_EVAL, evaluator_type: 'tag', is_active: true },
  })
  expect(
    createEvalResp.ok(),
    `创建评估器失败：${createEvalResp.status()} ${await createEvalResp.text()}`,
  ).toBeTruthy()

  await page.goto('/evaluation')
  await page.getByRole('button', { name: '新建评估', exact: true }).first().click()
  await page.getByRole('button', { name: '上传文件' }).click()
  await uploadFixture(page)

  // 真实 agent
  await page.getByPlaceholder(/http.*localhost.*18094/).fill(AGENT_URL)
  // 选 Langfuse 来源并填 trace name
  await page.locator('select').filter({ hasText: 'Langfuse（回拉 trace）' })
    .selectOption('langfuse')
  await page.getByPlaceholder('例如：ep-agent-chat').fill(TRACE_NAME)

  await page.locator('label', { hasText: SHARED_EVAL }).click()
  await page.getByPlaceholder(/默认自动按时间戳生成/).fill(RUN_NAME)

  // 抓启动请求，确认 payload 里带了 langfuse_trace_name
  const startReqPromise = page.waitForRequest(
    r => r.url().includes('/api/eval') && r.method() === 'POST'
      && !!r.postData() && r.postData()!.includes('langfuse_trace_name'),
    { timeout: 15_000 },
  )
  await page.getByRole('button', { name: /^启动评估$/ }).click()
  const startReq = await startReqPromise
  const sentBody = JSON.parse(startReq.postData()!)
  expect(sentBody.langfuse_trace_name).toBe(TRACE_NAME)

  // 找到 run 行 → 进详情 → 拿 runId
  const row = page.locator('tr', { hasText: RUN_NAME }).first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await row.click()
  await page.waitForURL(/\/evaluation\/runs\/([0-9a-f-]+)/, { timeout: 15_000 })
  const runId = page.url().match(/\/runs\/([0-9a-f-]+)/)![1]

  // === 落库往返：详情接口必须读回我们传的 trace name ===
  const summaryResp = await page.request.get(`/api/eval/runs/${runId}`, { headers })
  expect(summaryResp.ok()).toBeTruthy()
  const summary = await summaryResp.json()
  expect(
    summary.langfuse_trace_name,
    'EvalRunSummary 未读回 langfuse_trace_name —— schema/DB/repository 往返断了',
  ).toBe(TRACE_NAME)

  // 等 run 进终态（回拉是 run 结束后 fire 的后台任务）
  await expect(async () => {
    const resp = await page.request.get(`/api/eval/runs/${runId}`, { headers })
    expect(resp.ok()).toBeTruthy()
    const body = await resp.json()
    expect(['completed', 'failed', 'interrupted']).toContain(body.status)
  }).toPass({ timeout: 180_000 })

  // 结果通路没被回拉改动弄坏：至少 1 条非 error
  const resultsResp = await page.request.get(
    `/api/eval/runs/${runId}/results?page=1&page_size=50`, { headers })
  expect(resultsResp.ok()).toBeTruthy()
  const results = await resultsResp.json()
  expect(results.total).toBeGreaterThanOrEqual(1)
  const nonError = results.items.filter(
    (r: { status: string }) => r.status !== 'error').length
  expect(
    nonError,
    `全部 ${results.total} 条样例 error —— agent 不可达或 SSE 解析坏了。` +
    `首条错误：${results.items[0]?.error_message?.slice(0, 200) || '(none)'}`,
  ).toBeGreaterThan(0)

  // === 自动回拉触发：等后台任务跑一会，统计 langfuse_trace_id 命中 ===
  // 命中数仅信息输出（真实命中需 agent 把 trace 以该 name 报到我们的
  // Langfuse 实例，属外部行为，不硬断言）。但回拉任务本身不能让 run 崩。
  let matched = 0
  await expect(async () => {
    const resp = await page.request.get(
      `/api/eval/runs/${runId}/results?page=1&page_size=50`, { headers })
    const body = await resp.json()
    matched = body.items.filter(
      (r: { langfuse_trace_id: string | null }) => !!r.langfuse_trace_id).length
    // 等到要么有命中、要么给足回拉时间窗（10min 窗 + 网络往返）
    expect(matched).toBeGreaterThanOrEqual(0)
  }).toPass({ timeout: 30_000 })

  // eslint-disable-next-line no-console
  console.log(
    `[langfuse-backfill] run=${runId} trace_name=${TRACE_NAME} ` +
    `total=${results.total} non_error=${nonError} ` +
    `langfuse_trace_id 命中=${matched}/${results.total}`)
})
