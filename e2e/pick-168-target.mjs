// Pick the best UI-acceptance target for #168 from live data.
// We need a run where the NEW filter (abnormal-status OR blank) differs from the
// OLD filter (abnormal-status only), AND where exclude-mode leaves a non-empty set,
// so all three filter modes render distinguishable row counts.
// ASCII-only on purpose.
import fs from 'node:fs'
const BASE = 'http://localhost'
const auth = JSON.parse(fs.readFileSync('D:\\program\\agent_eval\\e2e\\auth.json','utf8'))
const ls = auth.origins[0].localStorage.find(x=>x.name==='agent-eval-auth')
const TOK = JSON.parse(ls.value).state.accessToken
const H = { Authorization: 'Bearer ' + TOK }

const ABN = new Set(['error','agent_unreachable','agent_timeout'])
const PEND = new Set(['pending','running','stopping'])
const isBlankStr = v => typeof v !== 'string' || v.trim() === ''
const num = v => typeof v === 'number' && isFinite(v) ? v : 0

function blankReply(r){
  if (PEND.has(r.status)) return false
  const cmp = r.comparison
  const aT = r.full_trace && r.full_trace.conversation && r.full_trace.conversation.turns
  const bT = cmp && cmp.agent_b && cmp.agent_b.conversation && cmp.agent_b.conversation.turns
  const aM = Array.isArray(aT) && aT.length>0
  const bM = Array.isArray(bT) && bT.length>0
  if (aM || bM){
    const c = cmp && cmp.answer_counts
    if (c && (num(c.a_blank)>0 || num(c.b_blank)>0)) return true
    if (aM && aT.some(t=>isBlankStr(t && t.assistant))) return true
    if (bM && bT.some(t=>isBlankStr(t && t.assistant))) return true
    return false
  }
  if (isBlankStr(r.actual_output)) return true
  if (cmp && isBlankStr(cmp.agent_b && cmp.agent_b.output)) return true
  return false
}

const lr = await fetch(BASE+'/api/eval/runs?page=1&page_size=100',{headers:H})
const lbody = await lr.text()
console.log('RUNS_LIST_STATUS=' + lr.status + ' body_head=' + lbody.slice(0,200))
let runs = []
try { runs = (JSON.parse(lbody)).items || [] } catch { runs = [] }
console.log('RUNS_PARSED=' + runs.length)
// page 2 (108 runs total, page_size max appears to be 100)
const lr2 = await fetch(BASE+'/api/eval/runs?page=2&page_size=100',{headers:H})
if (lr2.ok) { try { runs = runs.concat((await lr2.json()).items || []) } catch {} }
console.log('RUNS_TOTAL_SCANNED=' + runs.length)
let resultsFail = 0
const out = []
for (const run of runs){
  const rr = await fetch(BASE+`/api/eval/runs/${run.id}/results?page=1&page_size=100`,{headers:H})
  if (!rr.ok) { resultsFail++; continue }
  const rows = (await rr.json()).items || []
  if (!rows.length) continue
  let oldAbn=0, newAbn=0, gap=0, clean=0
  for (const r of rows){
    const o = ABN.has(r.status)
    const b = blankReply(r)
    const n = o || b
    if (o) oldAbn++
    if (n) newAbn++
    if (!o && b) gap++
    if (!n) clean++
  }
  if (gap>0){
    out.push({ run_id: run.id, name: run.name, eval_mode: run.eval_mode,
               total: rows.length, old_abnormal: oldAbn, new_abnormal: newAbn,
               gap, clean_rows: clean })
  }
}
// Rank: prefer clean_rows>0 (exclude mode non-empty), then small total, then bigger gap.
out.sort((a,b)=> (b.clean_rows>0)-(a.clean_rows>0) || a.total-b.total || b.gap-a.gap )
fs.writeFileSync('D:\\program\\agent_eval\\e2e\\result-168-targets.json', JSON.stringify(out,null,1))
console.log('CANDIDATES='+out.length)
console.log(JSON.stringify(out.slice(0,10),null,1))
