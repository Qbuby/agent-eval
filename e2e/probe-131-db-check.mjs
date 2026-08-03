// #131 DB 侧核验：UI 上「按版本号切到 v1」执行后，这 4 条样例的当前版本指针
// 是否真的落到 v1。走 /agent-replies/states（读的是 agent_reply_case_states
// .current_version_id），不是弹窗自己的预览接口，避免自证。
import fs from 'node:fs'

const BASE = 'http://localhost'
const REFS = [
  '74bbdebd-221f-4472-a7df-8357112d714f',
  '09e98588-d838-4836-98e3-8eed2a939b9e',
  'adf5406d-de74-4b4b-ab8b-f69c3e4c8830',
  '03c5f665-d21d-48ca-9915-c9142e132236',
]

const raw = fs.readFileSync('D:/program/agent_eval/e2e/auth.json', 'utf8')
const m = raw.match(/eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+/)
if (!m) throw new Error('auth.json 里没找到 JWT')
const H = { Authorization: `Bearer ${m[0]}`, 'Content-Type': 'application/json' }

const r = await fetch(
  `${BASE}/api/agent-replies/states?dataset_type=benchmark&case_refs=${REFS.join(',')}`,
  { headers: H },
)
console.log('states ->', r.status)
const states = await r.json()

let bad = 0
for (const s of states) {
  const okRow = s.current_version_number === 1
  if (!okRow) bad++
  console.log(
    `  ${okRow ? 'ok  ' : 'BAD '}${s.case_ref}  cur=v${s.current_version_number ?? '-'}` +
      `  label=${s.current_version_label ?? '(无)'}  n=${s.version_count}`,
  )
}
console.log(bad === 0 ? '\nDBCHECK=PASS 4 条当前版本均为 v1' : `\nDBCHECK=FAIL ${bad} 条未落到 v1`)
