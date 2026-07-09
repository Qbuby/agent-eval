import { useEffect, useMemo, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { evaluationApi } from '@/services'
import { Drawer, ExportMenu } from '@/components/ui'
import { directionMark, getScoreMeta, isPassing, tone } from '@/lib/scoreSemantics'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { EvalResultRow, EvalRunDetail, EvalResultsPage } from '@/types'

// Token-driven bar palette — pulls from CSS vars so light/dark theme stays in sync.
const BAR_COLORS = [
  'rgb(var(--accent))',
  'rgb(var(--positive))',
  'rgb(var(--warning))',
  'rgb(var(--info))',
  'rgb(var(--negative))',
  'rgb(var(--accent-hover))',
  'rgb(var(--positive) / 0.7)',
  'rgb(var(--info) / 0.7)',
]
const RESULTS_PAGE_SIZE = 200

const COST_METRIC_DEFS: Array<{
  key: string
  label: string
  direction: 'higher_better' | 'lower_better'
  fmt?: (v: number) => string
}> = [
  { key: 'avg_latency_ms', label: '平均时延', direction: 'lower_better', fmt: v => `${Math.round(v)}ms` },
  { key: 'avg_first_answer_token_ms', label: '首答 token', direction: 'lower_better', fmt: v => `${Math.round(v)}ms` },
  { key: 'avg_total_tokens', label: '平均总 token', direction: 'lower_better', fmt: v => `${Math.round(v)}` },
  { key: 'avg_prompt_tokens', label: '平均输入 token', direction: 'lower_better', fmt: v => `${Math.round(v)}` },
  { key: 'avg_completion_tokens', label: '平均输出 token', direction: 'lower_better', fmt: v => `${Math.round(v)}` },
  { key: 'avg_tool_calls', label: '平均工具调用', direction: 'lower_better', fmt: v => v.toFixed(2) },
  { key: 'cache_hit_rate', label: '缓存命中率', direction: 'higher_better', fmt: v => `${(v * 100).toFixed(1)}%` },
]

type AlignKey = 'case_id' | 'question'

interface RunStats {
  total: number
  passed: number
  dimensionAverages: Record<string, number>
  costSuccess: Record<string, number>
}

export default function EvaluationComparePage() {
  const [searchParams] = useSearchParams()
  const ids = (searchParams.get('ids') || '').split(',').map(s => s.trim()).filter(Boolean)

  const queries = useQueries({
    queries: ids.map(id => ({
      queryKey: ['eval-run', id],
      queryFn: () => evaluationApi.getRun(id).then(r => r.data),
      enabled: !!id,
    })),
  })

  const resultsQueries = useQueries({
    queries: ids.map(id => ({
      queryKey: ['eval-results-compare', id],
      queryFn: () => evaluationApi
        .getResults(id, { page: 1, page_size: RESULTS_PAGE_SIZE })
        .then(r => r.data),
      enabled: !!id,
    })),
  })

  const runs = useMemo<EvalRunDetail[]>(() =>
    queries.map(q => q.data).filter((x): x is EvalRunDetail => !!x),
  [queries])

  const resultsByRun = useMemo<Record<string, EvalResultsPage | undefined>>(() => {
    const map: Record<string, EvalResultsPage | undefined> = {}
    ids.forEach((id, i) => { map[id] = resultsQueries[i].data })
    return map
  }, [ids, resultsQueries])

  const loading = queries.some(q => q.isLoading) || resultsQueries.some(q => q.isLoading)
  const anyError = queries.find(q => q.isError) ?? resultsQueries.find(q => q.isError)

  // ─── A/B selection (only meaningful when ≥2 runs loaded) ─────────────────
  const [aRunId, setARunId] = useState<string>('')
  const [bRunId, setBRunId] = useState<string>('')

  // ─── Sample alignment ─────────────────────────────────────────────────────
  const [alignKey, setAlignKey] = useState<AlignKey>('case_id')
  const [dimView, setDimView] = useState<'agg' | 'trend'>('agg')
  const [selectedAlignKey, setSelectedAlignKey] = useState<string | null>(null)
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set())
  const [exportError, setExportError] = useState<string | null>(null)

  // 切换对齐键后，旧的 selected key 失效（case_id ≠ 问题文本哈希）
  useEffect(() => {
    setSelectedKeys(new Set())
  }, [alignKey])

  const aligned = useMemo(
    () => buildAlignedSamples(runs, resultsByRun, alignKey),
    [runs, resultsByRun, alignKey],
  )

  const usingSubset = selectedKeys.size > 0

  // 子集模式：从勾选的 aligned rows 重算每个 run 的统计
  // 默认：直接用后端 summary_scores
  const statsByRun = useMemo<Record<string, RunStats>>(() => {
    const out: Record<string, RunStats> = {}
    if (usingSubset) {
      for (const run of runs) {
        const rows: EvalResultRow[] = []
        for (const a of aligned) {
          if (!selectedKeys.has(a.key)) continue
          const row = a.byRun[run.id]
          if (row) rows.push(row)
        }
        out[run.id] = computeStatsFromRows(rows)
      }
    } else {
      for (const run of runs) {
        const total = run.summary_scores?.counts?.total ?? 0
        const passed = run.summary_scores?.counts?.passed ?? 0
        const dimensionAverages = run.summary_scores?.dimension_averages ?? {}
        const rawCost = (run.summary_scores?.cost_success ?? {}) as Record<string, number | null>
        const costSuccess: Record<string, number> = {}
        for (const [k, v] of Object.entries(rawCost)) {
          if (typeof v === 'number') costSuccess[k] = v
        }
        out[run.id] = { total, passed, dimensionAverages, costSuccess }
      }
    }
    return out
  }, [runs, aligned, selectedKeys, usingSubset])

  const dimAggChart = useMemo(() => buildDimensionAggChart(runs, statsByRun), [runs, statsByRun])
  const dimTrendChart = useMemo(() => buildTurnTrendChart(runs, statsByRun), [runs, statsByRun])
  const hasMultiTurnDims = dimTrendChart.series.length > 0
  const costChart = useMemo(() => buildCostChart(runs, statsByRun), [runs, statsByRun])
  const passRateChart = useMemo(() => buildPassRateChart(runs, statsByRun), [runs, statsByRun])

  if (ids.length === 0) {
    return (
      <div className="text-[12px] text-text-tertiary">
        请先在评估历史页勾选至少两个运行，再点「对比所选」。
        <Link to="/evaluation" className="ml-2 underline text-accent">返回历史</Link>
      </div>
    )
  }

  const aRun = runs.find(r => r.id === (aRunId || runs[0]?.id))
  const bRun = runs.find(r => r.id === (bRunId || runs[1]?.id))
  const selectedAligned = selectedAlignKey
    ? aligned.find(a => a.key === selectedAlignKey) ?? null
    : null

  return (
    <div>
      <Link to="/evaluation" className="back-link mb-2">
        ← 评估列表
      </Link>
      <header className="mb-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="page-eyebrow">评估</div>
            <h1 className="page-title">运行对比</h1>
            <p className="page-subtitle">{ids.length} 个运行 · 维度分 · 成本 · 通过率 · 样例对齐</p>
          </div>
          {ids.length > 0 && (
            <ExportMenu
              label="导出对比"
              onExport={async (format) => {
                try {
                  await evaluationApi.exportCompare(ids, format, alignKey)
                  setExportError(null)
                } catch (e) {
                  setExportError(toToastMessage(formatApiError(e)))
                }
              }}
            />
          )}
        </div>
        {exportError && <p className="text-[12px] text-negative mt-2">{exportError}</p>}
      </header>

      {loading && <div className="text-[12px] text-text-tertiary py-10 text-center">加载中…</div>}
      {anyError && !loading && (
        <div className="text-[12px] text-negative py-4">
          部分运行加载失败：{(anyError.error as Error)?.message || '未知错误'}
        </div>
      )}

      {!loading && runs.length > 0 && (
        <>
          <section className="card p-4 mb-5 overflow-x-auto">
            <h3 className="page-eyebrow mb-3">概览</h3>
            <table className="table-base">
              <thead>
                <tr>
                  <th>运行</th>
                  <th>智能体模型</th>
                  <th className="text-right">样例数</th>
                  <th className="text-right">通过率</th>
                  <th className="text-right">成功·时延</th>
                  <th className="text-right">成功·首答</th>
                  <th className="text-right">成功·token</th>
                  <th className="text-right">成功·工具调用</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r, i) => {
                  const total = r.summary_scores?.counts?.total ?? 0
                  const passed = r.summary_scores?.counts?.passed ?? 0
                  const cs = r.summary_scores?.cost_success ?? {}
                  const model = (r.agent_config as { model?: string; type?: string }).model
                    || (r.agent_config as { type?: string }).type
                    || '—'
                  return (
                    <tr key={r.id}>
                      <td>
                        <Link to={`/evaluation/runs/${r.id}`} className="text-text-primary hover:text-accent transition-colors no-underline">
                          <span className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle" style={{ background: BAR_COLORS[i % BAR_COLORS.length] }} />
                          <span className="font-mono">{r.id.slice(0, 8)}</span>
                        </Link>
                      </td>
                      <td>{model}</td>
                      <td className="text-right tabular-nums">{total}</td>
                      <td className="text-right tabular-nums">{total ? `${Math.round((passed / total) * 100)}%` : '—'}</td>
                      <td className="text-right tabular-nums">
                        {cs.avg_latency_ms != null ? `${Math.round(cs.avg_latency_ms as number)}ms` : '—'}
                      </td>
                      <td className="text-right tabular-nums">
                        {cs.avg_first_answer_token_ms != null ? `${Math.round(cs.avg_first_answer_token_ms as number)}ms` : '—'}
                      </td>
                      <td className="text-right tabular-nums">
                        {cs.avg_total_tokens != null ? Math.round(cs.avg_total_tokens as number) : '—'}
                      </td>
                      <td className="text-right tabular-nums">
                        {cs.avg_tool_calls != null ? (cs.avg_tool_calls as number).toFixed(1) : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <p className="mt-2 text-[10px] text-text-tertiary">
              概览始终展示完整运行的统计；下方图表与改进总结会跟随勾选的样例子集变化。
            </p>
          </section>

          {usingSubset && (
            <div className="mb-3 flex items-center justify-between gap-3 px-3 py-2 rounded-md border border-accent/40 bg-accent/8 text-[12px] text-text-primary">
              <span>
                <span className="badge badge-info mr-2">子集模式</span>
                下方图表与 A/B 报告基于 <span className="font-mono">{selectedKeys.size}</span> 个已勾选样例重算。
              </span>
              <button
                onClick={() => setSelectedKeys(new Set())}
                className="text-action text-[11px]"
              >
                清空选择
              </button>
            </div>
          )}

          {/* A/B summary report */}
          {runs.length >= 2 && aRun && bRun && statsByRun[aRun.id] && statsByRun[bRun.id] && (
            <ABSummarySection
              runs={runs}
              aRun={aRun}
              bRun={bRun}
              aStats={statsByRun[aRun.id]}
              bStats={statsByRun[bRun.id]}
              usingSubset={usingSubset}
              onChangeA={setARunId}
              onChangeB={setBRunId}
            />
          )}

          {passRateChart.length > 0 && (
            <section className="card p-4 mb-5">
              <h3 className="page-eyebrow mb-3">通过率对比{usingSubset && ' · 子集'}</h3>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={passRateChart} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--separator) / 0.3)" />
                  <XAxis dataKey="run" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} domain={[0, 100]} label={{ value: '%', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
                  <Bar dataKey="passRate" name="通过率 (%)" fill="rgb(var(--positive))" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </section>
          )}

          {dimAggChart.data.length > 0 && (
            <section className="card p-4 mb-5">
              <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
                <h3 className="page-eyebrow">维度平均分（0-1）{usingSubset && ' · 子集'}</h3>
                {hasMultiTurnDims && (
                  <div className="inline-flex rounded-md border border-border overflow-hidden text-[11px]">
                    {(['agg', 'trend'] as const).map(v => (
                      <button
                        key={v}
                        onClick={() => setDimView(v)}
                        className={`px-2.5 py-1 transition-colors ${
                          dimView === v
                            ? 'bg-accent/10 text-accent'
                            : 'text-text-secondary hover:bg-fill/5'
                        }`}
                      >
                        {v === 'agg' ? '聚合' : '逐轮趋势'}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {(!hasMultiTurnDims || dimView === 'agg') ? (
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={dimAggChart.data} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--separator) / 0.3)" />
                    <XAxis dataKey="dimension" tick={{ fontSize: 10 }} interval={0} angle={-15} textAnchor="end" height={50} />
                    <YAxis tick={{ fontSize: 10 }} domain={[0, 1]} />
                    <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    {dimAggChart.runKeys.map((k, i) => (
                      <Bar key={k} dataKey={k} fill={BAR_COLORS[i % BAR_COLORS.length]} radius={[3, 3, 0, 0]} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={dimTrendChart.data} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--separator) / 0.3)" />
                    <XAxis dataKey="turnLabel" tick={{ fontSize: 10 }} interval={0} height={30} />
                    <YAxis tick={{ fontSize: 10 }} domain={[0, 1]} />
                    <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    {dimTrendChart.series.map((s, i) => (
                      <Line
                        key={s.key}
                        type="monotone"
                        dataKey={s.key}
                        name={s.label}
                        stroke={BAR_COLORS[i % BAR_COLORS.length]}
                        strokeWidth={2}
                        dot={{ r: 2 }}
                        connectNulls
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              )}
              {hasMultiTurnDims && (
                <p className="mt-2 text-[10px] text-text-tertiary">
                  {dimView === 'agg'
                    ? '多轮维度按各轮均值聚合成一根柱；切「逐轮趋势」看每轮变化。'
                    : '每条线 = 一个运行的一个维度，x 轴为对话轮次。会话级分数不在此图，见上方卡片。'}
                </p>
              )}
            </section>
          )}

          {costChart.data.length > 0 && (
            <section className="card p-4 mb-5">
              <h3 className="page-eyebrow mb-3">成本指标（成功样例均值）{usingSubset && ' · 子集'}</h3>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={costChart.data} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--separator) / 0.3)" />
                  <XAxis dataKey="metric" tick={{ fontSize: 10 }} interval={0} angle={-15} textAnchor="end" height={50} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  {costChart.runKeys.map((k, i) => (
                    <Bar key={k} dataKey={k} fill={BAR_COLORS[i % BAR_COLORS.length]} radius={[3, 3, 0, 0]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
              <p className="mt-2 text-[10px] text-text-tertiary">
                时延与 token 量纲不同，图表用于相对比较，数值请看上方表格。
              </p>
            </section>
          )}

          {/* Sample-level alignment & comparison */}
          <SampleAlignmentSection
            runs={runs}
            aligned={aligned}
            alignKey={alignKey}
            onChangeAlignKey={k => { setAlignKey(k); setSelectedAlignKey(null) }}
            onSelect={setSelectedAlignKey}
            selectedAlignKey={selectedAlignKey}
            selectedKeys={selectedKeys}
            onSetSelected={setSelectedKeys}
          />
        </>
      )}

      <Drawer
        open={!!selectedAligned}
        onClose={() => setSelectedAlignKey(null)}
        width="wide"
        title={selectedAligned ? selectedAligned.label : '样例对比'}
        subtitle={selectedAligned ? `按 ${alignKey === 'case_id' ? 'case_id' : '问题文本'} 对齐` : undefined}
      >
        {selectedAligned && (
          <SampleCompareDetail aligned={selectedAligned} runs={runs} />
        )}
      </Drawer>
    </div>
  )
}


// ─── A/B summary section ────────────────────────────────────────────────────

function ABSummarySection({ runs, aRun, bRun, aStats, bStats, usingSubset, onChangeA, onChangeB }: {
  runs: EvalRunDetail[]
  aRun: EvalRunDetail
  bRun: EvalRunDetail
  aStats: RunStats
  bStats: RunStats
  usingSubset: boolean
  onChangeA: (id: string) => void
  onChangeB: (id: string) => void
}) {
  const passRateA = aStats.total > 0 ? aStats.passed / aStats.total : null
  const passRateB = bStats.total > 0 ? bStats.passed / bStats.total : null
  const passRateDelta = deltaPct(passRateB, passRateA)

  const dimsA = aStats.dimensionAverages
  const dimsB = bStats.dimensionAverages
  const dimGroups = buildDimGroups(dimsA, dimsB)

  const costA = aStats.costSuccess
  const costB = bStats.costSuccess

  return (
    <section className="card p-4 mb-5">
      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <h3 className="page-eyebrow">
          改进总结报告
          {usingSubset && <span className="ml-2 badge badge-info">子集</span>}
        </h3>
        <div className="flex items-center gap-2 text-[11px]">
          <RunPicker label="A" runs={runs} value={aRun.id} onChange={onChangeA} excludeId={bRun.id} />
          <span className="text-text-tertiary">→</span>
          <RunPicker label="B" runs={runs} value={bRun.id} onChange={onChangeB} excludeId={aRun.id} />
        </div>
      </div>

      <p className="text-[12px] text-text-secondary mb-4 leading-relaxed">
        以两次评估的平均指标做对比（不是和某条基线比，而是 A/B 两次运行的平均值），
        正向变化标 <span className="text-positive">绿</span>、负向标 <span className="text-negative">红</span>。
        通过率/维度分按各维度的"越高越好/越低越好"语义着色；成本指标越低越好（缓存命中率除外）。
      </p>

      {/* Pass rate */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <DeltaCard
          label="通过率"
          a={passRateA != null ? `${(passRateA * 100).toFixed(1)}%` : '—'}
          b={passRateB != null ? `${(passRateB * 100).toFixed(1)}%` : '—'}
          delta={passRateDelta}
          direction="higher_better"
        />
        <DeltaCard
          label="样例数"
          a={String(aStats.total)}
          b={String(bStats.total)}
          delta={null}
          direction="higher_better"
          neutral
        />
      </div>

      {/* Dimension averages — 多轮维度按 base 聚合成一张卡，可展开逐轮 */}
      {dimGroups.length > 0 && (
        <div className="mb-4">
          <div className="field-label">维度均分</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {dimGroups.map(g => (
              <DimensionGroupCard key={g.base} group={g} />
            ))}
          </div>
        </div>
      )}

      {/* Cost metrics */}
      <div>
        <div className="field-label">成本指标（成功样例均值）</div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {COST_METRIC_DEFS.map(metric => {
            const va = costA[metric.key] as number | null | undefined
            const vb = costB[metric.key] as number | null | undefined
            const display = (v: number | null | undefined) =>
              v == null ? '—' : (metric.fmt ? metric.fmt(v) : String(v))
            return (
              <DeltaCard
                key={metric.key}
                label={metric.label}
                a={display(va)}
                b={display(vb)}
                delta={deltaPct(vb ?? null, va ?? null)}
                direction={metric.direction}
              />
            )
          })}
        </div>
      </div>
    </section>
  )
}

function RunPicker({ label, runs, value, onChange, excludeId }: {
  label: string
  runs: EvalRunDetail[]
  value: string
  onChange: (id: string) => void
  excludeId?: string
}) {
  return (
    <label className="inline-flex items-center gap-1.5">
      <span className="text-text-tertiary tracking-wider">{label}</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="select-sm font-mono"
      >
        {runs.map(r => (
          <option key={r.id} value={r.id} disabled={r.id === excludeId}>
            {runLabel(r)}
          </option>
        ))}
      </select>
    </label>
  )
}

function DeltaCard({ label, a, b, delta, direction, neutral }: {
  label: string
  a: string
  b: string
  delta: number | null
  direction: 'higher_better' | 'lower_better'
  neutral?: boolean
}) {
  const sign = delta == null ? '' : delta > 0 ? '+' : ''
  const trend = delta == null ? null : (delta === 0 ? 'flat' : (delta > 0 ? 'up' : 'down'))
  let cls = 'text-text-tertiary'
  if (!neutral && trend && trend !== 'flat') {
    const goodDir = direction === 'higher_better' ? 'up' : 'down'
    cls = trend === goodDir ? 'text-positive' : 'text-negative'
  }
  return (
    <div className="border border-border rounded-md p-3 bg-fill/5">
      <div className="text-[10px] text-text-tertiary mb-1.5">{label}</div>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[12px] text-text-secondary">{a}</span>
        <span className="text-text-tertiary text-[11px]">→</span>
        <span className="font-mono text-[14px] text-text-primary">{b}</span>
        {delta != null && (
          <span className={`ml-auto font-mono text-[12px] ${cls}`}>
            {trend === 'up' && '▲ '}
            {trend === 'down' && '▼ '}
            {trend === 'flat' && '— '}
            {sign}{delta.toFixed(1)}%
          </span>
        )}
      </div>
    </div>
  )
}

// 维度分组卡：单轮维度=普通 DeltaCard 行为；多轮维度=聚合均值 + 可展开逐轮 mini 明细
function DimensionGroupCard({ group }: { group: DimGroup }) {
  const [open, setOpen] = useState(false)
  const meta = getScoreMeta(group.base)
  const label = `${meta.label} · ${directionMark(meta)}`
  const delta = deltaPct(group.aggB, group.aggA)
  const sign = delta == null ? '' : delta > 0 ? '+' : ''
  const trend = delta == null ? null : (delta === 0 ? 'flat' : (delta > 0 ? 'up' : 'down'))
  let cls = 'text-text-tertiary'
  if (trend && trend !== 'flat') {
    const goodDir = meta.direction === 'higher_better' ? 'up' : 'down'
    cls = trend === goodDir ? 'text-positive' : 'text-negative'
  }
  const fmt = (v: number | null) => (v == null ? '—' : v.toFixed(3))

  return (
    <div className="border border-border rounded-md p-3 bg-fill/5">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-[10px] text-text-tertiary">{label}</span>
        {group.isMultiTurn && (
          <span className="badge badge-info text-[9px]">{group.turnCount} 轮 · 均值</span>
        )}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[12px] text-text-secondary">{fmt(group.aggA)}</span>
        <span className="text-text-tertiary text-[11px]">→</span>
        <span className="font-mono text-[14px] text-text-primary">{fmt(group.aggB)}</span>
        {delta != null && (
          <span className={`ml-auto font-mono text-[12px] ${cls}`}>
            {trend === 'up' && '▲ '}
            {trend === 'down' && '▼ '}
            {trend === 'flat' && '— '}
            {sign}{delta.toFixed(1)}%
          </span>
        )}
      </div>
      {group.isMultiTurn && (
        <>
          <button
            onClick={() => setOpen(o => !o)}
            className="mt-2 text-[10px] text-accent hover:text-accent-hover transition-colors"
          >
            {open ? '▾ 收起逐轮' : '▸ 展开逐轮'}
          </button>
          {open && (
            <div className="mt-2 flex flex-col gap-1 border-t border-border/60 pt-2">
              {group.subs.map(s => {
                const d = deltaPct(s.b, s.a)
                const st = d == null ? null : (d === 0 ? 'flat' : (d > 0 ? 'up' : 'down'))
                let scls = 'text-text-tertiary'
                if (st && st !== 'flat') {
                  const gd = meta.direction === 'higher_better' ? 'up' : 'down'
                  scls = st === gd ? 'text-positive' : 'text-negative'
                }
                return (
                  <div key={s.key} className="flex items-baseline gap-2 text-[10px]">
                    <span className="text-text-tertiary w-14 shrink-0">{s.label}</span>
                    <span className="font-mono text-text-secondary">{fmt(s.a)}</span>
                    <span className="text-text-tertiary">→</span>
                    <span className="font-mono text-text-primary">{fmt(s.b)}</span>
                    {d != null && (
                      <span className={`ml-auto font-mono ${scls}`}>
                        {st === 'up' && '▲'}{st === 'down' && '▼'}{st === 'flat' && '—'}
                        {d > 0 ? '+' : ''}{d.toFixed(1)}%
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}


// ─── Sample alignment table ────────────────────────────────────────────────

interface AlignedSample {
  key: string                      // alignment key (case_id or normalized question)
  label: string                    // human display
  byRun: Record<string, EvalResultRow | undefined>
}

function buildAlignedSamples(
  runs: EvalRunDetail[],
  resultsByRun: Record<string, EvalResultsPage | undefined>,
  alignKey: AlignKey,
): AlignedSample[] {
  const map = new Map<string, AlignedSample>()
  for (const run of runs) {
    const page = resultsByRun[run.id]
    if (!page) continue
    for (const row of page.items) {
      const k = alignmentKeyFor(row, alignKey)
      if (!k) continue
      const label = labelFor(row, alignKey)
      const existing = map.get(k) ?? { key: k, label, byRun: {} }
      existing.byRun[run.id] = row
      if (existing.label.length === 0 && label) existing.label = label
      map.set(k, existing)
    }
  }
  return Array.from(map.values()).sort((a, b) => a.label.localeCompare(b.label))
}

function alignmentKeyFor(row: EvalResultRow, alignKey: AlignKey): string | null {
  if (alignKey === 'case_id') {
    return row.benchmark_case_id || row.test_case_id || null
  }
  const q = (row.question || '').trim()
  if (!q) return null
  return q.replace(/\s+/g, ' ').toLowerCase()
}

function labelFor(row: EvalResultRow, alignKey: AlignKey): string {
  if (alignKey === 'case_id') {
    const id = row.benchmark_case_id || row.test_case_id || ''
    const q = row.question || ''
    return q ? `${id.slice(0, 8)} · ${truncate(q, 80)}` : id.slice(0, 8)
  }
  return truncate(row.question || '', 100)
}

function SampleAlignmentSection({
  runs, aligned, alignKey, onChangeAlignKey, onSelect, selectedAlignKey,
  selectedKeys, onSetSelected,
}: {
  runs: EvalRunDetail[]
  aligned: AlignedSample[]
  alignKey: AlignKey
  onChangeAlignKey: (k: AlignKey) => void
  onSelect: (key: string | null) => void
  selectedAlignKey: string | null
  selectedKeys: Set<string>
  onSetSelected: (next: Set<string>) => void
}) {
  const [search, setSearch] = useState('')
  const [diffOnly, setDiffOnly] = useState(false)

  const filtered = useMemo(() => {
    let rows = aligned
    if (search) {
      const s = search.toLowerCase()
      rows = rows.filter(r => r.label.toLowerCase().includes(s) || r.key.toLowerCase().includes(s))
    }
    if (diffOnly) {
      rows = rows.filter(r => sampleHasDifference(r, runs))
    }
    return rows
  }, [aligned, search, diffOnly, runs])

  const fullCoverage = aligned.filter(a => runs.every(r => a.byRun[r.id])).length
  const visibleAllSelected = filtered.length > 0 && filtered.every(r => selectedKeys.has(r.key))
  const visibleSomeSelected = filtered.some(r => selectedKeys.has(r.key))

  const toggleOne = (key: string, checked: boolean) => {
    const next = new Set(selectedKeys)
    if (checked) next.add(key)
    else next.delete(key)
    onSetSelected(next)
  }
  const toggleVisibleAll = (checked: boolean) => {
    const next = new Set(selectedKeys)
    if (checked) filtered.forEach(r => next.add(r.key))
    else filtered.forEach(r => next.delete(r.key))
    onSetSelected(next)
  }

  return (
    <section className="card p-4 mb-5">
      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <h3 className="page-eyebrow">样例对齐与对比</h3>
        <div className="flex items-center gap-2 text-[11px]">
          <span className="text-text-tertiary">对齐键：</span>
          {(['case_id', 'question'] as const).map(k => (
            <button
              key={k}
              onClick={() => onChangeAlignKey(k)}
              className={`px-2 py-0.5 rounded-md border text-[11px] transition-colors ${
                alignKey === k
                  ? 'border-accent text-accent bg-accent/10'
                  : 'border-border text-text-secondary hover:border-border-strong'
              }`}
            >
              {k === 'case_id' ? 'benchmark_case_id' : '问题文本'}
            </button>
          ))}
        </div>
      </div>

      <div className="toolbar mb-3">
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="按问题/ID 搜索"
          className="input-sm w-[260px]"
        />
        <label className="inline-flex items-center gap-1.5 text-[12px] cursor-pointer">
          <input
            type="checkbox"
            checked={diffOnly}
            onChange={e => setDiffOnly(e.target.checked)}
            className="accent-accent"
          />
          只看分数有差异的
        </label>
        <span className="text-[11px] text-text-tertiary">
          共 {aligned.length} 组（{fullCoverage} 组全 run 覆盖） · 显示 {filtered.length}
          {selectedKeys.size > 0 && (
            <> · 已选 <span className="text-accent font-mono">{selectedKeys.size}</span></>
          )}
        </span>
        {selectedKeys.size > 0 && (
          <button
            onClick={() => onSetSelected(new Set())}
            className="text-action text-[11px]"
          >
            清空选择
          </button>
        )}
      </div>

      <div className="overflow-x-auto table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th className="w-8">
                <input
                  type="checkbox"
                  checked={visibleAllSelected}
                  ref={el => {
                    if (el) el.indeterminate = !visibleAllSelected && visibleSomeSelected
                  }}
                  onChange={e => toggleVisibleAll(e.target.checked)}
                  className="accent-accent"
                  title="全选/取消可见样例"
                  aria-label="全选可见样例"
                />
              </th>
              <th>{alignKey === 'case_id' ? 'Case' : '问题'}</th>
              {runs.map((r, i) => (
                <th key={r.id}>
                  <span className="inline-block w-2 h-2 rounded-full mr-1 align-middle" style={{ background: BAR_COLORS[i % BAR_COLORS.length] }} />
                  {runLabel(r)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={runs.length + 2} className="empty-state">
                  {aligned.length === 0
                    ? '没有可对齐的样例。换一种对齐键试试。'
                    : '没有匹配的样例。'}
                </td>
              </tr>
            )}
            {filtered.map(a => {
              const checked = selectedKeys.has(a.key)
              return (
                <tr
                  key={a.key}
                  onClick={() => onSelect(a.key)}
                  className={`cursor-pointer ${selectedAlignKey === a.key ? 'bg-accent/5' : ''}`}
                >
                  <td onClick={e => e.stopPropagation()} className="w-8">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={e => toggleOne(a.key, e.target.checked)}
                      className="accent-accent"
                      aria-label="勾选用于子集对比"
                    />
                  </td>
                  <td className="max-w-[320px]">
                    <div className="truncate" title={a.label}>{a.label}</div>
                  </td>
                  {runs.map(r => {
                    const row = a.byRun[r.id]
                    return (
                      <td key={r.id} className="align-top">
                        {row ? <SampleCellSummary row={row} /> : <span className="text-text-tertiary">未运行</span>}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function SampleCellSummary({ row }: { row: EvalResultRow }) {
  const scoreEntries = Object.entries(row.scores)
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center gap-1.5">
        <StatusDot status={row.status} />
        <span className="text-[10px] text-text-tertiary">{row.status}</span>
        {row.latency_ms != null && (
          <span className="text-[10px] text-text-tertiary tabular-nums">· {row.latency_ms}ms</span>
        )}
      </div>
      {scoreEntries.length > 0 && (
        <div className="flex flex-wrap gap-0.5 mt-0.5">
          {scoreEntries.slice(0, 3).map(([n, v]) => {
            const meta = getScoreMeta(n)
            const t = tone(n, v)
            const cls = t === 'good' ? 'badge badge-positive' : 'badge badge-negative'
            return (
              <span
                key={n}
                className={`${cls} font-mono`}
                title={`${meta.label}: ${v.toFixed(2)}`}
              >
                {meta.label}:{v.toFixed(2)}
              </span>
            )
          })}
          {scoreEntries.length > 3 && (
            <span className="text-[10px] text-text-tertiary">+{scoreEntries.length - 3}</span>
          )}
        </div>
      )}
    </div>
  )
}

function StatusDot({ status }: { status: string }) {
  const color = ({
    pass: 'bg-positive',
    fail: 'bg-negative',
    error: 'bg-negative',
    agent_unreachable: 'bg-warning',
    agent_timeout: 'bg-warning',
  } as Record<string, string>)[status] ?? 'bg-fill/40'
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${color}`} />
}

function sampleHasDifference(a: AlignedSample, runs: EvalRunDetail[]): boolean {
  const presentRows = runs.map(r => a.byRun[r.id]).filter(Boolean) as EvalResultRow[]
  if (presentRows.length < 2) return true
  if (new Set(presentRows.map(r => r.status)).size > 1) return true
  const dims = new Set<string>()
  presentRows.forEach(r => Object.keys(r.scores).forEach(d => dims.add(d)))
  for (const d of dims) {
    const vals = presentRows.map(r => r.scores[d]).filter(v => v != null) as number[]
    if (vals.length < 2) return true
    if (Math.max(...vals) - Math.min(...vals) >= 0.01) return true
  }
  return false
}


// ─── Sample compare detail (drawer) ────────────────────────────────────────

function SampleCompareDetail({ aligned, runs }: {
  aligned: AlignedSample
  runs: EvalRunDetail[]
}) {
  return (
    <div className="text-[12px] flex flex-col gap-4">
      <div>
        <div className="field-label">问题</div>
        <div className="font-mono text-[12px] bg-fill/5 border border-border rounded-md p-2.5 whitespace-pre-wrap">
          {aligned.label || '—'}
        </div>
      </div>

      {runs.map((r, i) => {
        const row = aligned.byRun[r.id]
        return (
          <div key={r.id} className="card p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full" style={{ background: BAR_COLORS[i % BAR_COLORS.length] }} />
                <span className="font-mono text-[12px]">{runLabel(r)}</span>
                <Link to={`/evaluation/runs/${r.id}`} className="text-[11px] text-accent hover:text-accent-hover transition-colors">→ run 详情</Link>
              </div>
              {row && (
                <div className="flex items-center gap-2 text-[11px] text-text-secondary">
                  <span>{row.status}</span>
                  {row.latency_ms != null && <span className="font-mono">{row.latency_ms}ms</span>}
                  {row.total_tokens != null && <span className="font-mono">{row.total_tokens} tk</span>}
                </div>
              )}
            </div>

            {!row && (
              <div className="text-[11px] text-text-tertiary py-3 text-center border border-dashed border-border rounded-md">
                此 run 未运行该样例
              </div>
            )}

            {row && (
              <>
                {Object.keys(row.scores).length > 0 && (
                  <div className="mb-2">
                    <div className="field-label">评分</div>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(row.scores).map(([n, v]) => {
                        const meta = getScoreMeta(n)
                        const cls = isPassing(n, v) ? 'badge badge-positive' : 'badge badge-negative'
                        return (
                          <span
                            key={n}
                            className={cls}
                            title={`${meta.label} · ${directionMark(meta)}\n合格线 ${meta.threshold}\n${meta.description}`}
                          >
                            {meta.label}: {v.toFixed(2)}
                          </span>
                        )
                      })}
                    </div>
                  </div>
                )}

                <div className="mb-2">
                  <div className="field-label">输出</div>
                  <pre className="font-mono text-[11px] bg-fill/5 border border-border rounded-md p-2.5 max-h-[180px] overflow-y-auto whitespace-pre-wrap">
                    {row.actual_output || '（无输出）'}
                  </pre>
                </div>

                {row.error_message && (
                  <div className="mb-2">
                    <div className="field-label text-negative">错误</div>
                    <pre className="font-mono text-[11px] bg-negative/5 border border-negative/30 rounded-md p-2.5 whitespace-pre-wrap">
                      {row.error_message}
                    </pre>
                  </div>
                )}

                {Array.isArray(row.actual_tool_calls) && row.actual_tool_calls.length > 0 && (
                  <div>
                    <div className="field-label">工具调用 ({row.actual_tool_calls.length})</div>
                    <div className="flex flex-wrap gap-1">
                      {row.actual_tool_calls.slice(0, 12).map((c, idx) => {
                        const name = ((c as { tool_name?: string; name?: string }).tool_name
                          ?? (c as { name?: string }).name) || '?'
                        return (
                          <span key={idx} className="badge badge-warning font-mono">{name}</span>
                        )
                      })}
                      {row.actual_tool_calls.length > 12 && (
                        <span className="text-[10px] text-text-tertiary">+{row.actual_tool_calls.length - 12}</span>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}


// ─── Stats helpers ──────────────────────────────────────────────────────────

function computeStatsFromRows(rows: EvalResultRow[]): RunStats {
  const total = rows.length
  const passed = rows.filter(r => r.status === 'pass').length

  // 维度均分：所有行（不仅成功），按各维度独立计 N
  const dimSum: Record<string, { sum: number; count: number }> = {}
  for (const r of rows) {
    for (const [d, v] of Object.entries(r.scores)) {
      if (typeof v !== 'number') continue
      const acc = dimSum[d] ?? { sum: 0, count: 0 }
      acc.sum += v
      acc.count += 1
      dimSum[d] = acc
    }
  }
  const dimensionAverages: Record<string, number> = {}
  for (const [d, { sum, count }] of Object.entries(dimSum)) {
    if (count > 0) dimensionAverages[d] = sum / count
  }

  // 成本：仅成功样例
  const successRows = rows.filter(r => r.status === 'pass')
  const costSuccess: Record<string, number> = {}
  const avgField = (key: keyof EvalResultRow, dest: string) => {
    const vals: number[] = []
    for (const r of successRows) {
      const v = r[key]
      if (typeof v === 'number') vals.push(v)
    }
    if (vals.length > 0) costSuccess[dest] = vals.reduce((s, v) => s + v, 0) / vals.length
  }
  avgField('latency_ms', 'avg_latency_ms')
  avgField('first_thinking_token_ms', 'avg_first_thinking_token_ms')
  avgField('first_answer_token_ms', 'avg_first_answer_token_ms')
  avgField('total_tokens', 'avg_total_tokens')
  avgField('prompt_tokens', 'avg_prompt_tokens')
  avgField('completion_tokens', 'avg_completion_tokens')
  avgField('tool_call_count', 'avg_tool_calls')

  // Cache hit rate（Anthropic 口径）
  let read = 0
  let denom = 0
  let hasCache = false
  for (const r of successRows) {
    const pt = typeof r.prompt_tokens === 'number' ? r.prompt_tokens : null
    const cr = typeof r.cache_read_tokens === 'number' ? r.cache_read_tokens : null
    const cc = typeof r.cache_creation_tokens === 'number' ? r.cache_creation_tokens : 0
    if (pt != null && cr != null) {
      hasCache = true
      read += cr
      denom += pt - cc
    }
  }
  if (hasCache && denom > 0) costSuccess.cache_hit_rate = read / denom

  return { total, passed, dimensionAverages, costSuccess }
}


// ─── Helpers ────────────────────────────────────────────────────────────────

function deltaPct(b: number | null | undefined, a: number | null | undefined): number | null {
  if (a == null || b == null) return null
  if (a === 0) {
    if (b === 0) return 0
    return null
  }
  return ((b - a) / Math.abs(a)) * 100
}

function runLabel(r: EvalRunDetail): string {
  const model = (r.agent_config as { model?: string }).model
  return model ? `${r.id.slice(0, 6)} · ${model}` : r.id.slice(0, 8)
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

// ─── 多轮维度解析 ─────────────────────────────────────────────────────────
// 多轮评估的 score key 形如 `<base>.turn<N>` / `<base>.conversation`；单轮维度
// 无后缀。对比页把逐轮键按 base 聚合，避免 26+ 个维度把卡片区/图表撑爆。

type DimKind = 'turn' | 'conversation' | 'plain'

interface ParsedDim {
  base: string
  turn: number | null   // turn 序号；conversation / plain 为 null
  kind: DimKind
}

function parseDimKey(key: string): ParsedDim {
  const mTurn = key.match(/^(.*)\.turn(\d+)$/)
  if (mTurn) return { base: mTurn[1], turn: Number(mTurn[2]), kind: 'turn' }
  const mConv = key.match(/^(.*)\.conversation$/)
  if (mConv) return { base: mConv[1], turn: null, kind: 'conversation' }
  return { base: key, turn: null, kind: 'plain' }
}

function meanOf(vals: Array<number | null | undefined>): number | null {
  const nums = vals.filter((v): v is number => typeof v === 'number')
  if (nums.length === 0) return null
  return nums.reduce((s, v) => s + v, 0) / nums.length
}

interface DimGroupSub {
  key: string
  label: string          // 「第N轮」/「会话级」/ 维度名（plain）
  kind: DimKind
  turn: number | null
  a: number | null
  b: number | null
}

interface DimGroup {
  base: string
  isMultiTurn: boolean
  turnCount: number
  aggA: number | null
  aggB: number | null
  subs: DimGroupSub[]
}

function sortSubs<T extends { kind: DimKind; turn: number | null }>(a: T, b: T): number {
  // conversation 级排最后；turn 按序号升序
  if (a.kind === 'conversation' && b.kind !== 'conversation') return 1
  if (b.kind === 'conversation' && a.kind !== 'conversation') return -1
  return (a.turn ?? 0) - (b.turn ?? 0)
}

function buildDimGroups(
  dimsA: Record<string, number>,
  dimsB: Record<string, number>,
): DimGroup[] {
  const allKeys = Array.from(new Set([...Object.keys(dimsA), ...Object.keys(dimsB)]))
  const byBase = new Map<string, ParsedDim[]>()
  for (const k of allKeys) {
    const p = parseDimKey(k)
    const arr = byBase.get(p.base) ?? []
    arr.push(p)
    byBase.set(p.base, arr)
  }
  const groups: DimGroup[] = []
  for (const [base, parsed] of byBase) {
    const isMultiTurn = parsed.some(p => p.kind !== 'plain')
    const turnCount = parsed.filter(p => p.kind === 'turn').length
    const baseLabel = getScoreMeta(base).label
    const subs: DimGroupSub[] = parsed
      .slice()
      .sort(sortSubs)
      .map(p => ({
        key: p.kind === 'turn' ? `${base}.turn${p.turn}` : p.kind === 'conversation' ? `${base}.conversation` : base,
        label: p.kind === 'turn' ? `第 ${p.turn} 轮` : p.kind === 'conversation' ? '会话级' : baseLabel,
        kind: p.kind,
        turn: p.turn,
        a: dimsA[p.kind === 'turn' ? `${base}.turn${p.turn}` : p.kind === 'conversation' ? `${base}.conversation` : base] ?? null,
        b: dimsB[p.kind === 'turn' ? `${base}.turn${p.turn}` : p.kind === 'conversation' ? `${base}.conversation` : base] ?? null,
      }))
    const aggA = meanOf(subs.map(s => s.a))
    const aggB = meanOf(subs.map(s => s.b))
    groups.push({ base, isMultiTurn, turnCount, aggA, aggB, subs })
  }
  // 单轮维度排前，多轮维度排后；同类按 base 名排序
  return groups.sort((x, y) => {
    if (x.isMultiTurn !== y.isMultiTurn) return x.isMultiTurn ? 1 : -1
    return x.base.localeCompare(y.base)
  })
}

function buildPassRateChart(
  runs: EvalRunDetail[],
  statsByRun: Record<string, RunStats>,
): Array<{ run: string; passRate: number }> {
  return runs
    .map(r => {
      const s = statsByRun[r.id]
      if (!s || s.total === 0) return null
      return { run: runLabel(r), passRate: Math.round((s.passed / s.total) * 1000) / 10 }
    })
    .filter((x): x is { run: string; passRate: number } => x !== null)
}

// 聚合视图：多轮维度按 base 折叠成一根柱（跨轮 + 会话级取均值），单轮维度原样。
// x 轴 = base 维度名，每个 run 一根柱。把 26+ 个逐轮键压到几个 base。
function buildDimensionAggChart(
  runs: EvalRunDetail[],
  statsByRun: Record<string, RunStats>,
): {
  data: Array<Record<string, number | string>>
  runKeys: string[]
} {
  const runKeys: string[] = []
  // base -> runKey -> { sum, count }
  const byBase = new Map<string, Map<string, { sum: number; count: number }>>()
  const baseOrder: string[] = []
  for (const r of runs) {
    const key = runLabel(r)
    runKeys.push(key)
    const dims = statsByRun[r.id]?.dimensionAverages ?? {}
    for (const [d, v] of Object.entries(dims)) {
      if (typeof v !== 'number') continue
      const { base } = parseDimKey(d)
      if (!byBase.has(base)) { byBase.set(base, new Map()); baseOrder.push(base) }
      const inner = byBase.get(base)!
      const acc = inner.get(key) ?? { sum: 0, count: 0 }
      acc.sum += v
      acc.count += 1
      inner.set(key, acc)
    }
  }
  const data = baseOrder.map(base => {
    const row: Record<string, number | string> = { dimension: getScoreMeta(base).label }
    const inner = byBase.get(base)!
    for (const [runKey, { sum, count }] of inner) {
      if (count > 0) row[runKey] = Math.round((sum / count) * 100) / 100
    }
    return row
  })
  return { data, runKeys }
}

// 逐轮趋势视图：折线图。x 轴 = 轮次；每条线 = 一个 run × 一个 base 维度。
// 只纳入带 turn 的多轮维度；会话级 / 单轮维度不进折线（前者在卡片、后者无轮次）。
function buildTurnTrendChart(
  runs: EvalRunDetail[],
  statsByRun: Record<string, RunStats>,
): {
  data: Array<Record<string, number | string>>
  series: Array<{ key: string; label: string }>
} {
  const turnsSeen = new Set<number>()
  const series: Array<{ key: string; label: string }> = []
  const seriesSeen = new Set<string>()
  // seriesKey -> turn -> value
  const values = new Map<string, Map<number, number>>()

  for (const r of runs) {
    const runKey = runLabel(r)
    const dims = statsByRun[r.id]?.dimensionAverages ?? {}
    for (const [d, v] of Object.entries(dims)) {
      if (typeof v !== 'number') continue
      const p = parseDimKey(d)
      if (p.kind !== 'turn' || p.turn == null) continue
      turnsSeen.add(p.turn)
      const seriesKey = `${r.id}::${p.base}`
      if (!seriesSeen.has(seriesKey)) {
        seriesSeen.add(seriesKey)
        series.push({ key: seriesKey, label: `${runKey} · ${getScoreMeta(p.base).label}` })
      }
      if (!values.has(seriesKey)) values.set(seriesKey, new Map())
      values.get(seriesKey)!.set(p.turn, Math.round(v * 100) / 100)
    }
  }

  const turns = Array.from(turnsSeen).sort((a, b) => a - b)
  const data = turns.map(t => {
    const row: Record<string, number | string> = { turnLabel: `第${t}轮` }
    for (const s of series) {
      const val = values.get(s.key)?.get(t)
      if (val != null) row[s.key] = val
    }
    return row
  })
  return { data, series }
}

function buildCostChart(
  runs: EvalRunDetail[],
  statsByRun: Record<string, RunStats>,
): {
  data: Array<Record<string, number | string>>
  runKeys: string[]
} {
  const metrics: Array<{ key: string; label: string }> = [
    { key: 'avg_latency_ms', label: '时延 (ms)' },
    { key: 'avg_first_thinking_token_ms', label: '首思考 token (ms)' },
    { key: 'avg_first_answer_token_ms', label: '首答 token (ms)' },
    { key: 'avg_total_tokens', label: '总 token' },
    { key: 'avg_prompt_tokens', label: '输入 token' },
    { key: 'avg_completion_tokens', label: '输出 token' },
    { key: 'avg_tool_calls', label: '工具调用' },
  ]
  const runKeys: string[] = []
  const data = metrics.map(m => {
    const row: Record<string, number | string> = { metric: m.label }
    runs.forEach(r => {
      const key = runLabel(r)
      if (!runKeys.includes(key)) runKeys.push(key)
      const v = statsByRun[r.id]?.costSuccess?.[m.key]
      if (v != null) row[key] = Math.round(v as number)
    })
    return row
  }).filter(row => Object.keys(row).length > 1)
  return { data, runKeys }
}
