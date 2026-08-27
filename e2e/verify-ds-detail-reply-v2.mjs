// 任务 #114 实机验收 v2：DatasetDetailPage（/datasets/:name）agent 生成答案入口。
//
// 相对 v1 的口径修正（v1 的 4 个 FAIL 全是脚本自己的问题）：
//   1. 表头有 CSS uppercase，渲染文本是 AGENT回复 —— 匹配一律先 toLowerCase
//   2. colIdx === -1 时直接判失败；v1 交给 Playwright nth(-1) 取了末列（操作列），
//      导致「单元格非空」假通过、「未生成行不该有按钮」假失败
//   3. 弹窗标题是「agent 生成答案」（agent 后有空格），按钮文案无空格 —— 用容空格正则
// 中文 needle 用 \u 转义，避开本机编码坑。
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = 'http://localhost'
const DATASET = process.argv[2]
if (!DATASET) {
  console.log('usage: node verify-ds-detail-reply-v2.mjs <dataset-name>')
  process.exit(2)
}
const AUTH = new URL('./auth.json', import.meta.url)

const GEN_BTN = 'agent生成答案'                  // agent生成答案
const REPLY_COL = 'agent回复'                            // agent回复
const NOT_GEN = '未生成'                             // 未生成
const NEED_PICK = '请先勾选样例'         // 请先勾选样例
const DLG_RE = /agent\s*生成答案/                // agent 生成答案（容空格）
const DRAWER_RE = /agent\s*回复版本/             // agent 回复版本

const results = []
const ok = (n, c, extra = '') => results.push([c ? 'PASS' : 'FAIL', n, extra])
const skip = (n, extra = '') => results.push(['SKIP', n, extra])

function report(tag) {
  console.log(results.map(r => r.join('  ')).join('\n'))
  const failed = results.filter(r => r[0] === 'FAIL').length
  console.log(`VERIFY_114_UI=${tag ?? (failed === 0 ? 'PASSED' : `FAILED(${failed})`)}`)
  return failed
}

const browser = await chromium.launch({ headless: false })
const ctx = await browser.newContext({
  storageState: JSON.parse(fs.readFileSync(AUTH, 'utf8')),
  viewport: { width: 1600, height: 1000 },
})
const page = await ctx.newPage()

const errors = []
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
page.on('response', r => { if (r.status() === 401) errors.push(`401 ${r.url()}`) })

await page.goto(`${BASE}/datasets/${DATASET}`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

const url1 = page.url()
ok('authenticated (not redirected to /login)', !url1.includes('/login'), url1)
if (url1.includes('/login')) { report('BLOCKED(auth expired)'); await browser.close(); process.exit(3) }

// 1. 工具栏按钮：存在 + 无勾选时 disabled + title 提示
const genBtn = page.getByRole('button', { name: new RegExp(GEN_BTN) })
const btnCount = await genBtn.count()
ok('toolbar has gen button', btnCount === 1, `count=${btnCount}`)
if (btnCount !== 1) {
  await page.screenshot({ path: 'e2e/ds-detail-v2-FAIL.png' })
  report(); await browser.close(); process.exit(1)
}
ok('gen button disabled with no selection', await genBtn.isDisabled())
const title0 = await genBtn.getAttribute('title')
ok('gen button has hint title', (title0 || '').includes(NEED_PICK), String(title0))

// 2. 表头列（大小写不敏感：CSS uppercase）
const headers = await page.locator('table thead th').allInnerTexts()
const colIdx = headers.findIndex(h => h.toLowerCase().includes(REPLY_COL.toLowerCase()))
ok('table has reply column header', colIdx >= 0, JSON.stringify(headers))

// 3. 行内该列内容：未生成 -> 占位文案且无按钮；已生成 -> 有 vN 按钮
const rows = page.locator('table tbody tr')
const rowCount = await rows.count()
ok('rows rendered', rowCount > 0, `rows=${rowCount}`)

let firstVersionBtnRow = -1
if (colIdx >= 0 && rowCount > 0) {
  const n = Math.min(rowCount, 8)
  let good = 0
  const seen = []
  for (let i = 0; i < n; i++) {
    const cell = rows.nth(i).locator('td').nth(colIdx)
    const txt = (await cell.innerText()).trim()
    const btns = await cell.locator('button').count()
    seen.push(`${txt}|btn=${btns}`)
    // 合法状态只有两种：占位「未生成」无按钮，或有 vN 按钮
    if ((txt.includes(NOT_GEN) && btns === 0) || btns > 0) good++
    if (firstVersionBtnRow < 0 && btns > 0) firstVersionBtnRow = i
  }
  ok('reply cells in a valid state', good === n, `${good}/${n} ${JSON.stringify(seen)}`)
} else {
  skip('reply cells in a valid state', 'reply column not found')
}
await page.screenshot({ path: 'e2e/ds-detail-v2-list.png' })

// 4. 勾选后按钮可用 + 带计数
await rows.nth(0).locator('input[type=checkbox]').check()
await page.waitForTimeout(300)
ok('gen button enabled after selection', !(await genBtn.isDisabled()))
const label1 = (await genBtn.innerText()).trim()
ok('gen button shows count', /\(1\)/.test(label1), label1)

// 5. 弹窗打开 + 含 #113 的 URL 组合框
await genBtn.click()
await page.waitForTimeout(900)
const dlg = page.locator('[role=dialog]').filter({ hasText: DLG_RE }).first()
const dlgVisible = await dlg.isVisible().catch(() => false)
ok('generate dialog opens', dlgVisible)
if (dlgVisible) {
  const comboCount = await dlg.locator('[aria-expanded]').count()
  ok('dialog has url option picker (from #113)', comboCount >= 1, `triggers=${comboCount}`)
} else {
  const anyDlg = await page.locator('[role=dialog]').count()
  skip('dialog has url option picker (from #113)', `dialog not open; role=dialog count=${anyDlg}`)
}
await page.screenshot({ path: 'e2e/ds-detail-v2-dialog.png' })

await page.keyboard.press('Escape')
await page.waitForTimeout(600)

// 6. 已生成行 -> 版本抽屉（新数据集全为未生成，正常走 SKIP）
if (firstVersionBtnRow >= 0) {
  await rows.nth(firstVersionBtnRow).locator('td').nth(colIdx).locator('button').first().click()
  await page.waitForTimeout(1200)
  const drawerVisible = await page.getByText(DRAWER_RE).first().isVisible().catch(() => false)
  ok('versions drawer opens on vN click', drawerVisible)
  await page.screenshot({ path: 'e2e/ds-detail-v2-drawer.png' })
} else {
  skip('versions drawer', 'no generated row on this page')
}

ok('no console errors / 401s', errors.length === 0, errors.slice(0, 3).join(' | '))

const failed = report()
await browser.close()
process.exit(failed === 0 ? 0 : 1)
