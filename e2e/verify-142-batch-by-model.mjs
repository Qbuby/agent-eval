// 验收 #138/#139/#140/#141：批量切换弹窗的「按模型」维度 + 版本抽屉的模型标识。
//
// 数据前提（probe-142-models.mjs 实测）：ep-agent benchmark 的这 4 条样例各有
// v1(无备注) 与 v2(备注 v2-sonnet-4.6)，两版的 agent_config.model 都是 sonnet-4.6，
// 当前指针在 v1 —— 所以「按模型 sonnet-4.6」会各自解析到最新的 v2，预览 changed=4。
//
// 覆盖：
//   1) 「按什么挑」四种模式齐全（含新增的「指定模型」）
//   2) 切到「指定模型」后模型下拉带命中样例数「sonnet-4.6（4 条样例有）」
//   3) 预览显示「将切换 4」，且新增的「解析到的模型」汇总行给出 sonnet-4.6 · 4 条
//   4) 确认切换后弹窗关闭
//   5) 版本抽屉里每个版本行都带模型 badge（#140），当前版本已落到 v2
// DB 侧指针核验交给 probe-142-verify-db.mjs（走 states 接口，不自证）。
import { chromium } from 'playwright'

const BASE = 'http://localhost'
const PROJECT_ID = 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'
const HEADED = process.env.HEADLESS !== '1'
const MODEL = 'sonnet-4.6'

// 与 #131 同一批样例。必须带「请问RPLxxx车型」前缀：库里有多条同题干但无回复版本的
// 样例，只写后半段会被 .first() 抓到没有版本的那条。
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

const browser = await chromium.launch({ headless: !HEADED, slowMo: HEADED ? 80 : 0 })
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

  const searchBox = page.locator('input[placeholder="搜索问题…"]').first()
  const rows = page.locator('table.table-base tbody tr')

  // ── 勾选 4 条 ───────────────────────────────────────────────
  for (const [i, kw] of TARGETS.entries()) {
    await searchBox.fill(kw)
    await page.waitForFunction(
      (k) => {
        const trs = [...document.querySelectorAll('table.table-base tbody tr')]
        return trs.length > 0 && trs.length <= 5 && trs.some(tr => tr.innerText.includes(k))
      },
      kw,
      { timeout: 20000 },
    )
    await rows.filter({ hasText: kw }).first().locator('input[type=checkbox]').click()
    const label = (await batchBtn.innerText()).trim()
    ok(label.includes(`(${i + 1})`), `勾选第 ${i + 1} 条后按钮为「${label}」`)
  }

  // ── 打开弹窗，四种模式 ──────────────────────────────────────
  await batchBtn.click()
  await page.locator('text=批量切换当前版本').first().waitFor({ timeout: 10000 })

  const modeSel = page.locator('select').filter({ hasText: '最新版本' }).first()
  await modeSel.waitFor({ timeout: 8000 })
  const modeOpts = (await modeSel.locator('option').allInnerTexts()).map(s => s.trim())
  ok(
    ['最新版本', '指定版本号', '指定版本备注', '指定模型'].every(o => modeOpts.includes(o)),
    `「按什么挑」含四种模式（实际 ${JSON.stringify(modeOpts)}）`,
  )

  // ── 切到「指定模型」 ────────────────────────────────────────
  await modeSel.selectOption('model')
  const modelSel = page.locator('select').filter({ hasText: '条样例有' }).first()
  await modelSel.waitFor({ timeout: 15000 })
  const modelOpts = (await modelSel.locator('option').allInnerTexts()).map(s => s.trim())
  ok(
    modelOpts.some(o => o.includes(MODEL) && o.includes('4 条样例有')),
    `模型下拉含「${MODEL}（4 条样例有）」（实际 ${JSON.stringify(modelOpts)}）`,
  )
  await modelSel.selectOption(MODEL)

  // 模式提示应换成按模型那句
  const hint = await page.locator('text=/该模型生成的那条回复/').first().innerText()
  ok(hint.includes('该模型生成的那条回复'), `模式提示为「${hint.trim()}」`)

  // ── 预览：将切换 4 + 解析到的模型汇总 ───────────────────────
  const badge = page.locator('span.badge:has-text("将切换")').first()
  await badge.waitFor({ timeout: 20000 })
  const badgeText = (await badge.innerText()).trim()
  ok(badgeText.includes('4'), `预览徽标为「${badgeText}」`)

  const modelsRow = page.locator('div:has-text("解析到的模型：")').last()
  await modelsRow.waitFor({ timeout: 10000 })
  const modelsText = (await modelsRow.innerText()).replace(/\s+/g, ' ').trim()
  ok(
    modelsText.includes(MODEL) && /4\s*条/.test(modelsText),
    `「解析到的模型」汇总为「${modelsText}」`,
  )

  await page.screenshot({ path: 'shot-142-by-model-preview.png' })

  // ── 执行 ────────────────────────────────────────────────────
  const confirmBtn = page.locator('button:has-text("确认切换")').first()
  const confirmText = (await confirmBtn.innerText()).trim()
  ok(confirmText.includes('4'), `确认按钮为「${confirmText}」`)
  await confirmBtn.click()
  await page.locator('text=批量切换当前版本').first().waitFor({ state: 'detached', timeout: 25000 })
  ok(true, '确认后弹窗关闭')

  // ── 版本抽屉：模型 badge + 当前版本已是 v2 ──────────────────
  await searchBox.fill(TARGETS[0])
  await page.waitForFunction(
    (k) => {
      const trs = [...document.querySelectorAll('table.table-base tbody tr')]
      return trs.length > 0 && trs.length <= 5 && trs.some(tr => tr.innerText.includes(k))
    },
    TARGETS[0],
    { timeout: 20000 },
  )
  const targetRow = rows.filter({ hasText: TARGETS[0] }).first()
  // 行内版本按钮文案形如「v2 · 共2版」，切换成功后应显示 v2
  const verBtn = targetRow.locator('button[title*="回溯"]').first()
  await verBtn.waitFor({ timeout: 15000 })
  await page.waitForFunction(
    (k) => {
      const tr = [...document.querySelectorAll('table.table-base tbody tr')]
        .find(t => t.innerText.includes(k))
      const b = tr?.querySelector('button[title*="回溯"]')
      return !!b && /v2/.test(b.textContent || '')
    },
    TARGETS[0],
    { timeout: 20000 },
  )
  const verBtnText = (await verBtn.innerText()).trim()
  ok(/v2/.test(verBtnText), `列表行版本按钮已变为「${verBtnText}」`)

  await verBtn.click()
  await page.locator('text=/v2\\s*详情/').first().waitFor({ timeout: 15000 })

  // 抽屉里每个版本行的模型 badge（#140 新增，font-mono 的小徽标）
  const modelBadges = page.locator('span.font-mono')
  const badgeCount = await modelBadges.count()
  const badgeTexts = []
  for (let i = 0; i < badgeCount; i++) {
    badgeTexts.push((await modelBadges.nth(i).innerText()).trim())
  }
  const modelBadgeHits = badgeTexts.filter(t => t.includes(MODEL))
  ok(
    modelBadgeHits.length >= 2,
    `抽屉版本行带模型 badge ×${modelBadgeHits.length}（全部 font-mono 文本 ${JSON.stringify(badgeTexts)}）`,
  )

  // 当前版本徽标应在 v2 这一行
  const curRow = page.locator('button:has(span.badge-success:has-text("当前"))').first()
  await curRow.waitFor({ timeout: 10000 })
  const curText = (await curRow.innerText()).replace(/\s+/g, ' ').trim()
  ok(/^v2\b/.test(curText), `抽屉里「当前」标记在 v2 行（实际「${curText}」）`)
  ok(curText.includes(MODEL), `当前版本行同时显示模型「${MODEL}」`)

  await page.screenshot({ path: 'shot-142-drawer-model.png' })
  console.log('\nVERIFY=PASS')
} catch (e) {
  failed = e
  try {
    await page.screenshot({ path: 'shot-142-FAIL.png' })
  } catch {}
  console.log('\n' + String(e.message))
  console.log('VERIFY=FAIL')
} finally {
  if (HEADED) await page.waitForTimeout(1500)
  await browser.close()
}
process.exit(failed ? 1 : 0)
