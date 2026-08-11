// UI acceptance for #170: per-run actual-cost section + local model pricing drawer.
//
// Targets picked from LIVE data (probe_cost_runs.mjs), expected numbers computed
// independently by expect_cost_170.mjs from the same /results payload:
//   431d32d5  single-model   model=persisted-reply  4 rows
//   76431d94  comparative    A=sonnet-4.6 B=kimi-k3 10 rows both sides
//
// Assertions:
//   1. unpriced state  -> warning shown, totals are em-dash, unit-price rows absent
//   2. drawer          -> pricing-note wording is the CURRENT one (cache-write billed at
//                         the miss tier), and NOT the retired "not billed" wording
//   3. suggestion chip -> page model appears under "this page unpriced", one click adds a row
//   4. after save (NO page reload) -> totals/means/unit prices/token rows all match the
//                         independently computed expectation  => live recompute works
//   5. comparative     -> A and B priced separately, delta line direction+amount+pct match
//   6. prefix match    -> configuring "persisted" alone covers "persisted-reply" and the
//                         prefix hint names the matched key
//
// ALL CJK needles are \u escapes on purpose (repo hazard: tools return phantom content for
// files containing literal CJK). Keep this file pure ASCII.

import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const AUTH = 'D:\\program\\agent_eval\\e2e\\auth.json'
const OUT = 'D:\\program\\agent_eval\\e2e\\result-170-cost-ui.json'
const SHOT = 'D:\\program\\agent_eval\\e2e\\shot-170-'
const EXPECT = JSON.parse(readFileSync('D:\\program\\agent_eval\\e2e\\expect_cost_170.json', 'utf8'))

const STORAGE_KEY = 'agent-eval-model-pricing'
const DASH = '\u2014'

const T = {
  sectionTitle: '\u5b9e\u9645\u6210\u672c',                    // 实际成本
  pricingBtn: '\u6a21\u578b\u4ef7\u683c',                      // 模型价格
  cardSingle: '\u672c\u6b21\u8fd0\u884c\u6210\u672c',          // 本次运行成本
  cardA: 'A \u4fa7\u6210\u672c',                               // A 侧成本
  cardB: 'B \u4fa7\u6210\u672c',                               // B 侧成本
  total: '\u603b\u6210\u672c',                                 // 总成本
  mean: '\u5355\u6837\u4f8b\u5747\u503c',                      // 单样例均值
  unpricedWarn: '\u8be5\u6a21\u578b\u672a\u914d\u4ef7',        // 该模型未配价
  prefixHint: '\u6309\u524d\u7f00\u5339\u914d\u5230',          // 按前缀匹配到
  noteHead: '\u8ba1\u4ef7\u53e3\u5f84',                        // 计价口径
  noteGood: '\u7f13\u5b58\u5199\u5165 token \u6309\u672a\u547d\u4e2d\u6863\u8ba1\u8d39', // 缓存写入 token 按未命中档计费
  noteBadA: '\u4e0d\u8ba1\u8d39',                              // 不计费   (retired wording)
  noteBadB: '\u2212 \u7f13\u5b58\u547d\u4e2d \u2212 \u7f13\u5b58\u5199\u5165', // − 缓存命中 − 缓存写入 (retired)
  thModel: '\u6a21\u578b\u540d',                               // 模型名
  save: '\u4fdd\u5b58',                                        // 保存
  addModel: '\u6dfb\u52a0\u6a21\u578b',                        // 添加模型
  pageUnpriced: '\u672c\u9875\u672a\u914d\u4ef7',              // 本页未配价
  deltaLead: 'B \u76f8\u5bf9 A',                               // B 相对 A
  deltaMore: '\u591a\u82b1',                                   // 多花
  deltaLess: '\u7701\u4e0b',                                   // 省下
  rowPriceHit: '\u547d\u4e2d\u8f93\u5165\u5355\u4ef7',         // 命中输入单价
  rowPriceMiss: '\u672a\u547d\u4e2d\u8f93\u5165\u5355\u4ef7',  // 未命中输入单价
  rowPriceOut: '\u8f93\u51fa\u5355\u4ef7',                     // 输出单价
  rowN: '\u8ba1\u4ef7\u6837\u4f8b\u6570',                      // 计价样例数
  rowUnpriced: '\u672a\u914d\u4ef7\u6837\u4f8b',               // 未配价样例
  rowHit: '\u547d\u4e2d\u8f93\u5165 tok',                      // 命中输入 tok
  rowMiss: '\u672a\u547d\u4e2d\u8f93\u5165 tok',               // 未命中输入 tok
  rowOut: '\u8f93\u51fa tok',                                  // 输出 tok
  rowWrite: '\u7f13\u5b58\u5199\u5165 tok',                    // 缓存写入 tok
}

const report = { base: BASE, phases: [], failures: [] }
const fail = (m) => { report.failures.push(m); console.log('FAIL: ' + m) }
const ok = (m) => console.log('ok   ' + m)

function eqStr(tag, label, got, want) {
  if (got === want) { ok(`${tag} ${label} = ${got}`); return true }
  fail(`${tag} ${label}: got "${got}", expected "${want}"`)
  return false
}
function eqNum(tag, label, got, want) {
  const g = Number(String(got == null ? '' : got).replace(/[^0-9.-]/g, ''))
  if (g === want) { ok(`${tag} ${label} = ${g}`); return true }
  fail(`${tag} ${label}: got ${got} (${g}), expected ${want}`)
  return false
}

// Read the whole cost section into a plain object: one entry per card.
async function readSection(page, needles) {
  return await page.evaluate((N) => {
    const sections = Array.from(document.querySelectorAll('section'))
    const sec = sections.find(s => {
      const eyebrow = s.querySelector('.page-eyebrow')
      return eyebrow && (eyebrow.textContent || '').trim() === N.sectionTitle
    })
    if (!sec) return { found: false, sections: sections.length }
    const cards = Array.from(sec.querySelectorAll('.card')).map(card => {
      const metrics = {}
      for (const eb of card.querySelectorAll('.metric-eyebrow')) {
        const val = eb.nextElementSibling
        metrics[(eb.textContent || '').trim()] = val ? (val.textContent || '').trim() : null
      }
      const rows = {}
      for (const d of card.querySelectorAll('div')) {
        if (d.children.length !== 2) continue
        const [a, b] = d.children
        if (a.tagName !== 'SPAN' || b.tagName !== 'SPAN') continue
        rows[(a.textContent || '').trim()] = (b.textContent || '').trim()
      }
      const h3 = card.querySelector('h3')
      const mono = card.querySelector('h3 + span, .font-mono')
      return {
        title: h3 ? (h3.textContent || '').trim() : null,
        modelLabel: mono ? (mono.textContent || '').trim() : null,
        metrics,
        rows,
        unpriced: (card.textContent || '').includes(N.unpricedWarn),
        prefixHint: (card.textContent || '').includes(N.prefixHint),
      }
    })
    const deltaEl = Array.from(sec.querySelectorAll('div'))
      .filter(d => (d.textContent || '').includes(N.deltaLead) && d.children.length === 0)
    const deltaText = deltaEl.length
      ? (deltaEl[deltaEl.length - 1].textContent || '').trim()
      : (Array.from(sec.querySelectorAll('div')).map(d => d.textContent || '')
          .find(t => t.includes(N.deltaLead)) || null)
    return { found: true, cards, deltaText: deltaText ? deltaText.trim() : null }
  }, needles)
}

async function clearPricing(page) {
  await page.evaluate((k) => window.localStorage.removeItem(k), STORAGE_KEY)
}

async function openDrawer(page) {
  const btn = page.locator('button', { hasText: T.pricingBtn }).first()
  await btn.waitFor({ state: 'visible', timeout: 20000 })
  await btn.click()
  await page.locator(`text=${T.noteHead}`).first().waitFor({ state: 'visible', timeout: 10000 })
  await page.waitForTimeout(300)
}

// Wording check inside the open drawer.
async function checkNote(page, tag) {
  const body = await page.evaluate(() => document.body.innerText)
  if (!body.includes(T_NOTE_GOOD)) fail(`${tag} drawer note missing current wording`)
  else ok(`${tag} drawer note carries cache-write-at-miss-tier wording`)
  for (const [name, bad] of [['noteBadA', T_NOTE_BAD_A], ['noteBadB', T_NOTE_BAD_B]]) {
    if (body.includes(bad)) fail(`${tag} drawer still shows retired wording (${name})`)
    else ok(`${tag} drawer clean of retired wording (${name})`)
  }
}
const T_NOTE_GOOD = T.noteGood
const T_NOTE_BAD_A = T.noteBadA
const T_NOTE_BAD_B = T.noteBadB

// Add/fill one model row in the open drawer. Prefers the suggestion chip so that
// path gets exercised; falls back to "add model" + typing.
async function fillModelRow(page, tag, model, price, { useChip = true } = {}) {
  const table0 = page.locator('table').filter({ has: page.locator(`th:text-is("${T.thModel}")`) }).first()
  // A row for this model may already exist (re-pricing an already configured model).
  // Clicking "add model" in that case leaves an EMPTY row behind, and handleSave rejects
  // empty model names -> the drawer never closes. So only add a row when none exists.
  let exists = false
  if (await table0.count() > 0) {
    const rows0 = table0.locator('tbody tr')
    const n0 = await rows0.count()
    for (let i = 0; i < n0; i++) {
      if ((await rows0.nth(i).locator('input').first().inputValue()) === model) { exists = true; break }
    }
  }

  const chip = page.locator('button', { hasText: '+ ' + model }).first()
  const chipCount = await chip.count()
  if (useChip && chipCount > 0) {
    await chip.click()
    ok(`${tag} added row via suggestion chip "+ ${model}"`)
  } else if (!exists) {
    if (useChip) fail(`${tag} suggestion chip "+ ${model}" not offered`)
    await page.locator('button', { hasText: T.addModel }).first().click()
  } else {
    ok(`${tag} editing existing row for ${model} in place`)
  }
  await page.waitForTimeout(200)

  const table = page.locator('table').filter({ has: page.locator(`th:text-is("${T.thModel}")`) }).first()
  const rows = table.locator('tbody tr')
  const n = await rows.count()
  let idx = -1
  for (let i = 0; i < n; i++) {
    const v = await rows.nth(i).locator('input').first().inputValue()
    if (v === model) { idx = i; break }
    if (v === '') idx = i
  }
  if (idx < 0) { fail(`${tag} no editable row for ${model}`); return }
  const inputs = rows.nth(idx).locator('input')
  await inputs.nth(0).fill(model)
  await inputs.nth(1).fill(String(price.inputHit))
  await inputs.nth(2).fill(String(price.inputMiss))
  await inputs.nth(3).fill(String(price.output))
}

async function saveDrawer(page, tag) {
  await page.locator('button', { hasText: T.save }).first().click()
  await page.locator(`text=${T.noteHead}`).first().waitFor({ state: 'hidden', timeout: 10000 })
  await page.waitForTimeout(600)
  ok(`${tag} drawer saved and closed`)
}

function assertCard(tag, card, exp, price, currency = '$') {
  eqStr(tag, 'total', card.metrics[T.total], exp.totalStr)
  eqStr(tag, 'mean', card.metrics[T.mean], exp.meanStr)
  eqStr(tag, 'priceHit', card.rows[T.rowPriceHit], `${currency}${price.inputHit}/M`)
  eqStr(tag, 'priceMiss', card.rows[T.rowPriceMiss], `${currency}${price.inputMiss}/M`)
  eqStr(tag, 'priceOut', card.rows[T.rowPriceOut], `${currency}${price.output}/M`)
  eqNum(tag, 'n', card.rows[T.rowN], exp.n)
  eqNum(tag, 'unpricedCount', card.rows[T.rowUnpriced], exp.unpriced)
  eqNum(tag, 'hitTok', card.rows[T.rowHit], exp.hitInputTokens)
  eqNum(tag, 'missTok', card.rows[T.rowMiss], exp.missInputTokens)
  eqNum(tag, 'outTok', card.rows[T.rowOut], exp.outputTokens)
  eqNum(tag, 'writeTok', card.rows[T.rowWrite], exp.cacheWriteTokens)
  if (card.unpriced) fail(`${tag} still shows unpriced warning after pricing`)
}

const browser = await chromium.launch({ headless: false, slowMo: 40 })
const ctx = await browser.newContext({ storageState: AUTH, viewport: { width: 1560, height: 1300 } })
const page = await ctx.newPage()

try {
  // ---------------- Phase 1: single-model run ----------------
  {
    const exp = EXPECT['431d32d5']
    const tag = '431d32d5'
    const rec = { phase: 'single', run_id: exp.run_id }

    await page.goto(`${BASE}/evaluation/runs/${exp.run_id}`, { waitUntil: 'networkidle', timeout: 60000 })
    await clearPricing(page)
    await page.reload({ waitUntil: 'networkidle', timeout: 60000 })
    await page.waitForTimeout(1200)

    let sec = await readSection(page, T)
    rec.unpricedState = sec
    if (!sec.found) { fail(`${tag} cost section not found`); throw new Error('no section') }
    if (sec.cards.length !== 1) fail(`${tag} expected 1 card, got ${sec.cards.length}`)
    const c0 = sec.cards[0]
    eqStr(tag, 'card title', c0.title, T.cardSingle)
    if (!c0.unpriced) fail(`${tag} unpriced warning missing before pricing`)
    else ok(`${tag} unpriced warning shown before pricing`)
    eqStr(tag, 'total(unpriced)', c0.metrics[T.total], DASH)
    eqStr(tag, 'mean(unpriced)', c0.metrics[T.mean], DASH)
    if (c0.rows[T.rowPriceHit] != null) fail(`${tag} unit-price rows should be hidden when unpriced`)
    else ok(`${tag} unit-price rows hidden when unpriced`)
    eqNum(tag, 'unpricedCount(before)', c0.rows[T.rowUnpriced], exp.rows)
    await page.screenshot({ path: `${SHOT}${tag}-unpriced.png` })

    await openDrawer(page)
    await checkNote(page, tag)
    const bodyTxt = await page.evaluate(() => document.body.innerText)
    if (!bodyTxt.includes(T.pageUnpriced)) fail(`${tag} "this page unpriced" hint absent`)
    else ok(`${tag} "this page unpriced" hint present`)
    await page.screenshot({ path: `${SHOT}${tag}-drawer.png` })
    await fillModelRow(page, tag, exp.modelA, exp.priceA)
    await saveDrawer(page, tag)

    sec = await readSection(page, T)
    rec.pricedState = sec
    assertCard(tag, sec.cards[0], exp.A, exp.priceA)
    if (sec.cards[0].prefixHint) fail(`${tag} exact match should not show prefix hint`)
    else ok(`${tag} exact match: no prefix hint`)
    await page.screenshot({ path: `${SHOT}${tag}-priced.png` })

    // prefix-match branch: reconfigure with a strict prefix of the model name
    await clearPricing(page)
    await page.reload({ waitUntil: 'networkidle', timeout: 60000 })
    await page.waitForTimeout(1000)
    const prefix = exp.modelA.split('-')[0]
    await openDrawer(page)
    await fillModelRow(page, tag, prefix, exp.priceA, { useChip: false })
    await saveDrawer(page, tag)
    const secP = await readSection(page, T)
    rec.prefixState = secP
    const cp = secP.cards[0]
    if (!cp.prefixHint) fail(`${tag} prefix hint missing for key "${prefix}"`)
    else ok(`${tag} prefix hint shown for key "${prefix}"`)
    if (!(cp.metrics[T.total] || '').includes(prefix) && cp.metrics[T.total] !== exp.A.totalStr) {
      fail(`${tag} prefix-priced total ${cp.metrics[T.total]}, expected ${exp.A.totalStr}`)
    } else ok(`${tag} prefix-priced total = ${cp.metrics[T.total]}`)
    await page.screenshot({ path: `${SHOT}${tag}-prefix.png` })
    report.phases.push(rec)
  }

  // ---------------- Phase 2: comparative run ----------------
  {
    const exp = EXPECT['76431d94']
    const tag = '76431d94'
    const rec = { phase: 'comparative', run_id: exp.run_id }

    await page.goto(`${BASE}/evaluation/runs/${exp.run_id}`, { waitUntil: 'networkidle', timeout: 60000 })
    await clearPricing(page)
    await page.reload({ waitUntil: 'networkidle', timeout: 60000 })
    await page.waitForTimeout(1500)

    let sec = await readSection(page, T)
    rec.unpricedState = sec
    if (!sec.found) { fail(`${tag} cost section not found`); throw new Error('no section') }
    if (sec.cards.length !== 2) fail(`${tag} expected 2 cards, got ${sec.cards.length}`)
    eqStr(tag, 'card A title', sec.cards[0] && sec.cards[0].title, T.cardA)
    eqStr(tag, 'card B title', sec.cards[1] && sec.cards[1].title, T.cardB)
    if (sec.deltaText) fail(`${tag} delta line should be absent when unpriced: ${sec.deltaText}`)
    else ok(`${tag} no delta line while unpriced`)
    await page.screenshot({ path: `${SHOT}${tag}-unpriced.png` })

    await openDrawer(page)
    await checkNote(page, tag)
    await fillModelRow(page, tag, exp.modelA, exp.priceA)
    await fillModelRow(page, tag, exp.modelB, exp.priceB)
    await page.screenshot({ path: `${SHOT}${tag}-drawer.png` })
    await saveDrawer(page, tag)

    sec = await readSection(page, T)
    rec.pricedState = sec
    assertCard(tag + '/A', sec.cards[0], exp.A, exp.priceA)
    assertCard(tag + '/B', sec.cards[1], exp.B, exp.priceB)

    const want = `${T.deltaLead} ${exp.deltaDir === 'more' ? T.deltaMore : T.deltaLess} ${exp.deltaStr}`
    const got = sec.deltaText || ''
    if (!got.includes(want)) fail(`${tag} delta line "${got}" missing "${want}"`)
    else ok(`${tag} delta line direction+amount ok: ${got}`)
    if (!got.includes(exp.deltaPct)) fail(`${tag} delta pct ${exp.deltaPct} missing from "${got}"`)
    else ok(`${tag} delta pct ${exp.deltaPct} present`)
    rec.deltaText = got
    await page.screenshot({ path: `${SHOT}${tag}-priced.png`, fullPage: false })

    // live recompute: halve B's output price, B total must drop, no reload
    await openDrawer(page)
    const halved = { ...exp.priceB, output: exp.priceB.output / 2 }
    await fillModelRow(page, tag, exp.modelB, halved, { useChip: false })
    await saveDrawer(page, tag)
    const sec2 = await readSection(page, T)
    const bBefore = Number((sec.cards[1].metrics[T.total] || '').replace(/[^0-9.]/g, ''))
    const bAfter = Number((sec2.cards[1].metrics[T.total] || '').replace(/[^0-9.]/g, ''))
    rec.liveRecompute = { bBefore, bAfter }
    if (!(bAfter < bBefore)) fail(`${tag} B total did not drop after price cut (${bBefore} -> ${bAfter})`)
    else ok(`${tag} live recompute without reload: B ${bBefore} -> ${bAfter}`)
    const expAfter = exp.B.total - (exp.B.outputTokens / 1_000_000) * (exp.priceB.output / 2)
    const digits = Math.abs(expAfter) >= 1 ? 2 : Math.abs(expAfter) >= 0.01 ? 4 : 6
    eqStr(tag, 'B total after cut', sec2.cards[1].metrics[T.total], `$${expAfter.toFixed(digits)}`)
    await page.screenshot({ path: `${SHOT}${tag}-recompute.png` })
    report.phases.push(rec)
  }
} catch (e) {
  fail('exception: ' + (e && e.message ? e.message : String(e)))
} finally {
  report.verdict = report.failures.length === 0 ? 'VERIFY_170_UI_OK' : 'VERIFY_170_UI_FAIL'
  writeFileSync(OUT, JSON.stringify(report, null, 1))
  console.log('failures=' + report.failures.length)
  console.log(report.verdict)
  await browser.close()
}
