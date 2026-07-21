// 双模多评估器完整 headed UI 验收（只读；不启动评估、不修改数据）。
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const STORAGE = path.join(HERE, 'auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const OUT = path.join(HERE, 'comparative-full-acceptance.json')
const SHOT = path.join(HERE, 'comparative-full-acceptance.png')

const result = {
  launched: { headless: false, baseUrl: BASE },
  bundle: null,
  candidate: null,
  apiEvidence: null,
  detail: null,
  history: null,
  report: null,
  consoleErrors: [],
  pageErrors: [],
  apiErrors: [],
  screenshot: SHOT,
  passed: false,
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function evaluatorName(entry) {
  if (entry?.legacy) return '历史结果（评估器身份不可恢复）'
  return entry?.label || entry?.tag || '未命名评估器'
}

async function apiJson(page, url) {
  return page.evaluate(async endpoint => {
    const storageKey = 'agent-eval-auth'
    const readAuth = () => JSON.parse(localStorage.getItem(storageKey) || '{}')
    const request = async accessToken => fetch(endpoint, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })

    let auth = readAuth()
    let response = await request(auth?.state?.accessToken)
    if (response.status === 401 && auth?.state?.refreshToken) {
      const refreshed = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: auth.state.refreshToken }),
      })
      const refreshText = await refreshed.text()
      if (!refreshed.ok) {
        throw new Error(`/api/auth/refresh -> ${refreshed.status}: ${refreshText.slice(0, 300)}`)
      }
      const tokens = JSON.parse(refreshText)
      auth = {
        ...auth,
        state: {
          ...auth.state,
          accessToken: tokens.access_token,
          refreshToken: tokens.refresh_token,
        },
      }
      localStorage.setItem(storageKey, JSON.stringify(auth))
      response = await request(tokens.access_token)
    }

    const text = await response.text()
    if (!response.ok) throw new Error(`${endpoint} -> ${response.status}: ${text.slice(0, 300)}`)
    return JSON.parse(text)
  }, url)
}

async function discoverCandidate(page) {
  const listing = await apiJson(page, '/api/eval/runs?page=1&page_size=100')
  const comparative = listing.items.filter(run => run.eval_mode === 'comparative')
  for (const summary of comparative) {
    const detail = await apiJson(page, `/api/eval/runs/${summary.id}`)
    const evaluators = detail.summary_scores?.comparison_summary?.evaluators
    if (!Array.isArray(evaluators) || evaluators.length < 2) continue
    const results = await apiJson(page, `/api/eval/runs/${summary.id}/results?page=1&page_size=200`)
    const row = results.items.find(item => Array.isArray(item.comparison?.evaluator_verdicts)
      && item.comparison.evaluator_verdicts.length >= 2)
    if (!row) continue
    return { detail, results, row, evaluators }
  }
  throw new Error(`最近 ${listing.items.length} 个 run 中没有同时具备 ≥2 个 evaluator 汇总和逐样例裁决的 comparative run`)
}

async function main() {
  assert(fs.existsSync(STORAGE), `缺少登录态：${STORAGE}`)
  const browser = await chromium.launch({ headless: false, slowMo: 70 })
  const context = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1600, height: 1100 },
    storageState: STORAGE,
    acceptDownloads: true,
  })
  const page = await context.newPage()
  page.on('console', message => {
    if (message.type() === 'error') result.consoleErrors.push(message.text())
  })
  page.on('pageerror', error => result.pageErrors.push(error.message))
  page.on('response', response => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      result.apiErrors.push({ url: response.url(), status: response.status() })
    }
  })

  await page.goto('/evaluation', { waitUntil: 'domcontentloaded' })
  assert(!page.url().includes('/login'), '登录态失效，页面跳转到 /login')
  const scriptSrcs = await page.locator('script[src]').evaluateAll(nodes => nodes.map(node => node.getAttribute('src')))
  result.bundle = scriptSrcs.find(src => /index-.*\.js/.test(src || '')) || null

  const candidate = await discoverCandidate(page)
  const run = candidate.detail
  const evaluatorNames = candidate.evaluators.map(evaluatorName)
  result.candidate = { id: run.id, name: run.langfuse_run_name, evaluatorNames }
  result.apiEvidence = {
    evaluatorSummaryCount: candidate.evaluators.length,
    resultCount: candidate.results.total,
    sampleEvaluatorVerdictCount: candidate.row.comparison.evaluator_verdicts.length,
    sampleEvaluatorStatuses: candidate.row.comparison.evaluator_verdicts.map(v => ({ label: v.label, status: v.status })),
  }

  await page.goto(`/evaluation/runs/${run.id}`, { waitUntil: 'domcontentloaded' })
  await page.getByText('对比裁决（按评估器）', { exact: true }).waitFor({ timeout: 20_000 })
  const bodyText = await page.locator('body').innerText()
  const requiredMetrics = [
    '输入 token', '输出 token', '总 token', '缓存写入 token', '缓存命中 token',
    '工具调用数', '尝试次数', '总时延', '首思考 token 时延', '首回答 token 时延', '缓存命中率',
  ]
  const missingEvaluatorNames = evaluatorNames.filter(name => !bodyText.includes(name))
  const missingMetrics = requiredMetrics.filter(label => !bodyText.includes(label))
  const resourceHeading = '资源成本 / 性能对照（A / B · 全部执行样例）'
  const resourceSection = page.locator('section').filter({ hasText: resourceHeading }).first()
  const resourceHeaders = await resourceSection.locator('thead th').allTextContents()
  const hasHeader = (pattern) => resourceHeaders.some(header => pattern.test(header.trim()))
  result.detail = {
    headingVisible: bodyText.includes('对比裁决（按评估器）'),
    evaluatorNames,
    missingEvaluatorNames,
    resourceHeadingVisible: bodyText.includes(resourceHeading),
    missingMetrics,
    resourceHeaders,
    hasTotalAndMeanColumns: hasHeader(/^A(?:\s*·.*?)?\s+总量$/)
      && hasHeader(/^A 均值$/)
      && hasHeader(/^B(?:\s*·.*?)?\s+总量$/)
      && hasHeader(/^B 均值$/)
      && hasHeader(/^Δ 总量（B-A）$/)
      && hasHeader(/^Δ 均值（B-A）$/)
      && hasHeader(/^覆盖 n（A\/B）$/),
    comparativeRescoreHidden: !bodyText.includes('补评缺分维度'),
  }
  assert(result.detail.headingVisible, '详情页缺少按评估器裁决区')
  assert(missingEvaluatorNames.length === 0, `详情页缺少 evaluator：${missingEvaluatorNames.join(', ')}`)
  assert(result.detail.resourceHeadingVisible, '详情页缺少完整 A/B 资源成本区')
  assert(missingMetrics.length === 0, `详情页缺少资源指标：${missingMetrics.join(', ')}`)
  assert(result.detail.hasTotalAndMeanColumns, '详情页缺少总量/均值/差值/覆盖数列')
  assert(result.detail.comparativeRescoreHidden, 'comparative run 错误显示了补评缺分维度入口')

  // 三个新增图表：胜负占比条（自绘 flex 堆叠）、各维度 A/B 均分（recharts）、成本相对差异（recharts）。
  // 断言实际渲染出 DOM/SVG 节点，而非仅文本命中——图表挂了但标题在，文本检查会假绿。
  const verdictSection = page.locator('section').filter({ hasText: '对比裁决（按评估器）' }).first()
  const winRateBar = verdictSection.locator('[role="img"][aria-label^="胜负占比"]').first()
  await winRateBar.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
  const dimChartHeadingVisible = bodyText.includes('各维度 A / B 均分')
  // recharts 渲染为 <svg class="recharts-surface">；数出详情页里的图表 svg 数量。
  const rechartsCount = await page.locator('svg.recharts-surface').count()
  const costSection = page.locator('section').filter({ hasText: resourceHeading }).first()
  const costDeltaHeadingVisible = bodyText.includes('B 相对 A 的均值差异')
  const costDeltaChartCount = await costSection.locator('svg.recharts-surface').count()
  result.detail.charts = {
    winRateBarVisible: await winRateBar.isVisible().catch(() => false),
    dimChartHeadingVisible,
    rechartsCount,
    costDeltaHeadingVisible,
    costDeltaChartCount,
  }
  assert(result.detail.charts.winRateBarVisible, '详情页缺少胜负占比堆叠条')
  assert(dimChartHeadingVisible, '详情页缺少「各维度 A / B 均分」图表标题')
  assert(costDeltaHeadingVisible, '详情页缺少成本相对差异图表标题')
  assert(costDeltaChartCount >= 1, '成本区未渲染出 recharts 图表 svg')
  assert(rechartsCount >= 1, '详情页未渲染出任何 recharts 图表 svg')

  // 打开带有多 evaluator verdict 的真实样例，核对抽屉中各 evaluator 相互隔离。
  const sampleId = candidate.row.benchmark_case_id?.slice(0, 8) || candidate.row.id.slice(0, 8)
  const sampleCell = page.getByText(sampleId, { exact: true }).first()
  await sampleCell.scrollIntoViewIfNeeded()
  await sampleCell.click()
  const drawer = page.locator('[role="dialog"]').last()
  await drawer.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
  const visiblePanel = (await drawer.count()) > 0 ? drawer : page.locator('body')
  const drawerText = await visiblePanel.innerText()
  const verdictNames = candidate.row.comparison.evaluator_verdicts.map(evaluatorName)
  const missingDrawerEvaluators = verdictNames.filter(name => !drawerText.includes(name))
  result.detail.drawer = { sampleId, verdictNames, missingDrawerEvaluators }
  assert(missingDrawerEvaluators.length === 0, `样例抽屉缺少 evaluator：${missingDrawerEvaluators.join(', ')}`)
  await page.screenshot({ path: SHOT, fullPage: true })

  // 历史列表用运行名过滤，核对每个 evaluator 都保留自己的 A/B/平摘要。
  await page.goto('/evaluation', { waitUntil: 'domcontentloaded' })
  const search = page.getByPlaceholder('搜运行名 / 模型 / URL / 项目')
  if (run.langfuse_run_name) {
    await search.fill(run.langfuse_run_name)
    await page.waitForTimeout(900)
  }
  const historyText = await page.locator('table tbody').innerText()
  const missingHistoryEvaluators = evaluatorNames.filter(name => !historyText.includes(name))
  result.history = {
    searchedBy: run.langfuse_run_name || null,
    missingEvaluatorNames: missingHistoryEvaluators,
    hasComparativeCounts: historyText.includes('A ') && historyText.includes('B ') && historyText.includes('平 '),
  }
  assert(missingHistoryEvaluators.length === 0, `历史列表缺少 evaluator 摘要：${missingHistoryEvaluators.join(', ')}`)
  assert(result.history.hasComparativeCounts, '历史列表缺少 A/B/平计数')

  // 通过真实 UI 下载 HTML 报告，并用浏览器重新解析，核对结构和内容。
  await page.goto(`/evaluation/runs/${run.id}`, { waitUntil: 'domcontentloaded' })
  const reportButton = page.getByRole('button', { name: '导出报告', exact: true })
  await reportButton.waitFor({ state: 'visible', timeout: 15_000 })
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 120_000 }),
    reportButton.click(),
  ])
  const reportPath = path.join(HERE, `accept-${download.suggestedFilename()}`)
  await download.saveAs(reportPath)
  const html = fs.readFileSync(reportPath, 'utf8')
  const reportPage = await context.newPage()
  await reportPage.setContent(html, { waitUntil: 'domcontentloaded' })
  const reportText = await reportPage.locator('body').innerText()
  const missingReportEvaluators = evaluatorNames.filter(name => !reportText.includes(name))
  const missingReportMetrics = requiredMetrics.filter(label => !reportText.includes(label))
  result.report = {
    filename: reportPath,
    bytes: Buffer.byteLength(html),
    hasMalformedBackslashTag: /<\\\/?[a-z]/i.test(html),
    evaluatorBlockCount: await reportPage.locator('section.card').filter({ hasText: '对比裁决（按评估器）' }).count(),
    resourceBlockCount: await reportPage.locator('section.card').filter({ hasText: '资源成本 / 性能对照（全部执行样例）' }).count(),
    missingEvaluatorNames: missingReportEvaluators,
    missingMetrics: missingReportMetrics,
  }
  assert(!result.report.hasMalformedBackslashTag, '导出报告包含畸形反斜杠 HTML 标签')
  assert(result.report.evaluatorBlockCount === 1, '导出报告缺少按评估器裁决区')
  assert(result.report.resourceBlockCount === 1, '导出报告缺少资源成本区')
  assert(missingReportEvaluators.length === 0, `导出报告缺少 evaluator：${missingReportEvaluators.join(', ')}`)
  assert(missingReportMetrics.length === 0, `导出报告缺少资源指标：${missingReportMetrics.join(', ')}`)

  assert(result.pageErrors.length === 0, `页面异常：${result.pageErrors.join(' | ')}`)
  assert(result.apiErrors.length === 0, `API 错误：${JSON.stringify(result.apiErrors)}`)
  result.passed = true
  fs.writeFileSync(OUT, JSON.stringify(result, null, 2))
  await browser.close()
  console.log(JSON.stringify(result, null, 2))
}

main().catch(error => {
  result.failure = error.stack || String(error)
  fs.writeFileSync(OUT, JSON.stringify(result, null, 2))
  console.error(error)
  process.exit(1)
})
