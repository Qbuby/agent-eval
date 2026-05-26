import { useMemo } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { evaluationApi } from '@/services'
import type { EvalRunDetail } from '@/types'

const BAR_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4', '#f87171', '#8b5cf6']

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

  const runs = useMemo<EvalRunDetail[]>(() =>
    queries.map(q => q.data).filter((x): x is EvalRunDetail => !!x),
  [queries])

  const loading = queries.some(q => q.isLoading)
  const anyError = queries.find(q => q.isError)

  const dimensionChart = useMemo(() => buildDimensionChart(runs), [runs])
  const costChart = useMemo(() => buildCostChart(runs), [runs])
  const passRateChart = useMemo(() => buildPassRateChart(runs), [runs])

  if (ids.length === 0) {
    return (
      <div className="text-[12px] text-text-tertiary">
        请先在评估历史页勾选至少两个运行，再点「对比所选」。
        <Link to="/evaluation" className="ml-2 underline text-accent">返回历史</Link>
      </div>
    )
  }

  return (
    <div>
      <header className="mb-5">
        <div className="flex items-center gap-2 mb-1">
          <Link to="/evaluation" className="text-[11px] text-text-tertiary hover:text-accent">← 评估列表</Link>
        </div>
        <h1 className="text-lg font-light tracking-tight mb-1">运行对比</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">
          {ids.length} 个运行 · 维度分 · 成本 · 通过率
        </p>
      </header>

      {loading && <div className="text-[12px] text-text-tertiary py-10 text-center">加载中…</div>}
      {anyError && !loading && (
        <div className="text-[12px] text-negative py-4">
          部分运行加载失败：{(anyError.error as Error)?.message || '未知错误'}
        </div>
      )}

      {!loading && runs.length > 0 && (
        <>
          <section className="border border-border rounded-[6px] bg-surface p-4 mb-5 overflow-x-auto">
            <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-3">概览</h3>
            <table className="w-full text-[11px] border-collapse">
              <thead>
                <tr className="text-text-tertiary">
                  <th className="text-left py-1.5 px-2 font-normal">运行</th>
                  <th className="text-left py-1.5 px-2 font-normal">智能体模型</th>
                  <th className="text-right py-1.5 px-2 font-normal">样例数</th>
                  <th className="text-right py-1.5 px-2 font-normal">通过率</th>
                  <th className="text-right py-1.5 px-2 font-normal">成功·时延</th>
                  <th className="text-right py-1.5 px-2 font-normal">成功·首答</th>
                  <th className="text-right py-1.5 px-2 font-normal">成功·token</th>
                  <th className="text-right py-1.5 px-2 font-normal">成功·工具调用</th>
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
                    <tr key={r.id} className="border-t border-border">
                      <td className="py-1.5 px-2">
                        <Link to={`/evaluation/runs/${r.id}`} className="hover:text-accent">
                          <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: BAR_COLORS[i % BAR_COLORS.length] }} />
                          <span className="font-mono">{r.id.slice(0, 8)}</span>
                        </Link>
                      </td>
                      <td className="py-1.5 px-2">{model}</td>
                      <td className="py-1.5 px-2 text-right">{total}</td>
                      <td className="py-1.5 px-2 text-right">{total ? `${Math.round((passed / total) * 100)}%` : '—'}</td>
                      <td className="py-1.5 px-2 text-right">
                        {cs.avg_latency_ms != null ? `${Math.round(cs.avg_latency_ms as number)}ms` : '—'}
                      </td>
                      <td className="py-1.5 px-2 text-right">
                        {cs.avg_first_answer_token_ms != null ? `${Math.round(cs.avg_first_answer_token_ms as number)}ms` : '—'}
                      </td>
                      <td className="py-1.5 px-2 text-right">
                        {cs.avg_total_tokens != null ? Math.round(cs.avg_total_tokens as number) : '—'}
                      </td>
                      <td className="py-1.5 px-2 text-right">
                        {cs.avg_tool_calls != null ? (cs.avg_tool_calls as number).toFixed(1) : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </section>

          {passRateChart.length > 0 && (
            <section className="border border-border rounded-[6px] bg-surface p-4 mb-5">
              <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-3">通过率对比</h3>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={passRateChart} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border, #e5e5e5)" />
                  <XAxis dataKey="run" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} domain={[0, 100]} label={{ value: '%', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
                  <Bar dataKey="passRate" name="通过率 (%)" fill="#10b981" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </section>
          )}

          {dimensionChart.data.length > 0 && (
            <section className="border border-border rounded-[6px] bg-surface p-4 mb-5">
              <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-3">维度平均分（0-1）</h3>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={dimensionChart.data} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border, #e5e5e5)" />
                  <XAxis dataKey="dimension" tick={{ fontSize: 10 }} interval={0} angle={-15} textAnchor="end" height={50} />
                  <YAxis tick={{ fontSize: 10 }} domain={[0, 1]} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  {dimensionChart.runKeys.map((k, i) => (
                    <Bar key={k} dataKey={k} fill={BAR_COLORS[i % BAR_COLORS.length]} radius={[3, 3, 0, 0]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </section>
          )}

          {costChart.data.length > 0 && (
            <section className="border border-border rounded-[6px] bg-surface p-4 mb-5">
              <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-3">成本指标（成功样例均值）</h3>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={costChart.data} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border, #e5e5e5)" />
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
        </>
      )}
    </div>
  )
}


function runLabel(r: EvalRunDetail): string {
  const model = (r.agent_config as { model?: string }).model
  return model ? `${r.id.slice(0, 6)} · ${model}` : r.id.slice(0, 8)
}

function buildPassRateChart(runs: EvalRunDetail[]): Array<{ run: string; passRate: number }> {
  return runs
    .filter(r => (r.summary_scores?.counts?.total ?? 0) > 0)
    .map(r => {
      const total = r.summary_scores!.counts!.total!
      const passed = r.summary_scores!.counts!.passed ?? 0
      return { run: runLabel(r), passRate: Math.round((passed / total) * 1000) / 10 }
    })
}

function buildDimensionChart(runs: EvalRunDetail[]): {
  data: Array<Record<string, number | string>>
  runKeys: string[]
} {
  const runKeys: string[] = []
  const seen = new Set<string>()
  const byDim: Record<string, Record<string, number>> = {}
  for (const r of runs) {
    const key = runLabel(r)
    runKeys.push(key)
    const dims = r.summary_scores?.dimension_averages ?? {}
    for (const [d, v] of Object.entries(dims)) {
      seen.add(d)
      byDim[d] ||= {}
      byDim[d][key] = Math.round((v as number) * 100) / 100
    }
  }
  const data = Array.from(seen).map(d => ({
    dimension: d,
    ...byDim[d],
  }))
  return { data, runKeys }
}

function buildCostChart(runs: EvalRunDetail[]): {
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
      const v = r.summary_scores?.cost_success?.[m.key as keyof typeof r.summary_scores.cost_success]
      if (v != null) row[key] = Math.round(v as number)
    })
    return row
  }).filter(row => Object.keys(row).length > 1)
  return { data, runKeys }
}
