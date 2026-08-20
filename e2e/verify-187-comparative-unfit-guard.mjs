// #187 验收：双模对比模式下勾选单模评估器时，前端要在页面上拦住（徽标 + blocker + 禁用启动），
// 而不是让用户点下去只拿到一句后端启动失败。
// 选择器依据 _diag187*.mjs 实测的真实 DOM：
//   - 模式 tab 真身是 button.page-tab，且只在「新建评估」表单里存在（表单外点它会超时）
//   - 评估器行是 label（内含 checkbox），名字是 label 首行文本，不是独立元素
//   - 评估器清单是异步查询，必须显式等行出现，不能靠固定 sleep
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost'
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const USER_FILE = path.join(SCRIPT_DIR, 'test-user.json')
const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'
const BLOCKER_TEXT = '双模对比要用对比专用评估器'
const UNFIT_BADGE = '不支持对比'

const out = { steps: {}, checks: {} }
const browser = await chromium.launch({ headless: false, executablePath: EXEC })
const context = await browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 1000 } })
const page = await context.newPage()

await page.goto('/login')
const user = JSON.parse(fs.readFileSync(USER_FILE, 'utf8'))
await page.getByPlaceholder('输入用户名').fill(user.username)
await page.getByPlaceholder('输入密码').fill(user.password)
await page.getByRole('button', { name: /继续|登录/ }).click()
await page.waitForURL(u => !u.pathname.includes('/login'), { timeout: 15_000 })
await page.waitForLoadState('networkidle')

// 后端评估器清单：按后端 _validate_comparative_evaluator_specs 同一口径分「对比可用 / 单模」两类。
const evaluators = await page.evaluate(async () => {
  const raw = localStorage.getItem('agent-eval-auth')
  const token = raw ? JSON.parse(raw)?.state?.accessToken : null
  const r = await fetch('/api/eval/evaluators', { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  const b = await r.json()
  const list = Array.isArray(b) ? b : (b.items ?? b.data ?? [])
  return list.map(e => ({ id: e.id, name: e.name, type: e.evaluator_type, mapping: e?.params?.variable_mapping ?? null }))
})
const isCapable = e => {
  if (e.type !== 'configurable_judge') return false
  const m = e.mapping
  if (!m || typeof m !== 'object') return true   // 后端会回落到 DEFAULT_COMPARATIVE_VARIABLE_MAPPING
  return Object.values(m).some(v => String(v).trim() === 'output_a' || String(v).trim() === 'output_b')
}
const capable = evaluators.filter(isCapable)
const unfit = evaluators.filter(e => !isCapable(e))
out.steps.evaluators = { total: evaluators.length, capable: capable.length, unfit: unfit.length }
out.steps.unfitSample = unfit.slice(0, 3).map(e => e.name)
out.steps.capableSample = capable.slice(0, 3).map(e => e.name)

await page.goto('/evaluation')
await page.waitForLoadState('networkidle')

// 评估器区在「新建评估」表单里，初始页没有（诊断实测 hasEvaluatorSection:false）。
const newBtn = page.locator('button.page-tab').filter({ hasText: '新建评估' }).first()
await newBtn.click({ timeout: 15_000 })
out.steps.newBtnClicked = true

const rows = page.locator('label').filter({ has: page.locator('input[type=checkbox]') })
// 评估器清单是异步查询：显式等第一行出现，别用固定 sleep（上一版 rowCount=0 就是死等 800ms 不够）。
await rows.first().waitFor({ state: 'attached', timeout: 20_000 })
out.steps.rowCountSingleMode = await rows.count()

// 切「双模对比」tab：真身是 button.page-tab，且此刻表单已展开。
const cmpTab = page.locator('button.page-tab').filter({ hasText: '双模对比' }).first()
out.steps.cmpTabCount = await cmpTab.count()
await cmpTab.click({ timeout: 15_000 })
// 等对比模式真的渲染出来（A/B 两侧配置区），而不是等固定时长。
await page.getByText('B 模型', { exact: false }).first().waitFor({ state: 'attached', timeout: 15_000 })
out.steps.modeSwitched = true

await rows.first().waitFor({ state: 'attached', timeout: 20_000 })
const rowCount = await rows.count()
out.steps.rowCount = rowCount

// 名字取 label 首行文本，按精确名字建索引，避免「多轮-任务完成度」被「多轮-任务完成度对比」串到。
const rowNames = []
for (let i = 0; i < rowCount; i++) {
  const t = await rows.nth(i).innerText()
  rowNames.push((t.split('\n')[0] || '').trim())
}
const rowIndexByName = new Map()
rowNames.forEach((n, i) => { if (!rowIndexByName.has(n)) rowIndexByName.set(n, i) })
out.steps.rowNamesSample = rowNames.slice(0, 3)

const pickBox = name => rowIndexByName.has(name)
  ? rows.nth(rowIndexByName.get(name)).locator('input[type=checkbox]')
  : null

// 断言 1：单模评估器在对比模式下带「不支持对比」徽标
out.steps.unfitBadgeCount = await page.getByText(UNFIT_BADGE, { exact: true }).count()
out.checks.unfitBadgeRendered = out.steps.unfitBadgeCount > 0

// 勾一个单模评估器（挑一个在页面上真实存在的）
const targetUnfit = unfit.find(e => rowIndexByName.has(e.name)) || null
const unfitBox = targetUnfit ? pickBox(targetUnfit.name) : null
if (unfitBox) {
  await unfitBox.check({ timeout: 10_000 })
  await page.getByText(BLOCKER_TEXT, { exact: false }).first()
    .waitFor({ state: 'attached', timeout: 10_000 }).catch(() => {})
}
out.steps.clickedUnfit = targetUnfit ? targetUnfit.name : null

// 断言 2：出现 blocker 文案，且那一行点名了被勾中的评估器
const bodyUnfitPicked = await page.locator('body').innerText()
out.checks.blockerShown = bodyUnfitPicked.includes(BLOCKER_TEXT)
out.steps.blockerLines = bodyUnfitPicked.split('\n').filter(l => l.includes(BLOCKER_TEXT))
out.checks.blockerNamesEvaluator = !!targetUnfit && out.steps.blockerLines.some(l => l.includes(targetUnfit.name))

// 断言 3：启动按钮禁用
const startBtn = page.getByRole('button', { name: /开始评估|启动评估|开始|启动/ }).last()
out.steps.startBtnFound = await startBtn.count() > 0
out.steps.startBtnText = out.steps.startBtnFound ? (await startBtn.textContent() || '').trim() : null
out.checks.startDisabledWhenUnfit = out.steps.startBtnFound ? await startBtn.isDisabled() : null

await page.screenshot({ path: path.join(SCRIPT_DIR, 'shot-187-unfit-blocked.png'), fullPage: true })

// 断言 4：取消勾选 → blocker 应消失
if (unfitBox) {
  await unfitBox.uncheck({ timeout: 10_000 })
  await page.getByText(BLOCKER_TEXT, { exact: false }).first()
    .waitFor({ state: 'detached', timeout: 10_000 }).catch(() => {})
}
const bodyUnpicked = await page.locator('body').innerText()
out.checks.blockerClearsOnUncheck = !bodyUnpicked.includes(BLOCKER_TEXT)

// 断言 5：改勾一个对比可用评估器 → 不应出现该 blocker
const targetOk = capable.find(e => rowIndexByName.has(e.name)) || null
const okBox = targetOk ? pickBox(targetOk.name) : null
if (okBox) { await okBox.check({ timeout: 10_000 }); await page.waitForTimeout(600) }
out.steps.clickedCapable = targetOk ? targetOk.name : null
const bodyOkPicked = await page.locator('body').innerText()
out.checks.noBlockerForCapable = !bodyOkPicked.includes(BLOCKER_TEXT)

await page.screenshot({ path: path.join(SCRIPT_DIR, 'shot-187-capable-ok.png'), fullPage: true })

const required = ['unfitBadgeRendered', 'blockerShown', 'blockerNamesEvaluator', 'startDisabledWhenUnfit', 'blockerClearsOnUncheck', 'noBlockerForCapable']
out.PASS = required.every(k => out.checks[k] === true)
out.failed = required.filter(k => out.checks[k] !== true)
fs.writeFileSync(path.join(SCRIPT_DIR, 'verify-187.json'), JSON.stringify(out, null, 2))
console.log(JSON.stringify(out, null, 2))
await browser.close()
