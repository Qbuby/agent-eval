import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Legend,
} from 'recharts'
import { evaluationApi, tracesApi } from '@/services'
import type { EvalResultRow, EvalRunDetail, RunDetail, ConversationTrace, TurnExpectation, ChecklistItem, ScoreDetail } from '@/types'
import { RunNodeRow, RunDetailBody, type NodeCache } from '@/components/RunTreeView'
import { CotTimeline, ToolCallsTable } from '@/components/TraceTimeline'
import MarkdownView from '@/components/MarkdownView'
import { Button, Drawer, ErrorCard, ExportMenu } from '@/components/ui'
import {
  getScoreMeta, isPassing, directionMark, tone,
} from '@/lib/scoreSemantics'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { NormalizedError } from '@/lib/errors'
import type { ExportFormat } from '@/lib/download'
import { exportRunReport } from '@/lib/reportExport'
import { collapseScoreKey, collapseDimAvg } from '@/lib/dimensionCollapse'
import {
  deriveFacts, deriveAcceptance, deriveCostScored, deriveCostAbnormal,
  acceptancePassRateText, runDecisionLabel, type EvalFacts, type EvalAcceptance,
} from '@/lib/evalSemantics'

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

  const [exportError, setExportError] = useState<NormalizedError | null>(null)
  const [reportBusy, setReportBusy] = useState(false)
  const resultsQuery = useQuery({
    queryKey: ['eval-results', runId],
    queryFn: () => evaluationApi.getAllResults(runId!),
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

  const rescoreMutation = useMutation({
    mutationFn: () => evaluationApi.rescoreRun(runId!).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-run', runId] })
      qc.invalidateQueries({ queryKey: ['eval-results', runId] })
    },
  })

  const run = runQuery.data
  const langfuseHost = deriveLangfuseHost(run)

  const [selectedRowId, setSelectedRowId] = useState<string | null>(null)
  // 快速筛选：异常样例三态（不筛 / 仅异常 / 排除异常，仅指执行异常）+ 分数低于阈值（阈值 + 指定维度）。
  const [abnormalMode, setAbnormalMode] = useState<'all' | 'only' | 'exclude'>('all')
  const [threshold, setThreshold] = useState('')
  const [thresholdDim, setThresholdDim] = useState('')

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

  const facts = deriveFacts(run.summary_scores ?? run)
  const acceptance = deriveAcceptance(run.summary_scores ?? run)
  // 按评估器聚合（折叠 .turnN / .conversation）。后端新 run 已折叠，这里再折一次
  // 兼容旧 run（其 summary 仍是轮次级）——幂等，已折叠的 key 不含轮次后缀不受影响。
  const dimAvg = collapseDimAvg(run.summary_scores?.dimension_averages ?? {})
  const costScored = deriveCostScored(run.summary_scores)
  const costAbnormal = deriveCostAbnormal(run.summary_scores)
  const toolUsage = (run.summary_scores?.tool_usage ?? []) as Array<{
    name: string; calls: number; errors: number; cases: number
  }>
  const scoreDistribution = collapseScoreDistribution(
    (run.summary_scores?.score_distribution ?? null) as null | {
      buckets: string[]; by_dimension: Record<string, number[]>
    },
  )
  const allItems = resultsQuery.data?.items ?? []
  // 供「低分维度」下拉：折叠逐轮后的评估器维度全集（跨所有样例）。
  const filterDims = collectFilterDims(allItems)
  // 执行异常状态集合（仅执行异常，不含 fail —— fail 是判分未过而非跑挂）。
  const thr = threshold.trim() === '' ? null : Number(threshold)
  const thrValid = thr != null && !Number.isNaN(thr)
  const items = allItems.filter((r: EvalResultRow) => {
    const isAbnormal = ABNORMAL_STATUSES.has(r.status)
    if (abnormalMode === 'only' && !isAbnormal) return false
    if (abnormalMode === 'exclude' && isAbnormal) return false
    if (thrValid && !rowBelowThreshold(r, thr, thresholdDim)) return false
    return true
  })
  const filterActive = abnormalMode !== 'all' || thrValid
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
          <ExportMenu
            disabled={!runId}
            onExport={async (format: ExportFormat) => {
              if (!runId) return
              try {
                await evaluationApi.exportResults(runId, format)
                setExportError(null)
              } catch (e) {
                setExportError(formatApiError(e))
              }
            }}
          />
          <Button
            variant="secondary"
            size="sm"
            loading={reportBusy}
            onClick={async () => {
              if (!runId) return
              setReportBusy(true)
              // 先取 LLM 解读（几秒），拿到后嵌入报告；解读是增强项，
              // 请求失败也照样导出无解读的基础报告，不阻断下载。
              let analysis: string | undefined
              try {
                const res = await evaluationApi.getRunReport(runId)
                analysis = res.data?.report || undefined
                setExportError(null)
              } catch {
                setExportError(null)
              }
              try {
                const reportItems = (await evaluationApi.getAllResults(runId)).items
                exportRunReport(run, reportItems, (d) => getScoreMeta(d).label, analysis)
              } catch (e) {
                setExportError(formatApiError(e))
              } finally {
                setReportBusy(false)
              }
            }}
            title="导出本次评估的 HTML 分析报告（含 AI 解读 + 合格率/维度/分布/工具统计）"
          >
            导出报告
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
          <Button
            variant="secondary"
            size="sm"
            loading={rescoreMutation.isPending}
            onClick={() => rescoreMutation.mutate()}
            title="对评分未出全的样例（evaluation_error）复用已存回答，只补缺失维度的 judge 打分"
          >
            补评缺分维度
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
        <div className="mb-3">
          <ErrorCard
            error={formatApiError(langfusePullMutation.error, { fallbackTitle: '拉取失败' })}
            variant="compact"
          />
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
        <div className="mb-3">
          <ErrorCard
            error={formatApiError(reaggregateMutation.error, { fallbackTitle: '重算失败' })}
            variant="compact"
          />
        </div>
      )}

      {rescoreMutation.data && (
        <div className="mb-3 text-[12px] text-text-secondary border border-border bg-fill/5 rounded-md px-3 py-2">
          已补评：扫描 {rescoreMutation.data.results_scanned} 条缺分样例，
          补回维度 {rescoreMutation.data.dimensions_recovered} 个，
          恢复完整 {rescoreMutation.data.results_completed} 条
          {rescoreMutation.data.results_still_missing > 0 ? `，仍缺 ${rescoreMutation.data.results_still_missing} 条（上游 judge 仍未出分，可稍后再点）` : ''}
        </div>
      )}
      {rescoreMutation.isError && (
        <div className="mb-3">
          <ErrorCard
            error={formatApiError(rescoreMutation.error, { fallbackTitle: '补评失败' })}
            variant="compact"
          />
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
        <MetaCard label="总数" value={facts.total || run.progress.total || '—'} />
        <MetaCard
          label="Agent 执行"
          value={facts.execution_success}
          hint={`成功 · 异常 ${facts.execution_abnormal}`}
        />
        <MetaCard
          label="Judge 评分"
          value={facts.evaluation_completed}
          hint={`完成 · 跳过 ${facts.skipped}`}
        />
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
            {toToastMessage(formatApiError(backfillMutation.error, { fallbackMessage: '查询失败' }))}
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
        facts={facts}
        acceptance={acceptance}
      />

      <section className="grid grid-cols-2 gap-3 mb-5">
        <CostCard title="评分样例的成本" data={costScored} />
        <CostCard title="执行异常样例的成本" data={costAbnormal} />
      </section>

      <RetryStatsCard stats={run.summary_scores?.retry_stats} />

      <section>
        <div className="section-row">
          <div className="page-eyebrow">
            样例结果 · 共 {resultsQuery.data?.total ?? 0} 条
            {filterActive && <span className="text-text-tertiary">（筛出 {items.length}）</span>}
          </div>
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

        {/* 快速筛选：异常样例三态（不筛 / 仅异常 / 排除异常）+ 分数低于阈值（阈值 + 维度）。纯前端过滤当前已加载的样例。 */}
        <div className="toolbar mb-2">
          <div className="flex items-center gap-1.5 text-[12px]">
            <span className="text-text-tertiary">异常样例</span>
            <span className="text-[10px] text-text-tertiary">(error / 不可达 / 超时)</span>
            <div className="inline-flex rounded-md border border-border overflow-hidden text-[11px]">
              {([
                ['all', '不筛'],
                ['only', '仅异常'],
                ['exclude', '排除异常'],
              ] as const).map(([mode, label]) => (
                <button
                  key={mode}
                  onClick={() => setAbnormalMode(mode)}
                  className={`px-2.5 py-1 transition-colors ${
                    abnormalMode === mode
                      ? 'bg-accent/10 text-accent'
                      : 'text-text-secondary hover:bg-fill/5'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-1.5 text-[12px]">
            <span className="text-text-tertiary">分数低于</span>
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={threshold}
              onChange={e => setThreshold(e.target.value)}
              placeholder="阈值"
              className="input-sm w-[80px]"
            />
            <select
              value={thresholdDim}
              onChange={e => setThresholdDim(e.target.value)}
              className="input-sm w-[160px]"
            >
              <option value="">任一维度</option>
              {filterDims.map(d => (
                <option key={d} value={d}>{getScoreMeta(d).label}</option>
              ))}
            </select>
          </div>
          {filterActive && (
            <button
              onClick={() => { setAbnormalMode('all'); setThreshold(''); setThresholdDim('') }}
              className="text-action text-[11px]"
            >
              清除筛选
            </button>
          )}
        </div>

        {exportError && (
          <div className="mb-2">
            <ErrorCard error={exportError} />
          </div>
        )}
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
                  {filterActive
                    ? '没有符合筛选条件的样例。'
                    : run.status === 'running' ? '还没产出样例结果…' : '没有样例结果'}
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


// 分数单元格：评估器多了之后逐轮 badge 会堆叠十几个撑爆行高。默认按**评估器**
// 聚合，每个评估器只显示 1 个 badge（该评估器所有轮/会话的平均分）；点「展开」
// 才铺开逐轮明细。tone 按聚合均分是否达标判定。
function ScoreCell({ scores }: { scores: Record<string, number> }) {
  const [open, setOpen] = useState(false)
  const entries = Object.entries(scores)
  if (entries.length === 0) return <span className="text-text-tertiary">—</span>

  // 按评估器名聚合（折叠 .turnN / .conversation）。
  const grouped: Record<string, number[]> = {}
  for (const [k, v] of entries) {
    if (typeof v !== 'number') continue
    const dim = collapseScoreKey(k)
    ;(grouped[dim] ??= []).push(v)
  }
  const groups = Object.entries(grouped)
    .map(([dim, vals]) => ({
      dim,
      avg: vals.reduce((s, x) => s + x, 0) / vals.length,
      count: vals.length,
    }))
    .sort((a, b) => a.dim.localeCompare(b.dim))

  // 展开态：铺开全部逐轮明细（原始 key）。
  if (open) {
    return (
      <div className="flex flex-col gap-1">
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setOpen(false) }}
          className="self-start text-[11px] text-accent hover:text-accent-hover"
        >
          收起 ▲
        </button>
        <div className="flex flex-wrap gap-1">
          {entries.map(([n, v]) => {
            const meta = getScoreMeta(n)
            const cls = tone(n, v) === 'good' ? 'badge badge-positive' : 'badge badge-negative'
            return (
              <span key={n} className={cls} title={`${meta.label} · ${directionMark(meta)} · 合格线 ${meta.threshold}`}>
                {n}: {v.toFixed(2)}
              </span>
            )
          })}
        </div>
      </div>
    )
  }

  // 收起态（默认）：每评估器 1 个 badge，显示均分；轮次多时括注轮数。
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap gap-1">
        {groups.map(({ dim, avg, count }) => {
          const meta = getScoreMeta(dim)
          const cls = isPassing(dim, avg) ? 'badge badge-positive' : 'badge badge-negative'
          return (
            <span
              key={dim}
              className={cls}
              title={`${meta.label} · ${count} 项均分 · ${directionMark(meta)} · 合格线 ${meta.threshold}`}
            >
              {meta.label}: {avg.toFixed(2)}
              {count > 1 && <span className="opacity-60"> ({count})</span>}
            </span>
          )
        })}
      </div>
      {entries.length > groups.length && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setOpen(true) }}
          className="self-start text-[11px] text-accent hover:text-accent-hover"
        >
          展开逐轮（{entries.length}）▼
        </button>
      )}
    </div>
  )
}


function ResultRow({ row, langfuseHost, selected, onSelect }: {
  row: EvalResultRow
  langfuseHost: string | null
  selected: boolean
  onSelect: () => void
}) {
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
        <ScoreCell scores={row.scores} />
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
      const msg = toToastMessage(formatApiError(err, { fallbackMessage: '加载失败' }))
      setNodeCache(prev => ({ ...prev, [childId]: { loading: false, error: msg } }))
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

      {row.full_trace?.conversation ? (
        <div className="mb-3">
          <div className="field-label">多轮对话回放</div>
          <ConversationResultView
            conversation={row.full_trace.conversation}
            scores={row.scores}
            scoreDetails={row.score_details}
          />
        </div>
      ) : (
        <div className="mb-3">
          <div className="field-label">输出</div>
          <pre className="font-mono text-[11px] bg-fill/5 border border-border rounded-md p-2.5 max-h-[240px] overflow-y-auto whitespace-pre-wrap">
            {row.actual_output || '（无输出）'}
          </pre>
        </div>
      )}

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
  dimAvg, radarData, scoreDistribution, toolUsage, facts, acceptance,
}: {
  dimAvg: Record<string, number>
  radarData: Array<{ dimension: string; score: number; fullMark: number }>
  scoreDistribution: { buckets: string[]; by_dimension: Record<string, number[]> } | null
  toolUsage: Array<{ name: string; calls: number; errors: number; cases: number }>
  facts: EvalFacts
  acceptance: EvalAcceptance
}) {
  const hasDims = Object.keys(dimAvg).length > 0
  const hasTools = toolUsage.length > 0
  if (!hasDims && !hasTools) return null

  // 验收通过率仅在配置了显式验收策略时展示；否则明确「仅评分」，不编造合格率。
  const passRateText = acceptancePassRateText(acceptance)

  return (
    <section className="card p-4 mb-5">
      <h3 className="page-eyebrow mb-4">综合报告</h3>

      <div className="flex items-center gap-4 mb-5 pb-4 border-b border-separator">
        <div className="text-center">
          {passRateText != null ? (
            <>
              <div className="text-[28px] font-display font-semibold tracking-[-0.5px] tabular-nums">{passRateText}</div>
              <div className="text-[10px] text-text-tertiary">验收通过率 · {runDecisionLabel(acceptance.run_decision)}</div>
            </>
          ) : (
            <>
              <div className="text-[15px] font-medium text-text-secondary">仅评分</div>
              <div className="text-[10px] text-text-tertiary">未配置验收规则</div>
            </>
          )}
        </div>
        <div className="flex-1 grid grid-cols-3 gap-2 text-center text-[11px]">
          <div>
            <div className="font-mono text-[14px]">{facts.total}</div>
            <div className="text-text-tertiary">总样例</div>
          </div>
          <div>
            <div className="font-mono text-[14px] text-positive">{facts.evaluation_completed}</div>
            <div className="text-text-tertiary">评分完成</div>
          </div>
          <div>
            <div className="font-mono text-[14px] text-negative">{facts.execution_abnormal}</div>
            <div className="text-text-tertiary">执行异常</div>
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


// checklist 逐条判定明细：把 judge 的每条 pass/fail/na + 证据渲染成清单，
// 支撑「可验证可溯源」的打分链路——分数 = pass /(pass+fail) 由后端机械算出，
// 这里把算分依据逐条摊开，评审人可对着证据复核每一条判定。
function ChecklistDetail({ checks, reasoning }: {
  checks?: ChecklistItem[]
  reasoning?: string
}) {
  const [open, setOpen] = useState(false)
  const items = checks ?? []
  const nPass = items.filter(c => c.verdict === 'pass').length
  const nFail = items.filter(c => c.verdict === 'fail').length
  const nNa = items.filter(c => c.verdict === 'na').length
  const mark = (v: string) =>
    v === 'pass' ? { s: '✓', cls: 'text-positive' }
      : v === 'fail' ? { s: '✗', cls: 'text-negative' }
        : { s: '—', cls: 'text-text-tertiary' }

  return (
    <div className="rounded-md border border-border bg-fill/5 text-[11px]">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-fill/10 transition-colors"
      >
        <span className="text-text-tertiary">{open ? '▾' : '▸'}</span>
        <span className="font-medium text-text-primary">检查项</span>
        {items.length > 0 && (
          <span className="text-text-secondary tabular-nums">
            <span className="text-positive">{nPass} 通过</span>
            {nFail > 0 && <span className="text-negative"> · {nFail} 未过</span>}
            {nNa > 0 && <span className="text-text-tertiary"> · {nNa} 不适用</span>}
          </span>
        )}
      </button>
      {open && (
        <div className="px-2.5 pb-2 space-y-1.5">
          {items.map((c, ci) => {
            const m = mark(c.verdict)
            return (
              <div key={c.id || ci} className="flex gap-1.5">
                <span className={`${m.cls} font-bold shrink-0`}>{m.s}</span>
                <div className="min-w-0">
                  <div className="text-text-primary">
                    {c.id && <span className="text-text-tertiary font-mono mr-1">{c.id}</span>}
                    {c.desc || '（无描述）'}
                  </div>
                  {c.evidence && (
                    <div className="text-text-tertiary mt-0.5">证据：{c.evidence}</div>
                  )}
                </div>
              </div>
            )
          })}
          {items.length === 0 && reasoning && (
            <div className="text-text-secondary whitespace-pre-wrap">{reasoning}</div>
          )}
          {items.length > 0 && reasoning && (
            <div className="text-text-tertiary border-t border-separator pt-1 mt-1 whitespace-pre-wrap">
              {reasoning}
            </div>
          )}
        </div>
      )}
    </div>
  )
}


// 多轮评估结果：把回放出的逐轮 user/assistant 渲染成气泡，每条 user 下方挂
// 该轮的逐轮分数（score key 形如 `<label>.turn<turn_index>`）。会话级分数
// （`<label>.conversation`）与逐轮分数已在上方「评分」区统一展示，这里只做
// 逐轮对齐，方便按轮核对。
function ConversationResultView({
  conversation, scores, scoreDetails,
}: {
  conversation: ConversationTrace
  scores: Record<string, number>
  scoreDetails?: Record<string, ScoreDetail>
}) {
  const turns = conversation.turns ?? []
  // turn_index → 该轮所有分数项（跨多个 evaluator label）。
  const perTurnScores = (turnIndex: number): Array<[string, number]> =>
    Object.entries(scores).filter(([k]) => k.endsWith(`.turn${turnIndex}`))
  // 会话级分数项（score key 形如 `<label>.conversation`），以整段对话为对象打分。
  const convScores: Array<[string, number]> =
    Object.entries(scores).filter(([k]) => k.endsWith('.conversation'))
  // turn_index → 该轮期望（评判要点 / 期望输出），按 turn_index 对齐。
  const expByIndex = new Map<number, TurnExpectation>()
  for (const te of conversation.turn_expectations ?? []) {
    if (typeof te.turn_index === 'number') expByIndex.set(te.turn_index, te)
  }

  return (
    <div className="space-y-3">
      {conversation.goal && (
        <div className="rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-[12px]">
          <span className="font-medium text-accent">会话目标</span>
          <div className="mt-1 text-text-secondary">
            <MarkdownView text={conversation.goal} />
          </div>
        </div>
      )}

      {turns.map((t, i) => {
        const turnScores = perTurnScores(t.turn_index)
        const exp = expByIndex.get(t.turn_index)
        const criteria = exp?.criteria ?? []
        const turnSteps = t.steps ?? []
        const turnToolCalls = t.tool_calls ?? []
        return (
          <div key={i} className="space-y-1.5">
            {/* user 气泡（右） */}
            <div className="flex flex-col items-end">
              <div className="max-w-[85%] rounded-lg px-3 py-2 bg-accent/10 border border-accent/20">
                <div className="text-[10px] uppercase tracking-wide text-text-tertiary mb-1">
                  用户 · 第 {i + 1} 轮
                </div>
                <div className="text-[12px] text-text-primary">
                  <MarkdownView text={t.user} />
                </div>
              </div>
              {/* 该轮期望（评判要点 / 期望输出）——按什么标准打分 */}
              {(criteria.length > 0 || exp?.expected_output) && (
                <div className="max-w-[85%] mt-1 rounded-md border border-border bg-fill/5 px-3 py-2 text-[11px] text-text-secondary">
                  {criteria.length > 0 && (
                    <div>
                      <span className="font-medium text-text-primary">评判要点：</span>
                      <ul className="list-disc list-inside mt-0.5 space-y-0.5">
                        {criteria.map((c, ci) => <li key={ci}>{c}</li>)}
                      </ul>
                    </div>
                  )}
                  {exp?.expected_output && (
                    <div className={criteria.length > 0 ? 'mt-1' : ''}>
                      <span className="font-medium text-text-primary">期望输出：</span>
                      <span className="ml-1">{exp.expected_output}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
            {/* assistant 气泡（左） */}
            <div className="flex flex-col items-start">
              <div className="max-w-[85%] rounded-lg px-3 py-2 bg-fill/5 border border-border">
                <div className="text-[10px] uppercase tracking-wide text-text-tertiary mb-1">
                  助手
                </div>
                <div className="text-[12px] text-text-primary">
                  <MarkdownView text={t.assistant || '（无回复）'} />
                </div>
              </div>
              {turnScores.length > 0 && (
                <div className="flex flex-col gap-1 mt-1 max-w-[85%] w-full">
                  {turnScores.map(([n, v]) => {
                    const t2 = tone(n, v)
                    const cls = t2 === 'good' ? 'badge badge-positive' : 'badge badge-negative'
                    const detail = scoreDetails?.[n]
                    return (
                      <div key={n} className="flex flex-col gap-1">
                        <div className="flex items-center gap-1.5">
                          <span className={cls} title={n}>
                            {getScoreMeta(collapseScoreKey(n)).label}: {v.toFixed(2)}
                          </span>
                        </div>
                        {(detail?.checks?.length || detail?.reasoning) && (
                          <ChecklistDetail checks={detail?.checks} reasoning={detail?.reasoning} />
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
              {/* 该轮工具调用 / 推理步骤（与单轮 CotTimeline 同形态） */}
              {turnSteps.length > 0 ? (
                <div className="max-w-[85%] mt-1 w-full">
                  <div className="text-[10px] text-text-tertiary mb-1">本轮步骤（{turnSteps.length}）</div>
                  <CotTimeline steps={turnSteps} />
                </div>
              ) : turnToolCalls.length > 0 && (
                <div className="max-w-[85%] mt-1 text-[11px] text-text-tertiary">
                  本轮工具调用：{turnToolCalls.length} 次
                </div>
              )}
            </div>
          </div>
        )
      })}

      {turns.length === 0 && (
        <div className="empty-state text-[12px]">无逐轮回放记录</div>
      )}

      {/* 会话级评分（score key 形如 `<label>.conversation`）——以整段对话为
          依据，与逐轮分离展示，逐条 checklist 支撑可溯源打分链路。 */}
      {convScores.length > 0 && (
        <div className="rounded-md border border-accent/20 bg-accent/5 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-text-tertiary mb-1.5">
            会话级评分
          </div>
          <div className="flex flex-col gap-1.5">
            {convScores.map(([n, v]) => {
              const t2 = tone(n, v)
              const cls = t2 === 'good' ? 'badge badge-positive' : 'badge badge-negative'
              const detail = scoreDetails?.[n]
              return (
                <div key={n} className="flex flex-col gap-1">
                  <div className="flex items-center gap-1.5">
                    <span className={cls} title={n}>
                      {getScoreMeta(collapseScoreKey(n)).label}: {v.toFixed(2)}
                    </span>
                  </div>
                  {(detail?.checks?.length || detail?.reasoning) && (
                    <ChecklistDetail checks={detail?.checks} reasoning={detail?.reasoning} />
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}


// CotTimeline / CotStepRow / ToolCallsTable / isToolCallError / ToolResultBadge
// 已抽到 @/components/TraceTimeline（与 tracing 详情页共用），此处改为 import。

// collapseScoreKey / collapseDimAvg 已抽到 @/lib/dimensionCollapse（与 reportExport 共用）。

// 「执行异常」状态：agent 跑挂 / 不可达 / 超时 / 报错。不含 fail —— fail 是判分
// 未达合格线（跑通了但没答好），不是执行异常，快筛「异常样例」时不纳入。
const ABNORMAL_STATUSES = new Set(['error', 'agent_unreachable', 'agent_timeout'])

// 收集所有样例出现过的评估器维度（折叠 .turnN / .conversation），供「低分维度」
// 下拉选择。按名排序，稳定展示。
function collectFilterDims(items: EvalResultRow[]): string[] {
  const set = new Set<string>()
  for (const r of items) {
    for (const k of Object.keys(r.scores ?? {})) {
      if (typeof r.scores[k] === 'number') set.add(collapseScoreKey(k))
    }
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b))
}

// 判定一条样例是否「低于阈值」：dim 为空 = 任一维度低于阈值即命中；dim 指定
// = 该维度（折叠逐轮后取其各轮/会话均值）低于阈值。无该维度分数的样例不命中。
function rowBelowThreshold(row: EvalResultRow, thr: number, dim: string): boolean {
  const entries = Object.entries(row.scores ?? {}).filter(
    ([, v]) => typeof v === 'number',
  ) as Array<[string, number]>
  if (entries.length === 0) return false
  if (!dim) {
    return entries.some(([, v]) => v < thr)
  }
  const vals = entries.filter(([k]) => collapseScoreKey(k) === dim).map(([, v]) => v)
  if (vals.length === 0) return false
  const avg = vals.reduce((s, v) => s + v, 0) / vals.length
  return avg < thr
}

// 分数分布：同一评估器各轮的桶计数逐桶相加，折叠成评估器级分布。
function collapseScoreDistribution(
  sd: { buckets: string[]; by_dimension: Record<string, number[]> } | null,
): { buckets: string[]; by_dimension: Record<string, number[]> } | null {
  if (!sd) return null
  const merged: Record<string, number[]> = {}
  for (const [k, counts] of Object.entries(sd.by_dimension)) {
    const dim = collapseScoreKey(k)
    if (!merged[dim]) merged[dim] = counts.map(() => 0)
    counts.forEach((c, i) => { merged[dim][i] = (merged[dim][i] ?? 0) + c })
  }
  return { buckets: sd.buckets, by_dimension: merged }
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
