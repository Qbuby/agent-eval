// #143：证明「按模型批量切换」是逐样例各自解析，而不是全批切到同一个 vN。
//
// 上一版把期望值写死在常量里，结果跑的时候后台正在并发生成新版本（v4），
// 期望值一过期就报假 BAD。这一版不写死任何 vN：先拉每条样例的真实版本链，
// 现场推导「按某模型时这条样例应该落到哪个 vN」，再拿它对接口结果。
//
// 有牙齿的断言在这三条：
//   1) 每条命中版本的 model 必须等于所选模型
//   2) 每条落到的 vN 必须等于「它自己链里带该模型的最大 vN」
//   3) 同一次意图解析出的 vN 集合必须不止一种 —— 若实现是全批切同一 vN，这里必然塌成 1 种
//
// 另外跑完把指针原样还回去，并核对跑动期间链是否被并发改动（漂移则显式报出，不算失败）。
import fs from 'node:fs'

const BASE = 'http://localhost'
const DT = 'benchmark'
const REFS = [
  '74bbdebd-221f-4472-a7df-8357112d714f',
  '09e98588-d838-4836-98e3-8eed2a939b9e',
  'adf5406d-de74-4b4b-ab8b-f69c3e4c8830',
  '03c5f665-d21d-48ca-9915-c9142e132236',
]

const raw = fs.readFileSync('D:/program/agent_eval/e2e/auth.json', 'utf8')
const jwt = raw.match(/eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+/)
if (!jwt) throw new Error('auth.json 里没找到 JWT')
const H = { Authorization: `Bearer ${jwt[0]}`, 'Content-Type': 'application/json' }

let bad = 0
const say = (s) => console.log(s)
function ok(cond, msg) {
  if (!cond) bad++
  say(`  ${cond ? 'ok  ' : 'BAD '}${msg}`)
}

async function jget(p) {
  const r = await fetch(BASE + p, { headers: H })
  const t = await r.text()
  let j = null
  try { j = JSON.parse(t) } catch {}
  return { status: r.status, body: j, text: t }
}
async function jpost(p, body) {
  const r = await fetch(BASE + p, { method: 'POST', headers: H, body: JSON.stringify(body) })
  const t = await r.text()
  let j = null
  try { j = JSON.parse(t) } catch {}
  return { status: r.status, body: j, text: t }
}
async function states() {
  const r = await jget(`/api/agent-replies/states?dataset_type=${DT}&case_refs=${REFS.join(',')}`)
  if (r.status !== 200) throw new Error(`states -> ${r.status} ${r.text}`)
  const m = {}
  for (const s of r.body) m[s.case_ref] = s.current_version_number
  return m
}
async function chains() {
  const m = {}
  for (const ref of REFS) {
    const r = await jget(`/api/agent-replies/versions?dataset_type=${DT}&case_ref=${ref}`)
    if (r.status !== 200) throw new Error(`versions(${ref.slice(0, 8)}) -> ${r.status} ${r.text}`)
    const arr = Array.isArray(r.body) ? r.body : (r.body?.items || r.body?.versions || [])
    m[ref] = arr.map(v => ({ vn: v.version_number, model: v.model, is_current: v.is_current }))
  }
  return m
}
// 这条样例链里带该模型的最大 vN；没有则 null
const expectFor = (versions, model) => {
  const hit = versions.filter(v => v.model === model).map(v => v.vn)
  return hit.length ? Math.max(...hit) : null
}
const resolve = (model) =>
  jpost('/api/agent-replies/batch-resolve',
        { dataset_type: DT, case_refs: REFS, selector: { mode: 'model', version_number: null, label: null, model } })
const setCurrent = (model) =>
  jpost('/api/agent-replies/batch-set-current',
        { dataset_type: DT, case_refs: REFS, selector: { mode: 'model', version_number: null, label: null, model } })

// ── 0) 现场快照：版本链 + 当前指针 ──────────────────────────
const chain0 = await chains()
const before = await states()

say('=== 现场快照（版本链 / 当前指针）===')
for (const ref of REFS) {
  const vs = chain0[ref].slice().sort((a, b) => b.vn - a.vn)
  say(`  ${ref.slice(0, 8)}  cur=v${before[ref] ?? '-'}  ${vs.map(v => `v${v.vn}=${v.model}`).join('  ')}`)
}

// states 的指针应与版本行上的 is_current 一致
for (const ref of REFS) {
  const flagged = chain0[ref].filter(v => v.is_current).map(v => v.vn)
  ok(flagged.length === 1 && flagged[0] === before[ref],
     `${ref.slice(0, 8)} states 指针 v${before[ref] ?? '-'} 与 is_current 标记 [${flagged.join(',') || '无'}] 一致`)
}

const MODELS = [...new Set(Object.values(chain0).flat().map(v => v.model).filter(Boolean))].sort()
say(`\n这批链里出现过的模型：${MODELS.join(' | ')}`)
if (MODELS.length < 2) {
  say('BAD 只有一种模型，无法证伪「全批切同一 vN」；需要至少两种模型的数据')
  bad++
}

let restored = false
try {
  // ── 1) 干跑：对每种模型都验一遍，期望值全部现场推导 ────────
  for (const model of MODELS) {
    say(`\n=== 干跑：按模型 = ${model} ===`)
    const exp = {}
    for (const ref of REFS) exp[ref] = expectFor(chain0[ref], model)
    const wantMatched = REFS.filter(r => exp[r] !== null).length

    const r = await resolve(model)
    ok(r.status === 200, `batch-resolve 返回 200（实际 ${r.status}）`)
    const items = r.body?.items || []
    const got = {}
    for (const i of items) {
      got[i.case_ref] = i.matched ? i.version_number : null
      say(`    ${i.case_ref.slice(0, 8)}  matched=${i.matched}  -> v${i.version_number ?? '-'}` +
          `  model=${i.model ?? '(无)'}  label=${i.version_label ?? '(无)'}  reason=${i.reason || '-'}`)
    }

    ok(items.length === REFS.length, `返回条数 ${items.length}，期望 ${REFS.length}`)
    ok(r.body?.matched_count === wantMatched,
       `命中 ${r.body?.matched_count} 条，按真实链推导应命中 ${wantMatched} 条`)
    ok((r.body?.missing_count ?? 0) === REFS.length - wantMatched,
       `无匹配 ${r.body?.missing_count ?? 0} 条，应为 ${REFS.length - wantMatched} 条`)

    for (const ref of REFS) {
      ok(got[ref] === exp[ref],
         `${ref.slice(0, 8)} 解析到 ${got[ref] === null ? '无匹配' : 'v' + got[ref]}` +
         `，按自己的链应为 ${exp[ref] === null ? '无匹配' : 'v' + exp[ref]}`)
    }
    // 命中版本的 model 必须就是所选模型
    ok(items.filter(i => i.matched).every(i => i.model === model),
       `每条命中版本的 model 都等于 ${model}`)
    // 没命中的要说明原因
    const miss = items.filter(i => !i.matched)
    if (miss.length) {
      ok(miss.every(i => (i.reason || '').includes(model)),
         `${miss.length} 条无匹配都给出了带模型名的原因`)
    }
    // 核心：解析出的 vN 不能塌成一种
    const vnSet = [...new Set(Object.values(got).filter(v => v !== null))]
    ok(vnSet.length > 1,
       `同一个「按模型」意图解析出多种版本号（实际 ${vnSet.map(v => 'v' + v).join(' / ')}）` +
       ` —— 若是全批切同一 vN 这里只会有 1 种`)
  }

  // ── 2) 实切：挑一个能产生多种 vN 的模型落库 ─────────────────
  const pick = MODELS.find(m => {
    const s = new Set(REFS.map(r => expectFor(chain0[r], m)).filter(v => v !== null))
    return s.size > 1
  }) || MODELS[0]
  say(`\n=== 执行：按模型 = ${pick} 设为当前版本 ===`)
  const exp = {}
  for (const ref of REFS) exp[ref] = expectFor(chain0[ref], pick)

  const r3 = await setCurrent(pick)
  ok(r3.status === 200, `batch-set-current 返回 200（实际 ${r3.status}）`)
  say(`    total=${r3.body?.total} changed=${r3.body?.changed_count}` +
      ` unchanged=${r3.body?.unchanged_count} missing=${r3.body?.missing_count}` +
      ` failed=${r3.body?.failed_count}`)
  ok((r3.body?.failed_count ?? 0) === 0, '没有切换失败的样例')

  // ── 3) 落库核验：走 states，不用弹窗自己的预览接口 ──────────
  say('\n=== 落库后的当前版本指针 ===')
  const after = await states()
  for (const ref of REFS) say(`    ${ref.slice(0, 8)}  cur=v${after[ref] ?? '-'}`)
  for (const ref of REFS) {
    if (exp[ref] === null) {
      ok(after[ref] === before[ref],
         `${ref.slice(0, 8)} 无匹配，指针保持原样 v${before[ref] ?? '-'}`)
    } else {
      ok(after[ref] === exp[ref],
         `${ref.slice(0, 8)} 当前版本落到 v${after[ref] ?? '-'}，期望 v${exp[ref]}`)
    }
  }
  const afterSet = [...new Set(REFS.map(r => after[r]))]
  ok(afterSet.length > 1,
     `落库后各样例当前版本号不全相同（实际 ${afterSet.map(v => 'v' + v).join(' / ')}）`)
} finally {
  // ── 4) 还原 ────────────────────────────────────────────────
  say('\n=== 还原改动前的指针 ===')
  const fails = []
  for (const ref of REFS) {
    const vn = before[ref]
    if (vn == null) { say(`  ${ref.slice(0, 8)} 原本没有指针，跳过`); continue }
    const rr = await jpost('/api/agent-replies/batch-set-current',
      { dataset_type: DT, case_refs: [ref], selector: { mode: 'version_number', version_number: vn, label: null, model: null } })
    say(`  ${rr.status === 200 ? 'ok ' : 'BAD'} ${ref.slice(0, 8)} 还原到 v${vn}（${rr.status}）` +
        (rr.status === 200 ? '' : `  ${rr.text.slice(0, 200)}`))
    if (rr.status !== 200) fails.push(ref)
  }
  const back = await states()
  restored = REFS.every(r => back[r] === before[r]) && !fails.length
  say(`  还原核验：${restored ? 'PASS' : 'FAIL'} ` +
      REFS.map(r => `${r.slice(0, 8)}=v${back[r] ?? '-'}`).join(' '))

  // 跑动期间是否有人并发改链（只报告，不判失败）
  const chain1 = await chains()
  const drift = REFS.filter(r => chain1[r].length !== chain0[r].length)
  if (drift.length) {
    say(`  注意：跑动期间版本链发生变化（${drift.map(r => r.slice(0, 8)).join(', ')}）` +
        `，本次结论基于开跑时的快照`)
  }

  say(`\nINDEP=${bad === 0 ? 'PASS' : `FAIL(${bad})`} RESTORE=${restored ? 'PASS' : 'FAIL'}`)
  process.exit(bad === 0 && restored ? 0 : 1)
}
