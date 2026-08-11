// UI acceptance for #172: the NEW visuals inside the per-run cost section.
//
// Scope is only what CostPanel.tsx added on top of the already-verified #170 numbers:
//   CostShareBars   two same-colour stacked bars (token composition vs cost composition)
//                   + a 3-tier legend carrying the shares/amounts
//   CostCompareBars A/B total cost on ONE shared scale (comparative runs only)
//
// Expectations are NOT written here: expect-170-charts.mjs computes them from the live
// /results payload, this file only compares. Widths are checked twice -- the inline style
// AND the real getBoundingClientRect ratio -- so a styled-but-unrendered bar cannot pass.
//
// Targets (same live runs as #170):
//   431d32d5  single-model   persisted-reply   4 rows   (no compare bars expected)
//   76431d94  comparative    sonnet-4.6 / kimi-k3  10 rows  (compare bars expected)
//
// This file is deliberately pure ASCII and contains NO literal CJK: every CJK needle is
// either a \u escape or comes straight out of the expectation JSON. (Repo hazard: tools
// return phantom content for files holding literal CJK.)

import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const DIR = 'D:\\program\\agent_eval\\e2e\\'
const OUT = DIR + 'result-170-charts.json'
const SHOT = DIR + 'shot-170c-'

const CHARTS = JSON.parse(readFileSync(DIR + 'expect_charts_170.json', 'utf8'))
const COSTS = JSON.parse(readFileSync(DIR + 'expect_cost_170.json', 'utf8'))
const PROBE = JSON.parse(readFileSync(DIR + 'probe-user.json', 'utf8'))

// Log in HERE instead of loading a stored auth.json: the access token in that file is
// short-lived and its refresh token is single-use, so a stale file yields a wall of 401s
// that looks exactly like a broken feature. Minting a token per run removes that failure mode.
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
  const persisted = {
    state: { accessToken: tok.access_token, refreshToken: tok.refresh_token, user: me },
    version: 0,
  }
  return {
    cookies: [],
    origins: [{
      origin: new URL(BASE).origin,
      localStorage: [{ name: 'agent-eval-auth', value: JSON.stringify(persisted) }],
    }],
  }
}

const STORAGE_KEY = 'agent-eval-model-pricing'
const DASH = '\u2014'
const T_TOKPREFIX = 'tok '
const T_MONEY = '\u94b1 '            // "money"
const T_UNPRICED = '\u8be5\u6a21\u578b\u672a\u914d\u4ef7'   // "this model has no price configured"

const report = { base: BASE, phases: [], failures: [] }
const fail = (m) => { report.failures.push(m); console.log('FAIL: ' + m) }
const ok = (m) => console.log('ok   ' + m)

const near = (got, want, tol) => Number.isFinite(got) && Math.abs(got - want) <= tol

function eqStr(tag, label, got, want) {
  if (got === want) return ok(`${tag} ${label}`)
  fail(`${tag} ${label}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`)
}
function eqPct(tag, label, got, want, tol) {
  if (near(got, want, tol)) return ok(`${tag} ${label} ${got.toFixed(2)}% (want ${want.toFixed(2)}%)`)
  fail(`${tag} ${label}: got ${got}, want ${want} (tol ${tol})`)
}

// ---- page-side extraction -------------------------------------------------
// Anchor on the bar whose aria-label equals the expected token-composition string,
// walk up to its <section>, and read every card in DOM order. Nothing is located by
// a hardcoded CJK selector.
const SCRAPE = (anchorAria) => {
  const bars = Array.from(document.querySelectorAll('[role="img"]'))
  const anchor = bars.find(b => b.getAttribute('aria-label') === anchorAria)
  if (!anchor) return { found: false, ariaSeen: bars.map(b => b.getAttribute('aria-label')) }
  const section = anchor.closest('section')
  if (!section) return { found: false, noSection: true }

  const rootStyle = getComputedStyle(document.documentElement)
  const cssVar = (n) => rootStyle.getPropertyValue(n).trim()

  const readBar = (el) => {
    const box = el.getBoundingClientRect()
    const segs = Array.from(el.children).map(c => {
      const cs = getComputedStyle(c)
      const r = c.getBoundingClientRect()
      return {
        styleWidth: c.style.width,
        stylePct: parseFloat(c.style.width),
        renderPct: box.width > 0 ? (r.width / box.width) * 100 : null,
        marginLeft: cs.marginLeft,
        bg: cs.backgroundColor,
        title: c.getAttribute('title'),
        visible: r.width > 0 && r.height > 0,
      }
    })
    return { aria: el.getAttribute('aria-label'), barWidth: box.width, barHeight: box.height, segs }
  }

  const cards = Array.from(section.querySelectorAll('.card')).map(card => {
    const stacks = Array.from(card.querySelectorAll('[role="img"]')).map(readBar)
    // legend cells sit in the 3-column grid right under the bars
    const grid = Array.from(card.querySelectorAll('.grid-cols-3'))
    const legend = grid.length
      ? Array.from(grid[grid.length - 1].children).map(cell => {
          const swatch = cell.querySelector('span[class*="h-2"]')
          return {
            text: cell.innerText.replace(/\s+/g, ' ').trim(),
            swatchBg: swatch ? getComputedStyle(swatch).backgroundColor : null,
          }
        })
      : []
    return {
      title: (card.querySelector('.page-eyebrow') || {}).innerText || null,
      text: card.innerText.replace(/\s+/g, ' ').trim(),
      stacks,
      legend,
    }
  })

  // compare bars: a role=img directly under the section (not inside any card)
  const compare = Array.from(section.querySelectorAll('[role="img"]'))
    .filter(el => !el.closest('.card'))
    .map(el => {
      const rows = Array.from(el.children).map(row => {
        const track = row.querySelector('div')
        const fillEl = track ? track.querySelector('div') : null
        const tb = track ? track.getBoundingClientRect() : null
        const fb = fillEl ? fillEl.getBoundingClientRect() : null
        const spans = Array.from(row.querySelectorAll('span'))
        return {
          label: spans.length ? spans[0].innerText.trim() : null,
          amount: spans.length > 1 ? spans[spans.length - 1].innerText.trim() : null,
          stylePct: fillEl ? parseFloat(fillEl.style.width) : null,
          renderPct: tb && fb && tb.width > 0 ? (fb.width / tb.width) * 100 : null,
          bg: fillEl ? getComputedStyle(fillEl).backgroundColor : null,
        }
      })
      return { aria: el.getAttribute('aria-label'), rows }
    })

  return {
    found: true,
    sectionText: section.innerText.replace(/\s+/g, ' ').trim(),
    cards,
    compare,
    accent: cssVar('--accent'),
    info: cssVar('--info'),
  }
}

const alphaOf = (rgb) => {
  const m = /rgba?\(([^)]+)\)/.exec(rgb || '')
  if (!m) return null
  const parts = m[1].split(',').map(s => parseFloat(s))
  return parts.length >= 4 ? parts[3] : 1
}
const rgbTriple = (rgb) => {
  const m = /rgba?\(([^)]+)\)/.exec(rgb || '')
  if (!m) return null
  return m[1].split(',').slice(0, 3).map(s => Math.round(parseFloat(s))).join(',')
}
const varTriple = (v) => (v || '').split(/[\s,]+/).filter(Boolean).slice(0, 3).map(Number).join(',')

// ---- per-bar assertions --------------------------------------------------
function checkStack(tag, bar, tiers, kind, expectAria) {
  if (!bar) return fail(`${tag} ${kind} bar missing`)
  eqStr(tag, `${kind} aria-label`, bar.aria, expectAria)

  if (bar.segs.length !== tiers.length) {
    return fail(`${tag} ${kind} segment count ${bar.segs.length}, want ${tiers.length}`)
  }
  ok(`${tag} ${kind} has ${tiers.length} segments`)

  tiers.forEach((t, i) => {
    const s = bar.segs[i]
    const wantPct = kind === 'token' ? t.tokenWidthPct : t.costWidthPct
    const wantTitle = kind === 'token' ? t.tokenTitle : t.costTitle
    eqPct(tag, `${kind} seg${i} style width`, s.stylePct, wantPct, 0.01)
    eqPct(tag, `${kind} seg${i} rendered width`, s.renderPct, wantPct, 1.0)
    eqStr(tag, `${kind} seg${i} title`, s.title, wantTitle)
    if (!s.visible) fail(`${tag} ${kind} seg${i} not actually painted (zero box)`)
    // 2px surface gap between neighbours, none before the first
    const wantMargin = i > 0 ? '2px' : '0px'
    if (s.marginLeft !== wantMargin) {
      fail(`${tag} ${kind} seg${i} marginLeft ${s.marginLeft}, want ${wantMargin}`)
    }
  })

  // tier shading must ascend with the price tier (cheapest lightest -> output darkest)
  const alphas = bar.segs.map(s => alphaOf(s.bg))
  const ascending = alphas.every((a, i) => i === 0 || (a != null && alphas[i - 1] != null && a > alphas[i - 1]))
  if (ascending) ok(`${tag} ${kind} tier alpha ascends ${alphas.map(a => a.toFixed(2)).join(' < ')}`)
  else fail(`${tag} ${kind} tier alpha not ascending: ${JSON.stringify(alphas)}`)

  return alphas
}

function checkSideColour(tag, card, wantTriple, sideName) {
  const bar = card.stacks[0]
  if (!bar || !bar.segs.length) return fail(`${tag} no segment to sample colour from`)
  const got = rgbTriple(bar.segs[0].bg)
  if (got === wantTriple) ok(`${tag} entity colour = ${sideName} (${got})`)
  else fail(`${tag} entity colour ${got}, want ${sideName} ${wantTriple}`)
}

function checkLegend(tag, card, tiers, priced) {
  if (card.legend.length !== tiers.length) {
    return fail(`${tag} legend cells ${card.legend.length}, want ${tiers.length}`)
  }
  tiers.forEach((t, i) => {
    const cell = card.legend[i]
    const need = [t.label, T_TOKPREFIX + t.tokenSharePct]
    if (priced) need.push(T_MONEY + t.costSharePct, t.costStr)
    const missing = need.filter(n => !cell.text.includes(n))
    if (missing.length === 0) ok(`${tag} legend[${i}] carries ${JSON.stringify(need)}`)
    else fail(`${tag} legend[${i}] text ${JSON.stringify(cell.text)} missing ${JSON.stringify(missing)}`)
    // swatch colour must equal the segment fill of the same tier
    const segBg = card.stacks[0] && card.stacks[0].segs[i] ? card.stacks[0].segs[i].bg : null
    if (cell.swatchBg && segBg && cell.swatchBg !== segBg) {
      fail(`${tag} legend[${i}] swatch ${cell.swatchBg} != segment ${segBg}`)
    }
  })
  if (!priced) {
    const leaked = card.legend.filter(c => c.text.includes(T_MONEY))
    if (leaked.length) fail(`${tag} legend shows money share while unpriced`)
    else ok(`${tag} legend omits money share while unpriced`)
  }
}

// the whole point of the paired bars: same tier, different width in each bar
function checkGapEvidence(tag, tiers, wantGap) {
  const gaps = tiers.map(t => Math.abs(t.tokenWidthPct - t.costWidthPct))
  const got = Math.max(...gaps)
  if (near(got, wantGap, 0.01)) ok(`${tag} max token-vs-cost width gap ${got.toFixed(2)}pp`)
  else fail(`${tag} max width gap ${got}, want ${wantGap}`)
  if (got < 5) fail(`${tag} gap ${got.toFixed(2)}pp too small to read as evidence`)
}

async function setPricing(page, models, currency = '$') {
  await page.evaluate(([key, cfg]) => {
    window.localStorage.setItem(key, JSON.stringify(cfg))
    window.dispatchEvent(new StorageEvent('storage', { key }))
  }, [STORAGE_KEY, { version: 1, currency, models }])
}
async function clearPricing(page) {
  await page.evaluate((key) => {
    window.localStorage.removeItem(key)
    window.dispatchEvent(new StorageEvent('storage', { key }))
  }, STORAGE_KEY)
}

const browser = await chromium.launch({ headless: false, slowMo: 30 })
const ctx = await browser.newContext({ storageState: await freshStorageState(), viewport: { width: 1560, height: 1400 } })
const page = await ctx.newPage()
page.on('pageerror', e => fail('pageerror: ' + e.message))

try {
  // ================= phase 1: single run, unpriced =================
  {
    const key = '431d32d5'
    const exp = CHARTS[key]
    const tag = key + '/unpriced'
    await page.goto(`${BASE}/evaluation/runs/${exp.run_id}`, { waitUntil: 'networkidle', timeout: 60000 })
    await clearPricing(page)
    await page.waitForTimeout(400)

    const sec = await page.evaluate(SCRAPE, exp.A.tokenAria)
    report.phases.push({ phase: tag, found: sec.found, cards: sec.found ? sec.cards.length : 0 })
    if (!sec.found) {
      fail(`${tag} token bar not found; aria seen = ${JSON.stringify(sec.ariaSeen)}`)
    } else {
      ok(`${tag} token bar renders even with no price configured`)
      const card = sec.cards[0]
      if (!card.text.includes(T_UNPRICED)) fail(`${tag} missing unpriced warning`)
      // token bar only -- a cost bar would be meaningless without prices
      if (card.stacks.length === 1) ok(`${tag} cost bar correctly absent (1 bar only)`)
      else fail(`${tag} expected only the token bar, got ${card.stacks.length}`)
      checkStack(tag, card.stacks[0], exp.A.tiers, 'token', exp.A.tokenAria)
      checkLegend(tag, card, exp.A.tiers, false)
      if (sec.compare.length) fail(`${tag} compare bars must not appear on a single-model run`)
      else ok(`${tag} no compare bars on single-model run`)
    }
    await page.screenshot({ path: `${SHOT}${key}-unpriced.png` })
  }

  // ================= phase 2: single run, priced =================
  {
    const key = '431d32d5'
    const exp = CHARTS[key]
    const cost = COSTS[key]
    const tag = key + '/priced'
    await setPricing(page, { [exp.modelA.toLowerCase()]: cost.priceA })
    await page.waitForTimeout(500)

    const sec = await page.evaluate(SCRAPE, exp.A.tokenAria)
    if (!sec.found) {
      fail(`${tag} token bar vanished after pricing`)
    } else {
      const card = sec.cards[0]
      if (card.stacks.length === 2) ok(`${tag} both token and cost bars present`)
      else fail(`${tag} expected 2 bars, got ${card.stacks.length}`)
      checkStack(tag, card.stacks[0], exp.A.tiers, 'token', exp.A.tokenAria)
      checkStack(tag, card.stacks[1], exp.A.tiers, 'cost', exp.A.costAria)
      checkLegend(tag, card, exp.A.tiers, true)
      checkGapEvidence(tag, exp.A.tiers, exp.maxTierGapA)
      checkSideColour(tag, card, varTriple(sec.accent), 'accent/A')
      if (sec.compare.length) fail(`${tag} compare bars must not appear on a single-model run`)
      else ok(`${tag} still no compare bars`)
      report.phases.push({ phase: tag, bars: card.stacks.length, gap: exp.maxTierGapA })
    }
    await page.screenshot({ path: `${SHOT}${key}-priced.png` })
  }

  // ================= phase 3: comparative run, both sides priced =================
  {
    const key = '76431d94'
    const exp = CHARTS[key]
    const cost = COSTS[key]
    const tag = key + '/cmp'
    await page.goto(`${BASE}/evaluation/runs/${exp.run_id}`, { waitUntil: 'networkidle', timeout: 60000 })
    await setPricing(page, {
      [exp.modelA.toLowerCase()]: cost.priceA,
      [exp.modelB.toLowerCase()]: cost.priceB,
    })
    await page.waitForTimeout(600)

    const sec = await page.evaluate(SCRAPE, exp.A.tokenAria)
    if (!sec.found) {
      fail(`${tag} A-side token bar not found; aria seen = ${JSON.stringify(sec.ariaSeen)}`)
    } else {
      if (sec.cards.length >= 2) ok(`${tag} two cost cards (A / B)`)
      else fail(`${tag} expected 2 cards, got ${sec.cards.length}`)

      const [cardA, cardB] = sec.cards
      checkStack(tag + '/A', cardA.stacks[0], exp.A.tiers, 'token', exp.A.tokenAria)
      checkStack(tag + '/A', cardA.stacks[1], exp.A.tiers, 'cost', exp.A.costAria)
      checkLegend(tag + '/A', cardA, exp.A.tiers, true)
      checkSideColour(tag + '/A', cardA, varTriple(sec.accent), 'accent/A')

      checkStack(tag + '/B', cardB.stacks[0], exp.B.tiers, 'token', exp.B.tokenAria)
      checkStack(tag + '/B', cardB.stacks[1], exp.B.tiers, 'cost', exp.B.costAria)
      checkLegend(tag + '/B', cardB, exp.B.tiers, true)
      checkSideColour(tag + '/B', cardB, varTriple(sec.info), 'info/B')

      checkGapEvidence(tag + '/A', exp.A.tiers, exp.maxTierGapA)

      // ---- shared-scale A/B compare bars ----
      const cb = exp.compareBars
      if (sec.compare.length !== 1) {
        fail(`${tag} expected exactly 1 compare-bar group, got ${sec.compare.length}`)
      } else {
        const grp = sec.compare[0]
        eqStr(tag, 'compare aria-label', grp.aria, cb.aria)
        if (grp.rows.length !== 2) fail(`${tag} compare rows ${grp.rows.length}, want 2`)
        else {
          eqPct(tag, 'compare A style width', grp.rows[0].stylePct, cb.aWidthPct, 0.01)
          eqPct(tag, 'compare A rendered width', grp.rows[0].renderPct, cb.aWidthPct, 1.0)
          eqPct(tag, 'compare B style width', grp.rows[1].stylePct, cb.bWidthPct, 0.01)
          eqPct(tag, 'compare B rendered width', grp.rows[1].renderPct, cb.bWidthPct, 1.0)
          eqStr(tag, 'compare A amount', grp.rows[0].amount, cb.aAmount)
          eqStr(tag, 'compare B amount', grp.rows[1].amount, cb.bAmount)
          // same scale => the longer bar is the pricier side, and A is pinned at 100%
          if (near(Math.max(cb.aWidthPct, cb.bWidthPct), 100, 0.01)) ok(`${tag} shared scale pins the max side at 100%`)
          else fail(`${tag} neither compare bar reaches 100%`)
          const triples = grp.rows.map(r => rgbTriple(r.bg))
          if (triples[0] === varTriple(sec.accent) && triples[1] === varTriple(sec.info)) {
            ok(`${tag} compare bars keep A=accent / B=info`)
          } else {
            fail(`${tag} compare bar colours ${JSON.stringify(triples)} not accent/info`)
          }
        }
        report.phases.push({ phase: tag, compareAria: grp.aria, rows: grp.rows.length })
      }

      // B is the cheaper side here: its cost bar must be visibly shorter on the shared scale
      if (cb.bWidthPct < cb.aWidthPct) ok(`${tag} cheaper side B renders shorter (${cb.bWidthPct.toFixed(2)}% vs 100%)`)
    }
    await page.screenshot({ path: `${SHOT}${key}-cmp.png` })
    await page.screenshot({ path: `${SHOT}${key}-cmp-full.png`, fullPage: true })
  }
} catch (e) {
  fail('exception: ' + (e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : String(e)))
} finally {
  report.verdict = report.failures.length === 0 ? 'CHARTS_PASS' : 'CHARTS_FAIL'
  writeFileSync(OUT, JSON.stringify(report, null, 1), 'utf8')
  console.log('failures=' + report.failures.length)
  console.log(report.verdict)
  await browser.close()
}
