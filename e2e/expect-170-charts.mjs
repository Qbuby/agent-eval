// Expected geometry/labels for the #170 cost charts, derived INDEPENDENTLY of the
// frontend: reads only token counts + unit prices from expect_cost_170.json and
// redoes the arithmetic here. Emits expect_charts_170.json for the UI verifier.
//
// Why a separate file: if the verifier computed its own expectations from the same
// helpers the page uses, a wrong formula would agree with itself and pass.
//
// Pure ASCII on purpose (repo hazard: tools return phantom content for files
// containing literal CJK).

import { readFileSync, writeFileSync } from 'node:fs'

const IN = 'C:\\Users\\frh\\expect_cost_170.json'
const OUT = 'D:\\program\\agent_eval\\e2e\\expect_charts_170.json'

const PRICE_UNIT = 1_000_000

// Mirrors lib/pricing.ts formatCost: smaller amounts keep more decimals.
const fmtCost = (v, currency = '$') => {
  const abs = Math.abs(v)
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6
  return `${currency}${v.toFixed(digits)}`
}

// Mirrors CostPanel sharePct.
const pct = (v, total) => (total > 0 ? `${((v / total) * 100).toFixed(0)}%` : '\u2014')

// Tier order is fixed by the design: cheapest (cache hit) first, output last.
// Labels are \u-escaped CJK: hit-input / miss-input / output.
const TIER_LABELS = [
  '\u547d\u4e2d\u8f93\u5165',
  '\u672a\u547d\u4e2d\u8f93\u5165',
  '\u8f93\u51fa',
]

function sideExpect(side, agg, price) {
  if (!agg || !price) return null

  const tokens = [agg.hitInputTokens, agg.missInputTokens, agg.outputTokens]
  const unit = [price.inputHit, price.inputMiss, price.output]
  const costs = tokens.map((tok, i) => (tok / PRICE_UNIT) * unit[i])

  const tokTotal = tokens.reduce((s, v) => s + v, 0)
  const costTotal = costs.reduce((s, v) => s + v, 0)

  // Cross-check our arithmetic against the total the cost verifier already trusts.
  const drift = Math.abs(costTotal - agg.total)
  if (drift > 1e-9) {
    throw new Error(`${side} cost total drift ${drift}: got ${costTotal}, expect_cost has ${agg.total}`)
  }

  const tiers = TIER_LABELS.map((label, i) => ({
    label,
    tokens: tokens[i],
    cost: costs[i],
    // width percentages the segments should carry (style="width: N%")
    tokenWidthPct: (tokens[i] / tokTotal) * 100,
    costWidthPct: (costs[i] / costTotal) * 100,
    // strings the legend renders
    tokenSharePct: pct(tokens[i], tokTotal),
    costSharePct: pct(costs[i], costTotal),
    costStr: fmtCost(costs[i]),
    // segment tooltips
    tokenTitle: `${label}\uff1a${tokens[i].toLocaleString('en-US')} tok\uff08${pct(tokens[i], tokTotal)}\uff09`,
    costTitle: `${label}\uff1a${fmtCost(costs[i])}\uff08${pct(costs[i], costTotal)}\uff09`,
  }))

  return {
    side,
    tokTotal,
    costTotal,
    costTotalStr: fmtCost(costTotal),
    segments: tiers.length,
    tiers,
    // aria-labels: "token composition: <label> <pct>, ..." / "cost composition: ..."
    tokenAria:
      '\u0074\u006f\u006b\u0065\u006e \u6784\u6210\uff1a' +
      tiers.map(t => `${t.label} ${t.tokenSharePct}`).join('\uff0c'),
    costAria:
      '\u6210\u672c\u6784\u6210\uff1a' +
      tiers.map(t => `${t.label} ${t.costSharePct}`).join('\uff0c'),
  }
}

const src = JSON.parse(readFileSync(IN, 'utf8'))
const out = {}

for (const [key, run] of Object.entries(src)) {
  const a = sideExpect('a', run.A, run.priceA)
  const b = sideExpect('b', run.B, run.priceB)

  const rec = {
    run_id: run.run_id,
    comparative: run.comparative,
    modelA: run.modelA,
    modelB: run.modelB,
    A: a,
    B: b,
    compareBars: null,
  }

  // A/B same-scale bars: both widths scale off the larger total.
  if (a && b) {
    const max = Math.max(a.costTotal, b.costTotal)
    rec.compareBars = {
      max,
      aWidthPct: (a.costTotal / max) * 100,
      bWidthPct: (b.costTotal / max) * 100,
      aAmount: a.costTotalStr,
      bAmount: b.costTotalStr,
      // "total cost compare: A side <amt>, B side <amt>"
      aria:
        '\u603b\u6210\u672c\u5bf9\u6bd4\uff1a\u0041 \u4fa7 ' + a.costTotalStr +
        '\uff0c\u0042 \u4fa7 ' + b.costTotalStr,
    }
  }

  // The whole point of stacking token-share against cost-share: the same tier
  // occupies visibly different widths. Record the biggest gap so the verifier can
  // assert the two bars are genuinely not identical.
  if (a) {
    const gaps = a.tiers.map(t => Math.abs(t.tokenWidthPct - t.costWidthPct))
    rec.maxTierGapA = Math.max(...gaps)
  }

  out[key] = rec
}

writeFileSync(OUT, JSON.stringify(out, null, 1), 'utf8')

for (const [key, r] of Object.entries(out)) {
  console.log(`${key} ${r.comparative ? 'comparative' : 'single'} run=${r.run_id}`)
  for (const side of ['A', 'B']) {
    const s = r[side]
    if (!s) continue
    console.log(`  ${side} tokTotal=${s.tokTotal} costTotal=${s.costTotalStr} segs=${s.segments}`)
    s.tiers.forEach((t, i) => {
      console.log(
        `    tier${i} tok=${t.tokens} ${t.tokenSharePct} w=${t.tokenWidthPct.toFixed(2)}%` +
        ` | cost=${t.costStr} ${t.costSharePct} w=${t.costWidthPct.toFixed(2)}%`,
      )
    })
  }
  if (r.maxTierGapA != null) console.log(`  maxTierGapA=${r.maxTierGapA.toFixed(2)}pp`)
  if (r.compareBars) {
    console.log(`  compare A=${r.compareBars.aWidthPct.toFixed(2)}% B=${r.compareBars.bWidthPct.toFixed(2)}%`)
  }
}
console.log('WROTE ' + OUT)
