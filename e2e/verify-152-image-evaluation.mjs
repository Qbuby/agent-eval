// #148/#152 headed 验收：带图基准样例 → 真实评估 → agent 收到 image block
// → test_results 冻结 question_content → 结果详情抽屉真实渲染图片。
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const authPath = path.join(root, 'auth.json')
const screenshotPath = path.join(root, 'shot-152-image-evaluation.png')
const outPath = path.join(root, 'result-152-image-evaluation.json')
const projectId = 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'
const stamp = Date.now()
const marker = `IMAGE-EVAL-${stamp}`
const pngDataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAFUlEQVR4nGP8z8DAwMDAxAADEDYDAB0BAd/rMgloAAAAAElFTkSuQmCC'

if (!fs.existsSync(authPath)) throw new Error(`缺少登录态文件: ${authPath}`)
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

let caseId = null
let browser = null
const evidence = { marker, screenshot: screenshotPath }
try {
  // 后端入口会把 data URL 归一成 canonical base64 source，同时保留文本块。
  const created = await api(`/api/benchmark/${projectId}/cases`, {
    method: 'POST',
    body: JSON.stringify({
      question: [
        { type: 'text', text: marker },
        { type: 'image', source: { type: 'url', url: pngDataUrl }, name: 'acceptance.png' },
      ],
      reference_answer: 'IMAGE_BLOCK_RECEIVED',
      key_points: ['agent 必须收到 image block'],
      tags: ['acceptance-image'],
    }),
  })
  caseId = created.id
  evidence.case_id = caseId

  let evaluators = await api('/api/eval/evaluators?active_only=true')
  let evaluator = evaluators.find(e => e.evaluator_type === 'exact_match')
  if (!evaluator) {
    evaluator = await api('/api/eval/evaluators', {
      method: 'POST',
      body: JSON.stringify({
        name: `图片验收精确匹配-${stamp}`,
        tag: `image-acceptance-${stamp}`,
        evaluator_type: 'exact_match',
        description: '#152 图片验收临时评估器',
        params: {},
        is_active: true,
      }),
    })
  }

  await fetch('http://localhost:18001/reset')

  browser = await chromium.launch({ headless: false, slowMo: 40 })
  const context = await browser.newContext({
    baseURL: 'http://localhost',
    storageState: authPath,
    viewport: { width: 1440, height: 960 },
  })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', e => consoleErrors.push(e.message))

  await page.goto('/evaluation', { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('登录态失效，跳转 /login')

  // 在真实页面上下文发起运行；请求经过 localhost nginx → backend，非进程内调用。
  const started = await page.evaluate(async body => {
    const raw = localStorage.getItem('agent-eval-auth')
    const accessToken = raw ? JSON.parse(raw).state?.accessToken : ''
    const response = await fetch('/api/eval/runs/start', {
      method: 'POST',
      headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const text = await response.text()
    if (!response.ok) throw new Error(`启动评估失败 ${response.status}: ${text}`)
    return JSON.parse(text)
  }, {
    project_id: projectId,
    case_ids: [caseId],
    agent: {
      type: 'sse',
      url: 'http://mock-multimodal-agent:8001',
      model: 'multimodal-acceptance-mock',
      timeout: 30,
    },
    evaluator_ids: [evaluator.id],
    concurrency: 1,
    run_name: `带图真实评估-${marker}`,
  })
  const runId = started.run_id
  evidence.run_id = runId

  const deadline = Date.now() + 120_000
  let run = null
  while (Date.now() < deadline) {
    run = await api(`/api/eval/runs/${runId}`)
    if (['completed', 'failed', 'interrupted'].includes(run.status)) break
    await new Promise(resolve => setTimeout(resolve, 750))
  }
  if (!run || run.status !== 'completed') {
    throw new Error(`运行未成功完成: ${JSON.stringify(run)}`)
  }

  const results = await api(`/api/eval/runs/${runId}/results?page=1&page_size=20`)
  if (results.total !== 1) throw new Error(`结果数应为 1，实际 ${results.total}`)
  const item = results.items[0]
  evidence.result_id = item.id
  evidence.result_status = item.status
  evidence.question_content = item.question_content
  evidence.actual_output = item.actual_output
  if (!Array.isArray(item.question_content)) throw new Error('结果 API 未返回 question_content blocks')
  if (!item.question_content.some(b => b?.type === 'image')) throw new Error('结果快照中没有 image block')
  if (!(item.actual_output || '').includes('IMAGE_BLOCK_RECEIVED')) {
    throw new Error(`agent 回答未确认图片: ${JSON.stringify(item.actual_output)}`)
  }

  const mockResponse = await fetch('http://localhost:18001/last')
  const mockLast = await mockResponse.json()
  evidence.mock_last = mockLast
  if (mockLast.image_received !== true) throw new Error(`mock 未收到 image block: ${JSON.stringify(mockLast)}`)
  if (!Array.isArray(mockLast.question)) throw new Error('mock 收到的 question 不是 blocks 数组')

  await page.goto(`/evaluation/runs/${runId}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  const resultRow = page.locator('tbody tr', { hasText: marker }).first()
  await resultRow.waitFor({ state: 'visible', timeout: 20_000 })
  await resultRow.click()

  const questionArea = page.getByTestId('result-question-content')
  await questionArea.waitFor({ state: 'visible', timeout: 20_000 })
  const image = questionArea.locator('img').first()
  await image.waitFor({ state: 'visible', timeout: 10_000 })
  const imageMetrics = await image.evaluate(el => ({
    naturalWidth: el.naturalWidth,
    naturalHeight: el.naturalHeight,
    srcPrefix: el.getAttribute('src')?.slice(0, 32),
  }))
  evidence.image = imageMetrics
  if (imageMetrics.naturalWidth !== 3 || imageMetrics.naturalHeight !== 2) {
    throw new Error(`详情图片未正确解码: ${JSON.stringify(imageMetrics)}`)
  }
  await page.getByText('IMAGE_BLOCK_RECEIVED', { exact: false }).first()
    .waitFor({ state: 'visible', timeout: 10_000 })
  await page.screenshot({ path: screenshotPath, fullPage: true })

  const bundleSrcs = await page.locator('script[src]').evaluateAll(els => els.map(e => e.getAttribute('src')))
  evidence.bundle = bundleSrcs.find(src => /index-.*\.js/.test(src || '')) || null
  evidence.console_errors = consoleErrors
  if (consoleErrors.length) throw new Error(`页面异常: ${consoleErrors.join(' | ')}`)
  evidence.ok = true
  fs.writeFileSync(outPath, JSON.stringify(evidence, null, 2), 'utf8')
  console.log(JSON.stringify(evidence, null, 2))
} catch (error) {
  evidence.ok = false
  evidence.error = error.stack || String(error)
  fs.writeFileSync(outPath, JSON.stringify(evidence, null, 2), 'utf8')
  console.error(evidence.error)
  process.exitCode = 1
} finally {
  if (browser) await browser.close().catch(() => {})
  // 清理源样例；test_results 的独立快照保留，正好验证结果不依赖源行存活。
  if (caseId) await api(`/api/benchmark/cases/${caseId}`, { method: 'DELETE' }).catch(() => {})
}
