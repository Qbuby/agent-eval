// #175 UI 验收：Langfuse 指标页「环境筛选」下拉应渲染全部 10 个环境，
// 而不是修复前的 2 个（白名单只有 xinchai-prod / saas-prod）。
//
// 判定：
//   1. 下拉 option 集合 ⊇ 期望 10 个环境（含此前白名单外的 8 个）
//   2. 逐个选中原白名单外的环境后，明细表能出行（过滤链路通）
//   3. 截图留证
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const AUTH = 'D:\\program\\agent_eval\\e2e\\auth.json'

// API 层已确认的 10 个环境
const EXPECTED = [
  'xinchai-prod',
  'noble-prod',
  'ep-prod',
  'saas-prod',
  'noble-test',
  'service-agent-customer-prod',
  'xcmg-tj-prod',
  'xcmg-gj-prod',
  'ruyi-prod',
  'jiali-prod',
]
// 修复前白名单外、本次新进库的 8 个
const NEWLY_ADDED = [
  'ep-prod',
  'jiali-prod',
  'noble-prod',
  'noble-test',
  'ruyi-prod',
  'service-agent-customer-prod',
  'xcmg-gj-prod',
  'xcmg-tj-prod',
]

const report = { checks: [], options: [], perEnv: [], pass: false }
const fail = (name, detail) => report.checks.push({ name, ok: false, detail })
const ok = (name, detail) => report.checks.push({ name, ok: true, detail })

const browser = await chromium.launch({ headless: false, slowMo: 30 })
const ctx = await browser.newContext({
  storageState: AUTH,
  viewport: { width: 1560, height: 1200 },
})
const page = await ctx.newPage()

try {
  await page.goto(`${BASE}/tracing-metrics`, { waitUntil: 'networkidle', timeout: 60000 })

  const envSelect = page.locator('select[aria-label="环境筛选"]')
  await envSelect.waitFor({ state: 'visible', timeout: 30000 })

  // stats 查询回来后 option 才会填充，等到 option 数量稳定
  await page.waitForFunction(
    () => {
      const s = document.querySelector('select[aria-label="环境筛选"]')
      return s && s.options.length > 1
    },
    { timeout: 30000 },
  )

  // 时间范围放到最大，避免默认窗口把低频环境滤掉
  const rangeSelect = page.locator('select[aria-label="时间范围"]')
  const rangeValues = await rangeSelect.locator('option').evaluateAll((os) =>
    os.map((o) => o.value),
  )
  report.rangeValues = rangeValues
  if (rangeValues.length) {
    await rangeSelect.selectOption(rangeValues[rangeValues.length - 1])
    await page.waitForLoadState('networkidle')
    await page.waitForFunction(
      () => {
        const s = document.querySelector('select[aria-label="环境筛选"]')
        return s && s.options.length > 1
      },
      { timeout: 30000 },
    )
  }

  const options = await envSelect.locator('option').evaluateAll((os) =>
    os.map((o) => ({ value: o.value, label: o.textContent.trim() })),
  )
  report.options = options
  const values = options.map((o) => o.value).filter((v) => v !== '')

  // check 1: 全部 10 个环境都在
  const missing = EXPECTED.filter((e) => !values.includes(e))
  if (missing.length) fail('下拉含全部 10 个环境', `缺失: ${missing.join(', ')} | 实到: ${values.join(', ')}`)
  else ok('下拉含全部 10 个环境', `共 ${values.length} 项`)

  // check 2: 修复前白名单外的 8 个都在（这才是本次修复的核心）
  const missingNew = NEWLY_ADDED.filter((e) => !values.includes(e))
  if (missingNew.length) fail('新进库 8 环境全部可选', `缺失: ${missingNew.join(', ')}`)
  else ok('新进库 8 环境全部可选', NEWLY_ADDED.join(', '))

  await page.screenshot({ path: 'D:\\program\\agent_eval\\e2e\\shot-175-env-dropdown.png', fullPage: false })

  // check 3: 逐个选中新进库环境，明细表要出行
  for (const env of NEWLY_ADDED) {
    if (!values.includes(env)) {
      report.perEnv.push({ env, rows: null, note: 'option 不存在，跳过' })
      continue
    }
    await envSelect.selectOption(env)
    await page.waitForLoadState('networkidle')
    // 等 loading 落地：表格出现数据行或空态
    await page.waitForTimeout(900)
    const rows = await page.locator('table tbody tr').count()
    const bodyText = await page.locator('body').innerText()
    const hasEnvCell = bodyText.includes(env)
    report.perEnv.push({ env, rows, envShownInTable: hasEnvCell })
  }

  const emptyEnvs = report.perEnv.filter((r) => r.rows !== null && r.rows === 0).map((r) => r.env)
  if (emptyEnvs.length) fail('逐环境过滤有明细行', `无行: ${emptyEnvs.join(', ')}`)
  else ok('逐环境过滤有明细行', report.perEnv.map((r) => `${r.env}=${r.rows}`).join(' '))

  await page.screenshot({ path: 'D:\\program\\agent_eval\\e2e\\shot-175-env-filtered.png', fullPage: false })

  report.pass = report.checks.every((c) => c.ok)
} catch (e) {
  fail('脚本执行', String(e && e.message ? e.message : e))
  try {
    await page.screenshot({ path: 'D:\\program\\agent_eval\\e2e\\shot-175-error.png' })
  } catch {}
} finally {
  fs.writeFileSync(
    'D:\\program\\agent_eval\\e2e\\result-175-lf-envs-ui.json',
    JSON.stringify(report, null, 2),
    'utf8',
  )
  console.log(JSON.stringify(report, null, 2))
  console.log(report.pass ? 'VERDICT=PASS' : 'VERDICT=FAIL')
  await browser.close()
}
