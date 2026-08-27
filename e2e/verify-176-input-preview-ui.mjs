// #176 UI 验收：Tracing 指标页明细表 INPUT 列应显示用户问题正文，
// 而不是修复前的 {"messages":[{"role":"user","content":"… JSON 壳。
//
// 判定：
//   1. INPUT 列单元格里以 JSON 壳开头的行数 == 0
//   2. 抽样行的 INPUT 文本 == 抽屉里 INPUT 区块 messages 末条 user content
//   3. 截图留证（headed，实际渲染非幻影）
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const AUTH = 'D:\\program\\agent_eval\\e2e\\auth.json'
const SHOT_DIR = 'D:\\program\\agent_eval\\e2e\\'

const report = { checks: [], rows: [], samples: [], pass: false }
const fail = (name, detail) => report.checks.push({ name, ok: false, detail })
const ok = (name, detail) => report.checks.push({ name, ok: true, detail })

// 壳特征：以 { 或 [ 开头且紧跟 messages/role/content/tool_mode/output_schema
const shellRe = /^\s*[[{]\s*"?(messages|role|content|tool_mode|output_schema)/

const browser = await chromium.launch({ headless: false, slowMo: 30 })
const ctx = await browser.newContext({
  storageState: AUTH,
  viewport: { width: 1560, height: 1200 },
})
const page = await ctx.newPage()

try {
  await page.goto(`${BASE}/tracing-metrics`, { waitUntil: 'networkidle', timeout: 60000 })

  // 等明细表出数据行（表头锚定：INPUT 列是第 3 列）
  await page.waitForFunction(
    () => document.querySelectorAll('table tbody tr').length > 0,
    { timeout: 30000 },
  )
  await page.waitForTimeout(800)

  // 按表头文本定位 INPUT 列序号，别硬编码
  const headers = await page.locator('table thead th').evaluateAll((ths) =>
    ths.map((t) => t.textContent.trim()),
  )
  report.headers = headers
  const inputIdx = headers.findIndex((h) => h.toUpperCase() === 'INPUT')
  if (inputIdx < 0) {
    fail('定位 INPUT 列', `表头未找到 INPUT: ${headers.join(' | ')}`)
    throw new Error('INPUT 列缺失')
  }
  ok('定位 INPUT 列', `第 ${inputIdx + 1} 列`)

  // 取所有行的 INPUT 单元格文本 + title（title 是未截断全文）
  const cells = await page
    .locator(`table tbody tr td:nth-child(${inputIdx + 1})`)
    .evaluateAll((tds) =>
      tds.map((td) => ({
        text: td.textContent.trim(),
        title: td.getAttribute('title') || '',
      })),
    )
  report.rowCount = cells.length
  report.rows = cells.slice(0, 20).map((c) => c.text.slice(0, 60))

  if (!cells.length) {
    fail('明细表有行', '0 行')
    throw new Error('无数据行')
  }
  ok('明细表有行', `${cells.length} 行`)

  // check 1: 没有 JSON 壳（同时看可见文本和 title 全文）
  const shells = cells
    .map((c, i) => ({ i, ...c }))
    .filter((c) => shellRe.test(c.text) || shellRe.test(c.title))
  report.shellCount = shells.length
  report.shellSamples = shells.slice(0, 5).map((s) => ({ row: s.i, text: s.text.slice(0, 80) }))
  if (shells.length) fail('INPUT 列无 JSON 壳', `${shells.length}/${cells.length} 行仍是壳`)
  else ok('INPUT 列无 JSON 壳', `${cells.length} 行全部是正文`)

  await page.screenshot({ path: `${SHOT_DIR}shot-176-input-list.png`, fullPage: false })

  // check 2: 抽样 3 行，点开抽屉比对 INPUT 区块里的 user content
  let mismatch = 0
  const pickIdx = [0, 1, 2].filter((i) => i < cells.length)
  for (const idx of pickIdx) {
    const cellText = cells[idx].title || cells[idx].text
    await page.locator('table tbody tr').nth(idx).click()
    // 抽屉里 INPUT 区块出现
    await page.waitForTimeout(1200)
    const drawerText = await page.locator('body').innerText()

    // 从抽屉可见文本里找 "content": "…" 的值（抽屉 INPUT 是格式化 JSON）
    const m = drawerText.match(/"content":\s*"((?:[^"\\]|\\.)*)"/)
    const drawerContent = m ? m[1].replace(/\\"/g, '"') : null

    const truncated = cellText.endsWith('…')
    const base = truncated ? cellText.slice(0, -1) : cellText
    const aligned =
      drawerContent == null ? null : truncated ? drawerContent.startsWith(base) : drawerContent === base
    if (aligned === false) mismatch++
    report.samples.push({
      row: idx,
      cell: cellText.slice(0, 60),
      drawer: drawerContent == null ? null : drawerContent.slice(0, 60),
      aligned,
      truncated,
    })

    if (idx === 0) {
      await page.screenshot({ path: `${SHOT_DIR}shot-176-input-drawer.png`, fullPage: false })
    }
    // 关抽屉
    await page.keyboard.press('Escape')
    await page.waitForTimeout(500)
  }
  if (mismatch) fail('列表 INPUT 与抽屉正文一致', `${mismatch}/${pickIdx.length} 行不符`)
  else ok('列表 INPUT 与抽屉正文一致', `抽样 ${pickIdx.length} 行`)

  report.pass = report.checks.every((c) => c.ok)
} catch (e) {
  if (!report.checks.some((c) => !c.ok)) fail('脚本执行', String(e?.message || e))
  try {
    await page.screenshot({ path: `${SHOT_DIR}shot-176-error.png` })
  } catch {}
} finally {
  fs.writeFileSync(
    `${SHOT_DIR}result-176-input-preview-ui.json`,
    JSON.stringify(report, null, 2),
    'utf8',
  )
  console.log(JSON.stringify(report, null, 2))
  console.log(report.pass ? 'VERDICT=PASS' : 'VERDICT=FAIL')
  await browser.close()
}
