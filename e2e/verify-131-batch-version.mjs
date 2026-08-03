// 验收 #130 的批量切换版本入口（任务 #131）。
//
// 走 ep-agent 项目的 benchmark 页。库里这个项目恰好有 4 条样例各带 v1(无备注) 与
// v2(备注 v2-sonnet-4.6)，当前指针全在 v2 —— 所以验收方向是「按版本号切到 v1」，
// 预期预览为「将切换 4」，执行后 DB 指针落到 v1。
//
// 覆盖：
//   1) 未勾选时「批量切换版本」按钮 disabled
//   2) 逐条搜索勾选累积到 4 条，按钮文案带 (4)
//   3) 弹窗标题/「按什么挑」三种模式齐全
//   4) 切到「指定版本号」时版本号下拉带命中样例数，预览显示「将切换 4」
//   5) 确认切换后弹窗关闭
// DB 侧指针核验由外层 PowerShell 脚本做，这里只管 UI。
import { chromium } from 'playwright'

const BASE = 'http://localhost'
const PROJECT_ID = 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'
const HEADED = process.env.HEADLESS !== '1'

// 四条有回复版本的样例，用 question 前缀在 UI 里定位（搜索不会清空已选集合）。
// 必须带「请问RPLxxx车型」这段：库里有多条同题干的样例（EFX4-353 / EFL302 / EFL203P
// 等机型各一条），且那些都没有回复版本。只写「如何检修转向接近开关」会被 .first()
// 抓到无版本的那条，后端解析不出版本 → matched=1、下拉 case_count=1。
const TARGETS = [
  '请问RPL201车型，如何检查锂电池BMS是否损坏',
  '请问RPL201车型，锂电池使用时间变短了',
  '请问RPL301车型，如何检查锂电池电芯电压',
  '请问RPL301车型，如何检修转向接近开关',
]

function ok(cond, msg) {
  if (!cond) throw new Error('FAIL: ' + msg)
  console.log('  ok  ' + msg)
}

const browser = await chromium.launch({ headless: !HEADED, slowMo: HEADED ? 90 : 0 })
const ctx = await browser.newContext({
  storageState: 'auth.json',
  viewport: { width: 1500, height: 950 },
})
const page = await ctx.newPage()
page.on('pageerror', e => console.log('  [pageerror] ' + e.message))

let failed = null
try {
  await page.goto(`${BASE}/benchmark/${PROJECT_ID}`, { waitUntil: 'networkidle' })
  await page.waitForSelector('table.table-base tbody tr', { timeout: 25000 })

  const batchBtn = page.locator('button:has-text("批量切换版本")').first()
  await batchBtn.waitFor({ timeout: 10000 })
  ok(await batchBtn.isDisabled(), '未勾选时「批量切换版本」为 disabled')

  const searchBox = page.locator('input[placeholder="搜索问题…"]').first()
  const rows = page.locator('table.table-base tbody tr')

  // ── 逐条搜索 + 勾选，累积到 4 条 ────────────────────────────
  for (const [i, kw] of TARGETS.entries()) {
    await searchBox.fill(kw)
    // 等表格收敛到命中结果：行数变少且首行含关键词。
    await page.waitForFunction(
      (k) => {
        const trs = [...document.querySelectorAll('table.table-base tbody tr')]
        return trs.length > 0 && trs.length <= 5 && trs.some(tr => tr.innerText.includes(k))
      },
      kw,
      { timeout: 20000 },
    )
    const hit = rows.filter({ hasText: kw }).first()
    await hit.locator('input[type=checkbox]').click()
    const label = (await batchBtn.innerText()).trim()
    ok(label.includes(`(${i + 1})`), `勾选第 ${i + 1} 条后按钮为「${label}」`)
  }

  ok(!(await batchBtn.isDisabled()), '勾选后按钮可点')

  // ── 打开弹窗 ────────────────────────────────────────────────
  await batchBtn.click()
  const dialog = page.locator('text=批量切换当前版本').first()
  await dialog.waitFor({ timeout: 10000 })
  ok(true, '弹窗标题为「批量切换当前版本」')

  const modeSel = page.locator('select').filter({ hasText: '最新版本' }).first()
  await modeSel.waitFor({ timeout: 8000 })
  const modeOpts = (await modeSel.locator('option').allInnerTexts()).map(s => s.trim())
  ok(
    ['最新版本', '指定版本号', '指定版本备注'].every(o => modeOpts.includes(o)),
    `「按什么挑」含三种模式（实际 ${JSON.stringify(modeOpts)}）`,
  )

  // ── 切到「指定版本号」→ 选 v1 ───────────────────────────────
  await modeSel.selectOption('version_number')
  const numSel = page.locator('select').filter({ hasText: '条样例有' }).first()
  await numSel.waitFor({ timeout: 15000 })
  const numOpts = (await numSel.locator('option').allInnerTexts()).map(s => s.trim())
  ok(
    numOpts.some(o => o.startsWith('v1') && o.includes('4 条样例有')),
    `版本号下拉含「v1（4 条样例有）」（实际 ${JSON.stringify(numOpts)}）`,
  )
  await numSel.selectOption('1')

  // ── 预览应为「将切换 4」 ────────────────────────────────────
  const badge = page.locator('span.badge:has-text("将切换")').first()
  await badge.waitFor({ timeout: 20000 })
  const badgeText = (await badge.innerText()).trim()
  ok(badgeText.includes('4'), `预览徽标为「${badgeText}」`)
  const totalHint = await page.locator('text=/共\\s*4\\s*条/').first().innerText()
  ok(/共\s*4\s*条/.test(totalHint), `预览合计为「${totalHint.trim()}」`)

  await page.screenshot({ path: 'shot-131-batch-version-preview.png' })

  // ── 执行 ────────────────────────────────────────────────────
  const confirmBtn = page.locator('button:has-text("确认切换")').first()
  const confirmText = (await confirmBtn.innerText()).trim()
  ok(confirmText.includes('4'), `确认按钮为「${confirmText}」`)
  await confirmBtn.click()

  await page.locator('text=批量切换当前版本').first().waitFor({ state: 'detached', timeout: 25000 })
  ok(true, '确认后弹窗关闭')

  await page.screenshot({ path: 'shot-131-batch-version-done.png' })
  console.log('\nVERIFY=PASS')
} catch (e) {
  failed = e
  try {
    await page.screenshot({ path: 'shot-131-batch-version-FAIL.png' })
  } catch {}
  console.log('\n' + String(e.message))
  console.log('VERIFY=FAIL')
} finally {
  if (HEADED) await page.waitForTimeout(1500)
  await browser.close()
}
process.exit(failed ? 1 : 0)
