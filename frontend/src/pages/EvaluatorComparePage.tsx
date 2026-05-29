import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button, Drawer, ErrorCard, useToast } from '@/components/ui'
import { evaluationApi } from '@/services'
import { benchmarkApi, projectsApi, type BenchmarkCase } from '@/services/benchmark'
import { formatApiError, formatDryRunError, type NormalizedError } from '@/lib/errors'
import type { DryRunResponse, EvaluatorInstance } from '@/types'

interface SampleRow {
  id: string
  source: 'benchmark' | 'manual'
  benchmarkCaseId?: string
  input: string
  output: string
  expected_output: string
  results: Record<string, DryRunResponse | { error: NormalizedError } | 'pending' | undefined>
}

function newRow(partial?: Partial<SampleRow>): SampleRow {
  return {
    id: Math.random().toString(36).slice(2),
    source: 'manual',
    input: '',
    output: '',
    expected_output: '',
    results: {},
    ...partial,
  }
}

function isPending(v: unknown): v is 'pending' {
  return v === 'pending'
}

function isResponse(v: unknown): v is DryRunResponse {
  return !!v && typeof v === 'object' && 'scores' in (v as Record<string, unknown>)
}

interface JudgeBreakdown {
  composite_score?: number
  verdict?: string
  dimensions: Array<{ key: string; score: number; note: string }>
}

// 试着从 raw_content 里抽出多维分数（composite-score 模板的标准结构）
function parseJudgeBreakdown(raw: string): JudgeBreakdown | null {
  if (!raw) return null
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/)
  const jsonStr = (fenced ? fenced[1] : raw).trim()
  try {
    const obj = JSON.parse(jsonStr) as Record<string, unknown>
    if (!obj || typeof obj !== 'object') return null
    const dimensions: JudgeBreakdown['dimensions'] = []
    for (const [k, v] of Object.entries(obj)) {
      if (k === 'composite_score' || k === 'verdict') continue
      if (v && typeof v === 'object' && 'score' in (v as Record<string, unknown>)) {
        const item = v as { score: unknown; note?: unknown }
        if (typeof item.score === 'number') {
          dimensions.push({
            key: k,
            score: item.score,
            note: typeof item.note === 'string' ? item.note : '',
          })
        }
      }
    }
    return {
      composite_score: typeof obj.composite_score === 'number' ? obj.composite_score : undefined,
      verdict: typeof obj.verdict === 'string' ? obj.verdict : undefined,
      dimensions,
    }
  } catch {
    return null
  }
}

export default function EvaluatorComparePage() {
  const toast = useToast()

  const evaluatorsQuery = useQuery({
    queryKey: ['evaluator-instances'],
    queryFn: () => evaluationApi.listEvaluators(true).then(r => r.data),
  })
  const judgeEvaluators = useMemo(
    () => (evaluatorsQuery.data ?? []).filter(e =>
      e.evaluator_type === 'configurable_judge'
        || (e.params && typeof (e.params as Record<string, unknown>).provider_id === 'string'),
    ),
    [evaluatorsQuery.data],
  )

  const [aId, setAId] = useState<string>('')
  const [bId, setBId] = useState<string>('')

  const aEvaluator = judgeEvaluators.find(e => e.id === aId) || null
  const bEvaluator = judgeEvaluators.find(e => e.id === bId) || null

  const [rows, setRows] = useState<SampleRow[]>([newRow()])
  const [importDrawerOpen, setImportDrawerOpen] = useState(false)
  const [running, setRunning] = useState(false)

  async function runOne(rowId: string, evaluator: EvaluatorInstance) {
    if (!evaluator) return
    const row = rowsRef(rows, rowId)
    if (!row) return
    if (!row.input.trim() || !row.output.trim()) {
      setRows(prev => prev.map(r => r.id !== rowId ? r : ({
        ...r,
        results: { ...r.results, [evaluator.id]: {
          error: {
            title: '校验失败', message: '需先填 input 和 output', severity: 'error', code: 'bad_request',
          },
        } },
      })))
      return
    }
    setRows(prev => prev.map(r => r.id !== rowId ? r : ({
      ...r,
      results: { ...r.results, [evaluator.id]: 'pending' },
    })))
    try {
      const resp = await evaluationApi.dryRunEvaluator(evaluator.id, {
        params: evaluator.params,
        input: row.input,
        output: row.output,
        expected_output: row.expected_output || null,
      })
      setRows(prev => prev.map(r => r.id !== rowId ? r : ({
        ...r,
        results: { ...r.results, [evaluator.id]: resp.data },
      })))
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '试跑失败' })
      setRows(prev => prev.map(r => r.id !== rowId ? r : ({
        ...r,
        results: { ...r.results, [evaluator.id]: { error: norm } },
      })))
    }
  }

  async function runAll() {
    if (!aEvaluator || !bEvaluator) {
      toast.error('请先选两个评估器')
      return
    }
    if (rows.length === 0) {
      toast.error('请先添加至少一条样本')
      return
    }
    setRunning(true)
    try {
      for (const row of rows) {
        await Promise.all([
          runOne(row.id, aEvaluator),
          runOne(row.id, bEvaluator),
        ])
      }
    } finally {
      setRunning(false)
    }
  }

  async function runRow(rowId: string) {
    if (!aEvaluator || !bEvaluator) {
      toast.error('请先选两个评估器')
      return
    }
    setRunning(true)
    try {
      await Promise.all([
        runOne(rowId, aEvaluator),
        runOne(rowId, bEvaluator),
      ])
    } finally {
      setRunning(false)
    }
  }

  function updateRow(id: string, patch: Partial<SampleRow>) {
    setRows(prev => prev.map(r => r.id === id ? { ...r, ...patch, results: {} } : r))
  }

  function deleteRow(id: string) {
    setRows(prev => prev.filter(r => r.id !== id))
  }

  return (
    <div className="max-w-[1400px]">
      <header className="mb-5">
        <div className="page-eyebrow">
          <Link to="/evaluators" className="hover:underline">评估器</Link>
          <span className="mx-2 text-text-tertiary">/</span>
          <span>对比</span>
        </div>
        <h1 className="page-title">评估器对比</h1>
        <p className="page-subtitle">
          用同一组样本同时跑两个 LLM Judge，逐条看 score / verdict / 子维度差异
        </p>
      </header>

      {/* 顶部：评估器选择 + 全局动作（粘性） */}
      <div className="sticky top-0 z-10 -mx-4 px-4 py-3 mb-4 bg-surface-0/95 backdrop-blur border-b border-border-subtle">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr_auto] gap-3 items-end">
          <EvaluatorPicker
            label="评估器 A"
            value={aId}
            onChange={setAId}
            options={judgeEvaluators}
            excludeId={bId}
            loading={evaluatorsQuery.isLoading}
          />
          <EvaluatorPicker
            label="评估器 B"
            value={bId}
            onChange={setBId}
            options={judgeEvaluators}
            excludeId={aId}
            loading={evaluatorsQuery.isLoading}
          />
          <Button
            variant="primary"
            size="md"
            onClick={runAll}
            disabled={running || !aEvaluator || !bEvaluator || rows.length === 0}
          >
            {running ? '跑中…' : `跑全部 (${rows.length})`}
          </Button>
        </div>
        {judgeEvaluators.length < 2 && !evaluatorsQuery.isLoading && (
          <div className="mt-2 text-[12px] text-text-tertiary">
            目前只有 {judgeEvaluators.length} 个 LLM Judge 评估器，对比需要两个。
            <Link to="/evaluators" className="ml-1 underline text-accent">去新建</Link>
          </div>
        )}
      </div>

      {/* 样本卡片列表 */}
      <div className="section-row mb-3">
        <div className="page-eyebrow">样本（{rows.length}）</div>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => setImportDrawerOpen(true)}>
            从基准导入
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setRows(prev => [...prev, newRow()])}>
            + 手动添加
          </Button>
          {rows.length > 0 && (
            <Button size="sm" variant="ghost" onClick={() => setRows([])}>
              清空
            </Button>
          )}
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="empty-state">
          还没有样本。点「从基准导入」拉一批 case，或「手动添加」自己输入
        </div>
      ) : (
        <div className="space-y-4">
          {rows.map((row, i) => (
            <SampleCard
              key={row.id}
              index={i + 1}
              row={row}
              aEvaluator={aEvaluator}
              bEvaluator={bEvaluator}
              onChange={(patch) => updateRow(row.id, patch)}
              onDelete={() => deleteRow(row.id)}
              onRun={() => runRow(row.id)}
              running={running}
            />
          ))}
        </div>
      )}

      {importDrawerOpen && (
        <BenchmarkImportDrawer
          open={importDrawerOpen}
          onClose={() => setImportDrawerOpen(false)}
          onImport={(cases) => {
            const newRows = cases.map(c => newRow({
              source: 'benchmark',
              benchmarkCaseId: c.id,
              input: c.question,
              output: '',
              expected_output: c.reference_answer || '',
            }))
            setRows(prev => [...prev, ...newRows])
            setImportDrawerOpen(false)
            toast.success(`已导入 ${cases.length} 条`)
          }}
        />
      )}
    </div>
  )
}

// 在最新 state 里找 row（避免 closure 拿到旧数据）
function rowsRef(rows: SampleRow[], id: string): SampleRow | undefined {
  return rows.find(r => r.id === id)
}

// ────────────────────────────────────────────────────────────────────────
// SampleCard：一条样本的完整卡片，含输入区 + A/B 结果并排
// ────────────────────────────────────────────────────────────────────────

function SampleCard({
  index,
  row,
  aEvaluator,
  bEvaluator,
  onChange,
  onDelete,
  onRun,
  running,
}: {
  index: number
  row: SampleRow
  aEvaluator: EvaluatorInstance | null
  bEvaluator: EvaluatorInstance | null
  onChange: (patch: Partial<SampleRow>) => void
  onDelete: () => void
  onRun: () => void
  running: boolean
}) {
  const ra = aEvaluator ? row.results[aEvaluator.id] : undefined
  const rb = bEvaluator ? row.results[bEvaluator.id] : undefined

  const va = isResponse(ra) && !ra.error ? ra.scores[0]?.value ?? null : null
  const vb = isResponse(rb) && !rb.error ? rb.scores[0]?.value ?? null : null
  const diff = (va !== null && vb !== null) ? vb - va : null

  return (
    <div className="rounded-lg border border-border-subtle bg-surface-1 overflow-hidden">
      {/* 头部 */}
      <div className="flex items-center gap-3 px-4 py-2.5 bg-surface-2 border-b border-border-subtle">
        <span className="text-text-tertiary font-mono text-[12px] w-6">#{index}</span>
        <span className={`badge ${row.source === 'benchmark' ? 'badge-positive' : 'badge-neutral'} text-[10px]`}>
          {row.source === 'benchmark' ? '基准' : '手动'}
        </span>
        {diff !== null && (
          <span className="text-[12px] font-mono">
            <span className="text-text-tertiary">A→B:</span>{' '}
            <span className={diff > 0 ? 'text-positive' : diff < 0 ? 'text-negative' : 'text-text-secondary'}>
              {diff > 0 ? '+' : ''}{diff.toFixed(3)}
            </span>
          </span>
        )}
        <div className="flex-1" />
        <Button
          size="sm"
          variant="ghost"
          onClick={onRun}
          disabled={running || !aEvaluator || !bEvaluator}
        >
          跑这一条
        </Button>
        <button
          type="button"
          onClick={onDelete}
          className="text-action-danger text-[12px]"
        >
          删除
        </button>
      </div>

      {/* 输入区：纵向三个 textarea */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 p-4">
        <FieldTextarea
          label="用户输入 (input)"
          value={row.input}
          onChange={v => onChange({ input: v })}
          rows={6}
        />
        <FieldTextarea
          label="AI 回答 (output)"
          value={row.output}
          onChange={v => onChange({ output: v })}
          rows={6}
          placeholder={row.source === 'benchmark' ? '从基准导入：需手填 AI 回答' : ''}
        />
        <FieldTextarea
          label="期望答案 (expected)"
          value={row.expected_output}
          onChange={v => onChange({ expected_output: v })}
          rows={6}
        />
      </div>

      {/* 结果区：A / B 并排 */}
      {(aEvaluator || bEvaluator) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 border-t border-border-subtle">
          <div className="p-4 lg:border-r border-border-subtle">
            <ResultPanel side="A" evaluator={aEvaluator} result={ra} />
          </div>
          <div className="p-4 border-t lg:border-t-0 border-border-subtle">
            <ResultPanel side="B" evaluator={bEvaluator} result={rb} />
          </div>
        </div>
      )}
    </div>
  )
}

function FieldTextarea({
  label,
  value,
  onChange,
  rows = 4,
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  rows?: number
  placeholder?: string
}) {
  return (
    <label className="block">
      <span className="field-label">{label}</span>
      <textarea
        className="input font-mono text-[12px]"
        rows={rows}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </label>
  )
}

// ────────────────────────────────────────────────────────────────────────
// ResultPanel：单侧结果（A 或 B），inline 展示 score/verdict/子维度/reason
// ────────────────────────────────────────────────────────────────────────

function ResultPanel({
  side,
  evaluator,
  result,
}: {
  side: 'A' | 'B'
  evaluator: EvaluatorInstance | null
  result: SampleRow['results'][string]
}) {
  if (!evaluator) {
    return (
      <div>
        <PanelHeader side={side} name="未选择" />
        <div className="text-[12px] text-text-tertiary">在顶部选择评估器 {side}</div>
      </div>
    )
  }

  return (
    <div>
      <PanelHeader side={side} name={evaluator.name} />
      <ResultBody result={result} />
    </div>
  )
}

function PanelHeader({ side, name }: { side: 'A' | 'B'; name: string }) {
  return (
    <div className="flex items-baseline gap-2 mb-3">
      <span className={`inline-flex items-center justify-center w-5 h-5 rounded text-[11px] font-bold
        ${side === 'A' ? 'bg-blue-500/15 text-blue-400' : 'bg-purple-500/15 text-purple-400'}`}>
        {side}
      </span>
      <span className="text-[13px] font-medium truncate" title={name}>{name}</span>
    </div>
  )
}

function ResultBody({ result }: { result: SampleRow['results'][string] }) {
  if (result === undefined) {
    return <div className="text-[12px] text-text-tertiary">— 未运行</div>
  }
  if (isPending(result)) {
    return (
      <div className="flex items-center gap-2 text-[12px] text-text-tertiary">
        <span className="inline-block w-3 h-3 border-2 border-text-tertiary border-t-transparent rounded-full animate-spin" />
        跑中…
      </div>
    )
  }
  if (!isResponse(result)) {
    // { error: NormalizedError }
    return (
      <ErrorCard
        error={(result as { error: NormalizedError }).error}
        variant="compact"
      />
    )
  }

  // 正常 DryRunResponse
  if (result.error) {
    return (
      <div className="space-y-3 text-[12px]">
        <ErrorCard
          error={formatDryRunError(result.error)}
          variant="compact"
        />
        <details className="text-text-tertiary" open>
          <summary className="cursor-pointer text-[11px] hover:text-text-secondary">
            原始返回 · {result.model || '?'} · {result.usage?.total_tokens ?? '?'}t
          </summary>
          <pre className="mt-2 rounded bg-surface-2 p-2 text-[11px] overflow-x-auto whitespace-pre-wrap break-all max-h-60 overflow-y-auto">
{result.raw_content || '（空 — 模型未返回任何内容）'}
          </pre>
        </details>
      </div>
    )
  }

  const score = result.scores[0]
  const breakdown = parseJudgeBreakdown(result.raw_content)

  return (
    <div className="space-y-3 text-[12px]">
      {/* 主分数 + verdict */}
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-[28px] leading-none text-text-primary">
          {score ? score.value.toFixed(3) : '—'}
        </span>
        {breakdown?.verdict && <VerdictBadge verdict={breakdown.verdict} />}
      </div>

      {/* 子维度（如果是 composite-score 模板） */}
      {breakdown && breakdown.dimensions.length > 0 && (
        <div className="space-y-1.5">
          {breakdown.dimensions.map(d => (
            <div key={d.key} className="flex items-baseline gap-2">
              <span className="font-mono text-[11px] text-text-tertiary w-28 shrink-0 truncate" title={d.key}>
                {d.key}
              </span>
              <span className="font-mono text-[12px] w-12 shrink-0">{d.score.toFixed(2)}</span>
              <span className="text-[11px] text-text-secondary flex-1">{d.note || '—'}</span>
            </div>
          ))}
        </div>
      )}

      {/* reason（不是 composite-score 时） */}
      {!breakdown && score?.reason && (
        <div className="text-text-secondary whitespace-pre-wrap">{score.reason}</div>
      )}

      {/* 折叠：原始返回 + 模型 / token */}
      <details className="text-text-tertiary">
        <summary className="cursor-pointer text-[11px] hover:text-text-secondary">
          原始返回 · {result.model} · {result.usage?.total_tokens ?? '?'}t
        </summary>
        <pre className="mt-2 rounded bg-surface-2 p-2 text-[11px] overflow-x-auto whitespace-pre-wrap break-all max-h-60 overflow-y-auto">
{result.raw_content || '（空）'}
        </pre>
      </details>
    </div>
  )
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const v = verdict.toUpperCase()
  const cls =
    v === 'CORRECT' ? 'badge-positive'
    : v === 'INCORRECT' ? 'badge-negative'
    : 'badge-neutral'
  return <span className={`badge ${cls} text-[10px]`}>{v}</span>
}

// ────────────────────────────────────────────────────────────────────────
// EvaluatorPicker：评估器下拉选
// ────────────────────────────────────────────────────────────────────────

function EvaluatorPicker({
  label,
  value,
  onChange,
  options,
  excludeId,
  loading,
}: {
  label: string
  value: string
  onChange: (id: string) => void
  options: EvaluatorInstance[]
  excludeId?: string
  loading?: boolean
}) {
  const filtered = options.filter(o => o.id !== excludeId)
  return (
    <label className="block">
      <span className="field-label">{label}</span>
      <select
        className="input"
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={loading}
      >
        <option value="">{loading ? '加载中…' : '请选择评估器'}</option>
        {filtered.map(o => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </label>
  )
}

// ────────────────────────────────────────────────────────────────────────
// 基准导入抽屉
// ────────────────────────────────────────────────────────────────────────

function BenchmarkImportDrawer({
  open,
  onClose,
  onImport,
}: {
  open: boolean
  onClose: () => void
  onImport: (cases: BenchmarkCase[]) => void
}) {
  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
  })
  const [projectId, setProjectId] = useState<string>('')
  const [search, setSearch] = useState<string>('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  const casesQuery = useQuery({
    queryKey: ['benchmark-cases-compare', projectId, search],
    queryFn: () => benchmarkApi
      .listCases(projectId, { search: search || undefined, page: 1, page_size: 100 })
      .then(r => r.data),
    enabled: !!projectId,
  })

  const cases = casesQuery.data?.items ?? []

  function toggle(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function importSelected() {
    const picked = cases.filter(c => selectedIds.has(c.id))
    onImport(picked)
    setSelectedIds(new Set())
  }

  return (
    <Drawer open={open} onClose={onClose} title="从基准测试集导入" width="wide">
      <div className="space-y-3 text-[12px]">
        <label className="block">
          <span className="field-label">项目</span>
          <select
            className="input"
            value={projectId}
            onChange={e => { setProjectId(e.target.value); setSelectedIds(new Set()) }}
          >
            <option value="">{projectsQuery.isLoading ? '加载中…' : '请选择项目'}</option>
            {projectsQuery.data?.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>

        {projectId && (
          <>
            <label className="block">
              <span className="field-label">搜索 case</span>
              <input
                className="input"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="按 question 文本过滤…"
              />
            </label>

            <div className="section-row">
              <span className="text-text-tertiary">
                共 {cases.length} 条 · 已选 {selectedIds.size}
              </span>
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => setSelectedIds(new Set(cases.map(c => c.id)))}>
                  全选
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSelectedIds(new Set())}>
                  取消
                </Button>
                <Button size="sm" variant="primary" onClick={importSelected} disabled={selectedIds.size === 0}>
                  导入所选 ({selectedIds.size})
                </Button>
              </div>
            </div>

            <div className="rounded border border-border-subtle max-h-[60vh] overflow-y-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th className="w-8"></th>
                    <th>问题</th>
                    <th className="w-44">参考答案</th>
                  </tr>
                </thead>
                <tbody>
                  {casesQuery.isLoading && (
                    <tr><td colSpan={3} className="empty-state">加载中…</td></tr>
                  )}
                  {!casesQuery.isLoading && cases.length === 0 && (
                    <tr><td colSpan={3} className="empty-state">没有匹配的 case</td></tr>
                  )}
                  {cases.map(c => (
                    <tr key={c.id} className="cursor-pointer" onClick={() => toggle(c.id)}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selectedIds.has(c.id)}
                          onChange={() => toggle(c.id)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                      <td className="text-[12px] max-w-[420px]">
                        <div className="truncate" title={c.question}>{c.question}</div>
                      </td>
                      <td className="text-[11px] text-text-tertiary max-w-[200px]">
                        <div className="truncate" title={c.reference_answer || ''}>
                          {c.reference_answer || '—'}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </Drawer>
  )
}
