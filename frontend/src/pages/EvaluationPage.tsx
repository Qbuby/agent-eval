import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  benchmarkApi,
  evaluationApi,
  projectsApi,
} from '@/services'
import type {
  BenchmarkCase,
  Project,
} from '@/services/benchmark'
import type {
  EvalAgentConfig,
  EvalRunSummary,
  EvaluatorInstance,
  StartEvalRequest,
  UploadCasesResponse,
} from '@/types'

type Tab = 'history' | 'new'

export default function EvaluationPage() {
  const [tab, setTab] = useState<Tab>('history')

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-lg font-light tracking-tight mb-1">评估</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">
          Evaluation runs · LangSmith trace · local scoring
        </p>
      </header>

      <div className="flex gap-1 mb-5 border-b border-border">
        {([
          { id: 'history', label: '运行历史' },
          { id: 'new', label: '新建评估' },
        ] as { id: Tab; label: string }[]).map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-[12px] tracking-wide border-b-2 transition-all ${
              tab === t.id
                ? 'border-accent text-text-primary font-medium'
                : 'border-transparent text-text-secondary hover:text-text-primary'
            }`}
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
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [searchText, setSearchText] = useState('')
  const [startedAfter, setStartedAfter] = useState('')   // YYYY-MM-DD
  const [startedBefore, setStartedBefore] = useState('') // YYYY-MM-DD
  const [minPassRate, setMinPassRate] = useState<string>('')  // percent string
  const [selected, setSelected] = useState<Set<string>>(new Set())
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
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
          className="py-1.5 px-2 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent"
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
          placeholder="搜 run 名 / model / url / project"
          className="py-1.5 px-2 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent w-[220px]"
        />
        <span className="text-[10px] text-text-tertiary tracking-wider">起</span>
        <input
          type="date" value={startedAfter}
          onChange={e => { setStartedAfter(e.target.value); setPage(1) }}
          className="py-1 px-1.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent"
        />
        <span className="text-[10px] text-text-tertiary tracking-wider">至</span>
        <input
          type="date" value={startedBefore}
          onChange={e => { setStartedBefore(e.target.value); setPage(1) }}
          className="py-1 px-1.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent"
        />
        <input
          type="number" min={0} max={100} step={5}
          value={minPassRate}
          onChange={e => { setMinPassRate(e.target.value); setPage(1) }}
          placeholder="通过率 ≥ %"
          className="py-1.5 px-2 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent w-[110px]"
        />
        {filtersActive && (
          <button
            onClick={clearFilters}
            className="text-[11px] text-text-tertiary hover:text-text-primary underline"
          >
            清除筛选
          </button>
        )}
        <span className="text-[11px] text-text-tertiary">
          共 {runsQuery.data?.total ?? 0} 条
        </span>
        {selected.size > 0 && (
          <>
            <span className="text-[11px] text-text-tertiary">· 已选 {selected.size}</span>
            <button
              onClick={() => navigate(`/evaluation/compare?ids=${Array.from(selected).join(',')}`)}
              disabled={selected.size < 2}
              className="py-1.5 px-3 text-[11px] font-medium rounded-[6px] border border-accent text-accent disabled:opacity-40 hover:bg-accent-subtle"
            >
              对比所选（{selected.size}）
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="text-[11px] text-text-tertiary hover:text-text-primary underline"
            >
              清空
            </button>
          </>
        )}
        <button
          onClick={onNewRun}
          className="ml-auto inline-flex items-center gap-1.5 py-2 px-3.5 text-[11px] font-medium tracking-wide rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 transition-all"
        >
          + 新建评估
        </button>
      </div>

      <div className="border border-border rounded-[3px] overflow-hidden bg-surface">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <Th>
                <span className="sr-only">选择</span>
              </Th>
              <Th>ID</Th>
              <Th>状态</Th>
              <Th>Agent</Th>
              <Th>Run 名</Th>
              <Th>进度 / 总数</Th>
              <Th>通过率</Th>
              <Th>平均 Latency</Th>
              <Th>启动时间</Th>
              <Th>操作</Th>
            </tr>
          </thead>
          <tbody>
            {runsQuery.isLoading && (
              <tr><td colSpan={10} className="py-8 text-center text-[12px] text-text-tertiary">加载中…</td></tr>
            )}
            {runsQuery.data?.items.length === 0 && !runsQuery.isLoading && (
              <tr>
                <td colSpan={10} className="py-10 text-center text-[12px] text-text-tertiary">
                  {filtersActive
                    ? '没有匹配筛选的评估记录。'
                    : '还没有评估记录。点右上角「新建评估」启动第一个 run。'}
                </td>
              </tr>
            )}
            {runsQuery.data?.items.map(r => (
              <RunRow
                key={r.id}
                run={r}
                selected={selected.has(r.id)}
                onToggle={() => toggle(r.id)}
                onClick={() => navigate(`/evaluation/runs/${r.id}`)}
                onDelete={async () => {
                  if (!confirm(`删除评估 ${r.id.slice(0, 8)}？\n（软删除，可在 DB 直接恢复 deleted_at 字段）`)) return
                  await evaluationApi.deleteRun(r.id)
                  setSelected(prev => {
                    const next = new Set(prev)
                    next.delete(r.id)
                    return next
                  })
                  qc.invalidateQueries({ queryKey: ['eval-runs'] })
                }}
              />
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 mt-4 text-[11px] text-text-secondary">
          <button
            disabled={page <= 1}
            onClick={() => setPage(p => p - 1)}
            className="px-2 py-0.5 border border-border rounded-[4px] disabled:opacity-40 hover:border-accent"
          >
            ‹ 上一页
          </button>
          <span>{page} / {totalPages}</span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage(p => p + 1)}
            className="px-2 py-0.5 border border-border rounded-[4px] disabled:opacity-40 hover:border-accent"
          >
            下一页 ›
          </button>
        </div>
      )}
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">
      {children}
    </th>
  )
}

function RunRow({ run, selected, onToggle, onClick, onDelete }: {
  run: EvalRunSummary
  selected: boolean
  onToggle: () => void
  onClick: () => void
  onDelete: () => void
}) {
  const counts = run.summary_scores?.counts
  const total = counts?.total ?? run.progress.total ?? 0
  const completed = run.progress.completed ?? counts?.total ?? 0
  const passed = counts?.passed ?? 0
  const passRate = total > 0 ? `${Math.round((passed / total) * 100)}%` : '—'
  const avgLatency = firstDefined(
    run.summary_scores?.cost_success?.avg_latency_ms,
    run.summary_scores?.cost_failure?.avg_latency_ms,
  )

  const agent = run.agent_config as { model?: string; url?: string; type?: string }
  const agentLabel = agent?.model || agent?.type || '—'

  return (
    <tr onClick={onClick} className="hover:bg-accent-subtle/40 cursor-pointer transition-colors">
      <Td>
        <input
          type="checkbox"
          checked={selected}
          onClick={e => e.stopPropagation()}
          onChange={onToggle}
          className="accent-accent"
        />
      </Td>
      <Td mono>{run.id.slice(0, 8)}</Td>
      <Td><StatusBadge status={run.status} /></Td>
      <Td>{agentLabel}</Td>
      <Td mono>{run.langfuse_run_name ?? '—'}</Td>
      <Td>
        {run.status === 'running' ? `${completed}/${total || '?'}` : `${total}`}
      </Td>
      <Td>{passRate}</Td>
      <Td>{avgLatency != null ? `${Math.round(avgLatency)}ms` : '—'}</Td>
      <Td>{fmtTime(run.started_at)}</Td>
      <Td>
        <button
          onClick={e => { e.stopPropagation(); onDelete() }}
          className="text-[11px] text-negative hover:underline"
          title="软删除：行隐藏但 DB 保留 deleted_at"
        >
          删除
        </button>
      </Td>
    </tr>
  )
}

function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <td className={`py-2 px-3 border-b border-border text-[12px] ${mono ? 'font-mono text-[11px]' : 'text-text-primary'}`}>
      {children}
    </td>
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


// ─── New run tab ────────────────────────────────────────────────────────────

type CaseSourceTab = 'benchmark' | 'upload'

function NewRunTab({ onStarted }: { onStarted: () => void }) {
  const qc = useQueryClient()

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
        page: 1, page_size: 200,
      }).then(r => r.data),
    enabled: !!projectId && sourceTab === 'benchmark',
  })

  const effectiveCaseCount = useMemo(() => {
    if (!casesQuery.data) return 0
    if (selectionMode === 'pick') return pickedCaseIds.size
    return casesQuery.data.total
  }, [casesQuery.data, selectionMode, pickedCaseIds])

  // ── upload branch ──
  const [uploadedSource, setUploadedSource] = useState<UploadCasesResponse | null>(null)
  const uploadMutation = useMutation({
    mutationFn: (file: File) => evaluationApi.uploadCases(file).then(r => r.data),
    onSuccess: (data) => setUploadedSource(data),
  })

  // ── agent ──
  const [agentType, setAgentType] = useState<'sse' | 'openai' | 'sse_generic'>('sse')
  const [agentUrl, setAgentUrl] = useState('http://localhost:18094/api/agent/langgraph')
  const [agentApiKey, setAgentApiKey] = useState('')
  const [agentModel, setAgentModel] = useState('')
  const [agentLanguage, setAgentLanguage] = useState('请用中文回复')
  const [agentHeadersText, setAgentHeadersText] = useState('')
  const [agentPayloadText, setAgentPayloadText] = useState('')
  const [agentTimeout, setAgentTimeout] = useState(300)
  const [concurrency, setConcurrency] = useState(3)
  const [runName, setRunName] = useState('')
  const [langsmithProject, setLangsmithProject] = useState('')

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

  const hasCaseSource = sourceTab === 'benchmark' ? !!projectId : !!uploadedSource
  const canStart =
    hasCaseSource &&
    selectedEvaluatorIds.size > 0 &&
    agentUrl.trim().length > 0 &&
    !startMutation.isPending

  const handleStart = () => {
    let headers: Record<string, string> | undefined
    let payloadTpl: Record<string, unknown> | undefined
    try {
      if (agentHeadersText.trim()) headers = JSON.parse(agentHeadersText)
    } catch { alert('Headers 必须是合法 JSON'); return }
    try {
      if (agentPayloadText.trim()) payloadTpl = JSON.parse(agentPayloadText)
    } catch { alert('Payload template 必须是合法 JSON'); return }

    const agent: EvalAgentConfig = {
      type: agentType,
      url: agentUrl.trim(),
      api_key: agentApiKey || undefined,
      model: agentModel || undefined,
      headers,
      payload_template: payloadTpl,
      timeout: agentTimeout,
      language: agentLanguage,
    }

    const body: StartEvalRequest = {
      agent,
      evaluator_ids: Array.from(selectedEvaluatorIds),
      concurrency,
      run_name: runName.trim() || null,
      langsmith_project: langsmithProject.trim() || null,
    }

    if (sourceTab === 'upload' && uploadedSource) {
      body.case_source_id = uploadedSource.source_id
      body.limit = typeof limit === 'number' ? limit : null
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
        <div className="flex gap-1 mb-3 border-b border-border">
          {([
            { id: 'benchmark', label: '从基准数据集' },
            { id: 'upload', label: '上传文件' },
          ] as { id: CaseSourceTab; label: string }[]).map(t => (
            <button
              key={t.id}
              onClick={() => setSourceTab(t.id)}
              className={`px-3 py-1.5 text-[12px] border-b-2 transition-all ${
                sourceTab === t.id
                  ? 'border-accent text-text-primary font-medium'
                  : 'border-transparent text-text-secondary hover:text-text-primary'
              }`}
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
              <span className="ml-auto text-[11px] text-text-tertiary">
                {selectionMode === 'pick' ? `已选 ${pickedCaseIds.size} 条` : `命中约 ${effectiveCaseCount} 条`}
              </span>
            </div>

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
                  placeholder="e.g. 电池,维修" className="input"
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
                  <button onClick={() => setPickedCaseIds(new Set())}
                          className="text-[11px] px-2 py-1 border border-border rounded-[4px] hover:border-accent">
                    清空
                  </button>
                </div>
                <div className="border border-border rounded-[4px] max-h-[280px] overflow-y-auto divide-y divide-border">
                  {casesQuery.data?.items.map((c: BenchmarkCase) => {
                    const checked = pickedCaseIds.has(c.id)
                    return (
                      <label key={c.id}
                             className="flex items-start gap-2 py-1.5 px-2 hover:bg-accent-subtle/40 cursor-pointer text-[12px]">
                        <input
                          type="checkbox" checked={checked}
                          onChange={() => {
                            const s = new Set(pickedCaseIds)
                            if (s.has(c.id)) s.delete(c.id); else s.add(c.id)
                            setPickedCaseIds(s)
                          }}
                          className="mt-0.5 w-3.5 h-3.5 accent-accent shrink-0"
                        />
                        <span className="flex-1 break-all">
                          {c.question.length > 120 ? c.question.slice(0, 120) + '…' : c.question}
                        </span>
                      </label>
                    )
                  })}
                  {casesQuery.data?.items.length === 0 && (
                    <div className="py-6 text-center text-[11px] text-text-tertiary">
                      {projectId ? '没有匹配的 case' : '先选项目'}
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
              <div className="mt-3 border border-border rounded-[4px] bg-surface p-2">
                <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">前 3 条预览</div>
                {uploadedSource.preview.map((c, i) => {
                  const q = String((c as { question?: unknown }).question ?? '')
                  return (
                    <div key={i} className="text-[11px] py-1 border-b border-border/40 last:border-b-0">
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
      </Section>

      {/* Step 2: agent */}
      <Section title="2. 配置 Agent">
        <div className="grid grid-cols-2 gap-3">
          <Field label="类型">
            <select value={agentType} onChange={e => setAgentType(e.target.value as typeof agentType)} className="input">
              <option value="sse">SSE (LangGraph v2)</option>
              <option value="openai">OpenAI 兼容</option>
              <option value="sse_generic">SSE 通用模板</option>
            </select>
          </Field>
          <Field label="Model（可选，展示用）">
            <input type="text" value={agentModel} onChange={e => setAgentModel(e.target.value)} className="input" />
          </Field>
          <Field label="Agent URL">
            <input type="text" value={agentUrl} onChange={e => setAgentUrl(e.target.value)}
                   placeholder="http://localhost:18094/api/agent/langgraph" className="input" />
          </Field>
          <Field label="API Key（可选）">
            <input type="password" value={agentApiKey} onChange={e => setAgentApiKey(e.target.value)} className="input" />
          </Field>
          <Field label="Timeout（秒）">
            <input type="number" min={10} value={agentTimeout} onChange={e => setAgentTimeout(Number(e.target.value))} className="input" />
          </Field>
          <Field label="Concurrency">
            <input type="number" min={1} max={20} value={concurrency} onChange={e => setConcurrency(Number(e.target.value))} className="input" />
          </Field>
          {agentType === 'sse' && (
            <Field label="language 参数">
              <input type="text" value={agentLanguage} onChange={e => setAgentLanguage(e.target.value)} className="input" />
            </Field>
          )}
          <Field label="LangSmith Project（用于拉回 trace）">
            <input type="text" value={langsmithProject} onChange={e => setLangsmithProject(e.target.value)}
                   placeholder="e.g. ep-agent / ruyi-agent" className="input" />
          </Field>
        </div>

        <details className="mt-3">
          <summary className="text-[11px] text-text-secondary cursor-pointer">高级：自定义 headers / payload</summary>
          <div className="grid grid-cols-2 gap-3 mt-2">
            <Field label="Headers (JSON)">
              <textarea value={agentHeadersText} onChange={e => setAgentHeadersText(e.target.value)}
                        rows={3} placeholder='{"X-Custom": "value"}' className="input font-mono text-[11px]" />
            </Field>
            <Field label="Payload template (JSON, SSE generic 专用)">
              <textarea value={agentPayloadText} onChange={e => setAgentPayloadText(e.target.value)}
                        rows={3} placeholder='{"question": "{input}"}' className="input font-mono text-[11px]" />
            </Field>
          </div>
        </details>
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
                     className="flex items-start gap-2 border border-border rounded-[4px] p-3 cursor-pointer">
                <input
                  type="checkbox" checked={checked}
                  onChange={() => {
                    const s = new Set(selectedEvaluatorIds)
                    if (s.has(e.id)) s.delete(e.id); else s.add(e.id)
                    setSelectedEvaluatorIds(s)
                  }}
                  className="mt-0.5 w-3.5 h-3.5 accent-accent"
                />
                <div className="flex-1">
                  <div className="font-medium text-[12px]">{e.name}
                    <span className="ml-2 text-[10px] font-mono text-blue-700 bg-blue-50 px-1 py-0.5 rounded border border-blue-200" title="写到 Langfuse trace 的 tag">
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
      <Section title="4. Run 名（可选）">
        <input
          type="text" value={runName} onChange={e => setRunName(e.target.value)}
          placeholder="默认自动按时间戳生成" className="input max-w-[420px]"
        />
      </Section>

      {/* Start */}
      <div className="flex items-center gap-3 pt-2 border-t border-border">
        <button
          disabled={!canStart}
          onClick={handleStart}
          className="inline-flex items-center gap-1.5 py-2.5 px-5 text-[12px] font-medium tracking-wide rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 active:scale-[0.97] disabled:opacity-40 transition-all"
        >
          {startMutation.isPending ? '启动中…' : '启动评估'}
        </button>
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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border border-border rounded-[6px] bg-surface p-4">
      <h3 className="text-[12px] font-medium mb-3 tracking-tight">{title}</h3>
      {children}
    </section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] tracking-widest uppercase text-text-tertiary">{label}</span>
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
  const e = err as { response?: { data?: { detail?: string } }; message?: string }
  return e?.response?.data?.detail || e?.message || '未知错误'
}
