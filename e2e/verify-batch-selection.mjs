// 验收批量选择的跨页体验（承接「切页全选会丢掉上一页选中」的修复）。
//
// 覆盖四点：
//   1) 表头全选后翻页再全选，选中数累加而不是被覆盖
//   2) 上一页只选了一部分时，表头 checkbox 走半选态（indeterminate）
//   3) 每页条数可切到 50 / 100，行数随之变化
//   4) 「选择全部 N 条」把当前筛选的全部样例并入选中集合
//
// 走真实存在且有 108 条样例的多轮数据集详情页，headed 跑，失败即抛。
import { chromium } from 'playwright'

const BASE = 'http://localhost'
const HEADED = process.env.HEADLESS !== '1'

function ok(cond, msg) {
  if (!cond) throw new Error('FAIL: ' + msg)
  console.log('  ok  ' + msg)
}

const browser = await chromium.launch({ headless: !HEADED, slowMo: HEADED ? 120 : 0 })
const ctx = await browser.newContext({
  storageState: 'auth.json',
  viewport: { width: 1500, height: 950 },
})
const page = await ctx.newPage()
page.on('pageerror', e => console.log('  [pageerror] ' + e.message))

let failed = null
try {
  await page.goto(BASE + '/conversations/noble-agent-multichat', { waitUntil: 'networkidle' })
  await page.waitForSelector('table.table-base tbody tr', { timeout: 20000 })

  // SelectionBar 初始显示「未选择样例」，选中后才显示「已选 N 条」。
  const bar = page.locator('text=/未选择样例|已选\\s*\\d+\\s*条/').first()
  await bar.waitFor({ timeout: 10000 })
  const readSelected = async () => {
    const text = await bar.innerText()
    if (text.includes('未选择样例')) return 0
    const match = text.match(/已选\s*(\d+)\s*条/)
    if (!match) throw new Error(`无法解析已选数量：${text}`)
    return parseInt(match[1], 10)
  }
  ok((await readSelected()) === 0, '初始未选择样例')

  const headCb = page.locator('table.table-base thead input[type=checkbox]').first()
  const rows = page.locator('table.table-base tbody tr')

  // ── 1) 跨页累积 ─────────────────────────────────────────────
  const page1Rows = await rows.count()
  ok(page1Rows > 0, `第 1 页有 ${page1Rows} 行`)
  await headCb.click()
  const afterP1 = await readSelected()
  ok(afterP1 === page1Rows, `全选第 1 页 → 已选 ${afterP1} 条`)

  // 翻到第 2 页
  const nextBtn = page.locator('button:has-text("下一页")').first()
  ok(await nextBtn.isVisible(), '存在「下一页」按钮')
  await nextBtn.click()
  await page.waitForTimeout(1200)
  await page.waitForSelector('table.table-base tbody tr')
  const stillSelected = await readSelected()
  ok(stillSelected === afterP1, `翻页后选中数保持 ${stillSelected}（未被清空）`)

  const headCbP2Checked = await headCb.isChecked()
  ok(headCbP2Checked === false, '第 2 页表头未勾选（当页未选中任何行）')

  const page2Rows = await rows.count()
  await headCb.click()
  const afterP2 = await readSelected()
  ok(
    afterP2 === afterP1 + page2Rows,
    `第 2 页再全选 → 已选 ${afterP2} 条 = ${afterP1} + ${page2Rows}（跨页累加，旧实现会掉回 ${page2Rows}）`,
  )

  // ── 2) 半选态 ───────────────────────────────────────────────
  // 取消当页第一行，表头应变成 indeterminate 而不是全空。
  await rows.first().locator('input[type=checkbox]').click()
  const partial = await readSelected()
  ok(partial === afterP2 - 1, `取消一行 → 已选 ${partial} 条`)
  const indet = await headCb.evaluate(el => el.indeterminate)
  ok(indet === true, '表头 checkbox 处于半选态（indeterminate）')
  ok((await headCb.isChecked()) === false, '半选时 checked 为 false')

  // 回到第 1 页确认那一页仍是全选（选择没被后续操作破坏）。
  const prevBtn = page.locator('button:has-text("上一页")').first()
  await prevBtn.click()
  await page.waitForTimeout(1200)
  const backIndet = await headCb.evaluate(el => el.indeterminate)
  ok((await headCb.isChecked()) === true && backIndet === false, '回到第 1 页表头仍为全选态')

  // ── 3) 每页条数 ─────────────────────────────────────────────
  const sizeSel = page.locator('select[aria-label="每页条数"]').first()
  ok(await sizeSel.isVisible(), '存在「每页条数」下拉')
  const opts = await sizeSel.locator('option').allInnerTexts()
  ok(
    ['20', '50', '100'].every(v => opts.some(o => o.trim() === v)),
    `可选条数含 20/50/100（实际 ${JSON.stringify(opts.map(o => o.trim()))}）`,
  )
  await sizeSel.selectOption('50')
  await page.waitForTimeout(1800)
  await page.waitForSelector('table.table-base tbody tr')
  const rows50 = await rows.count()
  ok(rows50 > page1Rows, `切到每页 50 → 当页 ${rows50} 行（原 ${page1Rows}）`)

  // ── 4) 跨页全选 ─────────────────────────────────────────────
  await page.locator('button:has-text("清空选择")').first().click()
  await page.waitForTimeout(400)
  ok((await readSelected()) === 0, '「清空选择」清零')

  const selectAllBtn = page.locator('button:has-text("选择全部")').first()
  const totalText = await selectAllBtn.innerText()
  const declaredTotal = parseInt(totalText.match(/(\d+)/)[1], 10)
  ok(declaredTotal > rows50, `「${totalText.trim()}」的 N=${declaredTotal} 大于当页 ${rows50}`)
  await selectAllBtn.click()
  await page.waitForTimeout(3000)
  const afterAll = await readSelected()
  ok(afterAll === declaredTotal, `跨页全选 → 已选 ${afterAll} 条 = 总数 ${declaredTotal}`)
  ok((await headCb.isChecked()) === true, '跨页全选后当页表头为全选态')

  await page.screenshot({ path: 'shot-batch-selection.png', fullPage: false })
  console.log('\nVERIFY=PASS')
} catch (e) {
  failed = e
  try {
    await page.screenshot({ path: 'shot-batch-selection-FAIL.png', fullPage: false })
  } catch {}
  console.log('\n' + String(e.message))
  console.log('VERIFY=FAIL')
} finally {
  if (HEADED) await page.waitForTimeout(1500)
  await browser.close()
}
process.exit(failed ? 1 : 0)
