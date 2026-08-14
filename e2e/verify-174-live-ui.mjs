// UI acceptance for #174 against a REAL multi-turn run (no seeded fixture).
//
// Run under test: created by _probe174_multi.mjs against the 18001 mock agent,
// which reports usage_metadata {input 10, output 8, total 18} per turn and no
// cache tokens. Case has 3 user turns -> session 30/24/54.
//
// Checks:
//   1. drawer renders 3 user turns
//   2. each turn shows its own token line with 10 / 8 / 18
//   3. sum(per-turn) == session totals shown by the API
//   4. cost section warns unpriced, then prices to the exact expected total
//
// Pure ASCII source on purpose (CJK literals in this repo trigger phantom reads).
import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const AUTH = 'D:\\program\\agent_eval\\e2e\\auth.json'
const OUT = 'D:\\program\\agent_eval\\e2e\\result-174-live-ui.json'
const SHOT = 'D:\\program\\agent_eval\\e2e\\shot-174-live-'

const RUN_ID = process.env.RUN_ID || 'b7b29e98-96e2-4818-afcd-da110b990333'
const STORAGE_KEY = 'agent-eval-model-pricing'
const MODEL = 'T174-MULTI mock'

const EXPECT_TURN = { in: 10, out: 8, total: 18 }
const N_TURNS = 3
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

const report = { base: BASE, run_id: RUN_ID, failures: [] }
const fail = (m) => { report.failures.push(m); console.log('FAIL: ' + m) }
const ok = (m) => console.log('ok   ' + m)

// ---- pull the row from the API so the UI assertions have a real anchor ----
const auth = JSON.parse(readFileSync(AUTH, 'utf8'))
const rawLs = auth.origins?.[0]?.localStorage?.find(x => x.name === 'agent-eval-auth')?.value
const token = JSON.parse(rawLs)?.state?.accessToken
if (!token) throw new Error('no accessToken in auth.json')

const api = await fetch(`${BASE}/api/eval/runs/${RUN_ID}/results?page=1&page_size=50`, {
  headers: { Authorization: 'Bearer ' + token },
})
if (api.status !== 200) throw new Error('results API HTTP ' + api.status)
const rows = (await api.json()).items || []
if (rows.length !== 1) fail('expected 1 result row from API, got ' + rows.length)
const row = rows[0]
const SESSION = {
  prompt: row.prompt_tokens, completion: row.completion_tokens, total: row.total_tokens,
}
report.apiSession = SESSION
report.apiQuestion = String(row.question || '').slice(0, 80)
console.log('API row=' + String(row.id).slice(0, 8) + ' session=' +
  JSON.stringify(SESSION) + ' turns=' + (row.full_trace?.conversation?.turns || []).length)

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
      found: true, card: true, total,
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
  const trs = table.locator('tbody tr')
  const n = await trs.count()
  let idx = -1
  for (let i = 0; i < n; i++) {
    const v = await trs.nth(i).locator('input').first().inputValue()
    if (v === MODEL) { idx = i; break }
    if (v === '') idx = i
  }
  if (idx < 0) { fail('no editable pricing row'); return }
  const inputs = trs.nth(idx).locator('input')
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

  // Only one result row in this run; anchor on the question cell's title attr
  // (the cell is CSS-truncated, so text matching is unreliable) and click the
  // enclosing <tr> to open the replay drawer.
  const anchor = String(row.question || '').slice(0, 20).replace(/"/g, '')
  let tr = page.locator('tr').filter({ has: page.locator(`[title*="${anchor}"]`) }).first()
  if (await tr.count() === 0) {
    // Fallback: the run has exactly one data row, so take the first tbody tr.
    tr = page.locator('table tbody tr').first()
    ok('anchored on first tbody row (title attr not matched)')
  }
  if (await tr.count() === 0) {
    fail('no result row visible in results table')
  } else {
    await tr.click()
    await page.locator(`text=${T.turnUser}`).first()
      .waitFor({ state: 'visible', timeout: 15000 })
      .catch(() => fail('result drawer did not render the per-turn replay'))
    await page.waitForTimeout(500)
  }
  await page.screenshot({ path: `${SHOT}replay.png`, fullPage: true })

  // ---- 1 & 2: per-turn token lines ----
  const seen = await readTurns(page, T)
  report.seen = seen
  if (seen.turnCount !== N_TURNS) fail(`turn count: got ${seen.turnCount}, expected ${N_TURNS}`)
  else ok(`turn count = ${N_TURNS}`)
  if (seen.lines.length !== N_TURNS) {
    fail(`per-turn token lines: got ${seen.lines.length}, expected ${N_TURNS} :: ` +
      JSON.stringify(seen.lines))
  } else ok(`per-turn token lines = ${N_TURNS}`)

  const parsed = seen.lines.map(t => ({ nums: (t.match(/\d+/g) || []).map(Number), text: t }))
  report.parsed = parsed
  for (let i = 0; i < parsed.length; i++) {
    const got = parsed[i].nums.slice(0, 3).join(',')
    const want = [EXPECT_TURN.in, EXPECT_TURN.out, EXPECT_TURN.total].join(',')
    if (got !== want) fail(`turn ${i + 1} tokens: got [${got}], expected [${want}] :: "${parsed[i].text}"`)
    else ok(`turn ${i + 1} tokens = in ${EXPECT_TURN.in} / out ${EXPECT_TURN.out} / total ${EXPECT_TURN.total}`)
  }

  // ---- 3: rendered per-turn sum == session totals ----
  const sum = parsed.reduce((a, p) => ({
    in: a.in + (p.nums[0] || 0), out: a.out + (p.nums[1] || 0), total: a.total + (p.nums[2] || 0),
  }), { in: 0, out: 0, total: 0 })
  report.sums = { rendered: sum, session: SESSION }
  if (sum.in !== SESSION.prompt) fail(`sum(per-turn).in = ${sum.in}, session prompt = ${SESSION.prompt}`)
  else ok(`sum(per-turn).in == session prompt ${SESSION.prompt}`)
  if (sum.out !== SESSION.completion) fail(`sum(per-turn).out = ${sum.out}, session completion = ${SESSION.completion}`)
  else ok(`sum(per-turn).out == session completion ${SESSION.completion}`)
  if (sum.total !== SESSION.total) fail(`sum(per-turn).total = ${sum.total}, session total = ${SESSION.total}`)
  else ok(`sum(per-turn).total == session total ${SESSION.total}`)

  // ---- 4: cost section prices this multi-turn run ----
  const before = await readCostTotal(page, T)
  report.costBefore = before
  if (!before.found) fail('cost section not found on multi-turn run detail')
  else if (!before.unpriced) fail('cost section should warn unpriced before pricing')
  else ok('cost section present and warns unpriced')

  await page.keyboard.press('Escape')
  await page.locator('div.fixed.inset-0.z-50').first()
    .waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})
  await page.waitForTimeout(300)

  await priceModel(page)
  const after = await readCostTotal(page, T)
  report.costAfter = after

  // No cache tokens in this run: all prompt tokens bill at the miss tier.
  const expTotal = (SESSION.prompt / 1e6) * PRICE.inputMiss
    + (SESSION.completion / 1e6) * PRICE.output
  const digits = Math.abs(expTotal) >= 1 ? 2 : Math.abs(expTotal) >= 0.01 ? 4 : 6
  const wantTotal = `$${expTotal.toFixed(digits)}`
  report.costExpected = { expTotal, wantTotal }
  if (after.total !== wantTotal) fail(`cost total: got "${after.total}", expected "${wantTotal}"`)
  else ok(`cost total = ${after.total}`)
  if (after.unpriced) fail('cost section still warns unpriced after pricing')

  await page.screenshot({ path: `${SHOT}cost.png`, fullPage: true })
} catch (e) {
  fail('exception: ' + (e && e.message ? e.message : String(e)))
} finally {
  report.verdict = report.failures.length === 0 ? 'VERIFY_174_LIVE_UI_OK' : 'VERIFY_174_LIVE_UI_FAIL'
  writeFileSync(OUT, JSON.stringify(report, null, 1))
  console.log('failures=' + report.failures.length)
  console.log(report.verdict)
  await browser.close()
}
