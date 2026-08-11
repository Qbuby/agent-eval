// Verify the SERVED bundle actually contains the new CostPanel chart code.
//
// Repo hazard: docker build/tag/up "success" output can be phantom, and a failed
// tsc silently keeps the old bundle. So never trust the build log -- fetch the
// bundle Windows localhost actually serves and look for the new needles by byte.
//
// Pure ASCII on purpose: every CJK needle is a \u escape, because tools return
// phantom content / false negatives for files containing literal CJK.

const BASE = process.env.BASE_URL || 'http://localhost'

const NEEDLES = {
  // CostShareBars: the two stacked bars' labels
  'label:token-composition': 'token 构成',              // token 构成
  'label:cost-composition': '成本构成',         // 成本构成
  // three billing tiers (legend + segment titles)
  'tier:hit-input': '命中输入',                 // 命中输入
  'tier:miss-input': '未命中输入',          // 未命中输入
  // CostCompareBars aria-label prefix
  'aria:total-cost-compare': '总成本对比',  // 总成本对比
  // legend money-share prefix
  'legend:money-share': '钱 ',                              // "钱 "
  // section + a token row that must coexist
  'section:actual-cost': '实际成本',            // 实际成本
  'row:priced-samples': '计价样例数',       // 计价样例数
}

const html = await (await fetch(BASE + '/', { redirect: 'follow' })).text()
const m = html.match(/src="(\/assets\/index-[^"]+\.js)"/)
if (!m) {
  console.log('FAIL: no bundle src in index.html')
  console.log(html.slice(0, 400))
  process.exit(1)
}
const bundleUrl = BASE + m[1]
const buf = Buffer.from(await (await fetch(bundleUrl)).arrayBuffer())

console.log('bundle=' + m[1])
console.log('bytes=' + buf.length)

let missing = 0
for (const [tag, needle] of Object.entries(NEEDLES)) {
  const at = buf.indexOf(Buffer.from(needle, 'utf8'))
  if (at < 0) {
    missing++
    console.log(`FAIL ${tag}: needle not in served bundle`)
  } else {
    console.log(`ok   ${tag} @${at}`)
  }
}

// role="img" count is a cheap structural signal that the bars ship as graphics.
const roleImg = (buf.toString('utf8').match(/role:"img"/g) || []).length
console.log('role-img-occurrences=' + roleImg)

console.log(missing === 0 ? 'VERDICT=BUNDLE_HAS_NEW_CHARTS' : `VERDICT=STALE_OR_MISSING (${missing})`)
process.exit(missing === 0 ? 0 : 1)
