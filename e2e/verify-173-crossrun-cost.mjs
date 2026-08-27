// #173 -- cross-run real-money cost section on EvaluationComparePage.
//
// What the user reported: "the compare page for two runs still has no cost display".
// The page only had token/latency metrics. This verifies the newly mounted
// CrossRunCostSection actually RENDERS -- code being in the bundle proves nothing.
//
// Why colours are read via getComputedStyle: tierFill() builds `rgb(${var} / a)`.
// If the caller passes an already-wrapped `rgb(var(--x))` the result is illegal CSS,
// the fill silently falls back to transparent, and every numeric assertion still passes.
// Only the resolved backgroundColor exposes that failure.
//
// Widths are checked twice (inline style % and rendered rect %) because a correct
// style string can still render at zero width inside a collapsed flex parent.
//
// This file is deliberately pure ASCII: every CJK needle is a \u escape.
// (Repo hazard: tools return phantom content for files holding literal CJK.)

import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const DIR = 'D:\\program\\agent_eval\\e2e\\'
const OUT = DIR + 'report_173_crossrun.json'
const SHOT = DIR + 'shot-173-'

const EXP = JSON.parse(readFileSync(DIR + 'expect_173_crossrun.json', 'utf8'))
const PROBE = JSON.parse(readFileSync(DIR + 'probe-user.json', 'utf8'))

// The generator emits idShort/model/totalStr/meanStr; the assertions below read
// label/totalText/meanText. Derive them once here rather than at every use site --
// a missing key yields want:"undefined", which fails loudly but blames the feature
// instead of the script. The run label must match the page's runLabel(): the
// separator is U+00B7 MIDDLE DOT, not an ASCII hyphen.
const LABEL_SEP = ' · '
for (const r of EXP.runs) {
  r.label = r.model ? r.idShort + LABEL_SEP + r.model : r.idShort
  r.totalText = r.totalStr
  r.meanText = r.meanStr
}
const labelOf = (pick) => (pick.model ? pick.idShort + LABEL_SEP + pick.model : pick.idShort)
EXP.cheapestLabel = labelOf(EXP.cheapest)
EXP.dearestLabel = labelOf(EXP.dearest)

const STORAGE_KEY = 'agent-eval-model-pricing'
const TIER_ALPHA = [0.32, 0.6, 1]

// CJK needles, all escaped.
const T = {
  section: '\u5b9e\u7b97\u6210\u672c\uff08\u6309\u8fd0\u884c\uff09', // "real cost (by run)"
  tokBar: 'token \u6784\u6210',                                       // "token composition"
  costBar: '\u6210\u672c\u6784\u6210',                                // "cost composition"
  cheapest: '\u5355\u6837\u4f8b\u6700\u7701',                         // "cheapest per sample"
  dearest: '\u6700\u8d35',                                            // "dearest"
  totalScale: '\u603b\u6210\u672c\u5bf9\u6bd4',                       // aria prefix "total cost compare"
  meanScale: '\u5355\u6837\u4f8b\u5747\u6210\u672c\u5bf9\u6bd4',      // aria prefix "mean cost compare"
  unpriced: '\u8be5\u6a21\u578b\u672a\u914d\u4ef7',                   // "model has no price"
  totalLabel: '\u603b\u6210\u672c',                                   // "total cost"
  meanLabel: '\u5355\u6837\u4f8b\u5747\u503c',                        // "mean per sample"
}

const report = { base: BASE, expect: EXP, checks: [], failures: [] }
const fail = (m) => { report.failures.push(m); console.log('FAIL: ' + m) }
const ok = (m) => { report.checks.push(m); console.log('ok   ' + m) }
const near = (got, want, tol) => Number.isFinite(got) && Math.abs(got - want) <= tol

// Log in here rather than reusing auth.json: access tokens are short-lived and the
// refresh token is single-use, so a stale file produces a 401 wall that is
// indistinguishable from a broken feature.
async function freshStorageState() {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: PROBE.username, password: PROBE.password }),
  })
  if (!r.ok) throw new Error(`login -> ${r.status} ${(await r.text()).slice(0, 200)}`)
  const tok = await r.json()
  const meRes = await fetch(`${BASE}/api/auth/me`, {
    headers: { Authorization: `Bearer ${tok.access_token}` },
  })
  if (!meRes.ok) throw new Error(`me -> ${meRes.status}`)
  const me = await meRes.json()
  console.log(`logged in as ${me.username} role=${me.role}`)
  return {
    cookies: [],
    origins: [{
      origin: new URL(BASE).origin,
      localStorage: [{
        name: 'agent-eval-auth',
        value: JSON.stringify({
          state: { accessToken: tok.access_token, refreshToken: tok.refresh_token, user: me },
          version: 0,
        }),
      }],
    }],
  }
}

const parseRgb = (s) => {
  const m = /^rgba?\(([^)]+)\)$/.exec(String(s || '').trim())
  if (!m) return null
  const p = m[1].split(/[,/]/).map(x => Number(x.trim()))
  if (p.length < 3 || p.slice(0, 3).some(n => Number.isNaN(n))) return null
  return { r: p[0], g: p[1], b: p[2], a: p.length > 3 && !Number.isNaN(p[3]) ? p[3] : 1 }
}

async function setPricing(page, models) {
  await page.evaluate(([key, cfg]) => {
    window.localStorage.setItem(key, JSON.stringify(cfg))
    window.dispatchEvent(new StorageEvent('storage', { key }))
  }, [STORAGE_KEY, { version: 1, currency: '$', models }])
}

// Scrape the cost section: anchor on the .page-eyebrow whose text starts with the
// section title, walk up to the <section>, then read every card inside it.
async function scrape(page, needles) {
  return page.evaluate((N) => {
    const eyebrows = [...document.querySelectorAll('.page-eyebrow')]
    const hit = eyebrows.find(e => (e.textContent || '').trim().startsWith(N.section))
    if (!hit) return { found: false }
    const sec = hit.closest('section')
    if (!sec) return { found: false, reason: 'no section ancestor' }

    const barOf = (card, label) => {
      const lab = [...card.querySelectorAll('div')]
        .find(d => (d.textContent || '').trim() === label && d.children.length === 0)
      if (!lab) return null
      const bar = lab.parentElement && lab.parentElement.querySelector('[role="img"]')
      if (!bar) return null
      const segs = [...bar.children].map(seg => {
        const cs = getComputedStyle(seg)
        const rect = seg.getBoundingClientRect()
        return {
          styleWidth: seg.style.width,
          bg: cs.backgroundColor,
          marginLeft: cs.marginLeft,
          renderWidth: rect.width,
        }
      })
      return { aria: bar.getAttribute('aria-label'), barWidth: bar.getBoundingClientRect().width, segs }
    }

    const cards = [...sec.querySelectorAll('.card')].map(card => {
      const title = card.querySelector('.page-eyebrow')
      const vals = [...card.querySelectorAll('.metric-value')].map(v => (v.textContent || '').trim())
      const swatches = [...card.querySelectorAll('span.inline-block.h-2.w-2.rounded-sm')]
        .map(s => getComputedStyle(s).backgroundColor)
      return {
        title: title ? (title.textContent || '').trim() : null,
        metricValues: vals,
        swatches,
        unpriced: (card.textContent || '').includes(N.unpriced),
        tokBar: barOf(card, N.tokBar),
        costBar: barOf(card, N.costBar),
      }
    })

    // Scale bars live directly under the section (not in a card); match by aria prefix.
    // The group caption is the sibling right above the role="img" rows container.
    // It is read as rendered geometry, not just text: an aria-only or sr-only
    // caption leaves adjacent groups visually indistinguishable, which is exactly
    // the defect this block guards (two runs with equal sample counts produce
    // identical bar lengths in both groups).
    const scaleOf = (prefix) => {
      const el = [...sec.querySelectorAll('[role="img"]')]
        .find(e => (e.getAttribute('aria-label') || '').startsWith(prefix))
      if (!el) return null
      const capEl = el.previousElementSibling
      const capRect = capEl ? capEl.getBoundingClientRect() : null
      const caption = capEl ? {
        text: (capEl.textContent || '').trim(),
        width: capRect.width,
        height: capRect.height,
        fontSize: getComputedStyle(capEl).fontSize,
        color: getComputedStyle(capEl).color,
      } : null
      const groupTop = el.parentElement ? el.parentElement.getBoundingClientRect().top : 0
      const rows = [...el.children].map(row => {
        const lab = row.querySelector('span')
        const track = row.querySelector('div')
        const fillEl = track ? track.querySelector('div') : null
        const money = row.lastElementChild
        return {
          label: lab ? (lab.textContent || '').trim() : null,
          styleWidth: fillEl ? fillEl.style.width : null,
          bg: fillEl ? getComputedStyle(fillEl).backgroundColor : null,
          fillWidth: fillEl ? fillEl.getBoundingClientRect().width : 0,
          trackWidth: track ? track.getBoundingClientRect().width : 0,
          money: money ? (money.textContent || '').trim() : null,
          top: row.getBoundingClientRect().top,
          bottom: row.getBoundingClientRect().bottom,
        }
      })
      return { aria: el.getAttribute('aria-label'), caption, groupTop, rows }
    }

    return {
      found: true,
      sectionText: (sec.textContent || '').replace(/\s+/g, ' ').trim(),
      cards,
      totalScale: scaleOf(N.totalScale),
      meanScale: scaleOf(N.meanScale),
    }
  }, needles)
}

// Three tiers of one entity: same hue, alphas strictly ascending toward TIER_ALPHA.
function checkTierBar(tag, bar, wantRgb) {
  if (!bar) return fail(`${tag}: bar missing`)
  if (bar.segs.length !== 3) return fail(`${tag}: segs=${bar.segs.length} want 3`)
  let prevA = -1
  bar.segs.forEach((s, i) => {
    const c = parseRgb(s.bg)
    if (!c) return fail(`${tag}[${i}]: unparseable bg "${s.bg}" (illegal CSS -> silent transparent)`)
    if (c.a === 0) return fail(`${tag}[${i}]: alpha=0, segment invisible`)
    if (c.r !== wantRgb[0] || c.g !== wantRgb[1] || c.b !== wantRgb[2]) {
      fail(`${tag}[${i}]: hue ${c.r},${c.g},${c.b} want ${wantRgb.join(',')}`)
    }
    if (!near(c.a, TIER_ALPHA[i], 0.02)) fail(`${tag}[${i}]: alpha=${c.a} want ~${TIER_ALPHA[i]}`)
    if (c.a <= prevA) fail(`${tag}[${i}]: alpha not ascending (${prevA} -> ${c.a})`)
    prevA = c.a
    if (!(s.renderWidth > 0)) fail(`${tag}[${i}]: rendered width 0`)
    if (i > 0 && s.marginLeft !== '2px') fail(`${tag}[${i}]: gap=${s.marginLeft} want 2px`)
  })
  if (report.failures.length === 0 || !report.failures.some(f => f.startsWith(tag))) {
    ok(`${tag}: 3 tiers, hue ${wantRgb.join(',')}, alphas ascending, 2px gaps, non-zero`)
  }
}

function checkScale(tag, scale, wantRows, widthKey) {
  if (!scale) return fail(`${tag}: scale bar missing`)
  if (scale.rows.length !== wantRows.length) {
    return fail(`${tag}: rows=${scale.rows.length} want ${wantRows.length}`)
  }
  wantRows.forEach((w, i) => {
    const r = scale.rows[i]
    const stylePct = Number(String(r.styleWidth || '').replace('%', ''))
    if (!near(stylePct, w[widthKey], 0.01)) {
      fail(`${tag}[${i}] ${w.label}: styleWidth=${r.styleWidth} want ${w[widthKey].toFixed(1)}%`)
    }
    const renderPct = r.trackWidth > 0 ? (r.fillWidth / r.trackWidth) * 100 : NaN
    if (!near(renderPct, w[widthKey], 1.0)) {
      fail(`${tag}[${i}] ${w.label}: renderPct=${Number.isFinite(renderPct) ? renderPct.toFixed(2) : 'NaN'} want ${w[widthKey].toFixed(1)}%`)
    }
    if (r.money !== w.money) fail(`${tag}[${i}] ${w.label}: money="${r.money}" want "${w.money}"`)
    const c = parseRgb(r.bg)
    if (!c) fail(`${tag}[${i}]: unparseable bg "${r.bg}"`)
    else if (c.r !== w.rgb[0] || c.g !== w.rgb[1] || c.b !== w.rgb[2]) {
      fail(`${tag}[${i}] ${w.label}: hue ${c.r},${c.g},${c.b} want ${w.rgb.join(',')}`)
    }
  })
  if (!report.failures.some(f => f.startsWith(tag))) {
    ok(`${tag}: ${wantRows.length} rows, widths ${wantRows.map(w => w[widthKey].toFixed(1) + '%').join(' / ')} (style+rect), colours per run`)
  }
}

// The group caption must be ON SCREEN, not only in aria-label. Regression guard:
// both groups list the same run labels, and when the runs have equal sample counts
// the two groups render byte-identical bar lengths -- the caption is then the ONLY
// thing telling total-cost from mean-cost. A text-only check would pass on an
// sr-only caption, so width/height/font-size are asserted as rendered.
function checkCaption(tag, scale, wantText) {
  if (!scale) return  // absence already reported by checkScale
  const c = scale.caption
  if (!c) return fail(`${tag}: no caption element above the bars (calibre invisible on screen)`)
  if (c.text !== wantText) return fail(`${tag}: caption="${c.text}" want "${wantText}"`)
  if (!(c.width > 0 && c.height > 0)) {
    return fail(`${tag}: caption "${c.text}" has zero box (${c.width}x${c.height}) -- not visible`)
  }
  if (parseFloat(c.fontSize) < 8) fail(`${tag}: caption fontSize=${c.fontSize} too small to read`)
  const a = parseRgb(c.color)
  if (a && a.a === 0) fail(`${tag}: caption fully transparent`)
  if (!report.failures.some(f => f.startsWith(tag))) {
    ok(`${tag}: caption "${c.text}" visible (${c.width.toFixed(0)}x${c.height.toFixed(0)}px @ ${c.fontSize})`)
  }
}

// Grouping must be legible as grouping: the gap BETWEEN the two groups has to beat
// the gap between rows inside a group, otherwise four bars read as one list of four
// regardless of captions.
function checkGrouping(totalScale, meanScale) {
  const tag = 'grouping'
  if (!totalScale || !meanScale) return
  const rowsA = totalScale.rows, rowsB = meanScale.rows
  if (rowsA.length < 2 || rowsB.length < 1) return
  const innerGaps = []
  for (let i = 1; i < rowsA.length; i++) innerGaps.push(rowsA[i].top - rowsA[i - 1].bottom)
  const innerMax = Math.max(...innerGaps)
  // Between-group distance: last bar of group 1 to the top of group 2 (its caption).
  const between = meanScale.groupTop - rowsA[rowsA.length - 1].bottom
  if (!(between > innerMax)) {
    fail(`${tag}: between-group gap ${between.toFixed(1)}px <= max row gap ${innerMax.toFixed(1)}px -- four bars read as one group`)
  } else {
    ok(`${tag}: between-group ${between.toFixed(1)}px > row gap ${innerMax.toFixed(1)}px`)
  }
}

const browser = await chromium.launch({ headless: false, slowMo: 30 })
const ctx = await browser.newContext({
  storageState: await freshStorageState(),
  viewport: { width: 1560, height: 1400 },
})
const page = await ctx.newPage()
page.on('pageerror', e => fail('pageerror: ' + e.message))

try {
  const ids = EXP.runs.map(r => r.id).join(',')
  const url = `${BASE}/evaluation/compare?ids=${ids}`
  console.log('open ' + url)
  await page.goto(url, { waitUntil: 'domcontentloaded' })
  // Seed prices before results land so the first paint of the cost section is priced.
  const models = {}
  for (const r of EXP.runs) models[r.model] = r.price
  await setPricing(page, models)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await setPricing(page, models)

  await page.waitForFunction((needle) => {
    return [...document.querySelectorAll('.page-eyebrow')]
      .some(e => (e.textContent || '').trim().startsWith(needle))
  }, T.section, { timeout: 30000 }).catch(() => {})

  const s = await scrape(page, T)
  if (!s.found) {
    fail('cost section NOT rendered on compare page (this is the exact bug the user reported)')
  } else {
    ok('cost section rendered')

    if (s.cards.length !== EXP.runs.length) {
      fail(`cards=${s.cards.length} want ${EXP.runs.length}`)
    } else {
      ok(`${s.cards.length} run cards`)
    }

    EXP.runs.forEach((want, i) => {
      const card = s.cards[i]
      if (!card) return fail(`card[${i}] missing`)
      const tag = `card[${i}] ${want.label}`
      if (card.title !== want.label) fail(`${tag}: title="${card.title}" want "${want.label}"`)
      if (card.unpriced) fail(`${tag}: shows unpriced warning -- price key did not match model name`)
      const wantVals = [want.totalText, want.meanText]
      wantVals.forEach((v, k) => {
        if (card.metricValues[k] !== v) {
          fail(`${tag}: metric[${k}]="${card.metricValues[k]}" want "${v}"`)
        }
      })
      if (!report.failures.some(f => f.startsWith(tag))) ok(`${tag}: ${want.totalText} / ${want.meanText}`)
      checkTierBar(`${tag} tokBar`, card.tokBar, want.rgb)
      checkTierBar(`${tag} costBar`, card.costBar, want.rgb)
      if (card.swatches.length !== 3) fail(`${tag}: legend swatches=${card.swatches.length} want 3`)
    })

    const wantRows = EXP.runs.map(r => ({
      label: r.label,
      rgb: r.rgb,
      totalWidthPct: r.totalWidthPct,
      meanWidthPct: r.meanWidthPct,
      money: r.totalText,
    }))
    checkScale('totalScale', s.totalScale, wantRows, 'totalWidthPct')
    checkScale('meanScale', s.meanScale, wantRows.map((w, i) => ({
      ...w, money: EXP.runs[i].meanText,
    })), 'meanWidthPct')

    // Readability of the two adjacent groups: visible captions + distinguishable spacing.
    checkCaption('totalCaption', s.totalScale, T.totalScale)
    checkCaption('meanCaption', s.meanScale, T.meanScale)
    if (s.totalScale && s.meanScale && s.totalScale.caption && s.meanScale.caption) {
      if (s.totalScale.caption.text === s.meanScale.caption.text) {
        fail(`captions: both groups titled "${s.totalScale.caption.text}" -- calibre indistinguishable`)
      } else {
        ok(`captions distinct: "${s.totalScale.caption.text}" vs "${s.meanScale.caption.text}"`)
      }
    }
    checkGrouping(s.totalScale, s.meanScale)

    // Spread sentence: cheapest / dearest / percentage, per-sample basis.
    const txt = s.sectionText
    const spreadNeedle = `${EXP.spreadPct.toFixed(1)}%`
    for (const [what, needle] of [
      ['cheapest label', T.cheapest],
      ['cheapest run', EXP.cheapestLabel],
      ['dearest label', T.dearest],
      ['dearest run', EXP.dearestLabel],
      ['spread pct', spreadNeedle],
    ]) {
      if (!txt.includes(needle)) fail(`spread text missing ${what}: "${needle}"`)
    }
    if (!report.failures.some(f => f.startsWith('spread'))) {
      ok(`spread text: cheapest ${EXP.cheapestLabel}, dearest ${EXP.dearestLabel}, +${spreadNeedle}`)
    }
  }

  await page.screenshot({ path: SHOT + 'compare.png' })
  await page.screenshot({ path: SHOT + 'compare-full.png', fullPage: true })
  report.screenshots = [SHOT + 'compare.png', SHOT + 'compare-full.png']
  report.scrape = s
} catch (e) {
  fail('exception: ' + (e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : String(e)))
} finally {
  report.verdict = report.failures.length === 0 ? 'CROSSRUN_COST_PASS' : 'CROSSRUN_COST_FAIL'
  writeFileSync(OUT, JSON.stringify(report, null, 1), 'utf8')
  console.log('VERDICT=' + report.verdict + ' failures=' + report.failures.length)
  await browser.close()
}
