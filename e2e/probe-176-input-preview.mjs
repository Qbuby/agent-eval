// #176 API 探针：Tracing 指标列表的 input_preview 应是用户问题正文，
// 而不是 {"messages":[{"role":"user","content":"… 这种 JSON 壳。
//
// 判定：
//   1. 拿到的 preview 里，以 JSON 壳开头的条数 == 0
//   2. 抽样条目的 preview 与 detail 接口里 input.messages 末条 user content 一致
import fs from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const AUTH = 'D:\\program\\agent_eval\\e2e\\auth.json'

const state = JSON.parse(fs.readFileSync(AUTH, 'utf8'))
// storageState 里 key=agent-eval-auth，值是 {state:{accessToken,...}} 的 JSON
let token = null
for (const o of state.origins || []) {
  for (const kv of o.localStorage || []) {
    try {
      const parsed = JSON.parse(kv.value)
      const t = parsed?.state?.accessToken || parsed?.accessToken || parsed?.access_token
      if (typeof t === 'string' && t.split('.').length === 3) token = t
    } catch {
      if (typeof kv.value === 'string' && kv.value.split('.').length === 3) token = kv.value
    }
  }
}

const report = { tokenFound: !!token, checks: [], samples: [], pass: false }
const fail = (name, detail) => report.checks.push({ name, ok: false, detail })
const ok = (name, detail) => report.checks.push({ name, ok: true, detail })

const headers = token ? { Authorization: `Bearer ${token}` } : {}

async function get(path) {
  const res = await fetch(`${BASE}${path}`, { headers })
  const text = await res.text()
  let body = null
  try {
    body = JSON.parse(text)
  } catch {}
  return { status: res.status, body, text: text.slice(0, 400) }
}

try {
  // 时间窗放宽到覆盖全量，page_size 上限 200（>200 会 422）
  // 参数名是 from / to（后端 alias），不是 from_ts / to_ts
  const from = '2020-01-01T00:00:00Z'
  const to = '2030-01-01T00:00:00Z'
  const list = await get(
    `/api/langfuse-metrics/traces?from=${from}&to=${to}&page=1&page_size=100`,
  )
  if (list.status !== 200) {
    fail('列表接口 200', `status=${list.status} body=${list.text}`)
    throw new Error('列表接口失败')
  }

  // 顶层可能是信封 {items:[...]} 或裸数组，两种都兜住
  const items = Array.isArray(list.body)
    ? list.body
    : list.body?.items || list.body?.data || list.body?.traces || []
  report.total = items.length
  if (!items.length) {
    fail('列表有数据', `items 为空，body keys=${Object.keys(list.body || {}).join(',')}`)
    throw new Error('列表为空')
  }
  ok('列表有数据', `${items.length} 条`)

  // check 1: 没有 JSON 壳开头的 preview
  const shellRe = /^\s*[[{]\s*"?(messages|role|content|tool_mode|output_schema)/
  const shells = items
    .filter((t) => typeof t.input_preview === 'string' && shellRe.test(t.input_preview))
    .map((t) => ({ id: t.langfuse_trace_id, preview: t.input_preview.slice(0, 80) }))
  report.shellCount = shells.length
  report.shellSamples = shells.slice(0, 5)
  if (shells.length) fail('preview 无 JSON 壳', `${shells.length}/${items.length} 条仍是壳`)
  else ok('preview 无 JSON 壳', `${items.length} 条全部是正文`)

  // check 2: 抽样与 detail 的 messages 末条 user content 对齐
  const picks = items.filter((t) => t.input_preview).slice(0, 5)
  let mismatch = 0
  for (const t of picks) {
    const d = await get(`/api/langfuse-metrics/traces/${t.langfuse_trace_id}`)
    const input = d.body?.input
    let expected = null
    const msgs = input?.messages
    if (Array.isArray(msgs)) {
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i]?.role === 'user') {
          const c = msgs[i].content
          expected = typeof c === 'string' ? c.trim() : null
          break
        }
      }
    }
    const preview = t.input_preview
    // preview 可能被截断（结尾 …），比对前缀
    const truncated = preview.endsWith('…')
    const base = truncated ? preview.slice(0, -1) : preview
    const aligned =
      expected == null ? null : truncated ? expected.startsWith(base) : expected === base
    if (aligned === false) mismatch++
    report.samples.push({
      id: t.langfuse_trace_id.slice(0, 12),
      preview: preview.slice(0, 60),
      expected: expected == null ? null : expected.slice(0, 60),
      aligned,
      truncated,
    })
  }
  if (mismatch) fail('preview 与 detail 正文一致', `${mismatch}/${picks.length} 条不符`)
  else ok('preview 与 detail 正文一致', `抽样 ${picks.length} 条`)

  report.pass = report.checks.every((c) => c.ok)
} catch (e) {
  if (!report.checks.some((c) => !c.ok)) fail('脚本执行', String(e?.message || e))
} finally {
  fs.writeFileSync(
    'D:\\program\\agent_eval\\e2e\\result-176-input-preview-api.json',
    JSON.stringify(report, null, 2),
    'utf8',
  )
  console.log(JSON.stringify(report, null, 2))
  console.log(report.pass ? 'VERDICT=PASS' : 'VERDICT=FAIL')
}
