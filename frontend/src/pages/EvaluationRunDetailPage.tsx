import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { evaluationApi, tracesApi } from '@/services'
import type { EvalResultRow, EvalRunDetail, RunDetail } from '@/types'
import { RunNodeRow, RunDetailBody, type NodeCache } from '@/components/RunTreeView'

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
  const items = resultsQuery.data?.items ?? []
  const latencyBars = buildLatencyBuckets(items)

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
        </div>
      </header>

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
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {Object.entries(dimAvg).map(([name, val]) => (
              <div key={name}>
                <div className="text-[10px] text-text-tertiary mb-0.5">{name}</div>
                <div className="flex items-center gap-2">
                  <div className="flex-1 h-1.5 bg-accent-subtle rounded-full overflow-hidden">
                    <div
                      className="h-full bg-accent rounded-full transition-all"
                      style={{ width: `${Math.max(0, Math.min(1, val)) * 100}%` }}
                    />
                  </div>
                  <span className="font-mono text-[12px] min-w-[40px] text-right">{val.toFixed(2)}</span>
                </div>
              </div>
            ))}
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
                <Th>Tokens (P/C/T)</Th>
                <Th>Tools</Th>
                <Th>Scores</Th>
                <Th>Trace</Th>
              </tr>
            </thead>
            <tbody>
              {resultsQuery.isLoading && (
                <tr><td colSpan={8} className="py-6 text-center text-[12px] text-text-tertiary">加载中…</td></tr>
              )}
              {items.map((r: EvalResultRow) => (
                <ResultRow key={r.id} row={r} langfuseHost={langfuseHost} project={activeProject} />
              ))}
              {items.length === 0 && !resultsQuery.isLoading && (
                <tr><td colSpan={8} className="py-8 text-center text-[11px] text-text-tertiary">
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
        <Td>
          {row.prompt_tokens != null || row.completion_tokens != null
            ? `${row.prompt_tokens ?? '?'}/${row.completion_tokens ?? '?'}/${row.total_tokens ?? '?'}`
            : '—'}
        </Td>
        <Td>{row.tool_call_count ?? 0}</Td>
        <Td>
          <div className="flex flex-wrap gap-1">
            {scoreEntries.length === 0 && <span className="text-text-tertiary">—</span>}
            {scoreEntries.map(([n, v]) => (
              <span
                key={n}
                className={`text-[10px] px-1.5 py-0.5 rounded border ${
                  v >= 0.5 ? 'border-green-300 bg-green-50 text-green-800' : 'border-red-300 bg-red-50 text-red-800'
                }`}
                title={n}
              >
                {shortName(n)}: {v.toFixed(2)}
              </span>
            ))}
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
          <td colSpan={8} className="p-3 text-[11px]">
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

function shortName(n: string): string {
  const i = n.lastIndexOf('.')
  return i >= 0 ? n.slice(i + 1) : n
}

function deriveLangfuseHost(run: EvalRunDetail | null | undefined): string | null {
  if (!run) return null
  const h = (run.summary_scores as { langfuse_host?: string } | null | undefined)?.langfuse_host
  return h ? h.replace(/\/+$/, '') : null
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
