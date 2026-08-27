// Verify the bundle actually served by http://localhost contains the #168
// blank-reply markers. All CJK needles are \u-escaped on purpose: this file
// stays pure ASCII so its bytes are trustworthy regardless of tooling.
const BASE = process.env.BASE_URL || 'http://localhost'

const NEEDLES = {
  // "空回复" = blank reply, the shared copy/tag text
  blankReply: '空回复',
  // "不可达" = unreachable, part of the filter caption
  unreachable: '不可达',
  // "超时" = timeout
  timeout: '超时',
  // "仅异常" = only-abnormal, the tri-state filter label
  onlyAbnormal: '仅异常',
  // "排除异常" = exclude-abnormal
  excludeAbnormal: '排除异常',
  // status literals the judgment relies on
  agentUnreachable: 'agent_unreachable',
  agentTimeout: 'agent_timeout',
  answerCounts: 'answer_counts',
  aBlank: 'a_blank',
  bBlank: 'b_blank',
}

async function main() {
  const out = { base: BASE }

  const indexRes = await fetch(BASE + '/', { headers: { 'cache-control': 'no-cache' } })
  const html = await indexRes.text()
  out.index_status = indexRes.status

  const bundles = [...html.matchAll(/src="(\/assets\/[^"]+\.js)"/g)].map(m => m[1])
  out.bundles = bundles
  if (!bundles.length) {
    out.verdict = 'FAIL: no bundle found in index.html'
    console.log(JSON.stringify(out, null, 2))
    process.exit(1)
  }

  let joined = ''
  out.bundle_bytes = {}
  for (const b of bundles) {
    const res = await fetch(BASE + b, { headers: { 'cache-control': 'no-cache' } })
    const buf = Buffer.from(await res.arrayBuffer())
    out.bundle_bytes[b] = buf.length
    joined += buf.toString('utf8')
  }

  out.found = {}
  for (const [k, v] of Object.entries(NEEDLES)) {
    out.found[k] = joined.indexOf(v) !== -1
  }
  const missing = Object.entries(out.found).filter(([, ok]) => !ok).map(([k]) => k)
  out.missing = missing
  out.verdict = missing.length === 0 ? 'BUNDLE_168_OK' : 'BUNDLE_168_FAIL'

  console.log(JSON.stringify(out, null, 2))
  process.exit(missing.length === 0 ? 0 : 1)
}

main().catch(e => {
  console.log(JSON.stringify({ verdict: 'BUNDLE_168_FAIL', error: String(e) }, null, 2))
  process.exit(1)
})
