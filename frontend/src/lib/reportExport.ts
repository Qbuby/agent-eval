// 评估分析报告导出（纯前端，无后端依赖）。
//
// 从页面已加载的 run / results 数据生成一份自包含的 HTML 文档（内联 CSS +
// 内联 SVG 图表，无外部资源），通过 downloadBlob 下载。单次评估结果与多次
// 对比结果各有一个入口，产出可直接用浏览器打开、可离线分享的分析报告。
//
// 设计要点：
// - 全部内联，无 CDN / 无字体外链，双击即可离线查看。
// - 图表用手写 SVG（雷达）/ div 宽度条（柱状），不引第三方库。
// - 所有动态文本走 esc() 转义，杜绝注入。

import { downloadBlob } from './download'
import { collapseDimAvg, collapseScoreKey } from './dimensionCollapse'
import {
  deriveFacts, deriveAcceptance, deriveCostScored,
  acceptancePassRateText, runDecisionLabel,
  type EvalFacts, type EvalAcceptance,
} from './evalSemantics'

// ─────────────────────────────────────────────────────────────────────
// 数据形态（与页面聚合结果对齐；只取报告需要的字段，容忍缺失）
// ─────────────────────────────────────────────────────────────────────

export interface ReportRun {
  id: string
  status: string
  started_at?: string | null
  finished_at?: string | null
  langfuse_run_name?: string | null
  langsmith_project?: string | null
  summary_scores?: {
    facts?: Partial<EvalFacts>
    acceptance?: Partial<EvalAcceptance>
    cost_scored?: Record<string, number | null>
    cost_execution_abnormal?: Record<string, number | null>
    counts?: { total?: number; passed?: number; failed?: number; unreachable?: number }
    dimension_averages?: Record<string, number>
    tool_usage?: Array<{ name: string; calls: number; errors: number; cases: number }>
    cost_success?: Record<string, number | null>
    cost_failure?: Record<string, number | null>
  } | null
}

export interface ReportResultRow {
  id: string
  question?: string | null
  status: string
  latency_ms?: number | null
  total_tokens?: number | null
  tool_call_count?: number | null
  error_message?: string | null
  scores?: Record<string, number>
}

// 维度 key → 展示标签（调用方可传入 scoreSemantics 的 label；缺省用 key 本身）。
export type DimLabel = (key: string) => string

// ─────────────────────────────────────────────────────────────────────
// 通用小工具
// ─────────────────────────────────────────────────────────────────────

function esc(v: unknown): string {
  const s = v == null ? '' : String(v)
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function fmtPct(n: number): string {
  if (!Number.isFinite(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return Number.isInteger(n) ? String(n) : n.toFixed(digits)
}

function fmtTime(s: string | null | undefined): string {
  if (!s) return '—'
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? esc(s) : d.toLocaleString('zh-CN')
}

// ─────────────────────────────────────────────────────────────────────
// SVG 雷达图（维度均分，domain 0..1）。少于 3 维返回空串（雷达无意义）。
// ─────────────────────────────────────────────────────────────────────

function radarSvg(dims: Array<{ label: string; value: number }>, size = 300): string {
  if (dims.length < 3) return ''
  const cx = size / 2
  const cy = size / 2
  const r = size * 0.34
  const n = dims.length
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2
  const pt = (i: number, radius: number) => {
    const a = angle(i)
    return [cx + radius * Math.cos(a), cy + radius * Math.sin(a)]
  }

  // 同心网格（4 圈）
  let grid = ''
  for (let ring = 1; ring <= 4; ring++) {
    const rr = (r * ring) / 4
    const pts = dims.map((_, i) => pt(i, rr).map(x => x.toFixed(1)).join(',')).join(' ')
    grid += `<polygon points="${pts}" fill="none" stroke="#e2e8f0" stroke-width="1"/>`
  }
  // 轴线 + 标签
  let axes = ''
  dims.forEach((d, i) => {
    const [x, y] = pt(i, r)
    axes += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#e2e8f0" stroke-width="1"/>`
    const [lx, ly] = pt(i, r + 22)
    const anchor = Math.abs(lx - cx) < 1 ? 'middle' : lx > cx ? 'start' : 'end'
    axes += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" font-size="11" fill="#475569" text-anchor="${anchor}" dominant-baseline="middle">${esc(d.label)}</text>`
  })
  // 数据多边形
  const dataPts = dims
    .map((d, i) => {
      const v = Math.max(0, Math.min(1, Number.isFinite(d.value) ? d.value : 0))
      return pt(i, r * v).map(x => x.toFixed(1)).join(',')
    })
    .join(' ')
  const poly = `<polygon points="${dataPts}" fill="rgba(79,70,229,0.22)" stroke="#4f46e5" stroke-width="2"/>`
  // 数据点
  let dots = ''
  dims.forEach((d, i) => {
    const v = Math.max(0, Math.min(1, Number.isFinite(d.value) ? d.value : 0))
    const [x, y] = pt(i, r * v)
    dots += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="#4f46e5"/>`
  })
  return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" xmlns="http://www.w3.org/2000/svg">${grid}${axes}${poly}${dots}</svg>`
}

// 水平柱：value/max → 宽度百分比
function hbar(label: string, value: number, max: number, sub: string, color = '#4f46e5'): string {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0
  return `
    <div class="hbar-row">
      <div class="hbar-label" title="${esc(label)}">${esc(label)}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:${pct.toFixed(1)}%;background:${color}"></div></div>
      <div class="hbar-val">${esc(sub)}</div>
    </div>`
}

// ─────────────────────────────────────────────────────────────────────
// HTML 外壳（内联样式）
// ─────────────────────────────────────────────────────────────────────

function htmlShell(title: string, body: string): string {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>${esc(title)}</title>
<style>
  :root { --ink:#1e293b; --sub:#64748b; --line:#e2e8f0; --accent:#4f46e5; --pos:#16a34a; --neg:#dc2626; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; color: var(--ink);
         margin: 0; padding: 32px; background: #f8fafc; line-height: 1.5; }
  .wrap { max-width: 980px; margin: 0 auto; }
  header.rpt { border-bottom: 2px solid var(--accent); padding-bottom: 16px; margin-bottom: 24px; }
  header.rpt h1 { margin: 0 0 6px; font-size: 22px; }
  header.rpt .meta { color: var(--sub); font-size: 13px; }
  section.card { background: #fff; border: 1px solid var(--line); border-radius: 10px;
                 padding: 20px; margin-bottom: 20px; }
  section.card > h2 { margin: 0 0 16px; font-size: 15px; letter-spacing: .3px; color: var(--accent);
                      text-transform: uppercase; }
  .kpis { display: flex; gap: 24px; align-items: center; flex-wrap: wrap; }
  .kpi-big { font-size: 34px; font-weight: 700; letter-spacing: -1px; }
  .kpi-grid { display: flex; gap: 20px; }
  .kpi { text-align: center; }
  .kpi .v { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .kpi .v.pos { color: var(--pos); } .kpi .v.neg { color: var(--neg); }
  .kpi .l { font-size: 11px; color: var(--sub); }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); }
  th { color: var(--sub); font-weight: 600; background: #f1f5f9; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .badge.pass { background: #dcfce7; color: #166534; }
  .badge.fail { background: #fee2e2; color: #991b1b; }
  .badge.other { background: #e2e8f0; color: #475569; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; }
  .hbar-row { display: grid; grid-template-columns: 130px 1fr 90px; gap: 10px; align-items: center; margin-bottom: 7px; font-size: 12px; }
  .hbar-label { color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .hbar-track { background: #f1f5f9; border-radius: 4px; height: 16px; overflow: hidden; }
  .hbar-fill { height: 100%; border-radius: 4px; }
  .hbar-val { text-align: right; font-variant-numeric: tabular-nums; }
  .radar-wrap { display: flex; justify-content: center; }
  .muted { color: var(--sub); font-size: 12px; }
  footer.rpt { color: var(--sub); font-size: 11px; text-align: center; margin-top: 24px; }
  .truncate { max-width: 460px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  section.card.analysis { border-left: 4px solid var(--accent); background: #fbfbff; }
  .md h3, .md h4, .md h5 { margin: 14px 0 6px; font-size: 13px; color: var(--ink); }
  .md p { margin: 6px 0; font-size: 13px; }
  .md ul { margin: 6px 0; padding-left: 22px; }
  .md li { font-size: 13px; margin: 3px 0; }
  .md strong { color: var(--accent); }
  @media print { body { background: #fff; padding: 0; } section.card { break-inside: avoid; } }
</style>
</head>
<body><div class="wrap">${body}
<footer class="rpt">由 Agent-Eval 生成 · ${esc(new Date().toLocaleString('zh-CN'))}</footer>
</div></body>
</html>`
}

function statusBadge(status: string): string {
  const s = (status || '').toLowerCase()
  const cls = s === 'passed' || s === 'pass' ? 'pass'
    : s === 'failed' || s === 'fail' ? 'fail' : 'other'
  return `<span class="badge ${cls}">${esc(status || '—')}</span>`
}

// 极简 markdown → HTML：支持 ### 标题 / **加粗** / - 列表 / 段落。
// LLM 输出视作不可信：先整体 esc()，再仅补白名单标签，杜绝注入。
function mdToHtml(md: string): string {
  const lines = esc(md).split(/\r?\n/)
  const out: string[] = []
  let inList = false
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false } }
  const inline = (t: string) =>
    t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  for (const raw of lines) {
    const line = raw.trim()
    if (!line) { closeList(); continue }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) { closeList(); const lvl = Math.min(h[1].length + 2, 6); out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`); continue }
    const li = line.match(/^[-*]\s+(.*)$/)
    if (li) { if (!inList) { out.push('<ul>'); inList = true } out.push(`<li>${inline(li[1])}</li>`); continue }
    closeList()
    out.push(`<p>${inline(line)}</p>`)
  }
  closeList()
  return out.join('\n')
}

// AI 分析解读区块（analysis 为 markdown；空则不渲染）。
function analysisSection(analysis?: string): string {
  const md = (analysis ?? '').trim()
  if (!md) return ''
  return `
  <section class="card analysis">
    <h2>AI 分析解读</h2>
    <div class="md">${mdToHtml(md)}</div>
  </section>`
}

// ─────────────────────────────────────────────────────────────────────
// 单次评估结果报告
// ─────────────────────────────────────────────────────────────────────

export function buildRunReportHtml(
  run: ReportRun,
  items: ReportResultRow[],
  dimLabel: DimLabel = k => k,
  analysis?: string,
): string {
  const ss = run.summary_scores ?? {}
  const facts = deriveFacts(ss)
  const acceptance = deriveAcceptance(ss)
  // 折叠多轮维度（correctness.turn0..N → correctness），避免雷达糊成一团、表格几十行。
  const dimAvg = collapseDimAvg(ss.dimension_averages ?? {})
  const tools = ss.tool_usage ?? []
  const costSuccess = deriveCostScored(ss)

  const runName = run.langfuse_run_name || run.id.slice(0, 8)
  const title = `评估报告 · ${runName}`

  // 头部
  const header = `
  <header class="rpt">
    <h1>评估分析报告</h1>
    <div class="meta">
      运行 <strong>${esc(runName)}</strong> · ID <code>${esc(run.id)}</code><br/>
      状态 ${statusBadge(run.status)} · 开始 ${fmtTime(run.started_at)} · 结束 ${fmtTime(run.finished_at)}
      ${run.langsmith_project ? ` · 项目 ${esc(run.langsmith_project)}` : ''}
    </div>
  </header>`

  // 概览 KPI —— 三层语义：验收通过率仅在配置了显式验收策略时展示，
  // 否则标「仅评分」，绝不用分数编造合格率。
  const prText = acceptancePassRateText(acceptance)
  const headline = prText != null
    ? `<div class="kpi-big">${esc(prText)}</div><div class="muted">验收通过率 · ${esc(runDecisionLabel(acceptance.run_decision))}</div>`
    : `<div class="kpi-big" style="font-size:20px">仅评分</div><div class="muted">未配置验收规则</div>`
  const overview = `
  <section class="card">
    <h2>概览</h2>
    <div class="kpis">
      ${headline}
      <div class="kpi-grid" style="margin-left:auto">
        <div class="kpi"><div class="v">${facts.total}</div><div class="l">总样例</div></div>
        <div class="kpi"><div class="v pos">${facts.evaluation_completed}</div><div class="l">评分完成</div></div>
        <div class="kpi"><div class="v">${facts.skipped}</div><div class="l">跳过</div></div>
        <div class="kpi"><div class="v neg">${facts.execution_abnormal}</div><div class="l">执行异常</div></div>
      </div>
    </div>
  </section>`

  // 维度均分（雷达 + 表）
  const dimEntries = Object.entries(dimAvg)
  const radarDims = dimEntries.map(([k, v]) => ({ label: dimLabel(k), value: v }))
  const radar = radarSvg(radarDims)
  const dimRows = dimEntries
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td>${esc(dimLabel(k))}</td><td class="num">${fmtNum(v, 3)}</td></tr>`)
    .join('')
  const dimSection = dimEntries.length ? `
  <section class="card">
    <h2>维度得分</h2>
    <div class="grid2">
      ${radar ? `<div class="radar-wrap">${radar}</div>` : ''}
      <div>
        <table>
          <thead><tr><th>评估维度</th><th class="num">平均分</th></tr></thead>
          <tbody>${dimRows}</tbody>
        </table>
      </div>
    </div>
  </section>` : ''

  // 工具调用统计
  const maxCalls = tools.reduce((m, t) => Math.max(m, t.calls), 0)
  const toolBars = tools
    .slice()
    .sort((a, b) => b.calls - a.calls)
    .slice(0, 12)
    .map(t => hbar(t.name, t.calls, maxCalls, `${t.calls} 次 / ${t.errors} 失败`,
      t.errors > 0 ? '#dc2626' : '#4f46e5'))
    .join('')
  const toolSection = tools.length ? `
  <section class="card">
    <h2>工具调用统计</h2>
    ${toolBars}
    <div class="muted" style="margin-top:8px">
      共 ${tools.reduce((s, t) => s + t.calls, 0)} 次调用，
      ${tools.reduce((s, t) => s + t.errors, 0)} 次失败，涉及 ${tools.length} 种工具
    </div>
  </section>` : ''

  // 成本指标
  const costRows = Object.entries(costSuccess)
    .filter(([, v]) => v != null)
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${fmtNum(v as number)}</td></tr>`)
    .join('')
  const costSection = costRows ? `
  <section class="card">
    <h2>成本 / 性能指标（评分样例）</h2>
    <table><thead><tr><th>指标</th><th class="num">数值</th></tr></thead><tbody>${costRows}</tbody></table>
  </section>` : ''

  // 样例明细（取折叠后维度全集做列；单元格取该 base 各轮均值）
  const allDims = Array.from(new Set(
    items.flatMap(r => Object.keys(r.scores ?? {}).map(collapseScoreKey)),
  )).slice(0, 6)
  const collapsedScoresOf = (r: ReportResultRow): Record<string, number> => {
    const acc: Record<string, { sum: number; n: number }> = {}
    for (const [k, v] of Object.entries(r.scores ?? {})) {
      if (typeof v !== 'number') continue
      const base = collapseScoreKey(k)
      if (!acc[base]) acc[base] = { sum: 0, n: 0 }
      acc[base].sum += v
      acc[base].n += 1
    }
    const out: Record<string, number> = {}
    for (const [b, { sum, n }] of Object.entries(acc)) out[b] = n ? sum / n : NaN
    return out
  }
  const dimHead = allDims.map(d => `<th class="num">${esc(dimLabel(d))}</th>`).join('')
  const rowsHtml = items.map((r, i) => {
    const cs = collapsedScoresOf(r)
    const dimCells = allDims
      .map(d => `<td class="num">${fmtNum(cs[d], 2)}</td>`)
      .join('')
    return `<tr>
      <td class="num">${i + 1}</td>
      <td class="truncate" title="${esc(r.question ?? '')}">${esc(r.question ?? '—')}</td>
      <td>${statusBadge(r.status)}</td>
      <td class="num">${r.latency_ms != null ? Math.round(r.latency_ms) + 'ms' : '—'}</td>
      <td class="num">${r.total_tokens ?? '—'}</td>
      ${dimCells}
    </tr>`
  }).join('')
  const detailSection = items.length ? `
  <section class="card">
    <h2>样例明细（${items.length} 条）</h2>
    <table>
      <thead><tr><th class="num">#</th><th>问题</th><th>状态</th><th class="num">时延</th><th class="num">Tokens</th>${dimHead}</tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
  </section>` : ''

  return htmlShell(title, header + overview + analysisSection(analysis) + dimSection + toolSection + costSection + detailSection)
}

// ─────────────────────────────────────────────────────────────────────
// 多次对比报告
// ─────────────────────────────────────────────────────────────────────

export interface CompareRunStats {
  facts: import('./evalSemantics').EvalFacts
  acceptance: import('./evalSemantics').EvalAcceptance
  dimensionAverages: Record<string, number>
  costSuccess: Record<string, number>
}

export function buildCompareReportHtml(
  runs: ReportRun[],
  statsByRun: Record<string, CompareRunStats>,
  dimLabel: DimLabel = k => k,
  analysis?: string,
): string {
  const title = `评估对比报告 · ${runs.length} 个运行`
  const runName = (r: ReportRun) => r.langfuse_run_name || r.id.slice(0, 8)

  const header = `
  <header class="rpt">
    <h1>评估对比分析报告</h1>
    <div class="meta">${runs.length} 个运行 · 执行/评分事实 · 验收通过率 · 维度得分 · 成本对比</div>
  </header>`

  // 运行清单
  const runList = runs.map((r, i) => `<tr>
    <td class="num">${i + 1}</td>
    <td>${esc(runName(r))}</td>
    <td><code>${esc(r.id.slice(0, 8))}</code></td>
    <td>${statusBadge(r.status)}</td>
    <td class="num">${fmtTime(r.finished_at)}</td>
  </tr>`).join('')
  const runsSection = `
  <section class="card">
    <h2>参与对比的运行</h2>
    <table>
      <thead><tr><th class="num">#</th><th>名称</th><th>ID</th><th>状态</th><th class="num">结束时间</th></tr></thead>
      <tbody>${runList}</tbody>
    </table>
  </section>`

  // 验收通过率对比（横向柱）—— 仅对配置了显式验收策略的 run 画条；
  // 未配置验收的 run 明确标「仅评分」，绝不当成 0%。
  const prBars = runs.map((r, i) => {
    const s = statsByRun[r.id]
    const acc = s?.acceptance
    const color = i % 2 === 0 ? '#4f46e5' : '#16a34a'
    if (!acc || !acc.configured) {
      return hbar(runName(r), 0, 1, '仅评分（未配置验收）', '#94a3b8')
    }
    const pr = acc.pass_rate
    const passed = acc.passed ?? 0
    const decided = acc.decided ?? 0
    return hbar(runName(r), pr != null ? pr : 0, 1,
      `${pr != null ? fmtPct(pr) : '无数据'} (${passed}/${decided})`, color)
  }).join('')
  const anyAcceptance = runs.some(r => statsByRun[r.id]?.acceptance?.configured)
  const prSection = `
  <section class="card">
    <h2>验收通过率对比</h2>
    ${anyAcceptance ? prBars : '<div class="muted">所有运行均未配置验收规则，仅评分，无通过率可比。</div>'}
  </section>`

  // 维度得分对比表（维度为行，run 为列）。折叠逐轮维度到评估器级（幂等，
  // 兼容 statsByRun 传入的原始逐轮 key）。
  const foldedDimByRun: Record<string, Record<string, number>> = {}
  for (const r of runs) {
    foldedDimByRun[r.id] = collapseDimAvg(statsByRun[r.id]?.dimensionAverages ?? {})
  }
  const allDims = Array.from(new Set(
    runs.flatMap(r => Object.keys(foldedDimByRun[r.id] ?? {})),
  ))
  const dimColHead = runs.map(r => `<th class="num">${esc(runName(r))}</th>`).join('')
  const dimRows = allDims.map(d => {
    const cells = runs.map(r => {
      const v = foldedDimByRun[r.id]?.[d]
      return `<td class="num">${fmtNum(v, 3)}</td>`
    }).join('')
    return `<tr><td>${esc(dimLabel(d))}</td>${cells}</tr>`
  }).join('')
  const dimSection = allDims.length ? `
  <section class="card">
    <h2>维度得分对比</h2>
    <table>
      <thead><tr><th>评估维度</th>${dimColHead}</tr></thead>
      <tbody>${dimRows}</tbody>
    </table>
  </section>` : ''

  // 成本对比表
  const allCost = Array.from(new Set(
    runs.flatMap(r => Object.keys(statsByRun[r.id]?.costSuccess ?? {})),
  ))
  const costColHead = runs.map(r => `<th class="num">${esc(runName(r))}</th>`).join('')
  const costRows = allCost.map(k => {
    const cells = runs.map(r => {
      const v = statsByRun[r.id]?.costSuccess?.[k]
      return `<td class="num">${fmtNum(v, 2)}</td>`
    }).join('')
    return `<tr><td>${esc(k)}</td>${cells}</tr>`
  }).join('')
  const costSection = allCost.length ? `
  <section class="card">
    <h2>成本 / 性能对比（评分样例）</h2>
    <table>
      <thead><tr><th>指标</th>${costColHead}</tr></thead>
      <tbody>${costRows}</tbody>
    </table>
  </section>` : ''

  return htmlShell(title, header + analysisSection(analysis) + runsSection + prSection + dimSection + costSection)
}

// ─────────────────────────────────────────────────────────────────────
// 下载入口
// ─────────────────────────────────────────────────────────────────────

function safeName(s: string): string {
  return (s || 'report').replace(/[^\w.-]+/g, '_').slice(0, 60)
}

export function downloadReportHtml(filename: string, html: string): void {
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  downloadBlob(blob, filename.endsWith('.html') ? filename : `${filename}.html`)
}

export function exportRunReport(run: ReportRun, items: ReportResultRow[], dimLabel?: DimLabel, analysis?: string): void {
  const html = buildRunReportHtml(run, items, dimLabel, analysis)
  const name = safeName(run.langfuse_run_name || run.id.slice(0, 8))
  downloadReportHtml(`eval-report-${name}`, html)
}

export function exportCompareReport(
  runs: ReportRun[],
  statsByRun: Record<string, CompareRunStats>,
  dimLabel?: DimLabel,
  analysis?: string,
): void {
  const html = buildCompareReportHtml(runs, statsByRun, dimLabel, analysis)
  downloadReportHtml(`eval-compare-${runs.length}runs`, html)
}
