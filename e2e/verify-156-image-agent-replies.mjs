// #156 headed 验收：benchmark / candidate 带图样例生成 agent 回复时，
// canonical image block 必须原样送到 agent，不能退化成含 [图片] 的纯文本。
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const authPath = path.join(root, 'auth.json')
const outPath = path.join(root, 'result-156-image-agent-replies.json')
const shotPath = path.join(root, 'shot-156-image-agent-replies.png')
const projectId = 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'
const candidateDataset = 'probe-ds-1785299523343'
const stamp = Date.now()
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

async function mock(pathname) {
  const response = await fetch(`http://localhost:18001${pathname}`)
  if (!response.ok) throw new Error(`mock ${pathname} -> ${response.status}`)
  return response.json()
}

async function waitJob(jobId) {
  const deadline = Date.now() + 120_000
  let job = null
  while (Date.now() < deadline) {
    job = await api(`/api/agent-replies/jobs/${jobId}`)
    if (['completed', 'failed', 'cancelled'].includes(job.status)) return job
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error(`生成任务超时: ${jobId} ${JSON.stringify(job)}`)
}

async function runGeneration(page, { datasetType, caseId, datasetName = null, projectId: pid = null }) {
  await mock('/reset')
  const started = await page.evaluate(async body => {
    const raw = localStorage.getItem('agent-eval-auth')
    const accessToken = raw ? JSON.parse(raw).state?.accessToken : ''
    const response = await fetch('/api/agent-replies/generate', {
      method: 'POST',
      headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const text = await response.text()
    if (!response.ok) throw new Error(`启动生成失败 ${response.status}: ${text}`)
    return JSON.parse(text)
  }, {
    dataset_type: datasetType,
    dataset_name: datasetName,
    project_id: pid,
    case_ids: [caseId],
    agent: {
      type: 'sse',
      url: 'http://mock-multimodal-agent:8001',
      model: 'multimodal-acceptance-mock',
      timeout: 30,
    },
    version_label: `image-acceptance-${stamp}`,
    concurrency: 1,
  })

  const job = await waitJob(started.job_id)
  if (job.status !== 'completed' || job.succeeded_count !== 1 || job.failed_count !== 0) {
    throw new Error(`${datasetType} 生成失败: ${JSON.stringify(job)}`)
  }
  const last = await mock('/last')
  if (last.image_received !== true) {
    throw new Error(`${datasetType} mock 未收到 image block: ${JSON.stringify(last)}`)
  }
  if (!Array.isArray(last.question) || !last.question.some(b => b?.type === 'image')) {
    throw new Error(`${datasetType} question 不是带 image 的 blocks: ${JSON.stringify(last.question)}`)
  }
  const versions = await api(`/api/agent-replies/versions?dataset_type=${datasetType}&case_ref=${encodeURIComponent(caseId)}`)
  const version = versions.find(v => v.version_label === `image-acceptance-${stamp}`)
  if (!version || version.status !== 'succeeded' || !(version.content || '').includes('IMAGE_BLOCK_RECEIVED')) {
    throw new Error(`${datasetType} 未生成成功版本: ${JSON.stringify(versions.slice(0, 2))}`)
  }
  return { job_id: started.job_id, version_id: version.id, mock_last: last }
}

let browser = null
const evidence = { stamp, screenshot: shotPath }
try {
  const makeQuestion = marker => [
    { type: 'text', text: marker },
    { type: 'image', source: { type: 'url', url: pngDataUrl }, name: 'acceptance.png' },
  ]

  const benchMarker = `IMAGE-REPLY-BENCH-${stamp}`
  const benchmark = await api(`/api/benchmark/${projectId}/cases`, {
    method: 'POST',
    body: JSON.stringify({ question: makeQuestion(benchMarker), reference_answer: 'IMAGE_BLOCK_RECEIVED', tags: ['acceptance-image-reply'] }),
  })
  evidence.benchmark_case_id = benchmark.id

  const candidateMarker = `IMAGE-REPLY-CAND-${stamp}`
  const candidate = await api('/api/candidates', {
    method: 'POST',
    body: JSON.stringify({ dataset_name: candidateDataset, question: makeQuestion(candidateMarker), answer: 'IMAGE_BLOCK_RECEIVED', tags: ['acceptance-image-reply'] }),
  })
  evidence.candidate_case_id = candidate.id

  browser = await chromium.launch({ headless: false, slowMo: 30 })
  const context = await browser.newContext({
    baseURL: 'http://localhost',
    storageState: authPath,
    viewport: { width: 1440, height: 960 },
  })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', e => consoleErrors.push(e.message))

  await page.goto(`/benchmark/${projectId}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('登录态失效，跳转 /login')
  evidence.benchmark = await runGeneration(page, {
    datasetType: 'benchmark', caseId: benchmark.id, projectId,
  })

  evidence.candidate = await runGeneration(page, {
    datasetType: 'candidate', caseId: candidate.id, datasetName: candidateDataset,
  })

  // UI 可视验收：列表行保留图片占位，并能从版本入口打开真实生成内容。
  // 图片真实解码由 verify-152 的结果详情验收覆盖；基准列表当前只展示 [图片] 占位。
  await page.goto(`/benchmark/${projectId}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  await page.waitForTimeout(1500)
  const search = page.locator('input[placeholder*="搜索"]').first()
  if (await search.count()) {
    await search.fill(benchMarker)
    await page.waitForTimeout(1200)
  }
  const row = page.locator('tbody tr', { hasText: benchMarker }).first()
  await row.waitFor({ state: 'visible', timeout: 20_000 })
  const rowText = await row.innerText()
  if (!rowText.includes('[图片]')) throw new Error(`benchmark 列表未显示图片占位: ${rowText}`)
  const versionButton = row.locator('button').filter({ hasText: /^v\d+/ }).first()
  await versionButton.waitFor({ state: 'visible', timeout: 10_000 })
  await versionButton.click()
  await page.getByText('IMAGE_BLOCK_RECEIVED', { exact: false }).first().waitFor({ state: 'visible', timeout: 10_000 })
  await page.screenshot({ path: shotPath, fullPage: true })

  evidence.ui = { image_placeholder_visible: true, version_drawer_visible: true }
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
}
