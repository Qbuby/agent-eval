import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Legend,
} from 'recharts'
import { evaluationApi, tracesApi } from '@/services'
import type { EvalResultRow, EvalRunDetail, RunDetail } from '@/types'
import { RunNodeRow, RunDetailBody, type NodeCache } from '@/components/RunTreeView'
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

  // ─── Trace lookup project ──────────────────────────────────────────────────
  // Default: project bound at start time. User can override and re-run the
  // backfill against a different project (e.g. fix permission later, or the
  // run actually landed in a different bucket).
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

  // Manual re-pull of Langfuse evaluator scores. Traces are already pushed
  // by the run, so we skip push and only pull. One quick attempt is enough
  // for an on-demand refresh — the user clicks again if Langfuse hasn't
  // finished judging yet.
  const langfusePullMutation = useMutation({
    mutationFn: () => evaluationApi
      .syncLangfuseScores(runId!, { push: false, pull_attempts: 1 })
      .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-results', runId] })
      qc.invalidateQueries({ queryKey: ['eval-run', runId] })
    },
  })

  // Re-aggregate summary_scores from existing per-case scores. Useful for
  // runs that finished before we started writing tool_usage / score_distribution.
  const reaggregateMutation = useMutation({
    mutationFn: () => evaluationApi.reaggregateRun(runId!).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-run', runId] })
    },
  })

  const run = runQuery.data
  const langfuseHost = deriveLangfuseHost(run)

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
  if (runQuery.isLoading) return <div className="text-[12px] text-text-tertiary">加载中…</div>
  if (runQuery.isError || !run) {
    return (
      <div className="text-[12px] text-negative">
        加载失败。<Link to="/evaluation" className="underline">返回列表</Link>
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

  return (
    <div>
      <header className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link to="/evaluation" className="text-[11px] text-text-tertiary hover:text-accent">← 评估列表</Link>
          </div>
          <h1 className="text-lg font-light tracking-tight mb-1">
            Run <span className="font-mono text-[14px]">{run.id.slice(0, 8)}</span>
          </h1>
          <p className="text-[10px] text-text-tertiary tracking-widest uppercase">
            {run.langfuse_run_name ?? '—'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={run.status} />
          {(run.status === 'running' || run.status === 'stopping') && (
            <button
              onClick={() => stopMutation.mutate()}
              disabled={stopMutation.isPending || run.status === 'stopping'}
              className="py-1.5 px-3 text-[11px] rounded-[6px] border border-border text-text-secondary hover:border-negative hover:text-negative disabled:opacity-40 transition-all"
            >
              {run.status === 'stopping' ? '停止中…' : '停止'}
            </button>
          )}
          <button
            onClick={() => navigate(`/evaluation/compare?ids=${runId}`)}
            className="py-1.5 px-3 text-[11px] rounded-[6px] border border-border text-text-secondary hover:border-accent hover:text-accent transition-all"
          >
            加入对比
          </button>
          <button
            onClick={() => langfusePullMutation.mutate()}
            disabled={langfusePullMutation.isPending}
            title="向 Langfuse 拉一次 observation 级评估器分数（已推送的 trace 不再重复推）"
            className="py-1.5 px-3 text-[11px] rounded-[6px] border border-border text-text-secondary hover:border-accent hover:text-accent disabled:opacity-40 transition-all"
          >
            {langfusePullMutation.isPending ? '拉取中…' : '重拉 Langfuse 分数'}
          </button>
          <button
            onClick={() => reaggregateMutation.mutate()}
            disabled={reaggregateMutation.isPending}
            title="从样例分数重新计算维度平均、工具调用统计、分数分布。老 run 跑这一下能补出综合报告区。"
            className="py-1.5 px-3 text-[11px] rounded-[6px] border border-border text-text-secondary hover:border-accent hover:text-accent disabled:opacity-40 transition-all"
          >
            {reaggregateMutation.isPending ? '重算中…' : '重算汇总'}
          </button>
        </div>
      </header>

      {/* Langfuse pull-back result banner */}
      {langfusePullMutation.data && (
        <div className="mb-3 text-[11px] text-text-secondary border border-border bg-accent-subtle/40 rounded-[6px] px-3 py-2">
          已从 Langfuse 拉回 <span className="font-mono">{langfusePullMutation.data.pull.pulled}</span> 条新分数
          （poll {langfusePullMutation.data.pull.polls} 次）。如果是 0，可能 Langfuse 评估器还没算完，等几十秒后再点一次。
        </div>
      )}
      {langfusePullMutation.isError && (
        <div className="mb-3 text-[11px] text-negative border border-red-200 bg-red-50 rounded-[6px] px-3 py-2">
          拉取失败：{(langfusePullMutation.error as { response?: { data?: { detail?: string } } })
            ?.response?.data?.detail || (langfusePullMutation.error as Error)?.message || 'unknown'}
        </div>
      )}

      {/* Reaggregate result banner */}
      {reaggregateMutation.data && (
        <div className="mb-3 text-[11px] text-text-secondary border border-border bg-accent-subtle/40 rounded-[6px] px-3 py-2">
          已重算：{reaggregateMutation.data.case_count} 条样例，
          维度 {reaggregateMutation.data.dimensions.length} 个
          ({reaggregateMutation.data.dimensions.join(', ') || '无'})，
          工具 {reaggregateMutation.data.tool_usage_count} 种
        </div>
      )}
      {reaggregateMutation.isError && (
        <div className="mb-3 text-[11px] text-negative border border-red-200 bg-red-50 rounded-[6px] px-3 py-2">
          重算失败：{(reaggregateMutation.error as { response?: { data?: { detail?: string } } })
            ?.response?.data?.detail || (reaggregateMutation.error as Error)?.message || 'unknown'}
        </div>
      )}

      {/* Runtime error banner — most samples couldn't reach the agent */}
      {run.summary_scores?.runtime_error && (
        <section className="mb-5 border border-amber-300 bg-amber-50 rounded-[6px] px-4 py-3">
          <div className="flex items-start gap-2">
            <span className="text-amber-700 text-[14px] mt-0.5">⚠</span>
            <div className="flex-1">
              <div className="text-[12px] font-medium text-amber-900 mb-1">
                Agent 不可达
              </div>
              <div className="text-[11px] text-amber-800 leading-relaxed">
                {run.summary_scores.runtime_error}
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Meta grid */}
      <section className="grid grid-cols-4 gap-3 mb-5">
        <MetaCard label="总数" value={counts.total ?? run.progress.total ?? '—'} />
        <MetaCard label="通过" value={counts.passed ?? 0} hint="pass (所有指标≥0.5)" />
        <MetaCard label="失败" value={counts.failed ?? 0} hint="fail / error" />
        <MetaCard
          label="启动 → 完成"
          value={fmtDuration(run.started_at, run.finished_at)}
        />
      </section>

      {/* Agent config */}
      <section className="border border-border rounded-[6px] bg-surface p-4 mb-5">
        <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-2">Agent 配置</h3>
        <div className="grid grid-cols-3 gap-3 text-[12px]">
          <KV k="Type" v={(run.agent_config as { type?: string }).type ?? '—'} />
          <KV k="Model" v={(run.agent_config as { model?: string }).model ?? '—'} />
          <KV k="URL" v={(run.agent_config as { url?: string }).url ?? '—'} mono />
        </div>
        <details className="mt-2">
          <summary className="text-[11px] text-text-secondary cursor-pointer">原始配置 / evaluators</summary>
          <div className="grid grid-cols-2 gap-3 mt-2">
            <JsonBlock label="agent_config" data={run.agent_config} />
            <JsonBlock label="evaluator_configs" data={run.evaluator_configs} />
          </div>
        </details>
      </section>

      {/* Trace project lookup */}
      <section className="border border-border rounded-[6px] bg-surface p-4 mb-5">
        <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-2">调用轨迹（LangSmith Project）</h3>
        <p className="text-[11px] text-text-secondary mb-3">
          输入要溯源的 LangSmith project 名称，平台会按 (project, 时间窗口, 问题文本)
          反查并把每条样例对应的 run 写回。当前已绑定:{' '}
          <span className="font-mono">{activeProject || '（未绑定）'}</span>
        </p>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={projectInput}
            onChange={e => setProjectInput(e.target.value)}
            placeholder="例如 ruyi-agent"
            className="flex-1 max-w-[360px] py-1.5 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent font-mono"
          />
          <button
            onClick={() => projectInput.trim() && backfillMutation.mutate(projectInput.trim())}
            disabled={!projectInput.trim() || backfillMutation.isPending}
            className="py-1.5 px-3 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent disabled:opacity-40 hover:opacity-90 transition-all"
          >
            {backfillMutation.isPending ? '查询中…' : '查询轨迹'}
          </button>
        </div>
        {backfillMutation.data && (() => {
          const d = backfillMutation.data
          // Three-state banner: matched > 0 (success), error_kind set
          // (real failure with a known cause), or zero matches with no
          // error (project name probably wrong / outside retention).
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
            <div className="mt-2 text-[11px] text-[#b87b00]">
              匹配 0 / {d.scanned} 条样例。LangSmith 能查通，但 project 「{d.project}」
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

      {/* Dimension averages */}
      {Object.keys(dimAvg).length > 0 && (
        <section className="border border-border rounded-[6px] bg-surface p-4 mb-5">
          <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-3">维度平均分（0-1）</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {Object.entries(dimAvg).map(([name, val]) => {
              const meta = getScoreMeta(name)
              const passing = isPassing(name, val)
              const pct = Math.max(0, Math.min(1, val)) * 100
              const threshPct = Math.max(0, Math.min(1, meta.threshold)) * 100
              return (
                <div key={name} title={meta.description}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] text-text-secondary">
                      {meta.label}
                    </span>
                    <span className={`text-[9px] tracking-widest uppercase ${
                      meta.direction === 'higher_better' ? 'text-text-tertiary' : 'text-amber-700'
                    }`}>
                      {directionMark(meta)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 relative h-2 bg-accent-subtle rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          passing ? 'bg-green-500' : 'bg-red-400'
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                      {/* Threshold marker */}
                      <div
                        className="absolute top-0 bottom-0 w-px bg-text-tertiary/70"
                        style={{ left: `${threshPct}%` }}
                        title={`合格线 ${meta.threshold}`}
                      />
                    </div>
                    <span className={`font-mono text-[12px] min-w-[40px] text-right ${
                      passing ? 'text-green-700' : 'text-red-700'
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

      {/* Latency distribution */}
      {latencyBars.length > 0 && (
        <section className="border border-border rounded-[6px] bg-surface p-4 mb-5">
          <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-3">
            延迟分布（按样例 · ms）
          </h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={latencyBars} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border, #e5e5e5)" />
              <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={0} />
              <YAxis tick={{ fontSize: 10 }} label={{ value: 'count', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
              <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                {latencyBars.map((_, i) => (
                  <Cell key={i} fill={['#93c5fd', '#60a5fa', '#3b82f6', '#6366f1', '#a855f7', '#f87171'][i] || '#3b82f6'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </section>
      )}

      {/* Comprehensive visualization report */}
      <ReportSection
        dimAvg={dimAvg}
        radarData={radarData}
        scoreDistribution={scoreDistribution}
        toolUsage={toolUsage}
        counts={counts}
      />

      {/* Cost metrics */}
      <section className="grid grid-cols-2 gap-3 mb-5">
        <CostCard title="成功样例的成本" data={costSuccess} />
        <CostCard title="失败样例的成本" data={costFailure} />
      </section>

      {/* Results table */}
      <section>
        <div className="flex items-center mb-2">
          <h3 className="text-[12px] font-medium">样例结果</h3>
          <span className="ml-2 text-[11px] text-text-tertiary">
            共 {resultsQuery.data?.total ?? 0} 条
          </span>
          {langfuseHost && run.summary_scores?.langfuse_dataset && (
            <a
              href={`${langfuseHost}/datasets`}
              target="_blank" rel="noreferrer"
              className="ml-auto text-[11px] text-accent hover:underline"
            >
              Langfuse UI ↗
            </a>
          )}
        </div>
        <div className="border border-border rounded-[3px] overflow-hidden bg-surface">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <Th>Case</Th>
                <Th>Question</Th>
                <Th>状态</Th>
                <Th>Latency</Th>
                <Th>输入 token</Th>
                <Th>输出 token</Th>
                <Th>缓存命中</Th>
                <Th>Tools</Th>
                <Th>Scores</Th>
                <Th>Trace</Th>
              </tr>
            </thead>
            <tbody>
              {resultsQuery.isLoading && (
                <tr><td colSpan={10} className="py-6 text-center text-[12px] text-text-tertiary">加载中…</td></tr>
              )}
              {items.map((r: EvalResultRow) => (
                <ResultRow key={r.id} row={r} langfuseHost={langfuseHost} project={activeProject} />
              ))}
              {items.length === 0 && !resultsQuery.isLoading && (
                <tr><td colSpan={10} className="py-8 text-center text-[11px] text-text-tertiary">
                  {run.status === 'running' ? '还没产出样例结果…' : '没有样例结果'}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}


function ResultRow({ row, langfuseHost, project }: {
  row: EvalResultRow
  langfuseHost: string | null
  project: string | null
}) {
  const [open, setOpen] = useState(false)
  const scoreEntries = Object.entries(row.scores)

  // ─── LangSmith trace lazy-loaded on expand ──────────────────────────────
  const [nodeCache, setNodeCache] = useState<NodeCache>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const nodeCacheRef = useRef(nodeCache)
  nodeCacheRef.current = nodeCache

  const traceQuery = useQuery({
    queryKey: ['eval-result-trace', row.id, project ?? ''],
    queryFn: () => evaluationApi.getResultTrace(row.id, project || undefined).then(r => r.data),
    enabled: open && !!row.langsmith_run_id,
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

  return (
    <>
      <tr
        onClick={() => setOpen(v => !v)}
        className="hover:bg-accent-subtle/40 cursor-pointer transition-colors"
      >
        <Td mono>{row.benchmark_case_id?.slice(0, 8) ?? row.id.slice(0, 8)}</Td>
        <Td>
          <div className="max-w-[260px] truncate" title={row.question || ''}>
            {row.question || '—'}
          </div>
        </Td>
        <Td><StatusBadge status={row.status} /></Td>
        <Td>{row.latency_ms != null ? `${row.latency_ms}ms` : '—'}</Td>
        <Td>{row.prompt_tokens ?? '—'}</Td>
        <Td>{row.completion_tokens ?? '—'}</Td>
        <Td>
          {row.cache_read_tokens != null
            ? <span title={`命中: ${row.cache_read_tokens}, 创建: ${row.cache_creation_tokens ?? 0}`}>
                {row.cache_read_tokens}
                {row.cache_creation_tokens != null && row.cache_creation_tokens > 0 && (
                  <span className="text-text-tertiary ml-1">/+{row.cache_creation_tokens}</span>
                )}
              </span>
            : '—'}
        </Td>
        <Td>{row.tool_call_count ?? 0}</Td>
        <Td>
          <div className="flex flex-wrap gap-1">
            {scoreEntries.length === 0 && <span className="text-text-tertiary">—</span>}
            {scoreEntries.map(([n, v]) => {
              const meta = getScoreMeta(n)
              const t = tone(n, v)
              const cls = t === 'good'
                ? 'border-green-300 bg-green-50 text-green-800'
                : 'border-red-300 bg-red-50 text-red-800'
              return (
                <span
                  key={n}
                  className={`text-[10px] px-1.5 py-0.5 rounded border ${cls}`}
                  title={`${meta.label} · ${directionMark(meta)} · 合格线 ${meta.threshold}\n${meta.description}`}
                >
                  {meta.label}: {v.toFixed(2)}
                </span>
              )
            })}
          </div>
        </Td>
        <Td>
          {row.langsmith_run_id ? (
            <span className="text-[11px] font-mono text-accent">{row.langsmith_run_id.slice(0, 8)}</span>
          ) : row.langfuse_trace_id && langfuseHost ? (
            <a
              href={`${langfuseHost}/trace/${row.langfuse_trace_id}`}
              target="_blank" rel="noreferrer"
              onClick={e => e.stopPropagation()}
              className="text-[11px] text-accent hover:underline font-mono"
            >
              {row.langfuse_trace_id.slice(0, 8)} ↗
            </a>
          ) : '—'}
        </Td>
      </tr>
      {open && (
        <tr className="bg-accent-subtle/30">
          <td colSpan={10} className="p-3 text-[11px]">
            <div className="mb-3">
              <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-0.5">输出</div>
              <pre className="font-mono text-[11px] bg-white border border-border rounded-[3px] p-2 max-h-[200px] overflow-y-auto whitespace-pre-wrap">
                {row.actual_output || '（无输出）'}
              </pre>
            </div>
            {row.error_message && (
              <div className="mb-3">
                <div className="text-[10px] tracking-widest uppercase text-negative mb-0.5">错误</div>
                <pre className="font-mono text-[11px] bg-red-50 border border-red-200 rounded-[3px] p-2 whitespace-pre-wrap">
                  {row.error_message}
                </pre>
              </div>
            )}
            {/* Tool calls captured during agent invocation */}
            {Array.isArray(row.actual_tool_calls) && row.actual_tool_calls.length > 0 && (
              <div className="mb-3">
                <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">
                  工具调用 ({row.actual_tool_calls.length})
                </div>
                <ToolCallsTable calls={row.actual_tool_calls as Array<Record<string, unknown>>} />
              </div>
            )}
            <div>
              <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">LangSmith Trace</div>
              {!row.langsmith_run_id && (
                <div className="text-[11px] text-text-tertiary border border-dashed border-border rounded-[4px] px-3 py-4 text-center">
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
                <div className="bg-surface border border-border rounded-[6px] p-3">
                  <RunDetailBody detail={root} compact />
                  {root.children.length > 0 && (
                    <div className="mt-3">
                      <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">
                        Children ({root.children.length})
                        {root.children_truncated && <span className="ml-2 text-[#b87b00]">已截断</span>}
                      </div>
                      <div className="border border-border rounded-[4px] bg-surface">
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
          </td>
        </tr>
      )}
    </>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">
      {children}
    </th>
  )
}
function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <td className={`py-2 px-3 border-b border-border text-[12px] ${mono ? 'font-mono text-[11px]' : ''}`}>
      {children}
    </td>
  )
}

function MetaCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="border border-border rounded-[6px] bg-surface p-3">
      <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">{label}</div>
      <div className="text-[18px] font-medium tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-text-tertiary mt-0.5">{hint}</div>}
    </div>
  )
}

function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10px] tracking-widest uppercase text-text-tertiary">{k}</div>
      <div className={mono ? 'font-mono text-[11px] break-all' : ''}>{v}</div>
    </div>
  )
}

function JsonBlock({ label, data }: { label: string; data: unknown }) {
  return (
    <div>
      <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">{label}</div>
      <pre className="font-mono text-[10px] bg-accent-subtle/40 border border-border rounded-[3px] p-2 max-h-[240px] overflow-y-auto whitespace-pre-wrap break-all">
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
    { k: 'cache_hit_rate', label: 'Cache hit rate', fmt: (v) => `${(v * 100).toFixed(1)}%` },
  ]
  return (
    <div className="border border-border rounded-[6px] bg-surface p-4">
      <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-2">{title}</h3>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
        {rows.map(row => {
          const v = data?.[row.k]
          return (
            <div key={row.k} className="flex justify-between border-b border-border/40 pb-1">
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

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: 'bg-blue-100 text-blue-700 border-blue-300',
    completed: 'bg-green-100 text-green-700 border-green-300',
    failed: 'bg-red-100 text-red-700 border-red-300',
    stopping: 'bg-orange-100 text-orange-700 border-orange-300',
    interrupted: 'bg-gray-200 text-gray-700 border-gray-300',
    pending: 'bg-gray-100 text-gray-600 border-gray-300',
    pass: 'bg-green-100 text-green-700 border-green-300',
    fail: 'bg-red-100 text-red-700 border-red-300',
    error: 'bg-red-200 text-red-800 border-red-400',
    // Infrastructure failure — render neutral grey-orange to distinguish
    // "agent didn't respond" from "agent answered wrong".
    agent_unreachable: 'bg-amber-100 text-amber-800 border-amber-300',
    agent_timeout: 'bg-amber-100 text-amber-800 border-amber-300',
  }
  const labels: Record<string, string> = {
    agent_unreachable: 'agent unreachable',
    agent_timeout: 'agent timeout',
  }
  const cls = styles[status] ?? 'bg-gray-100 text-gray-600 border-gray-300'
  const label = labels[status] ?? status
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border ${cls}`}>
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

// ─── Comprehensive Report Section ─────────────────────────────────────────

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
    <section className="border border-border rounded-[6px] bg-surface p-4 mb-5">
      <h3 className="text-[11px] tracking-widest uppercase text-text-tertiary mb-4">综合报告</h3>

      {/* Pass rate funnel */}
      <div className="flex items-center gap-4 mb-5 pb-4 border-b border-border/50">
        <div className="text-center">
          <div className="text-[28px] font-light tabular-nums">{passRate}%</div>
          <div className="text-[10px] text-text-tertiary">合格率</div>
        </div>
        <div className="flex-1 grid grid-cols-3 gap-2 text-center text-[11px]">
          <div>
            <div className="font-mono text-[14px]">{counts.total ?? 0}</div>
            <div className="text-text-tertiary">总样例</div>
          </div>
          <div>
            <div className="font-mono text-[14px] text-green-700">{counts.passed ?? 0}</div>
            <div className="text-text-tertiary">通过</div>
          </div>
          <div>
            <div className="font-mono text-[14px] text-red-700">{counts.failed ?? 0}</div>
            <div className="text-text-tertiary">失败</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Radar chart */}
        {radarData.length >= 3 && (
          <div>
            <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-2">维度雷达图</div>
            <ResponsiveContainer width="100%" height={240}>
              <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="70%">
                <PolarGrid stroke="var(--color-border, #e5e5e5)" />
                <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 10 }} />
                <PolarRadiusAxis angle={90} domain={[0, 1]} tick={{ fontSize: 9 }} tickCount={6} />
                <Radar name="得分" dataKey="score" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.25} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Score distribution histogram */}
        {scoreDistribution && Object.keys(scoreDistribution.by_dimension).length > 0 && (
          <div>
            <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-2">分数分布</div>
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
                          className="flex-1 bg-blue-400 rounded-t-sm transition-all"
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

        {/* Tool usage top-N */}
        {hasTools && (
          <div className={radarData.length < 3 && !scoreDistribution ? 'md:col-span-2' : ''}>
            <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-2">
              工具调用统计 (Top {Math.min(toolUsage.length, 10)})
            </div>
            <ResponsiveContainer width="100%" height={Math.min(toolUsage.length, 10) * 28 + 30}>
              <BarChart
                data={toolUsage.slice(0, 10)}
                layout="vertical"
                margin={{ top: 5, right: 30, left: 80, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border, #e5e5e5)" />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={75} />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
                <Bar dataKey="calls" name="调用次数" fill="#60a5fa" radius={[0, 3, 3, 0]} />
                <Bar dataKey="errors" name="失败次数" fill="#f87171" radius={[0, 3, 3, 0]} />
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


// ─── Per-case tool calls table ────────────────────────────────────────────

function ToolCallsTable({ calls }: { calls: Array<Record<string, unknown>> }) {
  // Group by tool_name for a compact summary
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
    <div className="border border-border rounded-[3px] overflow-hidden">
      <table className="w-full border-collapse text-[11px]">
        <thead>
          <tr className="bg-accent-subtle/60">
            <th className="text-left py-1 px-2 font-normal text-text-tertiary">工具</th>
            <th className="text-right py-1 px-2 font-normal text-text-tertiary">次数</th>
            <th className="text-right py-1 px-2 font-normal text-text-tertiary">失败</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, { count, errors }]) => (
            <tr key={name} className="border-t border-border/40">
              <td className="py-1 px-2 font-mono">{name}</td>
              <td className="py-1 px-2 text-right tabular-nums">{count}</td>
              <td className={`py-1 px-2 text-right tabular-nums ${errors > 0 ? 'text-red-700' : ''}`}>
                {errors || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {calls.length > 5 && (
        <details className="px-2 py-1 border-t border-border/40">
          <summary className="text-[10px] text-text-tertiary cursor-pointer">展开全部调用详情</summary>
          <div className="mt-1 max-h-[200px] overflow-y-auto">
            {calls.map((c, i) => (
              <div key={i} className="flex gap-2 py-0.5 border-b border-border/20 last:border-0">
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


// ─── Radar data builder ───────────────────────────────────────────────────

function buildRadarData(dimAvg: Record<string, number>) {
  return Object.entries(dimAvg).map(([name, val]) => ({
    dimension: getScoreMeta(name).label,
    score: val,
    fullMark: 1,
  }))
}


// Split a set of samples' latency_ms into fixed buckets for a histogram.
// Buckets are chosen empirically to be useful for 1-60 s agent calls.
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
