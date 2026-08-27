import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(.:)/, '$1'))
const authPath = path.join(root, 'auth.json')
const fixturePath = path.join(root, 'fixtures', 'reference-criteria-ui.json')
const screenshotPath = path.join(root, 'reference-criteria-ui.png')
const marker = `criteria-ui-${Date.now()}`
const criteria = [
  `必须包含冻结关键点甲-${marker}`,
  `必须包含冻结关键点乙-${marker}`,
]
const expectedOutput = `期望答案-${marker}`
const question = `MOCKTOOL 关键点快照验收 ${marker}`
const runName = `关键点快照UI验收-${marker}`

fs.mkdirSync(path.dirname(fixturePath), { recursive: true })
fs.writeFileSync(fixturePath, JSON.stringify({
  test_cases: [{
    name: marker,
    question,
    expected_output: expectedOutput,
    expected_output_criteria: criteria,
  }],
}, null, 2), 'utf8')

const auth = JSON.parse(fs.readFileSync(authPath, 'utf8'))
const authValue = auth.origins
  ?.find(o => o.origin === 'http://localhost')
  ?.localStorage?.find(x => x.name === 'agent-eval-auth')?.value
if (!authValue) throw new Error('auth.json 中没有 agent-eval-auth')
const token = JSON.parse(authValue).state.accessToken

async function api(pathname, init = {}) {
  const response = await fetch(`http://localhost${pathname}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  })
  const text = await response.text()
  if (!response.ok) throw new Error(`${init.method || 'GET'} ${pathname} -> ${response.status}: ${text}`)
  return text ? JSON.parse(text) : null
}

let evaluators = await api('/api/eval/evaluators?active_only=true')
let evaluator = evaluators.find(e => e.evaluator_type === 'exact_match')
if (!evaluator) {
  evaluator = await api('/api/eval/evaluators', {
    method: 'POST',
    body: JSON.stringify({
      name: `UI验收精确匹配-${marker}`,
      tag: `ui-reference-${marker}`,
      evaluator_type: 'exact_match',
      description: '关键点快照 UI 验收临时评估器',
      params: {},
      is_active: true,
    }),
  })
}

const browser = await chromium.launch({ headless: false })
const context = await browser.newContext({
  baseURL: 'http://localhost',
  storageState: authPath,
  viewport: { width: 1440, height: 900 },
})
const page = await context.newPage()

try {
  await page.goto('/evaluation', { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('验收登录态已失效，页面跳转到 /login')

  await page.getByRole('button', { name: '新建评估', exact: true }).first().click()
  await page.getByRole('button', { name: '上传文件', exact: true }).click()
  await page.locator('input[type="file"]').setInputFiles(fixturePath)
  await page.getByText(/已上传\s*1\s*条/).waitFor({ state: 'visible', timeout: 20_000 })
  await page.getByText(marker, { exact: false }).first().waitFor({ state: 'visible', timeout: 10_000 })

  await page.getByPlaceholder('http://localhost:18094/api/agent/langgraph').fill('http://mock-agent:8000')
  const evaluatorLabel = page.locator('label', { hasText: evaluator.name }).first()
  await evaluatorLabel.waitFor({ state: 'visible', timeout: 20_000 })
  await evaluatorLabel.locator('input[type="checkbox"]').check()
  await page.getByPlaceholder('默认自动按时间戳生成').fill(runName)

  const startButton = page.getByRole('button', { name: '启动评估', exact: true })
  if (await startButton.isDisabled()) {
    throw new Error(`启动按钮仍禁用：${await startButton.getAttribute('title')}`)
  }
  const startResponsePromise = page.waitForResponse(
    response => response.url().includes('/api/eval/runs/start') && response.request().method() === 'POST',
    { timeout: 30_000 },
  )
  await startButton.click()
  const startResponse = await startResponsePromise
  const startText = await startResponse.text()
  if (!startResponse.ok()) throw new Error(`启动评估失败 ${startResponse.status()}: ${startText}`)
  const started = JSON.parse(startText)
  const runId = started.run_id

  const deadline = Date.now() + 120_000
  let run
  while (Date.now() < deadline) {
    run = await api(`/api/eval/runs/${runId}`)
    if (['completed', 'failed', 'interrupted'].includes(run.status)) break
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
  if (!run || !['completed', 'failed', 'interrupted'].includes(run.status)) {
    throw new Error(`运行 ${runId} 在 120 秒内未结束`)
  }

  const results = await api(`/api/eval/runs/${runId}/results?page=1&page_size=50`)
  if (results.total !== 1) throw new Error(`结果数应为 1，实际为 ${results.total}`)
  const item = results.items[0]
  if (item.question !== question) throw new Error(`question 快照不一致：${item.question}`)
  if (item.expected_output !== expectedOutput) {
    throw new Error(`expected_output 快照不一致：${JSON.stringify(item.expected_output)}`)
  }
  if (JSON.stringify(item.expected_output_criteria) !== JSON.stringify(criteria)) {
    throw new Error(`criteria 快照不一致：${JSON.stringify(item.expected_output_criteria)}`)
  }
  if (!(item.actual_output || '').includes('mock agent')) {
    throw new Error(`mock agent 回答未落库：${JSON.stringify(item.actual_output)}`)
  }

  await page.goto(`/evaluation/runs/${runId}`, { waitUntil: 'domcontentloaded' })
  const resultRow = page.locator('tbody tr').filter({ hasText: question }).first()
  await resultRow.waitFor({ state: 'visible', timeout: 20_000 })
  await resultRow.click()
  await page.getByText(`评估参考依据 (${criteria.length})`, { exact: true })
    .waitFor({ state: 'visible', timeout: 20_000 })
  for (const criterion of criteria) {
    await page.getByText(criterion, { exact: true })
      .waitFor({ state: 'visible', timeout: 10_000 })
  }
  await page.screenshot({ path: screenshotPath, fullPage: true })

  console.log(JSON.stringify({
    ok: true,
    run_id: runId,
    result_id: item.id,
    run_status: run.status,
    result_status: item.status,
    expected_output: item.expected_output,
    expected_output_criteria: item.expected_output_criteria,
    screenshot: screenshotPath,
  }))
} finally {
  await browser.close()
}
