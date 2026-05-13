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
  BuiltinEvaluator,
  EvalAgentConfig,
  EvalRunSummary,
  EvaluatorConfig,
  StartEvalRequest,
} from '@/types'

type Tab = 'history' | 'new'

export default function EvaluationPage() {
  const [tab, setTab] = useState<Tab>('history')

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-lg font-light tracking-tight mb-1">评估</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">
          Evaluation runs · Langfuse-backed
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
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const pageSize = 15

  const runsQuery = useQuery({
    queryKey: ['eval-runs', page, statusFilter],
    queryFn: () =>
      evaluationApi
        .listRuns({ page, page_size: pageSize, status: statusFilter || undefined })
        .then(r => r.data),
    refetchInterval: (q) => {
      // Poll while any run is active
      const data = q.state.data
      if (!data) return false
      return data.items.some(it => it.status === 'running' || it.status === 'stopping') ? 3000 : false
    },
  })

  const totalPages = Math.max(1, Math.ceil((runsQuery.data?.total ?? 0) / pageSize))

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
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
        <span className="text-[11px] text-text-tertiary">
          共 {runsQuery.data?.total ?? 0} 条
        </span>
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
              <Th>ID</Th>
              <Th>状态</Th>
              <Th>模型</Th>
              <Th>Run 名</Th>
              <Th>进度 / 总数</Th>
              <Th>通过率</Th>
              <Th>平均 Latency</Th>
              <Th>启动时间</Th>
            </tr>
          </thead>
          <tbody>
            {runsQuery.isLoading && (
              <tr><td colSpan={8} className="py-8 text-center text-[12px] text-text-tertiary">加载中…</td></tr>
            )}
            {runsQuery.data?.items.length === 0 && !runsQuery.isLoading && (
              <tr>
                <td colSpan={8} className="py-10 text-center text-[12px] text-text-tertiary">
                  还没有评估记录。点右上角「新建评估」启动第一个 run。
                </td>
              </tr>
            )}
            {runsQuery.data?.items.map(r => (
              <RunRow key={r.id} run={r} onClick={() => navigate(`/evaluation/runs/${r.id}`)} />
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

function RunRow({ run, onClick }: { run: EvalRunSummary; onClick: () => void }) {
  const counts = run.summary_scores?.counts
  const total = counts?.total ?? run.progress.total ?? 0
  const completed = run.progress.completed ?? counts?.total ?? 0
  const passed = counts?.passed ?? 0
  const passRate = total > 0 ? `${Math.round((passed / total) * 100)}%` : '—'
  const avgLatency = firstDefined(
    run.summary_scores?.cost_success?.avg_latency_ms,
    run.summary_scores?.cost_failure?.avg_latency_ms,
  )

  return (
    <tr
      onClick={onClick}
      className="hover:bg-accent-subtle/40 cursor-pointer transition-colors"
    >
      <Td mono>{run.id.slice(0, 8)}</Td>
      <Td>
        <StatusBadge status={run.status} />
      </Td>
      <Td>{(run.agent_config as { model?: string })?.model ?? '—'}</Td>
      <Td mono>{run.langfuse_run_name ?? '—'}</Td>
      <Td>
        {run.status === 'running'
          ? `${completed}/${total || '?'}`
          : `${total}`}
      </Td>
      <Td>{passRate}</Td>
      <Td>{avgLatency != null ? `${Math.round(avgLatency)}ms` : '—'}</Td>
      <Td>{fmtTime(run.started_at)}</Td>
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

function NewRunTab({ onStarted }: { onStarted: () => void }) {
  const qc = useQueryClient()

  // Project + cases
  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
  })
  const [projectId, setProjectId] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [searchText, setSearchText] = useState('')

  const categoriesQuery = useQuery({
    queryKey: ['categories', projectId],
    queryFn: () => projectsApi.getCategories(projectId).then(r => r.data),
    enabled: !!projectId,
  })

  // We pull the first 200 cases as the candidate pool; user picks from here.
  const casesQuery = useQuery({
    queryKey: ['bench-cases-for-eval', projectId, categoryId, searchText],
    queryFn: () =>
      benchmarkApi
        .listCases(projectId, {
          category_id: categoryId || undefined,
          search: searchText || undefined,
          page: 1,
          page_size: 200,
        })
        .then(r => r.data),
    enabled: !!projectId,
  })

  const [selectionMode, setSelectionMode] = useState<'all' | 'filter' | 'pick'>('all')
  const [pickedCaseIds, setPickedCaseIds] = useState<Set<string>>(new Set())
  const [filterTags, setFilterTags] = useState('')
  const [limit, setLimit] = useState<number | ''>(10)

  const effectiveCaseCount = useMemo(() => {
    if (!casesQuery.data) return 0
    if (selectionMode === 'pick') return pickedCaseIds.size
    if (selectionMode === 'all') return casesQuery.data.total
    // filter mode — can't know exact without backend call; show the current page total as a hint
    return casesQuery.data.total
  }, [casesQuery.data, selectionMode, pickedCaseIds])

  // Agent config
  const [agentType, setAgentType] = useState<'openai' | 'sse'>('openai')
  const [agentUrl, setAgentUrl] = useState('https://kiro.aidong-ai.com/v1')
  const [agentApiKey, setAgentApiKey] = useState('')
  const [agentModel, setAgentModel] = useState('claude-haiku-4-5')
  const [agentHeadersText, setAgentHeadersText] = useState('')
  const [agentPayloadText, setAgentPayloadText] = useState('')
  const [agentTimeout, setAgentTimeout] = useState(120)

  // Evaluators
  const builtinEvalsQuery = useQuery({
    queryKey: ['builtin-evaluators'],
    queryFn: () => evaluationApi.listBuiltinEvaluators().then(r => r.data),
  })
  const [selectedEvalNames, setSelectedEvalNames] = useState<Set<string>>(
    new Set(['exact_match', 'llm_judge']),
  )
  const [llmJudgePromptOverride, setLlmJudgePromptOverride] = useState('')

  const [concurrency, setConcurrency] = useState(3)
  const [runName, setRunName] = useState('')

  const startMutation = useMutation({
    mutationFn: (body: StartEvalRequest) => evaluationApi.startRun(body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-runs'] })
      onStarted()
    },
  })

  const canStart =
    !!projectId &&
    selectedEvalNames.size > 0 &&
    agentUrl.trim().length > 0 &&
    !startMutation.isPending

  const handleStart = () => {
    let headers: Record<string, string> | undefined
    let payloadTpl: Record<string, unknown> | undefined
    try {
      if (agentHeadersText.trim()) headers = JSON.parse(agentHeadersText)
    } catch {
      alert('Headers 必须是合法 JSON 对象')
      return
    }
    try {
      if (agentPayloadText.trim()) payloadTpl = JSON.parse(agentPayloadText)
    } catch {
      alert('Payload template 必须是合法 JSON 对象')
      return
    }

    const agent: EvalAgentConfig = {
      type: agentType,
      url: agentUrl.trim(),
      api_key: agentApiKey || undefined,
      model: agentModel || undefined,
      headers,
      payload_template: payloadTpl,
      timeout: agentTimeout,
    }

    const evaluators: EvaluatorConfig[] = Array.from(selectedEvalNames).map(name => {
      const params: Record<string, unknown> = {}
      if (name === 'llm_judge' && llmJudgePromptOverride.trim()) {
        params.user_template = llmJudgePromptOverride.trim()
      }
      return { name, params }
    })

    const body: StartEvalRequest = {
      project_id: projectId,
      agent,
      evaluators,
      concurrency,
      run_name: runName.trim() || null,
    }

    if (selectionMode === 'pick') {
      body.case_ids = Array.from(pickedCaseIds)
    } else if (selectionMode === 'filter') {
      body.filter_category_id = categoryId || null
      body.filter_tags = filterTags
        .split(',').map(t => t.trim()).filter(Boolean) || null
      body.limit = typeof limit === 'number' ? limit : null
    } else {
      // all — optionally cap with limit
      body.filter_category_id = categoryId || null
      body.limit = typeof limit === 'number' ? limit : null
    }

    startMutation.mutate(body)
  }

  return (
    <div className="flex flex-col gap-5 max-w-[900px]">
      {/* Step 1: dataset */}
      <Section title="1. 选数据集">
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
      </Section>

      {/* Step 2: selection */}
      <Section title="2. 选择样例">
        <div className="flex items-center gap-3 mb-3">
          {(['all', 'filter', 'pick'] as const).map(m => (
            <label key={m} className="inline-flex items-center gap-1.5 text-[12px] cursor-pointer">
              <input
                type="radio"
                checked={selectionMode === m}
                onChange={() => setSelectionMode(m)}
                className="accent-accent"
              />
              {m === 'all' && '整个数据集'}
              {m === 'filter' && '按条件筛选'}
              {m === 'pick' && '手动勾选'}
            </label>
          ))}
          <span className="ml-auto text-[11px] text-text-tertiary">
            {selectionMode === 'pick'
              ? `已选 ${pickedCaseIds.size} 条`
              : `命中约 ${effectiveCaseCount} 条`}
          </span>
        </div>

        {selectionMode === 'filter' && (
          <div className="grid grid-cols-2 gap-3 mb-3">
            <Field label="标签（逗号分隔）">
              <input
                type="text"
                value={filterTags}
                onChange={e => setFilterTags(e.target.value)}
                placeholder="e.g. 电池,维修"
                className="input"
              />
            </Field>
            <Field label="最多跑多少条">
              <input
                type="number"
                min={1}
                value={limit}
                onChange={e => setLimit(e.target.value ? Number(e.target.value) : '')}
                className="input"
              />
            </Field>
          </div>
        )}

        {selectionMode === 'all' && (
          <Field label="最多跑多少条（空=全部）">
            <input
              type="number"
              min={1}
              value={limit}
              onChange={e => setLimit(e.target.value ? Number(e.target.value) : '')}
              className="input max-w-[180px]"
            />
          </Field>
        )}

        {selectionMode === 'pick' && (
          <div>
            <div className="flex items-center gap-2 mb-2">
              <input
                type="text"
                placeholder="搜索问题文本…"
                value={searchText}
                onChange={e => setSearchText(e.target.value)}
                className="input flex-1"
              />
              <button
                type="button"
                onClick={() => setPickedCaseIds(new Set())}
                className="text-[11px] px-2 py-1 border border-border rounded-[4px] hover:border-accent"
              >
                清空
              </button>
            </div>
            <div className="border border-border rounded-[4px] max-h-[280px] overflow-y-auto divide-y divide-border">
              {casesQuery.data?.items.map((c: BenchmarkCase) => {
                const checked = pickedCaseIds.has(c.id)
                return (
                  <label
                    key={c.id}
                    className="flex items-start gap-2 py-1.5 px-2 hover:bg-accent-subtle/40 cursor-pointer text-[12px]"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
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
      </Section>

      {/* Step 3: agent */}
      <Section title="3. 配置 Agent">
        <div className="grid grid-cols-2 gap-3">
          <Field label="类型">
            <select value={agentType} onChange={e => setAgentType(e.target.value as 'openai' | 'sse')} className="input">
              <option value="openai">OpenAI 兼容</option>
              <option value="sse">SSE 流</option>
            </select>
          </Field>
          <Field label="Model">
            <input type="text" value={agentModel} onChange={e => setAgentModel(e.target.value)} className="input" />
          </Field>
          <Field label={agentType === 'openai' ? '/chat/completions base URL' : 'SSE URL'}>
            <input type="text" value={agentUrl} onChange={e => setAgentUrl(e.target.value)} className="input" />
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
        </div>

        <details className="mt-3">
          <summary className="text-[11px] text-text-secondary cursor-pointer">高级：自定义 headers / payload</summary>
          <div className="grid grid-cols-2 gap-3 mt-2">
            <Field label="Headers (JSON)">
              <textarea
                value={agentHeadersText}
                onChange={e => setAgentHeadersText(e.target.value)}
                rows={3}
                placeholder='{"X-Custom": "value"}'
                className="input font-mono text-[11px]"
              />
            </Field>
            <Field label="Payload template (JSON, SSE only)">
              <textarea
                value={agentPayloadText}
                onChange={e => setAgentPayloadText(e.target.value)}
                rows={3}
                placeholder='{"question": "{input}"}'
                className="input font-mono text-[11px]"
              />
            </Field>
          </div>
        </details>
      </Section>

      {/* Step 4: evaluators */}
      <Section title="4. 评估器">
        {builtinEvalsQuery.data?.length === 0 && (
          <p className="text-[12px] text-text-tertiary">没有可用的评估器</p>
        )}
        <div className="flex flex-col gap-2">
          {builtinEvalsQuery.data?.map((e: BuiltinEvaluator) => {
            const checked = selectedEvalNames.has(e.name)
            return (
              <div key={e.name} className="border border-border rounded-[4px] p-3">
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      const s = new Set(selectedEvalNames)
                      if (s.has(e.name)) s.delete(e.name); else s.add(e.name)
                      setSelectedEvalNames(s)
                    }}
                    className="mt-0.5 w-3.5 h-3.5 accent-accent"
                  />
                  <div className="flex-1">
                    <div className="font-medium text-[12px]">{e.name}</div>
                    <div className="text-[11px] text-text-tertiary mt-0.5">{e.description}</div>
                  </div>
                </label>
                {checked && e.name === 'llm_judge' && (
                  <div className="mt-2 ml-6">
                    <Field label="User template (可选，留空用默认)">
                      <textarea
                        value={llmJudgePromptOverride}
                        onChange={ev => setLlmJudgePromptOverride(ev.target.value)}
                        rows={3}
                        className="input font-mono text-[11px]"
                        placeholder="## 用户问题&#10;{question}&#10;&#10;## AI 回答&#10;{answer}&#10;&#10;## 评分维度&#10;{dimensions}"
                      />
                    </Field>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </Section>

      {/* Step 5: run name */}
      <Section title="5. Run 名（可选）">
        <input
          type="text"
          value={runName}
          onChange={e => setRunName(e.target.value)}
          placeholder="默认自动按时间戳生成"
          className="input max-w-[420px]"
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
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch {
    return iso
  }
}

function extractError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: string } }; message?: string }
  return e?.response?.data?.detail || e?.message || '未知错误'
}
