// 任务 #114 实机验收：DatasetDetailPage（/datasets/:name）的 agent 生成答案入口。
// 断言点：
//   1. 工具栏出现「agent生成答案」按钮，且未勾选时 disabled + title 提示
//   2. 表头出现「agent回复」列
//   3. 每行该列有内容（未生成 -> 占位文案；已生成 -> 可点 vN）
//   4. 勾选样例后按钮变可用且带计数
//   5. 点开按钮弹出生成弹窗（含 URL 组合框，即 #113 的成果在本页同样生效）
//   6. 若存在已生成行，点 vN 能打开版本抽屉
// 用纯 \u 转义写中文，避开本机编码坑。
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = 'http://localhost'
const DATASET = process.argv[2] || 'ep-agent-feedback'
const AUTH = new URL('./auth.json', import.meta.url)

const T = {
  genBtn: 'agent生成答案', // agent生成答案
  replyCol: 'agent回复',           // agent回复
  notGen: '未生成',                                      // 未生成
  needPick: '请先勾选样例',                  // 请先勾选样例
  dlgTitle: 'agent生成答案', // 弹窗标题同名
  drawerTitle: 'agent 回复版本', // agent 回复版本
}

const results = []
const ok = (n, c, extra = '') => results.push([c ? 'PASS' : 'FAIL', n, extra])

const browser = await chromium.launch({ headless: false })
const ctx = await browser.newContext({
  storageState: JSON.parse(fs.readFileSync(AUTH, 'utf8')),
  viewport: { width: 1600, height: 1000 },
})
const page = await ctx.newPage()

const errors = []
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
page.on('response', r => {
  if (r.status() === 401) errors.push(`401 ${r.url()}`)
})

await page.goto(`${BASE}/datasets/${DATASET}`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

// 认证是否有效：URL 被踢到 /login 就说明 token 过期
const url1 = page.url()
ok('authenticated (not redirected to /login)', !url1.includes('/login'), url1)
if (url1.includes('/login')) {
  console.log(results.map(r => r.join('  ')).join('\n'))
  console.log('VERIFY_114_UI=BLOCKED(auth expired)')
  await browser.close()
  process.exit(3)
}

// 1. 工具栏按钮存在且初始 disabled
const genBtn = page.getByRole('button', { name: new RegExp(T.genBtn) })
const btnCount = await genBtn.count()
ok('toolbar has gen button', btnCount === 1, `count=${btnCount}`)
if (btnCount !== 1) {
  console.log(results.map(r => r.join('  ')).join('\n'))
  await page.screenshot({ path: 'e2e/ds-detail-agent-reply-FAIL.png', fullPage: false })
  console.log('VERIFY_114_UI=FAILED')
  await browser.close()
  process.exit(1)
}
const disabled0 = await genBtn.isDisabled()
ok('gen button disabled with no selection', disabled0)
const title0 = await genBtn.getAttribute('title')
ok('gen button has hint title', (title0 || '').includes(T.needPick), String(title0))

// 2. 表头列
const headers = await page.locator('table thead th').allInnerTexts()
ok('table has reply column header', headers.some(h => h.includes(T.replyCol)), JSON.stringify(headers))

// 3. 行内该列有内容
const rows = page.locator('table tbody tr')
const rowCount = await rows.count()
ok('rows rendered', rowCount > 0, `rows=${rowCount}`)
const colIdx = headers.findIndex(h => h.includes(T.replyCol))
let cellsWithContent = 0
let firstVersionBtnRow = -1
for (let i = 0; i < Math.min(rowCount, 8); i++) {
  const cell = rows.nth(i).locator('td').nth(colIdx)
  const txt = (await cell.innerText()).trim()
  if (txt.length > 0) cellsWithContent++
  if (firstVersionBtnRow < 0 && await cell.locator('button').count() > 0) firstVersionBtnRow = i
}
ok('reply cells all non-empty', cellsWithContent === Math.min(rowCount, 8), `${cellsWithContent}/${Math.min(rowCount, 8)}`)

await page.screenshot({ path: 'e2e/ds-detail-agent-reply-list.png' })

// 4. 勾选后按钮可用且带计数
await rows.nth(0).locator('input[type=checkbox]').check()
await page.waitForTimeout(300)
const disabled1 = await genBtn.isDisabled()
ok('gen button enabled after selection', !disabled1)
const label1 = (await genBtn.innerText()).trim()
ok('gen button shows count', /\(1\)/.test(label1), label1)

// 5. 点开弹窗
await genBtn.click()
await page.waitForTimeout(900)
const dlg = page.locator('[role=dialog], .fixed').filter({ hasText: new RegExp(T.dlgTitle) }).first()
const dlgVisible = await dlg.isVisible().catch(() => false)
ok('generate dialog opens', dlgVisible)
// #113 的 URL 组合框在本页也应生效
const combo = dlg.locator('[aria-expanded]')
const comboCount = await combo.count()
ok('dialog has url option picker (from #113)', comboCount >= 1, `triggers=${comboCount}`)
await page.screenshot({ path: 'e2e/ds-detail-agent-reply-dialog.png' })

// 关掉弹窗
await page.keyboard.press('Escape')
await page.waitForTimeout(500)

// 6. 已生成行 -> 版本抽屉
if (firstVersionBtnRow >= 0) {
  await rows.nth(firstVersionBtnRow).locator('td').nth(colIdx).locator('button').first().click()
  await page.waitForTimeout(1200)
  const drawerVisible = await page.getByText(new RegExp(T.drawerTitle)).first().isVisible().catch(() => false)
  ok('versions drawer opens on vN click', drawerVisible)
  await page.screenshot({ path: 'e2e/ds-detail-agent-reply-drawer.png' })
} else {
  results.push(['SKIP', 'versions drawer (no generated row on page)', ''])
}

ok('no console errors / 401s', errors.length === 0, errors.slice(0, 3).join(' | '))

console.log(results.map(r => r.join('  ')).join('\n'))
const failed = results.filter(r => r[0] === 'FAIL').length
console.log(`VERIFY_114_UI=${failed === 0 ? 'PASSED' : `FAILED(${failed})`}`)
await browser.close()
process.exit(failed === 0 ? 0 : 1)
