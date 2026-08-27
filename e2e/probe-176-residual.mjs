// #176 残留形状排查：UI 验收里出现两类非正文预览
//   a) 预览为 "—"（后端返回 null）
//   b) 预览是 {"goto":[],"graph":null,"resume":"..."} 这种 LangGraph resume 壳
// 目标：拿到这些 trace 的 input 原始结构，判断该不该继续提取。
import fs from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const AUTH = 'D:\\program\\agent_eval\\e2e\\auth.json'

const state = JSON.parse(fs.readFileSync(AUTH, 'utf8'))
let token = null
for (const o of state.origins || []) {
  for (const kv of o.localStorage || []) {
    try {
      const p = JSON.parse(kv.value)
      const t = p?.state?.accessToken || p?.accessToken || p?.access_token
      if (typeof t === 'string') token = t
    } catch {}
  }
}
const headers = { Authorization: `Bearer ${token}` }

async function get(path) {
  const r = await fetch(`${BASE}${path}`, { headers })
  const text = await r.text()
  let body = null
  try {
    body = JSON.parse(text)
  } catch {}
  return { status: r.status, body }
}

const out = { nullPreview: [], resumeShape: [], otherShell: [] }

const list = await get(
  '/api/langfuse-metrics/traces?from=2020-01-01T00:00:00Z&to=2030-01-01T00:00:00Z&page=1&page_size=200',
)
const items = list.body?.traces || []
out.total = items.length

// a) preview 为 null 的
const nulls = items.filter((t) => !t.input_preview).slice(0, 3)
for (const t of nulls) {
  const d = await get(`/api/langfuse-metrics/traces/${t.langfuse_trace_id}`)
  out.nullPreview.push({
    id: t.langfuse_trace_id.slice(0, 12),
    inputType: d.body?.input === null ? 'null' : typeof d.body?.input,
    inputKeys:
      d.body?.input && typeof d.body.input === 'object'
        ? Object.keys(d.body.input)
        : null,
    inputRaw: JSON.stringify(d.body?.input ?? null).slice(0, 300),
  })
}

// b) 仍以 { 或 [ 开头的（宽口径：任何 JSON 起始都算没提取成功）
const shells = items.filter(
  (t) => typeof t.input_preview === 'string' && /^\s*[[{]/.test(t.input_preview),
)
out.shellTotal = shells.length
for (const t of shells.slice(0, 5)) {
  const d = await get(`/api/langfuse-metrics/traces/${t.langfuse_trace_id}`)
  const input = d.body?.input
  out.resumeShape.push({
    id: t.langfuse_trace_id.slice(0, 12),
    preview: t.input_preview.slice(0, 100),
    inputKeys: input && typeof input === 'object' ? Object.keys(input) : null,
    inputRaw: JSON.stringify(input ?? null).slice(0, 500),
  })
}

fs.writeFileSync(
  'D:\\program\\agent_eval\\e2e\\result-176-residual.json',
  JSON.stringify(out, null, 2),
  'utf8',
)
console.log(JSON.stringify(out, null, 2))
