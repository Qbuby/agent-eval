// 验收 #127：三类数据集页面的「提炼关键点」入口在真实浏览器里渲染 + 可交互
//
// 覆盖（路由按 src/App.tsx 实际挂载，不猜）：
//   1. 备选数据集详情 /datasets/:name        → 工具栏批量按钮 + 弹窗 + 编辑弹窗内单条「AI 提炼」
//   2. 基准测试集     /benchmark/:projectId  → 工具栏批量按钮 + 弹窗 + 编辑弹窗内单条「AI 提炼」
//   3. 多轮对话集详情 /conversations/:name   → 工具栏批量按钮 + 弹窗（单条在 ConversationEditor 里）
//
// 只验渲染与开关，不点「开始提炼」（会打真实 LLM 并写库）。批量弹窗在未勾选时走全量模式，
// 会自动请求 /api/key-points/pending-count，故顺带核验该端点被真实调用。
//
// 导航注意：/datasets 与 /conversations 列表用的是可点击 <div class="card ..."> 卡片，
// 不是 <a href>（只有 /projects → /benchmark/:id 是真锚点）。所以卡片页按 card 文本
// 点进去，并优先挑「样例数」非 0 的数据集，否则详情页没有行、验不到单条「AI 提炼」。
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const authPath = path.join(root, 'auth.json')
if (!fs.existsSync(authPath)) throw new Error(`缺少登录态文件: ${authPath}`)

const browser = await chromium.launch({ headless: false })
const context = await browser.newContext({
  baseURL: 'http://localhost',
  storageState: authPath,
  viewport: { width: 1440, height: 900 },
})
const page = await context.newPage()

let failed = 0
const fail = (m) => { console.log(`FAIL ${m}`); failed++ }
const pass = (m) => console.log(`PASS ${m}`)

const kpCalls = []
page.on('response', (res) => {
  const u = res.url()
  if (u.includes('/api/key-points/')) {
    kpCalls.push({ url: u.replace(/^https?:\/\/[^/]+/, ''), status: res.status() })
  }
})

const shot = (n) => page.screenshot({ path: path.join(root, `shot-127-${n}.png`) }).catch(() => {})

// 卡片式列表页（/datasets、/conversations）：点进第一个「样例数」非 0 的卡片。
// 全为 0 时退回第一张卡片，并在日志里说明（详情页会没有行）。
async function enterFirstCard(listPath, label) {
  await page.goto(listPath, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('登录态失效，跳转到 /login')
  await page.waitForTimeout(2500)

  const cards = page.locator('div.card.cursor-pointer')
  const n = await cards.count()
  if (n === 0) {
    fail(`${label}: 列表页 ${listPath} 没渲染出任何数据集卡片`)
    await shot(`${label}-list-empty`)
    return false
  }

  let pick = -1
  for (let i = 0; i < n; i++) {
    const txt = (await cards.nth(i).innerText().catch(() => '')).replace(/\s+/g, '')
    if (txt && !txt.includes('样例数0')) { pick = i; break }
  }
  if (pick < 0) {
    console.log(`NOTE ${label}: ${n} 个数据集样例数全为 0，详情页将没有行（单条提炼验不到）`)
    pick = 0
  }

  const name = (await cards.nth(pick).innerText().catch(() => '')).split('\n')[0]
  console.log(`NOTE ${label}: 进入数据集「${name}」(卡片 ${pick + 1}/${n})`)
  await cards.nth(pick).click()
  await page.waitForTimeout(3000)
  return true
}

// 锚点式列表页（/projects → /benchmark/:projectId）
async function enterFirstAnchor(listPath, hrefPrefix, label) {
  await page.goto(listPath, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('登录态失效，跳转到 /login')
  await page.waitForTimeout(2000)
  const link = page.locator(`a[href^="${hrefPrefix}"]`).first()
  if (!(await link.isVisible().catch(() => false))) {
    fail(`${label}: 列表页 ${listPath} 没找到可进入的详情链接（数据为空？）`)
    await shot(`${label}-list-empty`)
    return false
  }
  await link.click()
  await page.waitForTimeout(2500)
  return true
}

// 批量按钮 + 弹窗：核验按钮存在、点开后表单项与「开始提炼」都在，然后 Esc 关掉
async function checkBatchDialog(label) {
  const btn = page.getByRole('button', { name: /提炼关键点/ })
  if (!(await btn.first().isVisible().catch(() => false))) {
    fail(`${label}: 工具栏没有「提炼关键点」按钮`)
    await shot(`${label}-no-btn`)
    return
  }
  pass(`${label}: 工具栏出现「提炼关键点」按钮`)

  await btn.first().click()
  await page.waitForTimeout(1800) // 等 pending-count 回来

  const hasConc = await page.getByText('并发', { exact: false }).first().isVisible().catch(() => false)
  const hasStart = await page.getByRole('button', { name: '开始提炼' }).isVisible().catch(() => false)
  if (hasConc && hasStart) pass(`${label}: 批量弹窗已打开（并发字段 + 「开始提炼」按钮都在）`)
  else fail(`${label}: 弹窗内容不完整 并发=${hasConc} 开始提炼=${hasStart}`)

  await shot(`${label}-dialog`)
  await page.keyboard.press('Escape')
  await page.waitForTimeout(500)
}

// 编辑弹窗内的单条「AI 提炼」
async function checkEditAiButton(label) {
  const edit = page.getByRole('button', { name: '编辑' }).first()
  if (!(await edit.isVisible().catch(() => false))) {
    fail(`${label}: 列表里没有「编辑」按钮，无法验收单条提炼`)
    return
  }
  await edit.click()
  await page.waitForTimeout(1200)
  const ai = page.getByRole('button', { name: 'AI 提炼' }).first()
  if (await ai.isVisible().catch(() => false)) {
    pass(`${label}: 编辑弹窗内渲染出单条「AI 提炼」按钮`)
    await shot(`${label}-edit-ai`)
  } else {
    fail(`${label}: 编辑弹窗内没有「AI 提炼」按钮`)
    await shot(`${label}-edit-missing`)
  }
  await page.keyboard.press('Escape')
  await page.waitForTimeout(500)
}

try {
  // ── 1. 备选数据集详情 /datasets/:name ──────────────────────────
  if (await enterFirstCard('/datasets', 'candidate')) {
    await checkBatchDialog('candidate')
    await checkEditAiButton('candidate')
  }

  // ── 2. 基准测试集 /benchmark/:projectId ────────────────────────
  if (await enterFirstAnchor('/projects', '/benchmark/', 'benchmark')) {
    await checkBatchDialog('benchmark')
    await checkEditAiButton('benchmark')
  }

  // ── 3. 多轮对话集详情 /conversations/:name ─────────────────────
  if (await enterFirstCard('/conversations', 'multichat')) {
    await checkBatchDialog('multichat')
  }

  // ── 4. pending-count 真被调用且都成功 ──────────────────────────
  const pc = kpCalls.filter((c) => c.url.includes('/pending-count'))
  if (pc.length === 0) fail('全程没有 /api/key-points/pending-count 请求，弹窗可能没真正打开')
  else {
    pass(`pending-count 被调用 ${pc.length} 次`)
    const bad = pc.filter((c) => c.status >= 300)
    if (bad.length) fail(`pending-count 有非 2xx：${JSON.stringify(bad)}`)
    else pass('pending-count 全部 2xx')
  }
} finally {
  console.log('')
  console.log('KP_CALLS=' + JSON.stringify(kpCalls))
  console.log(`RESULT=${failed === 0 ? 'PASS' : `FAIL (${failed})`}`)
  await browser.close()
  process.exit(failed === 0 ? 0 : 1)
}
