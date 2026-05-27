import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Legend,
} from 'recharts'
import { evaluationApi, tracesApi } from '@/services'
import type { EvalResultRow, EvalRunDetail, RunDetail, CotStep } from '@/types'
import { RunNodeRow, RunDetailBody, type NodeCache } from '@/components/RunTreeView'
import { Button, Drawer } from '@/components/ui'
import {
  getScoreMeta, isPassing, directionMark, tone,
} from '@/lib/scoreSemantics'

export default function EvaluationRunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const qc = useQueryClient()
  const navigate = useNavigate()

  const runQuery = useQuery({
    queryKey: ['eval-run', runId],
    queryFn: () => evaluationApi.getRun(runId!).then(r => r.data),
    enabled: !!runId,
    refetchInterval: (q) => {
      const d = q.state.data
      if (!d) return false
      return d.status === 'running' || d.status === 'stopping' ? 2500 : false
    },
  })

  const [resultsPage] = useState(1)
  const resultsPageSize = 50
  const resultsQuery = useQuery({
    queryKey: ['eval-results', runId, resultsPage],
    queryFn: () =>
      evaluationApi
        .getResults(runId!, { page: resultsPage, page_size: resultsPageSize })
        .then(r => r.data),
    enabled: !!runId,
    refetchInterval: () => (runQuery.data?.status === 'running' ? 5000 : false),
  })

  const stopMutation = useMutation({
    mutationFn: () => evaluationApi.stopRun(runId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['eval-run', runId] }),
  })

  const [projectInput, setProjectInput] = useState('')
  const [activeProject, setActiveProject] = useState<string | null>(null)

  const backfillMutation = useMutation({
    mutationFn: (project: string) => evaluationApi.backfillTrace(runId!, project).then(r => r.data),
    onSuccess: (data) => {
      setActiveProject(data.project)
      qc.invalidateQueries({ queryKey: ['eval-results', runId] })
      qc.invalidateQueries({ queryKey: ['eval-run', runId] })
    },
  })

  const langfusePullMutation = useMutation({
    mutationFn: () => evaluationApi
      .syncLangfuseScores(runId!, { push: false, pull_attempts: 1 })
      .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-results', runId] })
      qc.invalidateQueries({ queryKey: ['eval-run', runId] })
    },
  })

  const reaggregateMutation = useMutation({
    mutationFn: () => evaluationApi.reaggregateRun(runId!).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-run', runId] })
    },
  })

  const run = runQuery.data
  const langfuseHost = deriveLangfuseHost(run)

  const [selectedRowId, setSelectedRowId] = useState<string | null>(null)

  useEffect(() => {
    if (!run) return
    if (activeProject !== null) return
    const initial = run.langsmith_project || ''
    if (initial) {
      setProjectInput(initial)
      setActiveProject(initial)
    }
  }, [run, activeProject])

  if (!runId) return null
  if (runQuery.isLoading) return <div className="empty-state">加载中…</div>
  if (runQuery.isError || !run) {
    return (
      <div className="text-[12px] text-negative">
        加载失败。<Link to="/evaluation" className="text-accent hover:underline">返回列表</Link>
      </div>
    )
  }

  const counts = run.summary_scores?.counts ?? {}
  const dimAvg = run.summary_scores?.dimension_averages ?? {}
  const costSuccess = run.summary_scores?.cost_success ?? {}
  const costFailure = run.summary_scores?.cost_failure ?? {}
  const toolUsage = (run.summary_scores?.tool_usage ?? []) as Array<{
    name: string; calls: number; errors: number; cases: number
  }>
  const scoreDistribution = (run.summary_scores?.score_distribution ?? null) as null | {
    buckets: string[]; by_dimension: Record<string, number[]>
  }
  const items = resultsQuery.data?.items ?? []
  const latencyBars = buildLatencyBuckets(items)
  const radarData = buildRadarData(dimAvg)
  const selectedRow = selectedRowId
    ? items.find((r: EvalResultRow) => r.id === selectedRowId) ?? null
    : null

  return (
    <div>
      <Link to="/evaluation" className="back-link mb-2">
        ← 评估列表
      </Link>
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <div className="page-eyebrow">评估</div>
          <h1 className="page-title">
            Run <span className="font-mono text-[18px]">{run.id.slice(0, 8)}</span>
          </h1>
          <p className="page-subtitle">{run.langfuse_run_name ?? '—'}</p>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <RunStatusBadge status={run.status} />
          {(run.status === 'running' || run.status === 'stopping') && (
            <Button
              variant="secondary"
              size="sm"
              loading={stopMutation.isPending}
              disabled={run.status === 'stopping'}
              onClick={() => stopMutation.mutate()}
            >
              {run.status === 'stopping' ? '停止中…' : '停止'}
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={() => navigate(`/evaluation/compare?ids=${runId}`)}>
            加入对比
          </Button>
          <Button
            variant="secondary"
            size="sm"
            loading={langfusePullMutation.isPending}
            onClick={() => langfusePullMutation.mutate()}
            title="向 Langfuse 拉一次 observation 级评估器分数"
          >
            重拉 Langfuse 分数
          </Button>
          <Button
            variant="secondary"
            size="sm"
            loading={reaggregateMutation.isPending}
            onClick={() => reaggregateMutation.mutate()}
            title="从样例分数重新计算维度平均、工具调用统计、分数分布"
          >
            重算汇总
          </Button>
        </div>
      </header>

      {langfusePullMutation.data && (
        <div className="mb-3 text-[12px] text-text-secondary border border-border bg-fill/5 rounded-md px-3 py-2">
          已从 Langfuse 拉回 <span className="font-mono">{langfusePullMutation.data.pull.pulled}</span> 条新分数
          （poll {langfusePullMutation.data.pull.polls} 次）。如果是 0，可能 Langfuse 评估器还没算完，等几十秒后再点一次。
        </div>
      )}
      {langfusePullMutation.isError && (
        <div className="mb-3 text-[12px] text-negative border border-negative/30 bg-negative/5 rounded-md px-3 py-2">
          拉取失败：{(langfusePullMutation.error as { response?: { data?: { detail?: string } } })
            ?.response?.data?.detail || (langfusePullMutation.error as Error)?.message || 'unknown'}
        </div>
      )}

      {reaggregateMutation.data && (
        <div className="mb-3 text-[12px] text-text-secondary border border-border bg-fill/5 rounded-md px-3 py-2">
          已重算：{reaggregateMutation.data.case_count} 条样例，
          维度 {reaggregateMutation.data.dimensions.length} 个
          ({reaggregateMutation.data.dimensions.join(', ') || '无'})，
          工具 {reaggregateMutation.data.tool_usage_count} 种
        </div>
      )}
      {reaggregateMutation.isError && (
        <div className="mb-3 text-[12px] text-negative border border-negative/30 bg-negative/5 rounded-md px-3 py-2">
          重算失败：{(reaggregateMutation.error as { response?: { data?: { detail?: string } } })
            ?.response?.data?.detail || (reaggregateMutation.error as Error)?.message || 'unknown'}
        </div>
      )}

      {run.summary_scores?.runtime_error && (
        <section className="mb-5 border border-warning/30 bg-warning/10 rounded-lg px-4 py-3">
          <div className="flex items-start gap-2">
            <span className="text-warning text-[14px] mt-0.5">⚠</span>
            <div className="flex-1">
              <div className="text-[12px] font-medium text-text-primary mb-1">Agent 不可达</div>
              <div className="text-[11px] text-text-secondary leading-relaxed">
                {run.summary_scores.runtime_error}
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="grid grid-cols-4 gap-3 mb-5">
        <MetaCard label="总数" value={counts.total ?? run.progress.total ?? '—'} />
        <MetaCard label="通过" value={counts.passed ?? 0} hint="pass (所有指标≥0.5)" />
        <MetaCard label="失败" value={counts.failed ?? 0} hint="fail / error" />
        <MetaCard label="启动 → 完成" value={fmtDuration(run.started_at, run.finished_at)} />
      </section>

      <section className="card p-4 mb-5">
        <h3 className="page-eyebrow mb-2">Agent 配置</h3>
        <div className="grid grid-cols-3 gap-3 text-[12px]">
          <KV k="Type" v={(run.agent_config as { type?: string }).type ?? '—'} />
          <KV k="Model" v={(run.agent_config as { model?: string }).model ?? '—'} />
          <KV k="URL" v={(run.agent_config as { url?: string }).url ?? '—'} mono />
        </div>
        <details className="mt-3">
          <summary className="text-[11px] text-text-secondary cursor-pointer">原始配置 / evaluators</summary>
          <div className="grid grid-cols-2 gap-3 mt-2">
            <JsonBlock label="agent_config" data={run.agent_config} />
            <JsonBlock label="evaluator_configs" data={run.evaluator_configs} />
          </div>
        </details>
      </section>

      <section className="card p-4 mb-5">
        <h3 className="page-eyebrow mb-2">调用轨迹（LangSmith Project）</h3>
        <p className="text-[12px] text-text-secondary mb-3">
          输入要溯源的 LangSmith project 名称，平台会按 (project, 时间窗口, 问题文本) 反查并把每条样例对应的 run 写回。
          当前已绑定：<span className="font-mono">{activeProject || '（未绑定）'}</span>
        </p>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={projectInput}
            onChange={e => setProjectInput(e.target.value)}
            placeholder="例如 ruyi-agent"
            className="input max-w-[360px] font-mono"
          />
          <Button
            variant="primary"
            size="sm"
            disabled={!projectInput.trim()}
            loading={backfillMutation.isPending}
            onClick={() => projectInput.trim() && backfillMutation.mutate(projectInput.trim())}
          >
            查询轨迹
          </Button>
        </div>
        {backfillMutation.data && (() => {
          const d = backfillMutation.data
          if (d.matched > 0) {
            return (
              <div className="mt-2 text-[11px] text-positive">
                匹配 <span className="font-mono">{d.matched}</span> /{' '}
                <span className="font-mono">{d.scanned}</span> 条样例。展开下方任一样例查看 trace。
              </div>
            )
          }
          if (d.error_kind) {
            const kindMsg: Record<string, string> = {
              forbidden: 'LangSmith API key 对此 project 没有读权限（403）。请换一把有 read 权限的 key，或确认 project 归属。',
              unauthorized: 'LangSmith API key 无效（401）。请检查后端 LANGSMITH_API_KEY 配置。',
              not_found: `LangSmith 上找不到名为 "${d.project}" 的 project（404）。请检查拼写。`,
              network: 'LangSmith API 网络不可达（连接超时 / DNS 失败）。请检查后端的网络出口。',
              client_init: 'LangSmith 客户端未初始化。后端可能未配置 LANGSMITH_API_KEY。',
              unknown: 'LangSmith API 返回未知错误。',
            }
            return (
              <div className="mt-2 text-[11px] text-negative">
                <div>查询失败 · {kindMsg[d.error_kind] || kindMsg.unknown}</div>
                {d.error_message && (
                  <div className="mt-1 font-mono text-[10px] text-text-tertiary break-all">
                    详情：{d.error_message}
                  </div>
                )}
                <div className="mt-1 text-text-secondary">
                  本次扫描了 {d.scanned} 条样例，{d.errors} 次请求失败。
                </div>
              </div>
            )
          }
          return (
            <div className="mt-2 text-[11px] text-warning">
              匹配 0 / {d.scanned} 条样例。LangSmith 能查通，但 project「{d.project}」
              里没有时间窗口内、问题文本一致的 root run。请检查 project 名称是否正确，
              或样例发起时间是否在 LangSmith 数据保留期内。
            </div>
          )
        })()}
        {backfillMutation.isError && (
          <div className="mt-2 text-[11px] text-negative">
            {(backfillMutation.error as { response?: { data?: { detail?: string } } })
              ?.response?.data?.detail || '查询失败'}
          </div>
        )}
      </section>

      {Object.keys(dimAvg).length > 0 && (
        <section className="card p-4 mb-5">
          <h3 className="page-eyebrow mb-3">维度平均分（0-1）</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {Object.entries(dimAvg).map(([name, val]) => {
              const meta = getScoreMeta(name)
              const passing = isPassing(name, val)
              const pct = Math.max(0, Math.min(1, val)) * 100
              const threshPct = Math.max(0, Math.min(1, meta.threshold)) * 100
              return (
                <div key={name} title={meta.description}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] text-text-secondary">{meta.label}</span>
                    <span className={`text-[10px] tracking-[0.1em] uppercase ${
                      meta.direction === 'higher_better' ? 'text-text-tertiary' : 'text-warning'
                    }`}>
                      {directionMark(meta)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 relative h-2 bg-fill/10 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          passing ? 'bg-positive' : 'bg-negative'
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                      <div
                        className="absolute top-0 bottom-0 w-px bg-text-tertiary/70"
                        style={{ left: `${threshPct}%` }}
                        title={`合格线 ${meta.threshold}`}
                      />
                    </div>
                    <span className={`font-mono text-[12px] min-w-[40px] text-right ${
                      passing ? 'text-positive' : 'text-negative'
                    }`}>
                      {val.toFixed(2)}
                    </span>
                  </div>
                  <div className="text-[10px] text-text-tertiary mt-0.5">
                    合格线 {meta.threshold} · {passing ? '达标' : '未达标'}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {latencyBars.length > 0 && (
        <section className="card p-4 mb-5">
          <h3 className="page-eyebrow mb-3">延迟分布（按样例 · ms）</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={latencyBars} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--separator) / 0.3)" />
              <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={0} />
              <YAxis tick={{ fontSize: 10 }} label={{ value: 'count', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
              <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                {latencyBars.map((_, i) => {
                  const palette = [
                    'rgb(var(--positive))',
                    'rgb(var(--accent))',
                    'rgb(var(--info))',
                    'rgb(var(--accent-hover))',
                    'rgb(var(--warning))',
                    'rgb(var(--negative))',
                  ]
                  return <Cell key={i} fill={palette[i] || 'rgb(var(--accent))'} />
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </section>
      )}

      <ReportSection
        dimAvg={dimAvg}
        radarData={radarData}
        scoreDistribution={scoreDistribution}
        toolUsage={toolUsage}
        counts={counts}
      />

      <section className="grid grid-cols-2 gap-3 mb-5">
        <CostCard title="成功样例的成本" data={costSuccess} />
        <CostCard title="失败样例的成本" data={costFailure} />
      </section>

      <RetryStatsCard stats={run.summary_scores?.retry_stats} />

      <section>
        <div className="section-row">
          <div className="page-eyebrow">样例结果 · 共 {resultsQuery.data?.total ?? 0} 条</div>
          {langfuseHost && run.summary_scores?.langfuse_dataset && (
            <a
              href={`${langfuseHost}/datasets`}
              target="_blank" rel="noreferrer"
              className="text-[11px] text-accent hover:text-accent-hover transition-colors"
            >
              Langfuse 界面 ↗
            </a>
          )}
        </div>
        <div className="table-card">
          <table className="table-base">
            <thead>
              <tr>
                <th>样例</th>
                <th>问题</th>
                <th className="w-28">状态</th>
                <th className="w-20">时延</th>
                <th className="w-24">输入 token</th>
                <th className="w-24">输出 token</th>
                <th className="w-24">缓存命中</th>
                <th className="w-16">工具</th>
                <th className="w-16">重试</th>
                <th>分数</th>
                <th className="w-24">追踪</th>
              </tr>
            </thead>
            <tbody>
              {resultsQuery.isLoading && (
                <tr><td colSpan={11} className="empty-state">加载中…</td></tr>
              )}
              {items.map((r: EvalResultRow) => (
                <ResultRow
                  key={r.id}
                  row={r}
                  langfuseHost={langfuseHost}
                  selected={r.id === selectedRowId}
                  onSelect={() => setSelectedRowId(r.id)}
                />
              ))}
              {items.length === 0 && !resultsQuery.isLoading && (
                <tr><td colSpan={11} className="empty-state">
                  {run.status === 'running' ? '还没产出样例结果…' : '没有样例结果'}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <Drawer
        open={!!selectedRow}
        onClose={() => setSelectedRowId(null)}
        width="wide"
        title={selectedRow ? (selectedRow.question || '样例详情') : '样例详情'}
        subtitle={
          selectedRow
            ? `样例 ${selectedRow.benchmark_case_id?.slice(0, 8) ?? selectedRow.id.slice(0, 8)}`
            : undefined
        }
      >
        {selectedRow && (
          <ResultDetailPanel
            row={selectedRow}
            langfuseHost={langfuseHost}
            project={activeProject}
          />
        )}
      </Drawer>
    </div>
  )
}


function ResultRow({ row, langfuseHost, selected, onSelect }: {
  row: EvalResultRow
  langfuseHost: string | null
  selected: boolean
  onSelect: () => void
}) {
  const scoreEntries = Object.entries(row.scores)

  return (
    <tr
      onClick={onSelect}
      className={`cursor-pointer ${selected ? 'bg-accent/5' : ''}`}
    >
      <td className="font-mono text-[11px]">{row.benchmark_case_id?.slice(0, 8) ?? row.id.slice(0, 8)}</td>
      <td>
        <div className="max-w-[260px] truncate" title={row.question || ''}>
          {row.question || '—'}
        </div>
      </td>
      <td><RunStatusBadge status={row.status} /></td>
      <td className="tabular-nums">{row.latency_ms != null ? `${row.latency_ms}ms` : '—'}</td>
      <td className="tabular-nums">{row.prompt_tokens ?? '—'}</td>
      <td className="tabular-nums">{row.completion_tokens ?? '—'}</td>
      <td className="tabular-nums">
        {row.cache_read_tokens != null
          ? <span title={`命中: ${row.cache_read_tokens}, 创建: ${row.cache_creation_tokens ?? 0}`}>
              {row.cache_read_tokens}
              {row.cache_creation_tokens != null && row.cache_creation_tokens > 0 && (
                <span className="text-text-tertiary ml-1">/+{row.cache_creation_tokens}</span>
              )}
            </span>
          : '—'}
      </td>
      <td className="tabular-nums">{row.tool_call_count ?? 0}</td>
      <td>
        {row.attempts_made && row.attempts_made > 1
          ? <span className="text-warning" title={`实际尝试 ${row.attempts_made} 次（含重试）`}>
              {row.attempts_made}×
            </span>
          : <span className="text-text-tertiary">1</span>}
      </td>
      <td>
        <div className="flex flex-wrap gap-1">
          {scoreEntries.length === 0 && <span className="text-text-tertiary">—</span>}
          {scoreEntries.map(([n, v]) => {
            const meta = getScoreMeta(n)
            const t = tone(n, v)
            const cls = t === 'good' ? 'badge badge-positive' : 'badge badge-negative'
            return (
              <span
                key={n}
                className={cls}
                title={`${meta.label} · ${directionMark(meta)} · 合格线 ${meta.threshold}\n${meta.description}`}
              >
                {meta.label}: {v.toFixed(2)}
              </span>
            )
          })}
        </div>
      </td>
      <td>
        {row.langsmith_run_id ? (
          <span className="text-[11px] font-mono text-accent">{row.langsmith_run_id.slice(0, 8)}</span>
        ) : row.langfuse_trace_id && langfuseHost ? (
          <a
            href={`${langfuseHost}/trace/${row.langfuse_trace_id}`}
            target="_blank" rel="noreferrer"
            onClick={e => e.stopPropagation()}
            className="text-[11px] text-accent hover:text-accent-hover font-mono transition-colors"
          >
            {row.langfuse_trace_id.slice(0, 8)} ↗
          </a>
        ) : '—'}
      </td>
    </tr>
  )
}

function ResultDetailPanel({ row, langfuseHost, project }: {
  row: EvalResultRow
  langfuseHost: string | null
  project: string | null
}) {
  const [nodeCache, setNodeCache] = useState<NodeCache>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const nodeCacheRef = useRef(nodeCache)
  nodeCacheRef.current = nodeCache

  const traceQuery = useQuery({
    queryKey: ['eval-result-trace', row.id, project ?? ''],
    queryFn: () => evaluationApi.getResultTrace(row.id, project || undefined).then(r => r.data),
    enabled: !!row.langsmith_run_id,
    retry: false,
  })

  const fetchChild = useCallback(async (childId: string) => {
    setNodeCache(prev => (prev[childId]?.data || prev[childId]?.loading)
      ? prev : { ...prev, [childId]: { loading: true } })
    try {
      const res = await tracesApi.getDetail({
        run_id: childId,
        project_name: project || undefined,
      })
      setNodeCache(prev => ({ ...prev, [childId]: { loading: false, data: res.data } }))
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setNodeCache(prev => ({ ...prev, [childId]: { loading: false, error: msg || '加载失败' } }))
    }
  }, [project])

  const toggleExpand = useCallback((childId: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(childId)) next.delete(childId)
      else {
        next.add(childId)
        const cached = nodeCacheRef.current[childId]
        if (!cached?.data && !cached?.loading) fetchChild(childId)
      }
      return next
    })
  }, [fetchChild])

  const root: RunDetail | undefined = traceQuery.data
  const scoreEntries = Object.entries(row.scores)

  return (
    <div className="text-[11px]">
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div>
          <div className="field-label">状态</div>
          <RunStatusBadge status={row.status} />
        </div>
        <div>
          <div className="field-label">时延</div>
          <div className="font-mono text-[12px]">{row.latency_ms != null ? `${row.latency_ms}ms` : '—'}</div>
        </div>
        <div>
          <div className="field-label">Tokens (in / out)</div>
          <div className="font-mono text-[12px]">
            {row.prompt_tokens ?? '—'} / {row.completion_tokens ?? '—'}
          </div>
        </div>
      </div>

      {scoreEntries.length > 0 && (
        <div className="mb-4">
          <div className="field-label">评分</div>
          <div className="flex flex-wrap gap-1">
            {scoreEntries.map(([n, v]) => {
              const meta = getScoreMeta(n)
              const t = tone(n, v)
              const cls = t === 'good' ? 'badge badge-positive' : 'badge badge-negative'
              return (
                <span
                  key={n}
                  className={cls}
                  title={`${meta.label} · ${directionMark(meta)} · 合格线 ${meta.threshold}\n${meta.description}`}
                >
                  {meta.label}: {v.toFixed(2)}
                </span>
              )
            })}
          </div>
        </div>
      )}

      <div className="mb-3">
        <div className="field-label">输出</div>
        <pre className="font-mono text-[11px] bg-fill/5 border border-border rounded-md p-2.5 max-h-[240px] overflow-y-auto whitespace-pre-wrap">
          {row.actual_output || '（无输出）'}
        </pre>
      </div>

      {row.error_message && (
        <div className="mb-3">
          <div className="field-label text-negative">错误</div>
          <pre className="font-mono text-[11px] bg-negative/5 border border-negative/30 rounded-md p-2.5 whitespace-pre-wrap">
            {row.error_message}
          </pre>
        </div>
      )}

      {Array.isArray(row.full_trace?.steps) && row.full_trace!.steps!.length > 0 && (
        <div className="mb-3">
          <div className="field-label">思维链 ({row.full_trace!.steps!.length} 步)</div>
          <CotTimeline steps={row.full_trace!.steps!} />
        </div>
      )}

      {Array.isArray(row.actual_tool_calls) && row.actual_tool_calls.length > 0 && (
        <div className="mb-3">
          <div className="field-label">工具调用 ({row.actual_tool_calls.length})</div>
          <ToolCallsTable calls={row.actual_tool_calls as Array<Record<string, unknown>>} />
        </div>
      )}

      {row.langfuse_trace_id && langfuseHost && (
        <div className="mb-3">
          <a
            href={`${langfuseHost}/trace/${row.langfuse_trace_id}`}
            target="_blank" rel="noreferrer"
            className="text-[11px] text-accent hover:text-accent-hover font-mono transition-colors"
          >
            在 Langfuse 中查看 trace ↗
          </a>
        </div>
      )}

      <div>
        <div className="field-label">LangSmith 追踪</div>
        {!row.langsmith_run_id && (
          <div className="text-[11px] text-text-tertiary border border-dashed border-border rounded-md px-3 py-4 text-center">
            {project
              ? `暂未在 project «${project}» 找到对应 run。点击页面顶部"查询轨迹"重试，或换一个 project。`
              : '请在页面顶部输入 LangSmith project 名称并点击"查询轨迹"，平台会按时间窗口和问题文本反查对应 run。'}
          </div>
        )}
        {row.langsmith_run_id && traceQuery.isLoading && (
          <div className="text-[11px] text-text-tertiary px-3 py-4">加载中…</div>
        )}
        {row.langsmith_run_id && traceQuery.isError && (
          <div className="text-[11px] text-negative px-3 py-2">
            加载 trace 失败：{(traceQuery.error as Error)?.message || 'unknown'}
          </div>
        )}
        {root && (
          <div className="card p-3">
            <RunDetailBody detail={root} compact />
            {root.children.length > 0 && (
              <div className="mt-3">
                <div className="field-label">
                  Children ({root.children.length})
                  {root.children_truncated && <span className="ml-2 text-warning">已截断</span>}
                </div>
                <div className="border border-border rounded-md bg-surface">
                  {root.children.map(c => (
                    <RunNodeRow
                      key={c.id}
                      meta={c}
                      depth={0}
                      projectName={project || ''}
                      isOpen={expanded.has(c.id)}
                      state={nodeCache[c.id]}
                      nodeCache={nodeCache}
                      expanded={expanded}
                      onToggle={toggleExpand}
                      onRetry={fetchChild}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function MetaCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-eyebrow">{label}</div>
      <div className="metric-value">{value}</div>
      {hint && <div className="text-[10px] text-text-tertiary mt-0.5">{hint}</div>}
    </div>
  )
}

function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div>
      <div className="field-label">{k}</div>
      <div className={mono ? 'font-mono text-[11px] break-all' : ''}>{v}</div>
    </div>
  )
}

function JsonBlock({ label, data }: { label: string; data: unknown }) {
  return (
    <div>
      <div className="field-label">{label}</div>
      <pre className="font-mono text-[10px] bg-fill/5 border border-border rounded-md p-2.5 max-h-[240px] overflow-y-auto whitespace-pre-wrap break-all">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  )
}

function CostCard({ title, data }: { title: string; data: Record<string, number | null> }) {
  const rows: { k: string; label: string; fmt?: (v: number) => string }[] = [
    { k: 'count', label: 'Count' },
    { k: 'avg_prompt_tokens', label: 'Prompt tokens' },
    { k: 'avg_completion_tokens', label: 'Completion tokens' },
    { k: 'avg_total_tokens', label: 'Total tokens' },
    { k: 'avg_tool_calls', label: 'Tool calls' },
    { k: 'avg_messages', label: 'Messages' },
    { k: 'avg_latency_ms', label: 'Latency (ms)', fmt: (v) => `${Math.round(v)}ms` },
    { k: 'avg_first_thinking_token_ms', label: '首思考 token (ms)', fmt: (v) => `${Math.round(v)}ms` },
    { k: 'avg_first_answer_token_ms', label: '首答 token (ms)', fmt: (v) => `${Math.round(v)}ms` },
    { k: 'cache_hit_rate', label: 'Cache hit rate', fmt: (v) => `${(v * 100).toFixed(1)}%` },
  ]
  return (
    <div className="card p-4">
      <h3 className="page-eyebrow mb-2">{title}</h3>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
        {rows.map(row => {
          const v = data?.[row.k]
          return (
            <div key={row.k} className="flex justify-between border-b border-separator pb-1">
              <span className="text-text-tertiary">{row.label}</span>
              <span className="font-mono text-text-primary">
                {v == null ? '—' : (row.fmt ? row.fmt(v) : String(v))}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function RetryStatsCard({
  stats,
}: {
  stats?: {
    total_cases?: number
    cases_with_retries?: number
    max_attempts?: number
    avg_attempts?: number
    total_retries?: number
  }
}) {
  if (!stats || !stats.total_cases || (stats.cases_with_retries ?? 0) === 0) return null
  const ratio = stats.total_cases ? (stats.cases_with_retries ?? 0) / stats.total_cases : 0
  return (
    <section className="card p-4 mb-5">
      <h3 className="page-eyebrow mb-2">重试情况</h3>
      <div className="grid grid-cols-4 gap-4 text-[11px]">
        <Metric label="重试样例" value={`${stats.cases_with_retries} / ${stats.total_cases}`} hint={`${(ratio * 100).toFixed(1)}%`} />
        <Metric label="总重试次数" value={String(stats.total_retries ?? 0)} />
        <Metric label="平均尝试次数" value={(stats.avg_attempts ?? 1).toFixed(2)} />
        <Metric label="最大尝试次数" value={String(stats.max_attempts ?? 1)} />
      </div>
    </section>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col">
      <span className="field-label">{label}</span>
      <span className="font-mono text-[14px] text-text-primary mt-0.5">{value}</span>
      {hint && <span className="font-mono text-[10px] text-text-tertiary">{hint}</span>}
    </div>
  )
}

function RunStatusBadge({ status }: { status: string }) {
  const tone: Record<string, string> = {
    running: 'badge badge-info',
    completed: 'badge badge-positive',
    failed: 'badge badge-negative',
    stopping: 'badge badge-warning',
    interrupted: 'badge badge-neutral',
    pending: 'badge badge-neutral',
    pass: 'badge badge-positive',
    fail: 'badge badge-negative',
    error: 'badge badge-negative',
    agent_unreachable: 'badge badge-warning',
    agent_timeout: 'badge badge-warning',
  }
  const labels: Record<string, string> = {
    agent_unreachable: 'agent unreachable',
    agent_timeout: 'agent timeout',
  }
  const cls = tone[status] ?? 'badge badge-neutral'
  const label = labels[status] ?? status
  return (
    <span className={cls}>
      {status === 'running' && (
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      )}
      {label}
    </span>
  )
}

function fmtDuration(start: string | null, end: string | null): string {
  if (!start) return '—'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  const ms = Math.max(0, e - s)
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const r = sec % 60
  return `${m}m${r}s`
}

function deriveLangfuseHost(run: EvalRunDetail | null | undefined): string | null {
  if (!run) return null
  const h = (run.summary_scores as { langfuse_host?: string } | null | undefined)?.langfuse_host
  return h ? h.replace(/\/+$/, '') : null
}

function ReportSection({
  dimAvg, radarData, scoreDistribution, toolUsage, counts,
}: {
  dimAvg: Record<string, number>
  radarData: Array<{ dimension: string; score: number; fullMark: number }>
  scoreDistribution: { buckets: string[]; by_dimension: Record<string, number[]> } | null
  toolUsage: Array<{ name: string; calls: number; errors: number; cases: number }>
  counts: Record<string, number>
}) {
  const hasDims = Object.keys(dimAvg).length > 0
  const hasTools = toolUsage.length > 0
  if (!hasDims && !hasTools) return null

  const passRate = counts.total
    ? ((counts.passed ?? 0) / counts.total * 100).toFixed(1)
    : '—'

  return (
    <section className="card p-4 mb-5">
      <h3 className="page-eyebrow mb-4">综合报告</h3>

      <div className="flex items-center gap-4 mb-5 pb-4 border-b border-separator">
        <div className="text-center">
          <div className="text-[28px] font-display font-semibold tracking-[-0.5px] tabular-nums">{passRate}%</div>
          <div className="text-[10px] text-text-tertiary">合格率</div>
        </div>
        <div className="flex-1 grid grid-cols-3 gap-2 text-center text-[11px]">
          <div>
            <div className="font-mono text-[14px]">{counts.total ?? 0}</div>
            <div className="text-text-tertiary">总样例</div>
          </div>
          <div>
            <div className="font-mono text-[14px] text-positive">{counts.passed ?? 0}</div>
            <div className="text-text-tertiary">通过</div>
          </div>
          <div>
            <div className="font-mono text-[14px] text-negative">{counts.failed ?? 0}</div>
            <div className="text-text-tertiary">失败</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {radarData.length >= 3 && (
          <div>
            <div className="field-label">维度雷达图</div>
            <ResponsiveContainer width="100%" height={240}>
              <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="70%">
                <PolarGrid stroke="rgb(var(--separator) / 0.3)" />
                <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 10 }} />
                <PolarRadiusAxis angle={90} domain={[0, 1]} tick={{ fontSize: 9 }} tickCount={6} />
                <Radar name="得分" dataKey="score" stroke="rgb(var(--accent))" fill="rgb(var(--accent))" fillOpacity={0.25} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        )}

        {scoreDistribution && Object.keys(scoreDistribution.by_dimension).length > 0 && (
          <div>
            <div className="field-label">分数分布</div>
            <div className="space-y-3 max-h-[240px] overflow-y-auto">
              {Object.entries(scoreDistribution.by_dimension).map(([dim, bucketCounts]) => {
                const meta = getScoreMeta(dim)
                const max = Math.max(...bucketCounts, 1)
                return (
                  <div key={dim}>
                    <div className="text-[10px] text-text-secondary mb-1">{meta.label}</div>
                    <div className="flex items-end gap-0.5 h-[32px]">
                      {bucketCounts.map((c, i) => (
                        <div
                          key={i}
                          className="flex-1 bg-accent/70 rounded-t-sm transition-all"
                          style={{ height: `${(c / max) * 100}%`, minHeight: c > 0 ? 2 : 0 }}
                          title={`${scoreDistribution.buckets[i]}: ${c} 条`}
                        />
                      ))}
                    </div>
                    <div className="flex justify-between text-[8px] text-text-tertiary mt-0.5">
                      <span>0</span><span>1</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {hasTools && (
          <div className={radarData.length < 3 && !scoreDistribution ? 'md:col-span-2' : ''}>
            <div className="field-label">工具调用统计 (Top {Math.min(toolUsage.length, 10)})</div>
            <ResponsiveContainer width="100%" height={Math.min(toolUsage.length, 10) * 28 + 30}>
              <BarChart
                data={toolUsage.slice(0, 10)}
                layout="vertical"
                margin={{ top: 5, right: 30, left: 80, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--separator) / 0.3)" />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={75} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
                <Bar dataKey="calls" name="调用次数" fill="rgb(var(--accent))" radius={[0, 3, 3, 0]} />
                <Bar dataKey="errors" name="失败次数" fill="rgb(var(--negative))" radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <div className="mt-2 text-[10px] text-text-tertiary">
              共 {toolUsage.reduce((s, t) => s + t.calls, 0)} 次调用，
              {toolUsage.reduce((s, t) => s + t.errors, 0)} 次失败，
              涉及 {toolUsage.length} 种工具
            </div>
          </div>
        )}
      </div>
    </section>
  )
}


function CotTimeline({ steps }: { steps: CotStep[] }) {
  return (
    <div className="border border-border rounded-md bg-surface overflow-hidden">
      {steps.map((step, i) => (
        <CotStepRow key={i} step={step} index={i} last={i === steps.length - 1} />
      ))}
    </div>
  )
}

function CotStepRow({ step, index, last }: { step: CotStep; index: number; last: boolean }) {
  const [open, setOpen] = useState(step.type !== 'thought')
  const dur = step.duration_ms != null ? `${step.duration_ms}ms` : null
  const border = last ? '' : 'border-b border-separator'

  if (step.type === 'thought' || step.type === 'answer') {
    const isAnswer = step.type === 'answer'
    const tagCls = isAnswer ? 'badge badge-positive' : 'badge badge-neutral'
    const tagLabel = isAnswer ? '答复' : '思考'
    const text = step.content || ''
    const long = text.length > 200
    const preview = long && !open ? `${text.slice(0, 200)}…` : text
    return (
      <div className={`px-3 py-2 ${border} ${isAnswer ? 'bg-positive/5' : ''}`}>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] text-text-tertiary tabular-nums w-5 text-right">{index + 1}</span>
          <span className={tagCls}>{tagLabel}</span>
          {dur && <span className="text-[10px] text-text-tertiary tabular-nums">{dur}</span>}
          {long && (
            <button
              type="button"
              onClick={() => setOpen(o => !o)}
              className="ml-auto text-[10px] text-accent hover:text-accent-hover transition-colors"
            >
              {open ? '收起' : '展开'}
            </button>
          )}
        </div>
        <pre className="font-mono text-[11px] whitespace-pre-wrap text-text-primary">{preview || '（空）'}</pre>
      </div>
    )
  }

  const argsStr =
    step.args == null ? '' : typeof step.args === 'string' ? step.args : JSON.stringify(step.args, null, 2)
  const outStr =
    step.output == null ? '' : typeof step.output === 'string' ? step.output : JSON.stringify(step.output, null, 2)
  return (
    <div className={`px-3 py-2 bg-warning/5 ${border}`}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[10px] text-text-tertiary tabular-nums w-5 text-right">{index + 1}</span>
        <span className="badge badge-warning">工具</span>
        <span className="text-[11px] font-mono">{step.tool_name || '?'}</span>
        {dur && <span className="text-[10px] text-text-tertiary tabular-nums">{dur}</span>}
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          className="ml-auto text-[10px] text-accent hover:text-accent-hover transition-colors"
        >
          {open ? '收起' : '展开'}
        </button>
      </div>
      {open && (
        <div className="space-y-1.5 pl-7">
          {argsStr && (
            <div>
              <div className="text-[10px] text-text-tertiary mb-0.5">参数</div>
              <pre className="font-mono text-[10px] bg-surface border border-border rounded-md p-1.5 max-h-[140px] overflow-auto whitespace-pre-wrap">{argsStr}</pre>
            </div>
          )}
          {outStr && (
            <div>
              <div className="text-[10px] text-text-tertiary mb-0.5">输出</div>
              <pre className="font-mono text-[10px] bg-surface border border-border rounded-md p-1.5 max-h-[160px] overflow-auto whitespace-pre-wrap">{outStr}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}


function ToolCallsTable({ calls }: { calls: Array<Record<string, unknown>> }) {
  const grouped: Record<string, { count: number; errors: number }> = {}
  for (const c of calls) {
    const name = (c.tool_name || c.name || 'unknown') as string
    const slot = grouped[name] ?? (grouped[name] = { count: 0, errors: 0 })
    slot.count++
    const out = c.output
    if (typeof out === 'object' && out && (('error' in out) || ('isError' in out))) {
      slot.errors++
    } else if (typeof out === 'string' && out.toLowerCase().startsWith('error')) {
      slot.errors++
    }
  }
  const entries = Object.entries(grouped).sort((a, b) => b[1].count - a[1].count)

  return (
    <div className="table-card">
      <table className="table-base">
        <thead>
          <tr>
            <th>工具</th>
            <th className="text-right w-20">次数</th>
            <th className="text-right w-20">失败</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, { count, errors }]) => (
            <tr key={name}>
              <td className="font-mono text-[11px]">{name}</td>
              <td className="text-right tabular-nums">{count}</td>
              <td className={`text-right tabular-nums ${errors > 0 ? 'text-negative' : ''}`}>
                {errors || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {calls.length > 5 && (
        <details className="px-2 py-1 border-t border-separator">
          <summary className="text-[10px] text-text-tertiary cursor-pointer">展开全部调用详情</summary>
          <div className="mt-1 max-h-[200px] overflow-y-auto">
            {calls.map((c, i) => (
              <div key={i} className="flex gap-2 py-0.5 border-b border-separator last:border-0">
                <span className="text-[10px] text-text-tertiary w-4 text-right">{i + 1}</span>
                <span className="font-mono text-[10px]">{(c.tool_name || c.name || '?') as string}</span>
                {c.args != null && (
                  <span className="text-[10px] text-text-tertiary truncate max-w-[200px]">
                    {typeof c.args === 'string' ? c.args : JSON.stringify(c.args).slice(0, 80)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}


function buildRadarData(dimAvg: Record<string, number>) {
  return Object.entries(dimAvg).map(([name, val]) => ({
    dimension: getScoreMeta(name).label,
    score: val,
    fullMark: 1,
  }))
}


function buildLatencyBuckets(items: EvalResultRow[]): Array<{ label: string; count: number }> {
  const buckets = [
    { label: '<1s', max: 1000, count: 0 },
    { label: '1-3s', max: 3000, count: 0 },
    { label: '3-5s', max: 5000, count: 0 },
    { label: '5-10s', max: 10000, count: 0 },
    { label: '10-30s', max: 30000, count: 0 },
    { label: '>30s', max: Infinity, count: 0 },
  ]
  let any = false
  for (const r of items) {
    if (r.latency_ms == null) continue
    any = true
    for (const b of buckets) {
      if (r.latency_ms < b.max) { b.count++; break }
    }
  }
  return any ? buckets : []
}
