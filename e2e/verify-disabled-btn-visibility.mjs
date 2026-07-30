// Measure the disabled "agent gen" button's computed colors + contrast on the
// real page, so "is it visible" is a number, not an opinion.
// ASCII-only source on purpose (CJK phantom-read problem on this box).
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = 'http://localhost'
const DATASET = process.argv[2] || 'probe-ds-1785299523343'
const AUTH = new URL('./auth.json', import.meta.url)

const GEN = 'agent生成答案' // agent-gen-answer

function parseRgb(s) {
  const m = s.match(/rgba?\(([^)]+)\)/)
  if (!m) return null
  const p = m[1].split(',').map(x => parseFloat(x.trim()))
  return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }
}
function over(fg, bg) { // alpha-composite fg over opaque bg
  return {
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  }
}
function lum(c) {
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
  return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b)
}
function contrast(a, b) {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}
const fmt = c => `rgb(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)})`

const results = []
const ok = (n, c, extra = '') => results.push([c ? 'PASS' : 'FAIL', n, extra])

const browser = await chromium.launch({ headless: false })
const ctx = await browser.newContext({
  storageState: JSON.parse(fs.readFileSync(AUTH, 'utf8')),
  viewport: { width: 1600, height: 1000 },
})
const page = await ctx.newPage()
await page.goto(`${BASE}/datasets/${DATASET}`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

const btn = page.getByRole('button', { name: new RegExp(GEN) })
ok('gen button present', (await btn.count()) === 1)
ok('gen button is disabled (no selection)', await btn.isDisabled())

const m = await btn.evaluate(el => {
  const cs = getComputedStyle(el)
  // walk up for the nearest opaque background = what the button sits on
  let p = el.parentElement, pageBg = 'rgb(255, 255, 255)'
  while (p) {
    const b = getComputedStyle(p).backgroundColor
    const mm = b.match(/rgba?\(([^)]+)\)/)
    if (mm) { const q = mm[1].split(',').map(Number); if ((q[3] ?? 1) === 1) { pageBg = b; break } }
    p = p.parentElement
  }
  const r = el.getBoundingClientRect()
  return {
    color: cs.color,
    bg: cs.backgroundColor,
    borderColor: cs.borderColor,
    borderWidth: cs.borderTopWidth,
    pageBg,
    box: { w: Math.round(r.width), h: Math.round(r.height) },
    visible: r.width > 0 && r.height > 0,
  }
})

const pageBg = parseRgb(m.pageBg)
const btnBg = over(parseRgb(m.bg), pageBg)
const text = over(parseRgb(m.color), btnBg)
const bdr = over(parseRgb(m.borderColor), pageBg)

const cText = contrast(text, btnBg)
const cFillVsPage = contrast(btnBg, pageBg)
const cBorderVsPage = contrast(bdr, pageBg)

console.log(`RAW color=${m.color} bg=${m.bg} border=${m.borderColor} ${m.borderWidth} pageBg=${m.pageBg}`)
console.log(`COMPOSITED pageBg=${fmt(pageBg)} btnBg=${fmt(btnBg)} text=${fmt(text)} border=${fmt(bdr)}`)
console.log(`CONTRAST text/btnBg=${cText.toFixed(2)} btnBg/pageBg=${cFillVsPage.toFixed(2)} border/pageBg=${cBorderVsPage.toFixed(2)}`)
console.log(`BOX ${JSON.stringify(m.box)}`)

ok('button has non-zero box', m.visible, JSON.stringify(m.box))
// Disabled label should stay readable: WCAG AA for small text is 4.5, but a
// disabled control is exempt; still require clearly-above-noise 3.0.
ok('disabled label contrast >= 3.0', cText >= 3.0, cText.toFixed(2))
// The button must have a *shape*: either its fill or its border separates it
// from the page background by a perceptible amount.
ok('shape visible vs page (fill or border >= 1.10)', Math.max(cFillVsPage, cBorderVsPage) >= 1.10,
  `fill=${cFillVsPage.toFixed(2)} border=${cBorderVsPage.toFixed(2)}`)

await page.screenshot({ path: 'e2e/disabled-btn-light.png' })

// dark theme too: the same tokens must not collapse there
await page.evaluate(() => document.documentElement.classList.add('dark'))
await page.waitForTimeout(400)
const m2 = await btn.evaluate(el => {
  const cs = getComputedStyle(el)
  let p = el.parentElement, pageBg = 'rgb(0, 0, 0)'
  while (p) {
    const b = getComputedStyle(p).backgroundColor
    const mm = b.match(/rgba?\(([^)]+)\)/)
    if (mm) { const q = mm[1].split(',').map(Number); if ((q[3] ?? 1) === 1) { pageBg = b; break } }
    p = p.parentElement
  }
  return { color: cs.color, bg: cs.backgroundColor, borderColor: cs.borderColor, pageBg }
})
const pb2 = parseRgb(m2.pageBg)
const bb2 = over(parseRgb(m2.bg), pb2)
const tx2 = over(parseRgb(m2.color), bb2)
const bd2 = over(parseRgb(m2.borderColor), pb2)
const cText2 = contrast(tx2, bb2)
const cShape2 = Math.max(contrast(bb2, pb2), contrast(bd2, pb2))
console.log(`DARK text/btnBg=${cText2.toFixed(2)} shape=${cShape2.toFixed(2)} (btnBg=${fmt(bb2)} pageBg=${fmt(pb2)})`)
ok('dark: disabled label contrast >= 3.0', cText2 >= 3.0, cText2.toFixed(2))
ok('dark: shape visible >= 1.10', cShape2 >= 1.10, cShape2.toFixed(2))
await page.screenshot({ path: 'e2e/disabled-btn-dark.png' })

console.log(results.map(r => r.join('  ')).join('\n'))
const failed = results.filter(r => r[0] === 'FAIL').length
console.log(`VERIFY_DISABLED_BTN=${failed === 0 ? 'PASSED' : `FAILED(${failed})`}`)
await browser.close()
process.exit(failed === 0 ? 0 : 1)
