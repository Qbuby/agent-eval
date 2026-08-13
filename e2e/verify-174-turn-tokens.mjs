// UI acceptance for #174: multi-turn results must carry token/cost metrics.
//
// Seed (created by _t174_seed.py, marked T174-VERIFY, removed afterwards):
//   run    = 4d5cb9ef-2598-4cdd-aeb5-9d591d954e92   single-model, 1 result row
//   result = 4edea9e2-271d-4978-bb2e-ee41c9d2de27   3 turns
//     turn 1  usage  in 100 / out 10 / total 110 / cacheRead 5  / cacheWrite 1
//     turn 2  usage  in 200 / out 20 / total 220 / cacheRead 10 / cacheWrite 2
//     turn 3  usage = null  -> the whole per-turn token line must be ABSENT
//                              (not rendered as zeros)
//   session totals on the result row = per-turn sums: 300 / 30 / 330 / 15 / 3
//
// Assertions:
//   1. per-turn token line present on turns 1-2 with the exact seeded numbers
//   2. turn 3 renders NO token line (agent reported nothing -> stay silent)
//   3. sum(per-turn) == session-level totals shown on the row  => #174 no longer empty
//   4. cost section prices the run (was blank before): total > 0 after pricing,
//      and it equals the independently computed value from the session tokens
//
// ALL CJK needles are \u escapes on purpose (repo hazard: tools return phantom
// content for files containing literal CJK). Keep this file pure ASCII.

import { chromium } from 'playwright'
import { writeFileSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const AUTH = 'D:\\program\\agent_eval\\e2e\\auth.json'
const OUT = 'D:\\program\\agent_eval\\e2e\\result-174-turn-tokens.json'
const SHOT = 'D:\\program\\agent_eval\\e2e\\shot-174-'

const RUN_ID = '4d5cb9ef-2598-4cdd-aeb5-9d591d954e92'
const RESULT_ID = '4edea9e2-271d-4978-bb2e-ee41c9d2de27'
const STORAGE_KEY = 'agent-eval-model-pricing'
const MODEL = 'T174-VERIFY mock'

// Expected per-turn usage, mirroring the seed. index = display turn order.
const EXPECT_TURNS = [
  { in: 100, out: 10, total: 110, cacheRead: 5, cacheWrite: 1 },
  { in: 200, out: 20, total: 220, cacheRead: 10, cacheWrite: 2 },
  null, // no usage reported -> no line at all
]
const EXPECT_SESSION = { in: 300, out: 30, total: 330, cacheRead: 15, cacheWrite: 3 }
const PRICE = { inputHit: 1, inputMiss: 10, output: 100 }

const T = {
  tokenLead: '\u672c\u8f6e token\uff1a',          // 本轮 token：
  turnUser: '\u7528\u6237 \u00b7 \u7b2c',          // 用户 · 第
  costTitle: '\u5b9e\u9645\u6210\u672c',           // 实际成本
  pricingBtn: '\u6a21\u578b\u4ef7\u683c',          // 模型价格
  noteHead: '\u8ba1\u4ef7\u53e3\u5f84',            // 计价口径
  thModel: '\u6a21\u578b\u540d',                   // 模型名
  save: '\u4fdd\u5b58',                            // 保存
  addModel: '\u6dfb\u52a0\u6a21\u578b',            // 添加模型
  total: '\u603b\u6210\u672c',                     // 总成本
  unpricedWarn: '\u8be5\u6a21\u578b\u672a\u914d\u4ef7', // 该模型未配价
}

const report = { base: BASE, run_id: RUN_ID, result_id: RESULT_ID, failures: [] }
const fail = (m) => { report.failures.push(m); console.log('FAIL: ' + m) }
const ok = (m) => console.log('ok   ' + m)

// Pull every per-turn token line out of the conversation replay, in DOM order,
// plus how many assistant turns exist, so a missing line is distinguishable
// from a missing turn.
async function readTurns(page, needles) {
  return await page.evaluate((N) => {
    const all = Array.from(document.querySelectorAll('div'))
    const userBubbles = all.filter(d => {
      const t = (d.textContent || '')
      return t.includes(N.turnUser) && d.children.length === 0
    })
    const lines = all
      .filter(d => (d.textContent || '').startsWith(N.tokenLead))
      .map(d => (d.textContent || '').trim())
    return { turnCount: userBubbles.length, lines }
  }, needles)
}

// Parse "本轮 token：入 100 / 出 10 · 合计 110 · 缓存命中 5 · 缓存写入 1"
// by position: all integers in the line, in order.
function parseLine(text) {
  const nums = (text.match(/\d+/g) || []).map(Number)
  return { nums, text }
}

async function readCostTotal(page, needles) {
  return await page.evaluate((N) => {
    const sec = Array.from(document.querySelectorAll('section')).find(s => {
      const eb = s.querySelector('.page-eyebrow')
      return eb && (eb.textContent || '').trim() === N.costTitle
    })
    if (!sec) return { found: false }
    const card = sec.querySelector('.card')
    if (!card) return { found: true, card: false }
    let total = null
    for (const eb of card.querySelectorAll('.metric-eyebrow')) {
      if ((eb.textContent || '').trim() === N.total) {
        const v = eb.nextElementSibling
        total = v ? (v.textContent || '').trim() : null
      }
    }
    return {
      found: true,
      card: true,
      total,
      unpriced: (card.textContent || '').includes(N.unpricedWarn),
    }
  }, needles)
}

async function priceModel(page) {
  const btn = page.locator('button', { hasText: T.pricingBtn }).first()
  await btn.waitFor({ state: 'visible', timeout: 20000 })
  await btn.click()
  await page.locator(`text=${T.noteHead}`).first().waitFor({ state: 'visible', timeout: 10000 })
  await page.waitForTimeout(300)

  const chip = page.locator('button', { hasText: '+ ' + MODEL }).first()
  if (await chip.count() > 0) {
    await chip.click()
    ok(`priced via suggestion chip "+ ${MODEL}"`)
  } else {
    await page.locator('button', { hasText: T.addModel }).first().click()
    ok('priced via add-model row (no chip offered)')
  }
  await page.waitForTimeout(200)

  const table = page.locator('table')
    .filter({ has: page.locator(`th:text-is("${T.thModel}")`) }).first()
  const rows = table.locator('tbody tr')
  const n = await rows.count()
  let idx = -1
  for (let i = 0; i < n; i++) {
    const v = await rows.nth(i).locator('input').first().inputValue()
    if (v === MODEL) { idx = i; break }
    if (v === '') idx = i
  }
  if (idx < 0) { fail('no editable pricing row'); return }
  const inputs = rows.nth(idx).locator('input')
  await inputs.nth(0).fill(MODEL)
  await inputs.nth(1).fill(String(PRICE.inputHit))
  await inputs.nth(2).fill(String(PRICE.inputMiss))
  await inputs.nth(3).fill(String(PRICE.output))

  await page.locator('button', { hasText: T.save }).first().click()
  await page.locator(`text=${T.noteHead}`).first().waitFor({ state: 'hidden', timeout: 10000 })
  await page.waitForTimeout(600)
}

const browser = await chromium.launch({ headless: false, slowMo: 40 })
const ctx = await browser.newContext({ storageState: AUTH, viewport: { width: 1560, height: 1300 } })
const page = await ctx.newPage()

try {
  await page.goto(`${BASE}/evaluation/runs/${RUN_ID}`, { waitUntil: 'networkidle', timeout: 60000 })
  await page.evaluate((k) => window.localStorage.removeItem(k), STORAGE_KEY)
  await page.reload({ waitUntil: 'networkidle', timeout: 60000 })
  await page.waitForTimeout(1500)

  // Open the seeded result row so the conversation replay renders.
  // The replay lives in a Drawer opened by clicking the <tr> in the results
  // table (ResultRow onSelect). The question cell is truncated CSS-wise, so
  // anchor on its title attribute (EvaluationRunDetailPage.tsx: the question
  // cell renders <div className="... truncate" title={row.question}>) and take
  // the enclosing <tr>. Matching bare page text would hit the run title
  // heading instead, which is not clickable and never opens the drawer.
  const row = page.locator('tr')
    .filter({ has: page.locator('[title*="T174-VERIFY"]') }).first()
  if (await row.count() === 0) {
    fail('seeded result row (T174-VERIFY) not visible in results table')
  } else {
    await row.click()
    // Wait for the drawer body (per-turn replay) rather than a fixed sleep.
    await page.locator(`text=${T.turnUser}`).first()
      .waitFor({ state: 'visible', timeout: 15000 })
      .catch(() => fail('result drawer did not render the per-turn replay'))
    await page.waitForTimeout(500)
  }
  await page.screenshot({ path: `${SHOT}replay.png`, fullPage: true })

  // ---- 1 & 2: per-turn token lines ----
  const seen = await readTurns(page, T)
  report.turnsSeen = seen
  const wantLines = EXPECT_TURNS.filter(Boolean).length
  if (seen.turnCount !== EXPECT_TURNS.length) {
    fail(`turn count: got ${seen.turnCount}, expected ${EXPECT_TURNS.length}`)
  } else ok(`turn count = ${seen.turnCount}`)

  if (seen.lines.length !== wantLines) {
    fail(`per-turn token lines: got ${seen.lines.length}, expected ${wantLines} `
       + `(turn 3 has no usage and must render none) :: ${JSON.stringify(seen.lines)}`)
  } else ok(`per-turn token lines = ${wantLines} (turn 3 correctly silent)`)

  const parsed = seen.lines.map(parseLine)
  report.parsedLines = parsed
  const expects = EXPECT_TURNS.filter(Boolean)
  const sum = { in: 0, out: 0, total: 0, cacheRead: 0, cacheWrite: 0 }
  for (let i = 0; i < Math.min(parsed.length, expects.length); i++) {
    const e = expects[i]
    const want = [e.in, e.out, e.total, e.cacheRead, e.cacheWrite]
    const got = parsed[i].nums
    if (JSON.stringify(got) !== JSON.stringify(want)) {
      fail(`turn ${i + 1} tokens: got [${got}], expected [${want}] :: "${parsed[i].text}"`)
    } else ok(`turn ${i + 1} tokens = in ${e.in} / out ${e.out} / total ${e.total} `
            + `/ cacheRead ${e.cacheRead} / cacheWrite ${e.cacheWrite}`)
    sum.in += e.in; sum.out += e.out; sum.total += e.total
    sum.cacheRead += e.cacheRead; sum.cacheWrite += e.cacheWrite
  }

  // ---- 3: per-turn sums == session-level totals ----
  report.sums = { rendered: sum, session: EXPECT_SESSION }
  for (const k of Object.keys(EXPECT_SESSION)) {
    if (sum[k] !== EXPECT_SESSION[k]) {
      fail(`sum(per-turn).${k} = ${sum[k]}, session total = ${EXPECT_SESSION[k]}`)
    } else ok(`sum(per-turn).${k} == session ${EXPECT_SESSION[k]}`)
  }

  // ---- 4: cost section actually prices this multi-turn run ----
  const before = await readCostTotal(page, T)
  report.costBefore = before
  if (!before.found) fail('cost section not found on multi-turn run detail')
  else if (!before.unpriced) fail('cost section should warn unpriced before pricing')
  else ok('cost section present and warns unpriced')

  // The replay Drawer overlays the page (fixed inset-0 z-50) and would swallow
  // clicks aimed at the pricing button, so close it before touching the panel.
  await page.keyboard.press('Escape')
  await page.locator('div.fixed.inset-0.z-50').first()
    .waitFor({ state: 'detached', timeout: 10000 })
  await page.waitForTimeout(300)

  await priceModel(page)
  const after = await readCostTotal(page, T)
  report.costAfter = after

  // prompt_tokens includes cache reads+writes; cache write bills at the miss tier.
  const miss = EXPECT_SESSION.in - EXPECT_SESSION.cacheRead - EXPECT_SESSION.cacheWrite
  const expTotal = (EXPECT_SESSION.cacheRead / 1e6) * PRICE.inputHit
    + ((miss + EXPECT_SESSION.cacheWrite) / 1e6) * PRICE.inputMiss
    + (EXPECT_SESSION.out / 1e6) * PRICE.output
  const digits = Math.abs(expTotal) >= 1 ? 2 : Math.abs(expTotal) >= 0.01 ? 4 : 6
  const wantTotal = `$${expTotal.toFixed(digits)}`
  report.costExpected = { expTotal, wantTotal, missInputTokens: miss }
  if (after.total !== wantTotal) {
    fail(`cost total: got "${after.total}", expected "${wantTotal}"`)
  } else ok(`cost total = ${after.total} (multi-turn run is no longer blank)`)
  if (after.unpriced) fail('cost section still warns unpriced after pricing')

  await page.screenshot({ path: `${SHOT}cost.png`, fullPage: true })
} catch (e) {
  fail('exception: ' + (e && e.message ? e.message : String(e)))
} finally {
  report.verdict = report.failures.length === 0 ? 'VERIFY_174_UI_OK' : 'VERIFY_174_UI_FAIL'
  writeFileSync(OUT, JSON.stringify(report, null, 1))
  console.log('failures=' + report.failures.length)
  console.log(report.verdict)
  await browser.close()
}
