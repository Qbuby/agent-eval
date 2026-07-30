import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, useConfirm, useToast, ExportMenu } from '@/components/ui'
import {
  agentRepliesApi,
  benchmarkApi,
  datasetsApi,
  evaluationApi,
  projectsApi,
} from '@/services'
import type { CaseReplyState, ReplyDatasetType } from '@/services/agentReplies'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { ExportFormat } from '@/lib/download'
import type {
  BenchmarkCase,
  Project,
} from '@/services/benchmark'
import type {
  ConfigOption,
  EvalAgentConfig,
  EvalRunSummary,
  EvaluatorInstance,
  StartEvalRequest,
  TestCase,
  UploadCasesResponse,
} from '@/types'
import { configOptionToString, useConfigOptions } from '@/hooks/useConfigOptions'
import { OptionPicker } from '@/components/ui'
import {
  deriveFacts, deriveAcceptance, deriveCostScored, deriveCostAbnormal,
  acceptancePassRateText, runDecisionLabel,
} from '@/lib/evalSemantics'
import {
  evaluatorDisplayName,
  normalizeComparisonSummary,
} from '@/lib/comparativeMetrics'

type Tab = 'history' | 'new'

export default function EvaluationPage() {
  const [tab, setTab] = useState<Tab>('history')

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">评估</div>
        <h1 className="page-title">评估</h1>
        <p className="page-subtitle">运行管理 · Trace 关联 · 本地评分</p>
      </header>

      <div className="page-tabs mb-5">
        {([
          { id: 'history', label: '运行历史' },
          { id: 'new', label: '新建评估' },
        ] as { id: Tab; label: string }[]).map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`page-tab ${tab === t.id ? 'page-tab-active' : ''}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'history' && <HistoryTab onNewRun={() => setTab('new')} />}
      {tab === 'new' && <NewRunTab onStarted={() => setTab('history')} />}
    </div>
  )
}


// ─── History tab ────────────────────────────────────────────────────────────

function HistoryTab({ onNewRun }: { onNewRun: () => void }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [searchText, setSearchText] = useState('')
  const [startedAfter, setStartedAfter] = useState('')   // YYYY-MM-DD
  const [startedBefore, setStartedBefore] = useState('') // YYYY-MM-DD
  const [minPassRate, setMinPassRate] = useState<string>('')  // percent string
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const pageSize = 15

  // Convert calendar values to ISO. Empty input → undefined (param dropped).
  const toIsoStart = (d: string) => d ? new Date(d + 'T00:00:00').toISOString() : undefined
  const toIsoEnd = (d: string) => d ? new Date(d + 'T23:59:59.999').toISOString() : undefined
  const passRateNum = (() => {
    const v = Number(minPassRate)
    if (!minPassRate || Number.isNaN(v)) return undefined
    return Math.max(0, Math.min(100, v)) / 100
  })()

  const runsQuery = useQuery({
    queryKey: ['eval-runs', page, statusFilter, searchText, startedAfter, startedBefore, minPassRate],
    queryFn: () =>
      evaluationApi.listRuns({
        page, page_size: pageSize,
        status: statusFilter || undefined,
        q: searchText.trim() || undefined,
        started_after: toIsoStart(startedAfter),
        started_before: toIsoEnd(startedBefore),
        min_pass_rate: passRateNum,
      }).then(r => r.data),
    refetchInterval: (q) => {
      const data = q.state.data
      if (!data) return false
      return data.items.some(it => it.status === 'running' || it.status === 'stopping') ? 3000 : false
    },
  })

  const totalPages = Math.max(1, Math.ceil((runsQuery.data?.total ?? 0) / pageSize))

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const clearFilters = () => {
    setStatusFilter('')
    setSearchText('')
    setStartedAfter('')
    setStartedBefore('')
    setMinPassRate('')
    setPage(1)
  }
  const filtersActive = !!(statusFilter || searchText || startedAfter || startedBefore || minPassRate)

  return (
    <div>
      <div className="toolbar">
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
          className="select-sm"
        >
          <option value="">全部状态</option>
          <option value="running">运行中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="interrupted">已中断</option>
          <option value="stopping">停止中</option>
        </select>
        <input
          type="text" value={searchText}
          onChange={e => { setSearchText(e.target.value); setPage(1) }}
          placeholder="搜运行名 / 模型 / URL / 项目"
          className="input-sm w-[220px]"
        />
        <span className="text-[11px] text-text-tertiary">起</span>
        <input
          type="date" value={startedAfter}
          onChange={e => { setStartedAfter(e.target.value); setPage(1) }}
          className="input-sm"
        />
        <span className="text-[11px] text-text-tertiary">至</span>
        <input
          type="date" value={startedBefore}
          onChange={e => { setStartedBefore(e.target.value); setPage(1) }}
          className="input-sm"
        />
        <input
          type="number" min={0} max={100} step={5}
          value={minPassRate}
          onChange={e => { setMinPassRate(e.target.value); setPage(1) }}
          placeholder="验收通过率 ≥ %"
          className="input-sm w-[110px]"
        />
        {filtersActive && (
          <button
            onClick={clearFilters}
            className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
          >
            清除筛选
          </button>
        )}
        <span className="text-[11px] text-text-tertiary tabular-nums">
          共 {runsQuery.data?.total ?? 0} 条
        </span>
        {selected.size > 0 && (
          <>
            <span className="text-[11px] text-text-tertiary">· 已选 {selected.size}</span>
            <Button
              variant="tinted"
              size="sm"
              disabled={selected.size < 2}
              onClick={() => navigate(`/evaluation/compare?ids=${Array.from(selected).join(',')}`)}
            >
              对比所选（{selected.size}）
            </Button>
            <ExportMenu
              label={`导出明细（${selected.size}）`}
              onExport={async (format: ExportFormat) => {
                try {
                  await evaluationApi.exportRunsSummary(Array.from(selected), format)
                } catch (e) {
                  toast.error(toToastMessage(formatApiError(e)), '导出失败')
                }
              }}
            />
            <button
              onClick={() => setSelected(new Set())}
              className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
            >
              清空
            </button>
          </>
        )}
        <div className="flex-1" />
        <Button variant="primary" size="sm" onClick={onNewRun}>
          新建评估
        </Button>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <Th>
                <span className="sr-only">选择</span>
              </Th>
              <Th>ID</Th>
              <Th>状态</Th>
              <Th>智能体</Th>
              <Th>运行名</Th>
              <Th>进度 / 总数</Th>
              <Th>验收 / 对比裁决</Th>
              <Th>平均时延</Th>
              <Th>启动时间</Th>
              <Th>操作</Th>
            </tr>
          </thead>
          <tbody>
            {runsQuery.isLoading && (
              <tr><td colSpan={10} className="empty-state">加载中…</td></tr>
            )}
            {runsQuery.data?.items.length === 0 && !runsQuery.isLoading && (
              <tr>
                <td colSpan={10} className="empty-state">
                  {filtersActive
                    ? '没有匹配筛选的评估记录'
                    : '还没有评估记录。点右上角「新建评估」启动第一个 run'}
                </td>
              </tr>
            )}
            {runsQuery.data?.items.map(r => (
              <RunRow
                key={r.id}
                run={r}
                selected={selected.has(r.id)}
                deleting={deletingId === r.id}
                onToggle={() => toggle(r.id)}
                onClick={() => navigate(`/evaluation/runs/${r.id}`)}
                onDelete={async () => {
                  const ok = await confirm({
                    title: '删除评估',
                    description: `确定删除评估 ${r.id.slice(0, 8)}？\n这是软删除，可在 DB 恢复 deleted_at 字段。`,
                    confirmText: '删除',
                    danger: true,
                  })
                  if (!ok) return
                  setDeletingId(r.id)
                  try {
                    await evaluationApi.deleteRun(r.id)
                    setSelected(prev => {
                      const next = new Set(prev)
                      next.delete(r.id)
                      return next
                    })
                    qc.invalidateQueries({ queryKey: ['eval-runs'] })
                    toast.success('评估已删除')
                  } catch (err) {
                    toast.error(extractError(err), '删除失败')
                  } finally {
                    setDeletingId(null)
                  }
                }}
              />
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 mt-4 text-[12px] text-text-secondary">
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="pager-btn">
            ‹ 上一页
          </button>
          <span className="tabular-nums">{page} / {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} className="pager-btn">
            下一页 ›
          </button>
        </div>
      )}
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return <th>{children}</th>
}

function RunRow({ run, selected, deleting, onToggle, onClick, onDelete }: {
  run: EvalRunSummary
  selected: boolean
  deleting: boolean
  onToggle: () => void
  onClick: () => void
  onDelete: () => void
}) {
  const facts = deriveFacts(run.summary_scores ?? run)
  const acceptance = deriveAcceptance(run.summary_scores ?? run)
  const total = facts.total || run.progress.total || 0
  const completed = run.progress.completed ?? facts.total ?? 0
  // 通过率仅在配置了显式验收策略时展示；否则标「仅评分」，绝不用分数编造。
  const passRateText = acceptancePassRateText(acceptance)
  const isComparative = run.eval_mode === 'comparative'
  const comparisonSummaries = normalizeComparisonSummary(run.summary_scores?.comparison_summary)
  const qualityText = passRateText ?? '仅评分'
  const avgLatency = firstDefined(
    deriveCostScored(run.summary_scores)?.avg_latency_ms,
    deriveCostAbnormal(run.summary_scores)?.avg_latency_ms,
  )

  const agent = run.agent_config as { model?: string; url?: string; type?: string }
  const agentB = run.agent_config_b as { model?: string; url?: string; type?: string } | null
  const agentLabel = isComparative
    ? `${agent?.model || agent?.type || 'A'} vs ${agentB?.model || agentB?.type || 'B'}`
    : agent?.model || agent?.type || '—'

  return (
    <tr
      onClick={onClick}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      role="button"
      tabIndex={0}
      aria-label={`查看运行 ${run.id.slice(0, 8)}`}
      className="cursor-pointer focus-visible:outline-none focus-visible:shadow-focus"
    >
      <Td>
        <input
          type="checkbox"
          checked={selected}
          onClick={e => e.stopPropagation()}
          onChange={onToggle}
          className="accent-accent w-3.5 h-3.5"
        />
      </Td>
      <Td mono>{run.id.slice(0, 8)}</Td>
      <Td><StatusBadge status={run.status} /></Td>
      <Td>{agentLabel}</Td>
      <Td mono>{run.langfuse_run_name ?? '—'}</Td>
      <Td>
        {run.status === 'running' ? `${completed}/${total || '?'}` : `${total}`}
      </Td>
      <Td>
        {isComparative ? (
          comparisonSummaries.length > 0 ? (
            <div className="space-y-0.5 text-[10px] tabular-nums">
              {comparisonSummaries.map(summary => (
                <div
                  key={summary.evaluator_key}
                  className="whitespace-nowrap"
                  title={`${evaluatorDisplayName(summary)}：A 胜 ${summary.a_wins} · B 胜 ${summary.b_wins} · 平 ${summary.ties} · 评分失败 ${summary.evaluation_errors}`}
                >
                  <span className="text-text-secondary">{evaluatorDisplayName(summary)}：</span>
                  <span className="text-accent">A {summary.a_wins}</span>
                  <span className="text-text-tertiary"> · </span>
                  <span className="text-info">B {summary.b_wins}</span>
                  <span className="text-text-tertiary"> · 平 {summary.ties}</span>
                  {summary.evaluation_errors > 0 && (
                    <span className="text-negative"> · 失败 {summary.evaluation_errors}</span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <span className="text-[11px] text-text-tertiary" title="双模对比运行">双模 · 待出分</span>
          )
        ) : (
          <span title={acceptance.configured
            ? `运行结论：${runDecisionLabel(acceptance.run_decision)}`
            : '未配置验收规则，仅评分'}>
            {qualityText}
          </span>
        )}
      </Td>
      <Td>{avgLatency != null ? `${Math.round(avgLatency)}ms` : '—'}</Td>
      <Td>{fmtTime(run.started_at)}</Td>
      <Td>
        <button
          onClick={e => { e.stopPropagation(); onDelete() }}
          disabled={deleting}
          className="text-action-danger disabled:opacity-50"
          title="软删除：行隐藏但 DB 保留 deleted_at"
        >
          {deleting ? '删除中…' : '删除'}
        </button>
      </Td>
    </tr>
  )
}

function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <td className={mono ? 'font-mono text-[11px]' : ''}>
      {children}
    </td>
  )
}

function StatusBadge({ status }: { status: string }) {
  const tone: Record<string, string> = {
    running: 'badge badge-info',
    completed: 'badge badge-positive',
    failed: 'badge badge-negative',
    stopping: 'badge badge-warning',
    interrupted: 'badge badge-neutral',
    pending: 'badge badge-neutral',
  }
  const cls = tone[status] ?? 'badge badge-neutral'
  return (
    <span className={cls}>
      {status === 'running' && (
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      )}
      {status}
    </span>
  )
}


// ─── New run tab ────────────────────────────────────────────────────────────

type CaseSourceTab = 'benchmark' | 'upload' | 'conversation'
type TraceSource = 'none' | 'langsmith' | 'langfuse'
type EvalMode = 'single' | 'comparative'
type AgentType = 'sse' | 'openai' | 'sse_generic'
type ReplySource = 'live' | 'persisted'
type ReplyCaseOption = { id: string; label: string }

type AgentDraft = {
  type: AgentType
  url: string
  apiKey: string
  model: string
  language: string
  headersText: string
  payloadText: string
  timeout: number
}

const createAgentDraft = (): AgentDraft => ({
  type: 'sse',
  url: '',
  apiKey: '',
  model: '',
  language: '请用中文回复',
  headersText: '',
  payloadText: '',
  timeout: 300,
})

function NewRunTab({ onStarted }: { onStarted: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()

  // Case-source tabs
  const [sourceTab, setSourceTab] = useState<CaseSourceTab>('benchmark')

  // ── benchmark branch ──
  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
  })
  const [projectId, setProjectId] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [searchText, setSearchText] = useState('')
  const [selectionMode, setSelectionMode] = useState<'all' | 'filter' | 'pick'>('all')
  const [pickedCaseIds, setPickedCaseIds] = useState<Set<string>>(new Set())
  const [filterTags, setFilterTags] = useState('')
  const [limit, setLimit] = useState<number | ''>(10)

  const categoriesQuery = useQuery({
    queryKey: ['categories', projectId],
    queryFn: () => projectsApi.getCategories(projectId).then(r => r.data),
    enabled: !!projectId,
  })
  const casesQuery = useQuery({
    queryKey: ['bench-cases-for-eval', projectId, categoryId, searchText],
    queryFn: () =>
      benchmarkApi.listCases(projectId, {
        category_id: categoryId || undefined,
        search: searchText || undefined,
        page: 1, page_size: 100,
      }).then(r => r.data),
    enabled: !!projectId && sourceTab === 'benchmark',
  })

  const effectiveCaseCount = useMemo(() => {
    if (!casesQuery.data) return 0
    if (selectionMode === 'pick') return pickedCaseIds.size
    return casesQuery.data.total
  }, [casesQuery.data, selectionMode, pickedCaseIds])

  // 真正会跑的样例数 = 命中数和 limit 取小（pick 模式不受 limit 影响）
  const willRunCount = useMemo(() => {
    if (selectionMode === 'pick') return pickedCaseIds.size
    if (typeof limit === 'number' && limit > 0) {
      return Math.min(effectiveCaseCount, limit)
    }
    return effectiveCaseCount
  }, [selectionMode, pickedCaseIds, effectiveCaseCount, limit])

  // ── upload branch ──
  const [uploadedSource, setUploadedSource] = useState<UploadCasesResponse | null>(null)
  const uploadMutation = useMutation({
    mutationFn: (file: File) => evaluationApi.uploadCases(file).then(r => r.data),
    onSuccess: (data) => setUploadedSource(data),
  })

  // ── conversation dataset branch（多轮对话集，dataset_type=conversation）──
  const [convDataset, setConvDataset] = useState('')
  // 对齐 benchmark 来源的三种选样方式：全部 / 按类别筛选 / 手动勾选。
  const [convSelectionMode, setConvSelectionMode] = useState<'all' | 'filter' | 'pick'>('all')
  const [convCategoryId, setConvCategoryId] = useState('')  // ConversationCategoryRow UUID
  const [convPickedIds, setConvPickedIds] = useState<Set<string>>(new Set())
  const [convSearch, setConvSearch] = useState('')
  const convDatasetsQuery = useQuery({
    queryKey: ['datasets', 'conversation'],
    queryFn: () => datasetsApi.list({ type: 'conversation' }).then(r => r.data),
    enabled: sourceTab === 'conversation',
  })
  const convCategoriesQuery = useQuery({
    queryKey: ['conv-categories-for-eval', convDataset],
    queryFn: () => datasetsApi.listConvCategories(convDataset).then(r => r.data),
    enabled: !!convDataset && sourceTab === 'conversation',
  })
  // start_eval 用类别 UUID（filter_category_id），但样例列表接口按类别名过滤，
  // 故按选中的 id 反查出名字供列表查询用。
  const convCategoryName = useMemo(
    () => (convCategoriesQuery.data ?? []).find(c => c.id === convCategoryId)?.name,
    [convCategoriesQuery.data, convCategoryId],
  )
  const convCasesQuery = useQuery({
    queryKey: ['conv-cases-for-eval', convDataset, convCategoryName, convSearch],
    queryFn: () => datasetsApi.listCasesPaginated(convDataset, {
      page: 1, page_size: 100,
      search: convSearch || undefined,
      category: convCategoryName || undefined,
    }).then(r => r.data),
    enabled: !!convDataset && sourceTab === 'conversation',
  })
  const convEffectiveCount = useMemo(() => {
    if (convSelectionMode === 'pick') return convPickedIds.size
    return convCasesQuery.data?.total ?? 0
  }, [convCasesQuery.data, convSelectionMode, convPickedIds])
  const convWillRun = useMemo(() => {
    if (convSelectionMode === 'pick') return convPickedIds.size
    if (typeof limit === 'number' && limit > 0) return Math.min(convEffectiveCount, limit)
    return convEffectiveCount
  }, [convSelectionMode, convPickedIds, convEffectiveCount, limit])

  // ── agents / persisted replies ──
  const [evalMode, setEvalMode] = useState<EvalMode>('single')
  const [agentDraft, setAgentDraft] = useState<AgentDraft>(createAgentDraft)
  const [agentDraftB, setAgentDraftB] = useState<AgentDraft>(createAgentDraft)
  const [replySource, setReplySource] = useState<ReplySource>('live')
  const [replySourceB, setReplySourceB] = useState<ReplySource>('live')
  const [replyVersionIds, setReplyVersionIds] = useState<Record<string, string>>({})
  const [replyVersionIdsB, setReplyVersionIdsB] = useState<Record<string, string>>({})
  const [concurrency, setConcurrency] = useState(3)
  const [runName, setRunName] = useState('')

  // 前端预检只使用本次能精确推导出的样例集合；筛选结果超过首批 100 条时，
  // 仍展示已知范围，完整性由后端开跑前的 resolve_reply_versions 最终兜底。
  const replyDatasetType: ReplyDatasetType | null =
    sourceTab === 'benchmark' ? 'benchmark'
    : sourceTab === 'conversation' ? 'conversation'
    : null
  const replyCaseOptions = useMemo<ReplyCaseOption[]>(() => {
    if (sourceTab === 'benchmark') {
      const rows = casesQuery.data?.items ?? []
      const selected = selectionMode === 'pick'
        ? rows.filter(c => pickedCaseIds.has(c.id))
        : rows.slice(0, typeof limit === 'number' && limit > 0 ? limit : undefined)
      return selected.map(c => ({ id: c.id, label: c.question }))
    }
    if (sourceTab === 'conversation') {
      const rows = convCasesQuery.data?.items ?? []
      const selected = convSelectionMode === 'pick'
        ? rows.filter(c => !!c.id && convPickedIds.has(c.id))
        : rows.slice(0, typeof limit === 'number' && limit > 0 ? limit : undefined)
      return selected.flatMap(c => {
        if (!c.id) return []
        const firstUser = (c.input_messages ?? []).find(m => m.role === 'user')?.content
        return [{ id: c.id, label: firstUser || c.name || c.id }]
      })
    }
    return []
  }, [sourceTab, casesQuery.data, selectionMode, pickedCaseIds, limit, convCasesQuery.data, convSelectionMode, convPickedIds])
  const replyCaseRefs = useMemo(() => replyCaseOptions.map(c => c.id), [replyCaseOptions])
  const replyPrecheckExact = useMemo(() => {
    if (sourceTab === 'benchmark') {
      if (selectionMode === 'pick') return true
      const loaded = casesQuery.data?.items.length ?? 0
      const wanted = typeof limit === 'number' && limit > 0 ? Math.min(casesQuery.data?.total ?? 0, limit) : (casesQuery.data?.total ?? 0)
      return loaded >= wanted
    }
    if (sourceTab === 'conversation') {
      if (convSelectionMode === 'pick') return true
      const loaded = convCasesQuery.data?.items.length ?? 0
      const wanted = typeof limit === 'number' && limit > 0 ? Math.min(convCasesQuery.data?.total ?? 0, limit) : (convCasesQuery.data?.total ?? 0)
      return loaded >= wanted
    }
    return false
  }, [sourceTab, selectionMode, casesQuery.data, limit, convSelectionMode, convCasesQuery.data])
  const persistedEnabled = replySource === 'persisted' || (evalMode === 'comparative' && replySourceB === 'persisted')
  const replyStatesQuery = useQuery({
    queryKey: ['agent-reply-states', replyDatasetType, replyCaseRefs],
    queryFn: () => agentRepliesApi.listStates(replyDatasetType!, replyCaseRefs).then(r => r.data),
    enabled: persistedEnabled && !!replyDatasetType && replyCaseRefs.length > 0,
  })
  const replyStates = replyStatesQuery.data ?? []
  const replyStateMap = new Map(replyStates.map(s => [s.case_ref, s]))
  const missingReplyCases = replyCaseOptions.filter(c => !replyStateMap.get(c.id)?.has_reply)
  // Trace 来源：'none' = 不关联外部 trace（纯本地评分）；'langsmith' = agent
  // 自报 trace 到 LangSmith，运行后按 project+时间窗回查贴回 langsmith_run_id；
  // 'langfuse' = agent 自报 trace 到 Langfuse，运行后按 trace name+时间窗回查、
  // 按 question 匹配贴回 langfuse_trace_id（对称于 langsmith，project 由凭据对固定）。
  const [traceSource, setTraceSource] = useState<TraceSource>('none')
  const [langsmithProject, setLangsmithProject] = useState('')
  const [langfuseTraceName, setLangfuseTraceName] = useState('')

  // Multi-value config options — pickers in the form let users reuse
  // pre-saved presets from /config; the URL field also auto-prefills with
  // the default option on first load (falling back to a localhost hint
  // when no config is set).
  const endpointOpts = useConfigOptions('target_agent.endpoint_url')
  const apiKeyOpts = useConfigOptions('target_agent.api_key')
  const timeoutOpts = useConfigOptions('target_agent.timeout')
  const headersOpts = useConfigOptions('target_agent.headers')
  const payloadOpts = useConfigOptions('target_agent.request_template')
  const prefilledRef = useRef(false)
  useEffect(() => {
    if (prefilledRef.current) return
    if (endpointOpts.isLoading) return
    prefilledRef.current = true
    const defaultUrl = endpointOpts.defaultValue
      ? configOptionToString(endpointOpts.defaultValue)
      : 'http://localhost:18094/api/agent/langgraph'
    setAgentDraft(prev => prev.url ? prev : { ...prev, url: defaultUrl })
    setAgentDraftB(prev => prev.url ? prev : { ...prev, url: defaultUrl })
  }, [endpointOpts.isLoading, endpointOpts.defaultValue])

  // ── evaluator instances ──
  const evaluatorsQuery = useQuery({
    queryKey: ['evaluator-instances-active'],
    queryFn: () => evaluationApi.listEvaluators(true).then(r => r.data),
  })
  const [selectedEvaluatorIds, setSelectedEvaluatorIds] = useState<Set<string>>(new Set())

  // ── start mutation ──
  const startMutation = useMutation({
    mutationFn: (body: StartEvalRequest) => evaluationApi.startRun(body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-runs'] })
      onStarted()
    },
  })

  const hasCaseSource =
    sourceTab === 'benchmark' ? !!projectId
    : sourceTab === 'conversation' ? !!convDataset
    : !!uploadedSource
  const startBlockers: string[] = []
  if (!hasCaseSource) {
    startBlockers.push(
      sourceTab === 'benchmark' ? '选择基准项目'
      : sourceTab === 'conversation' ? '选择一个多轮对话集'
      : '上传一个样例文件',
    )
  }
  if (sourceTab === 'benchmark' && projectId && selectionMode === 'pick' && pickedCaseIds.size === 0) {
    startBlockers.push('勾选至少 1 条基准样例')
  }
  if (sourceTab === 'conversation' && convDataset && convSelectionMode === 'pick' && convPickedIds.size === 0) {
    startBlockers.push('勾选至少 1 条对话样例')
  }
  if (selectedEvaluatorIds.size === 0) startBlockers.push('勾选至少 1 个评估器')
  if (replySource === 'live' && agentDraft.url.trim().length === 0) {
    startBlockers.push(evalMode === 'comparative' ? '填写 A 模型智能体 URL' : '填写智能体 URL')
  }
  if (evalMode === 'comparative' && replySourceB === 'live' && agentDraftB.url.trim().length === 0) {
    startBlockers.push('填写 B 模型智能体 URL')
  }
  if (persistedEnabled && sourceTab === 'upload') {
    startBlockers.push('上传文件不支持使用已有回复')
  }
  if (persistedEnabled && replyCaseRefs.length > 0 && replyStatesQuery.isLoading) {
    startBlockers.push('等待已有回复预检完成')
  }
  if (persistedEnabled && replyPrecheckExact && !replyStatesQuery.isLoading && missingReplyCases.length > 0) {
    startBlockers.push(`${missingReplyCases.length} 条样例没有可用的已有回复`)
  }
  const canStart = startBlockers.length === 0 && !startMutation.isPending

  const parseAgentDraft = (draft: AgentDraft, label: string): EvalAgentConfig | null => {
    let headers: Record<string, string> | undefined
    let payloadTpl: Record<string, unknown> | undefined
    try {
      if (draft.headersText.trim()) headers = JSON.parse(draft.headersText)
    } catch { toast.error(`${label}请求头必须是合法 JSON`); return null }
    try {
      if (draft.payloadText.trim()) payloadTpl = JSON.parse(draft.payloadText)
    } catch { toast.error(`${label}请求体模板必须是合法 JSON`); return null }

    return {
      type: draft.type,
      url: draft.url.trim(),
      api_key: draft.apiKey || undefined,
      model: draft.model || undefined,
      headers,
      payload_template: payloadTpl,
      timeout: draft.timeout,
      language: draft.language,
    }
  }

  const handleStart = () => {
    // 后端 schema 始终要求 agent / agent_b。persisted 侧传不会被调用的占位配置，
    // runner 会依据 _reply_version 切到 PersistedReplyAdapter，不建立任何网络连接。
    const persistedAgent: EvalAgentConfig = {
      type: 'sse',
      url: 'persisted://agent-reply',
      model: 'persisted-reply',
    }
    const agent = replySource === 'persisted'
      ? persistedAgent
      : parseAgentDraft(agentDraft, evalMode === 'comparative' ? 'A 模型' : '')
    if (!agent) return

    const body: StartEvalRequest = {
      agent,
      evaluator_ids: Array.from(selectedEvaluatorIds),
      concurrency,
      run_name: runName.trim() || null,
      langsmith_project: traceSource === 'langsmith' ? (langsmithProject.trim() || null) : null,
      langfuse_trace_name: traceSource === 'langfuse' ? (langfuseTraceName.trim() || null) : null,
      reply_source: replySource,
      reply_version_ids: replySource === 'persisted' ? replyVersionIds : {},
    }

    if (evalMode === 'comparative') {
      const agentB = replySourceB === 'persisted'
        ? persistedAgent
        : parseAgentDraft(agentDraftB, 'B 模型')
      if (!agentB) return
      body.eval_mode = 'comparative'
      body.agent_b = agentB
      body.reply_source_b = replySourceB
      body.reply_version_ids_b = replySourceB === 'persisted' ? replyVersionIdsB : {}
    }

    if (sourceTab === 'upload' && uploadedSource) {
      body.case_source_id = uploadedSource.source_id
      body.limit = typeof limit === 'number' ? limit : null
    } else if (sourceTab === 'conversation') {
      // 多轮对话集：直读 LangSmith dataset，runner 走 multiturn 回放+逐轮/会话级打分。
      // 选样方式对齐 benchmark：pick=勾选具体样例(case_ids)，filter=按类别(filter_category_id)+limit，
      // all=全量+limit。case_ids/filter_category_id 复用 StartEvalRequest 既有字段，后端内存筛。
      body.conversation_dataset = convDataset
      if (convSelectionMode === 'pick') {
        body.case_ids = Array.from(convPickedIds)
      } else if (convSelectionMode === 'filter') {
        body.filter_category_id = convCategoryId || null
        body.limit = typeof limit === 'number' ? limit : null
      } else {
        body.limit = typeof limit === 'number' ? limit : null
      }
    } else {
      body.project_id = projectId
      if (selectionMode === 'pick') {
        body.case_ids = Array.from(pickedCaseIds)
      } else if (selectionMode === 'filter') {
        body.filter_category_id = categoryId || null
        body.filter_tags = filterTags.split(',').map(t => t.trim()).filter(Boolean) || null
        body.limit = typeof limit === 'number' ? limit : null
      } else {
        body.filter_category_id = categoryId || null
        body.limit = typeof limit === 'number' ? limit : null
      }
    }

    startMutation.mutate(body)
  }

  return (
    <div className="flex flex-col gap-5 max-w-[900px]">
      {/* Step 1: case source */}
      <Section title="1. 选择样例来源">
        <div className="page-tabs mb-3">
          {([
            { id: 'benchmark', label: '从基准数据集' },
            { id: 'upload', label: '上传文件' },
            { id: 'conversation', label: '多轮对话集' },
          ] as { id: CaseSourceTab; label: string }[]).map(t => (
            <button
              key={t.id}
              onClick={() => setSourceTab(t.id)}
              className={`page-tab ${sourceTab === t.id ? 'page-tab-active' : ''}`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {sourceTab === 'benchmark' && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="项目">
                <select
                  value={projectId}
                  onChange={e => {
                    setProjectId(e.target.value)
                    setCategoryId('')
                    setPickedCaseIds(new Set())
                  }}
                  className="input"
                >
                  <option value="">— 选择项目 —</option>
                  {projectsQuery.data?.map((p: Project) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="分类（可选）">
                <select
                  value={categoryId}
                  onChange={e => setCategoryId(e.target.value)}
                  disabled={!projectId}
                  className="input disabled:opacity-50"
                >
                  <option value="">全部分类</option>
                  {categoriesQuery.data?.map(c => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </Field>
            </div>

            <div className="flex items-center gap-3 mt-3 mb-2">
              {(['all', 'filter', 'pick'] as const).map(m => (
                <label key={m} className="inline-flex items-center gap-1.5 text-[12px] cursor-pointer">
                  <input type="radio" checked={selectionMode === m} onChange={() => setSelectionMode(m)} className="accent-accent" />
                  {m === 'all' && '全部'}
                  {m === 'filter' && '按条件筛选'}
                  {m === 'pick' && '手动勾选'}
                </label>
              ))}
            </div>

            {/* Selection summary banner */}
            {projectId && (
              <div className={`flex items-center gap-2 mb-3 px-3 py-2 rounded-md border text-[12px] ${
                willRunCount === 0
                  ? 'border-warning/30 bg-warning/10 text-warning'
                  : 'border-accent/30 bg-accent/5 text-text-primary'
              }`}>
                <span className="text-[14px]">{willRunCount === 0 ? '⚠' : '✓'}</span>
                <span>
                  本次将运行 <span className="font-mono font-medium">{willRunCount}</span> 条样例
                  {selectionMode === 'pick' && pickedCaseIds.size > 0 && (
                    <span className="text-text-tertiary ml-1.5">（手动勾选）</span>
                  )}
                  {selectionMode !== 'pick' && (
                    <>
                      <span className="text-text-tertiary ml-1.5">
                        （命中 {effectiveCaseCount} 条
                        {typeof limit === 'number' && limit > 0 && limit < effectiveCaseCount && `，受 limit ${limit} 限制`}
                        ）
                      </span>
                    </>
                  )}
                </span>
                {casesQuery.isLoading && <span className="text-text-tertiary ml-auto text-[11px]">载入中…</span>}
              </div>
            )}

            {selectionMode !== 'pick' && (
              <Field label="最多跑多少条（空=不限制）">
                <input
                  type="number" min={1} value={limit}
                  onChange={e => setLimit(e.target.value ? Number(e.target.value) : '')}
                  className="input max-w-[180px]"
                />
              </Field>
            )}
            {selectionMode === 'filter' && (
              <Field label="标签（逗号分隔）">
                <input
                  type="text" value={filterTags}
                  onChange={e => setFilterTags(e.target.value)}
                  placeholder="例如：电池,维修" className="input"
                />
              </Field>
            )}
            {selectionMode === 'pick' && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <input
                    type="text" placeholder="搜索问题文本…"
                    value={searchText} onChange={e => setSearchText(e.target.value)}
                    className="input flex-1"
                  />
                  <Button variant="secondary" size="sm" onClick={() => setPickedCaseIds(new Set())}>
                    清空
                  </Button>
                </div>
                <div className="border border-border rounded-md max-h-[280px] overflow-y-auto bg-surface">
                  {casesQuery.data?.items.map((c: BenchmarkCase) => {
                    const checked = pickedCaseIds.has(c.id)
                    return (
                      <label key={c.id}
                             className="flex items-start gap-2 py-1.5 px-2.5 hover:bg-fill/5 cursor-pointer text-[12px] border-b border-separator last:border-b-0">
                        <input
                          type="checkbox" checked={checked}
                          onChange={() => {
                            const s = new Set(pickedCaseIds)
                            if (s.has(c.id)) s.delete(c.id); else s.add(c.id)
                            setPickedCaseIds(s)
                          }}
                          className="mt-0.5 accent-accent shrink-0"
                        />
                        <span className="flex-1 break-all">
                          {c.question.length > 120 ? c.question.slice(0, 120) + '…' : c.question}
                        </span>
                      </label>
                    )
                  })}
                  {casesQuery.data?.items.length === 0 && (
                    <div className="empty-state">
                      {projectId ? '没有匹配的样例' : '先选项目'}
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {sourceTab === 'upload' && (
          <div>
            <div className="flex items-center gap-3">
              <input
                type="file" accept=".json,.jsonl"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) uploadMutation.mutate(f)
                }}
                className="text-[12px]"
              />
              {uploadMutation.isPending && <span className="text-[11px] text-text-tertiary">上传解析中…</span>}
              {uploadedSource && (
                <span className="text-[11px] text-positive">
                  已上传 {uploadedSource.count} 条：{uploadedSource.name}
                </span>
              )}
            </div>
            <p className="text-[10px] text-text-tertiary mt-2">
              支持 JSON（含 <code>test_cases</code> 数组或顶层数组）和 JSONL。每条必须有 <code>question</code> 字段。
              期望答案写在 <code>expected_output</code> 或 <code>reference_answer</code>。
            </p>
            {uploadedSource?.preview && uploadedSource.preview.length > 0 && (
              <div className="mt-3 border border-border rounded-md bg-fill/5 p-2.5">
                <div className="page-eyebrow mb-1">前 3 条预览</div>
                {uploadedSource.preview.map((c, i) => {
                  const q = String((c as { question?: unknown }).question ?? '')
                  return (
                    <div key={i} className="text-[11px] py-1 border-b border-separator last:border-b-0">
                      <span className="font-mono text-text-tertiary mr-2">
                        {String((c as { name?: unknown }).name ?? '')}
                      </span>
                      {q.length > 100 ? q.slice(0, 100) + '…' : q}
                    </div>
                  )
                })}
              </div>
            )}
            {uploadedSource && (
              <Field label="最多跑多少条（空=全部）">
                <input
                  type="number" min={1} value={limit}
                  onChange={e => setLimit(e.target.value ? Number(e.target.value) : '')}
                  className="input max-w-[180px] mt-3"
                />
              </Field>
            )}
          </div>
        )}

        {sourceTab === 'conversation' && (
          <div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="多轮对话数据集">
                <select
                  value={convDataset}
                  onChange={e => {
                    setConvDataset(e.target.value)
                    setConvCategoryId('')
                    setConvPickedIds(new Set())
                  }}
                  className="input"
                >
                  <option value="">选择对话数据集…</option>
                  {(convDatasetsQuery.data ?? []).map(d => (
                    <option key={d.name} value={d.name}>
                      {d.name}（{d.example_count} 条）
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="分类（可选）">
                <select
                  value={convCategoryId}
                  onChange={e => setConvCategoryId(e.target.value)}
                  disabled={!convDataset}
                  className="input disabled:opacity-50"
                >
                  <option value="">全部分类</option>
                  {(convCategoriesQuery.data ?? []).map(c => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </Field>
            </div>

            {convDatasetsQuery.data && convDatasetsQuery.data.length === 0 && (
              <p className="text-[11px] text-text-tertiary mt-2">
                还没有多轮对话数据集。请先到「多轮对话集」页创建并录入样例。
              </p>
            )}
            <p className="text-[10px] text-text-tertiary mt-2">
              固定 thread_id 逐轮回放每条样例的 user 消息给智能体，按 turn_expectations 逐轮打分、
              按 conversation_goal 做会话级打分。
            </p>

            {convDataset && (
              <>
                <div className="flex items-center gap-3 mt-3 mb-2">
                  {(['all', 'filter', 'pick'] as const).map(m => (
                    <label key={m} className="inline-flex items-center gap-1.5 text-[12px] cursor-pointer">
                      <input type="radio" checked={convSelectionMode === m} onChange={() => setConvSelectionMode(m)} className="accent-accent" />
                      {m === 'all' && '全部'}
                      {m === 'filter' && '按分类筛选'}
                      {m === 'pick' && '手动勾选'}
                    </label>
                  ))}
                </div>

                {/* 选样汇总横幅（对齐 benchmark） */}
                <div className={`flex items-center gap-2 mb-3 px-3 py-2 rounded-md border text-[12px] ${
                  convWillRun === 0
                    ? 'border-warning/30 bg-warning/10 text-warning'
                    : 'border-accent/30 bg-accent/5 text-text-primary'
                }`}>
                  <span className="text-[14px]">{convWillRun === 0 ? '⚠' : '✓'}</span>
                  <span>
                    本次将运行 <span className="font-mono font-medium">{convWillRun}</span> 条样例
                    {convSelectionMode === 'pick' && convPickedIds.size > 0 && (
                      <span className="text-text-tertiary ml-1.5">（手动勾选）</span>
                    )}
                    {convSelectionMode !== 'pick' && (
                      <span className="text-text-tertiary ml-1.5">
                        （命中 {convEffectiveCount} 条
                        {typeof limit === 'number' && limit > 0 && limit < convEffectiveCount && `，受 limit ${limit} 限制`}
                        ）
                      </span>
                    )}
                  </span>
                  {convCasesQuery.isLoading && <span className="text-text-tertiary ml-auto text-[11px]">载入中…</span>}
                </div>

                {convSelectionMode !== 'pick' && (
                  <Field label="最多跑多少条（空=全部）">
                    <input
                      type="number" min={1} value={limit}
                      onChange={e => setLimit(e.target.value ? Number(e.target.value) : '')}
                      className="input max-w-[180px]"
                    />
                  </Field>
                )}

                {convSelectionMode === 'pick' && (
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <input
                        type="text" placeholder="搜索样例名 / 描述…"
                        value={convSearch} onChange={e => setConvSearch(e.target.value)}
                        className="input flex-1"
                      />
                      <Button variant="secondary" size="sm" onClick={() => setConvPickedIds(new Set())}>
                        清空
                      </Button>
                    </div>
                    <div className="border border-border rounded-md max-h-[280px] overflow-y-auto bg-surface">
                      {convCasesQuery.data?.items.map((c: TestCase) => {
                        const cid = c.id ?? ''
                        const checked = convPickedIds.has(cid)
                        const firstUser = (c.input_messages ?? []).find(m => m.role === 'user')?.content ?? ''
                        // 展示 question（首条 user 消息）而非样例名；question 为空才退回样例名。
                        const label = firstUser || c.name
                        return (
                          <label key={cid}
                                 className="flex items-start gap-2 py-1.5 px-2.5 hover:bg-fill/5 cursor-pointer text-[12px] border-b border-separator last:border-b-0">
                            <input
                              type="checkbox" checked={checked}
                              onChange={() => {
                                const s = new Set(convPickedIds)
                                if (s.has(cid)) s.delete(cid); else s.add(cid)
                                setConvPickedIds(s)
                              }}
                              className="mt-0.5 accent-accent shrink-0"
                            />
                            <span className="flex-1 break-all">
                              {label.length > 120 ? label.slice(0, 120) + '…' : label}
                            </span>
                          </label>
                        )
                      })}
                      {convCasesQuery.data?.items.length === 0 && (
                        <div className="empty-state">没有匹配的样例</div>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </Section>

      {/* Step 2: agents */}
      <Section title="2. 配置智能体">
        <div className="page-tabs mb-3">
          {([
            { id: 'single', label: '单模评估' },
            { id: 'comparative', label: '双模对比' },
          ] as { id: EvalMode; label: string }[]).map(mode => (
            <button
              key={mode.id}
              type="button"
              onClick={() => setEvalMode(mode.id)}
              className={`page-tab ${evalMode === mode.id ? 'page-tab-active' : ''}`}
            >
              {mode.label}
            </button>
          ))}
        </div>

        <div className="flex flex-col gap-4">
          <ReplySourcePanel
            title={evalMode === 'comparative' ? 'A 模型' : '智能体'}
            source={replySource}
            onSourceChange={setReplySource}
            datasetType={replyDatasetType}
            cases={replyCaseOptions}
            states={replyStates}
            statesLoading={replyStatesQuery.isLoading}
            precheckExact={replyPrecheckExact}
            versionIds={replyVersionIds}
            onVersionIdsChange={setReplyVersionIds}
            persistedDisabled={sourceTab === 'upload'}
          >
            <AgentConfigFields
              title={evalMode === 'comparative' ? 'A 模型 · 实时调用配置' : '实时调用配置'}
              draft={agentDraft}
              onChange={setAgentDraft}
              endpointOptions={endpointOpts.options}
              apiKeyOptions={apiKeyOpts.options}
              timeoutOptions={timeoutOpts.options}
              headersOptions={headersOpts.options}
              payloadOptions={payloadOpts.options}
            />
          </ReplySourcePanel>
          {evalMode === 'comparative' && (
            <ReplySourcePanel
              title="B 模型"
              source={replySourceB}
              onSourceChange={setReplySourceB}
              datasetType={replyDatasetType}
              cases={replyCaseOptions}
              states={replyStates}
              statesLoading={replyStatesQuery.isLoading}
              precheckExact={replyPrecheckExact}
              versionIds={replyVersionIdsB}
              onVersionIdsChange={setReplyVersionIdsB}
              persistedDisabled={sourceTab === 'upload'}
            >
              <AgentConfigFields
                title="B 模型 · 实时调用配置"
                draft={agentDraftB}
                onChange={setAgentDraftB}
                endpointOptions={endpointOpts.options}
                apiKeyOptions={apiKeyOpts.options}
                timeoutOptions={timeoutOpts.options}
                headersOptions={headersOpts.options}
                payloadOptions={payloadOpts.options}
              />
            </ReplySourcePanel>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3 mt-4 pt-4 border-t border-separator">
          <Field label="并发数">
            <input type="number" min={1} max={20} value={concurrency} onChange={e => setConcurrency(Number(e.target.value))} className="input" />
          </Field>
          <Field label="Trace 来源">
            <select value={traceSource} onChange={e => setTraceSource(e.target.value as TraceSource)} className="input">
              <option value="none">不关联（仅本地评分）</option>
              <option value="langsmith">LangSmith（回拉 agent trace）</option>
              <option value="langfuse">Langfuse（回拉 trace）</option>
            </select>
          </Field>
          {traceSource === 'langsmith' && (
            <Field label="LangSmith 项目（agent 自报的项目名）">
              <input type="text" value={langsmithProject} onChange={e => setLangsmithProject(e.target.value)}
                     placeholder="例如：ep-agent / ruyi-agent" className="input" />
            </Field>
          )}
          {traceSource === 'langfuse' && (
            <Field label="Langfuse trace 名称（agent 自报的 trace name）">
              <input type="text" value={langfuseTraceName} onChange={e => setLangfuseTraceName(e.target.value)}
                     placeholder="例如：ep-agent-chat" className="input" />
            </Field>
          )}
        </div>
      </Section>

      {/* Step 3: evaluators */}
      <Section title="3. 评估器">
        {evaluatorsQuery.isLoading && (
          <p className="text-[12px] text-text-tertiary">加载评估器…</p>
        )}
        {!evaluatorsQuery.isLoading && (evaluatorsQuery.data?.length ?? 0) === 0 && (
          <p className="text-[12px] text-text-tertiary">
            还没有评估器实例。到左侧菜单「评估器」页面先建一个。
          </p>
        )}
        <div className="flex flex-col gap-2">
          {evaluatorsQuery.data?.map((e: EvaluatorInstance) => {
            const checked = selectedEvaluatorIds.has(e.id)
            return (
              <label key={e.id}
                     className={`flex items-start gap-2 border rounded-md p-3 cursor-pointer transition-colors ${
                       checked ? 'border-accent bg-accent/5' : 'border-border hover:border-border-strong'
                     }`}>
                <input
                  type="checkbox" checked={checked}
                  onChange={() => {
                    const s = new Set(selectedEvaluatorIds)
                    if (s.has(e.id)) s.delete(e.id); else s.add(e.id)
                    setSelectedEvaluatorIds(s)
                  }}
                  className="mt-0.5 accent-accent"
                />
                <div className="flex-1">
                  <div className="font-medium text-[12px] flex items-center gap-2">
                    {e.name}
                    <span className="badge badge-accent font-mono" title="写到 Langfuse trace 的 tag">
                      {e.tag || e.name}
                    </span>
                  </div>
                  <div className="text-[11px] text-text-tertiary mt-0.5">{e.description || '—'}</div>
                </div>
              </label>
            )
          })}
        </div>
      </Section>

      {/* Step 4: run name */}
      <Section title="4. 运行名（可选）">
        <input
          type="text" value={runName} onChange={e => setRunName(e.target.value)}
          placeholder="默认自动按时间戳生成" className="input max-w-[420px]"
        />
      </Section>

      {/* Start */}
      <div className="flex items-center gap-3 pt-3 border-t border-separator">
        <Button
          variant="primary"
          size="lg"
          disabled={!canStart}
          loading={startMutation.isPending}
          onClick={handleStart}
          title={
            startBlockers.length > 0
              ? `还差：${startBlockers.map((b, i) => `${i + 1}) ${b}`).join('  ')}`
              : '启动评估'
          }
        >
          启动评估
        </Button>
        {startBlockers.length > 0 && !startMutation.isPending && (
          <span className="text-[11px] text-text-tertiary">
            还差：{startBlockers.map((b, i) => (
              <span key={i} className="ml-2">
                <span className="inline-block min-w-[1em] text-center text-text-tertiary mr-0.5">{i + 1})</span>
                {b}
              </span>
            ))}
          </span>
        )}
        {startMutation.isError && (
          <span className="text-[11px] text-negative">
            启动失败：{extractError(startMutation.error)}
          </span>
        )}
      </div>
    </div>
  )
}


// ─── Small helpers ──────────────────────────────────────────────────────────

type ReplySourcePanelProps = {
  title: string
  source: ReplySource
  onSourceChange: (source: ReplySource) => void
  datasetType: ReplyDatasetType | null
  cases: ReplyCaseOption[]
  states: CaseReplyState[]
  statesLoading: boolean
  precheckExact: boolean
  versionIds: Record<string, string>
  onVersionIdsChange: (value: Record<string, string>) => void
  persistedDisabled: boolean
  children: React.ReactNode
}

/**
 * 单模 / A/B 每一侧独立选择回复来源。默认消费各样例当前版本；只有用户选中
 * 某条样例做覆盖时才请求该条的历史版本，避免列表较大时产生 N 个请求。
 */
function ReplySourcePanel({
  title,
  source,
  onSourceChange,
  datasetType,
  cases,
  states,
  statesLoading,
  precheckExact,
  versionIds,
  onVersionIdsChange,
  persistedDisabled,
  children,
}: ReplySourcePanelProps) {
  const [overrideCaseRef, setOverrideCaseRef] = useState('')
  const stateMap = new Map(states.map(state => [state.case_ref, state]))
  const missing = cases.filter(item => !stateMap.get(item.id)?.has_reply)
  const ready = cases.length - missing.length

  return (
    <div className="rounded-md border border-border p-3">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="page-eyebrow">{title}</div>
        <div className="inline-flex rounded-md border border-border bg-fill/5 p-0.5">
          <button
            type="button"
            onClick={() => onSourceChange('live')}
            className={`px-2.5 py-1 rounded text-[11px] transition-colors ${
              source === 'live' ? 'bg-surface text-text-primary shadow-sm' : 'text-text-tertiary hover:text-text-primary'
            }`}
          >
            实时调用 agent
          </button>
          <button
            type="button"
            disabled={persistedDisabled}
            onClick={() => !persistedDisabled && onSourceChange('persisted')}
            title={persistedDisabled ? '上传文件没有稳定的样例 ID，不能匹配已有回复' : '不再调用 agent，直接消费已生成回复'}
            className={`px-2.5 py-1 rounded text-[11px] transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
              source === 'persisted' ? 'bg-surface text-text-primary shadow-sm' : 'text-text-tertiary hover:text-text-primary'
            }`}
          >
            使用已有回复
          </button>
        </div>
      </div>

      {source === 'live' ? children : (
        <div className="space-y-3">
          <p className="text-[11px] text-text-secondary">
            评估将直接回放持久化回复，不建立 SSE / agent 连接。默认使用每条样例的当前版本。
          </p>

          {statesLoading ? (
            <div className="rounded-md border border-border bg-fill/5 px-3 py-2 text-[11px] text-text-tertiary">
              正在预检已有回复…
            </div>
          ) : (
            <div className={`rounded-md border px-3 py-2 text-[11px] ${
              missing.length > 0
                ? 'border-warning/30 bg-warning/10 text-warning'
                : 'border-positive/30 bg-positive/5 text-positive'
            }`}>
              已检查 {cases.length} 条：{ready} 条可用
              {missing.length > 0 && `，${missing.length} 条缺少回复`}
              {!precheckExact && (
                <span className="text-text-tertiary ml-1">
                  （当前仅预检已加载范围，启动时后端会校验最终样例集）
                </span>
              )}
            </div>
          )}

          {missing.length > 0 && (
            <div className="text-[11px] text-warning break-all">
              缺失：{missing.slice(0, 5).map(item => item.label).join('；')}
              {missing.length > 5 && ` 等 ${missing.length} 条`}
            </div>
          )}

          {cases.length > 0 && datasetType && (
            <div className="grid grid-cols-2 gap-3 pt-2 border-t border-separator">
              <Field label="覆盖历史版本（可选）">
                <select
                  value={overrideCaseRef}
                  onChange={e => setOverrideCaseRef(e.target.value)}
                  className="input"
                >
                  <option value="">选择一条样例…</option>
                  {cases.filter(item => stateMap.get(item.id)?.has_reply).map(item => (
                    <option key={item.id} value={item.id}>{item.label}</option>
                  ))}
                </select>
              </Field>
              <ReplyVersionOverride
                datasetType={datasetType}
                caseRef={overrideCaseRef}
                value={overrideCaseRef ? (versionIds[overrideCaseRef] ?? '') : ''}
                onChange={versionId => {
                  if (!overrideCaseRef) return
                  const next = { ...versionIds }
                  if (versionId) next[overrideCaseRef] = versionId
                  else delete next[overrideCaseRef]
                  onVersionIdsChange(next)
                }}
              />
            </div>
          )}

          {Object.keys(versionIds).length > 0 && (
            <div className="text-[10px] text-text-tertiary">
              已为 {Object.keys(versionIds).length} 条样例指定历史版本；其余使用当前版本。
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ReplyVersionOverride({
  datasetType,
  caseRef,
  value,
  onChange,
}: {
  datasetType: ReplyDatasetType
  caseRef: string
  value: string
  onChange: (versionId: string) => void
}) {
  const versionsQuery = useQuery({
    queryKey: ['agent-reply-versions', datasetType, caseRef],
    queryFn: () => agentRepliesApi.listVersions(datasetType, caseRef).then(r => r.data),
    enabled: !!caseRef,
  })
  const versions = (versionsQuery.data ?? []).filter(version => version.status === 'succeeded')

  return (
    <Field label="回复版本">
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={!caseRef || versionsQuery.isLoading}
        className="input disabled:opacity-50"
      >
        <option value="">
          {!caseRef ? '先选择样例' : versionsQuery.isLoading ? '加载版本中…' : '当前版本（默认）'}
        </option>
        {versions.map(version => (
          <option key={version.id} value={version.id}>
            v{version.version_number}{version.version_label ? ` · ${version.version_label}` : ''}
            {version.is_current ? '（当前）' : ''}
          </option>
        ))}
      </select>
      {versionsQuery.isError && (
        <span className="text-[10px] text-negative mt-1">版本加载失败，请重试。</span>
      )}
    </Field>
  )
}

function AgentConfigFields({
  title,
  draft,
  onChange,
  endpointOptions,
  apiKeyOptions,
  timeoutOptions,
  headersOptions,
  payloadOptions,
}: {
  title: string
  draft: AgentDraft
  onChange: (draft: AgentDraft) => void
  endpointOptions: ConfigOption[]
  apiKeyOptions: ConfigOption[]
  timeoutOptions: ConfigOption[]
  headersOptions: ConfigOption[]
  payloadOptions: ConfigOption[]
}) {
  const update = <K extends keyof AgentDraft>(key: K, value: AgentDraft[K]) => {
    onChange({ ...draft, [key]: value })
  }

  return (
    <div className="rounded-md border border-border p-3">
      <div className="page-eyebrow mb-2">{title}</div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="类型">
          <select value={draft.type} onChange={e => update('type', e.target.value as AgentType)} className="input">
            <option value="sse">SSE (LangGraph v2)</option>
            <option value="openai">OpenAI 兼容</option>
            <option value="sse_generic">SSE 通用模板</option>
          </select>
        </Field>
        <Field label="模型（可选，展示用）">
          <input type="text" value={draft.model} onChange={e => update('model', e.target.value)} className="input" />
        </Field>
        <Field label="智能体 URL">
          <div className="relative">
            <input
              type="text"
              value={draft.url}
              onChange={e => update('url', e.target.value)}
              placeholder="http://localhost:18094/api/agent/langgraph"
              className="input pr-9"
            />
            <OptionPicker
              options={endpointOptions}
              currentValue={draft.url}
              onPick={v => update('url', configOptionToString(v))}
            />
          </div>
        </Field>
        <Field label="API Key（可选）">
          <div className="relative">
            <input type="password" value={draft.apiKey} onChange={e => update('apiKey', e.target.value)} className="input pr-9" />
            <OptionPicker
              options={apiKeyOptions}
              currentValue={draft.apiKey}
              onPick={v => update('apiKey', configOptionToString(v))}
              maskValues
            />
          </div>
        </Field>
        <Field label="超时（秒）">
          <div className="relative">
            <input
              type="number"
              min={10}
              value={draft.timeout}
              onChange={e => update('timeout', Number(e.target.value))}
              className="input pr-9"
            />
            <OptionPicker
              options={timeoutOptions}
              currentValue={String(draft.timeout)}
              onPick={v => {
                const n = Number(configOptionToString(v))
                if (!Number.isNaN(n) && n > 0) update('timeout', n)
              }}
            />
          </div>
        </Field>
        {draft.type === 'sse' && (
          <Field label="language 参数">
            <input type="text" value={draft.language} onChange={e => update('language', e.target.value)} className="input" />
          </Field>
        )}
      </div>

      <details className="mt-3">
        <summary className="text-[11px] text-text-secondary cursor-pointer">高级：自定义 headers / payload</summary>
        <div className="grid grid-cols-2 gap-3 mt-2">
          <Field label="请求头 (JSON)">
            <div className="relative">
              <textarea
                value={draft.headersText}
                onChange={e => update('headersText', e.target.value)}
                rows={3}
                placeholder='{"X-Custom": "value"}'
                className="input pr-9 font-mono text-[11px]"
              />
              <OptionPicker
                options={headersOptions}
                currentValue={draft.headersText}
                onPick={v => update('headersText', configOptionToString(v))}
              />
            </div>
          </Field>
          <Field label="请求体模板 (JSON, SSE 通用专用)">
            <div className="relative">
              <textarea
                value={draft.payloadText}
                onChange={e => update('payloadText', e.target.value)}
                rows={3}
                placeholder='{"question": "{input}"}'
                className="input pr-9 font-mono text-[11px]"
              />
              <OptionPicker
                options={payloadOptions}
                currentValue={draft.payloadText}
                onPick={v => update('payloadText', configOptionToString(v))}
              />
            </div>
          </Field>
        </div>
      </details>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-4">
      <h3 className="text-subhead font-semibold mb-3 text-text-primary">{title}</h3>
      {children}
    </section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-0">
      <span className="field-label">{label}</span>
      {children}
    </label>
  )
}

function firstDefined<T>(...vals: (T | null | undefined)[]): T | null {
  for (const v of vals) {
    if (v !== null && v !== undefined) return v
  }
  return null
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function extractError(err: unknown): string {
  return toToastMessage(formatApiError(err, { fallbackMessage: '未知错误' }))
}

