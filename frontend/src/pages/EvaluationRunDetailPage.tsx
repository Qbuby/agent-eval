import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { evaluationApi } from '@/services'
import type { EvalResultRow, EvalRunDetail } from '@/types'

export default function EvaluationRunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const qc = useQueryClient()

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

  const run = runQuery.data
  const langfuseHost = deriveLangfuseHost(run)

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
        </div>
      </header>

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
                <tr><td colSpan={7} className="py-6 text-center text-[12px] text-text-tertiary">加载中…</td></tr>
              )}
              {resultsQuery.data?.items.map((r: EvalResultRow) => (
                <ResultRow key={r.id} row={r} langfuseHost={langfuseHost} />
              ))}
              {resultsQuery.data?.items.length === 0 && !resultsQuery.isLoading && (
                <tr><td colSpan={7} className="py-8 text-center text-[11px] text-text-tertiary">
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


function ResultRow({ row, langfuseHost }: { row: EvalResultRow; langfuseHost: string | null }) {
  const [open, setOpen] = useState(false)
  const scoreEntries = Object.entries(row.scores)

  return (
    <>
      <tr
        onClick={() => setOpen(v => !v)}
        className="hover:bg-accent-subtle/40 cursor-pointer transition-colors"
      >
        <Td mono>{row.benchmark_case_id?.slice(0, 8) ?? '—'}</Td>
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
          {row.langfuse_trace_id && langfuseHost ? (
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
          <td colSpan={7} className="p-3 text-[11px]">
            <div className="mb-2">
              <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-0.5">输出</div>
              <pre className="font-mono text-[11px] bg-white border border-border rounded-[3px] p-2 max-h-[240px] overflow-y-auto whitespace-pre-wrap">
                {row.actual_output || '（无输出）'}
              </pre>
            </div>
            {row.error_message && (
              <div>
                <div className="text-[10px] tracking-widest uppercase text-negative mb-0.5">错误</div>
                <pre className="font-mono text-[11px] bg-red-50 border border-red-200 rounded-[3px] p-2 whitespace-pre-wrap">
                  {row.error_message}
                </pre>
              </div>
            )}
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
  }
  const cls = styles[status] ?? 'bg-gray-100 text-gray-600 border-gray-300'
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border ${cls}`}>
      {status === 'running' && (
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      )}
      {status}
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
  // "llm_judge.accuracy" -> "accuracy"
  const i = n.lastIndexOf('.')
  return i >= 0 ? n.slice(i + 1) : n
}

function deriveLangfuseHost(run: EvalRunDetail | null | undefined): string | null {
  // The backend stamps langfuse_host into summary_scores when the run
  // completes, so we can deep-link to the Langfuse UI directly.
  if (!run) return null
  const h = (run.summary_scores as { langfuse_host?: string } | null | undefined)?.langfuse_host
  return h ? h.replace(/\/+$/, '') : null
}
