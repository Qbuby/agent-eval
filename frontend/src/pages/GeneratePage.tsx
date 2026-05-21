import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { generateApi, datasetsApi } from '@/services'
import type { TestCase } from '@/types'

type Scenario = 'faithfulness' | 'context_recall' | 'answer_relevancy' | 'context_precision' | 'context_relevancy' | 'hallucination'
type Category = 'normal' | 'bad_case' | 'edge_case'

interface GenerateForm {
  dataset: string
  scenario: Scenario | ''
  category: Category | ''
  count: number
  context: string
}

const SCENARIOS: { value: Scenario; label: string }[] = [
  { value: 'faithfulness', label: '忠实度 (Faithfulness)' },
  { value: 'context_recall', label: '上下文召回率 (Context Recall)' },
  { value: 'answer_relevancy', label: '答案相关性 (Answer Relevancy)' },
  { value: 'context_precision', label: '上下文精准度 (Context Precision)' },
  { value: 'context_relevancy', label: '上下文相关性 (Context Relevancy)' },
  { value: 'hallucination', label: '幻觉率 (Hallucination)' },
]

const CATEGORIES: { value: Category; label: string }[] = [
  { value: 'normal', label: '正常 Case (Normal)' },
  { value: 'bad_case', label: 'Bad Case' },
  { value: 'edge_case', label: 'Edge Case' },
]

// FastAPI 校验失败时 detail 是数组，直接 setError(detail) 会让 React 渲染崩溃
// (error #31: Objects are not valid as a React child)。统一在这里转成字符串。
function extractErrorMessage(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: unknown) => {
        if (typeof d === 'string') return d
        const o = d as { loc?: unknown[]; msg?: string }
        const loc = Array.isArray(o.loc) ? o.loc.slice(1).join('.') : ''
        return loc ? `${loc}: ${o.msg ?? '校验失败'}` : (o.msg ?? '校验失败')
      })
      .join('；')
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail)
  return (err as Error)?.message ?? ''
}

export default function GeneratePage() {
  const [form, setForm] = useState<GenerateForm>({
    dataset: '',
    scenario: '',
    count: 5,
    context: '',
    category: '',
  })
  const [previewCases, setPreviewCases] = useState<TestCase[]>([])
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [phase, setPhase] = useState<'form' | 'preview'>('form')
  const [error, setError] = useState('')

  const { data: datasets } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => datasetsApi.list().then((r) => r.data),
  })

  const generateMutation = useMutation({
    mutationFn: () =>
      generateApi.scenario({
        dataset: form.dataset,
        test_scenario: form.scenario,
        case_category: form.category || 'normal',
        count: form.count,
        context: form.context || undefined,
        dry_run: true,
      }),
    onSuccess: (res) => {
      const cases = (res.data.cases || []) as TestCase[]
      setPreviewCases(cases)
      setPhase('preview')
      setError('')
    },
    onError: (err: unknown) => {
      setError(extractErrorMessage(err) || '生成失败')
    },
  })

  const saveMutation = useMutation({
    mutationFn: () =>
      datasetsApi.addCases(form.dataset, { cases: previewCases }),
    onSuccess: () => {
      setPhase('form')
      setPreviewCases([])
      setError('')
    },
    onError: (err: unknown) => {
      setError(extractErrorMessage(err) || '保存失败')
    },
  })

  function handlePreview(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    generateMutation.mutate()
  }

  function removePreviewCase(index: number) {
    setPreviewCases((prev) => prev.filter((_, i) => i !== index))
  }

  function updatePreviewCase(index: number, updated: TestCase) {
    setPreviewCases((prev) => prev.map((c, i) => (i === index ? updated : c)))
    setEditingIndex(null)
  }

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-lg font-light tracking-tight mb-1">样例生成</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">SCENARIO GENERATION · TEST CASE CREATION</p>
      </header>

      {phase === 'form' && (
        <form onSubmit={handlePreview} className="bg-surface border border-border rounded-md p-5 space-y-4 max-w-[520px]">
          <div className="group">
            <label htmlFor="gen-dataset" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              目标数据集
            </label>
            <select
              id="gen-dataset"
              value={form.dataset}
              onChange={(e) => setForm({ ...form, dataset: e.target.value })}
              required
              className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            >
              <option value="">选择数据集</option>
              {datasets?.map((ds) => (
                <option key={ds.id} value={ds.name}>{ds.name}</option>
              ))}
            </select>
          </div>

          <div className="group">
            <label htmlFor="gen-scenario" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              测试场景
            </label>
            <select
              id="gen-scenario"
              value={form.scenario}
              onChange={(e) => setForm({ ...form, scenario: e.target.value as Scenario })}
              required
              className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            >
              <option value="">选择场景</option>
              {SCENARIOS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>

          <div className="group">
            <label htmlFor="gen-category" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              样例类别
            </label>
            <select
              id="gen-category"
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value as Category })}
              required
              className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            >
              <option value="">选择类别</option>
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </div>

          <div className="group">
            <label htmlFor="gen-count" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              生成数量
            </label>
            <input
              id="gen-count"
              type="number"
              min={1}
              max={20}
              value={form.count}
              onChange={(e) => setForm({ ...form, count: Number(e.target.value) })}
              className="w-24 py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            />
          </div>

          <div className="group">
            <label htmlFor="gen-context" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              上下文（可选）
            </label>
            <textarea
              id="gen-context"
              value={form.context}
              onChange={(e) => setForm({ ...form, context: e.target.value })}
              rows={3}
              className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 resize-y transition-all duration-200"
            />
          </div>

          {error && <p className="text-[11px] text-negative animate-fade-in">{error}</p>}

          <button
            type="submit"
            disabled={generateMutation.isPending}
            className="inline-flex items-center gap-1.5 py-2 px-4 text-[11px] font-medium tracking-wide rounded-[6px] bg-accent text-white border border-accent cursor-pointer hover:opacity-90 hover:scale-[1.02] active:scale-[0.97] focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:opacity-40 transition-all duration-200"
          >
            {generateMutation.isPending ? (
              <span className="inline-block w-3 h-3 border border-white/40 border-t-white rounded-full animate-spin" />
            ) : '生成预览'}
          </button>
        </form>
      )}

      {phase === 'preview' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <span className="text-[12px] text-text-secondary">
                已生成 {previewCases.length} 条样例，可编辑或删除后确认添加
              </span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => { setPhase('form'); setPreviewCases([]) }}
                className="py-1.5 px-3 text-[11px] font-medium tracking-wide rounded-[6px] bg-surface text-text-primary border border-border hover:border-accent active:scale-[0.97] transition-all duration-200"
              >
                返回修改
              </button>
              <button
                onClick={() => saveMutation.mutate()}
                disabled={previewCases.length === 0 || saveMutation.isPending}
                className="py-1.5 px-3 text-[11px] font-medium tracking-wide rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 active:scale-[0.97] disabled:opacity-40 transition-all duration-200"
              >
                {saveMutation.isPending ? '保存中...' : `确认添加 (${previewCases.length})`}
              </button>
            </div>
          </div>

          {error && <p className="text-[11px] text-negative animate-fade-in">{error}</p>}

          <div className="space-y-3">
            {previewCases.map((c, idx) => (
              <div key={idx} className="bg-surface border border-border rounded-md p-4 animate-fade-in hover:border-accent/20 transition-all">
                {editingIndex === idx ? (
                  <PreviewCaseEditor
                    caseData={c}
                    onSave={(updated) => updatePreviewCase(idx, updated)}
                    onCancel={() => setEditingIndex(null)}
                  />
                ) : (
                  <div>
                    <div className="flex justify-between items-start mb-2">
                      <span className="text-[12px] font-medium text-text-primary">{c.name || `样例 ${idx + 1}`}</span>
                      <div className="flex gap-2">
                        <button
                          onClick={() => setEditingIndex(idx)}
                          className="text-[10px] text-text-tertiary hover:text-accent active:scale-95 transition-all"
                        >
                          编辑
                        </button>
                        <button
                          onClick={() => removePreviewCase(idx)}
                          className="text-[10px] text-text-tertiary hover:text-negative active:scale-95 transition-all"
                        >
                          删除
                        </button>
                      </div>
                    </div>
                    {c.description && (
                      <p className="text-[11px] text-text-secondary mb-2">{c.description}</p>
                    )}
                    {c.tags && c.tags.length > 0 && (
                      <div className="flex gap-1 mb-2">
                        {c.tags.map((tag) => (
                          <span key={tag} className="px-1.5 py-0.5 bg-accent-subtle rounded text-[10px] text-text-secondary">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {c.input_messages && c.input_messages.length > 0 && (
                      <div className="mt-2 space-y-1">
                        {c.input_messages.map((msg, mi) => (
                          <div key={mi} className="text-[11px] font-mono bg-accent-subtle rounded px-2 py-1">
                            <span className="text-text-tertiary">{msg.role}:</span>{' '}
                            <span className="text-text-primary">{msg.content.length > 120 ? msg.content.slice(0, 120) + '...' : msg.content}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {c.expected_output && (
                      <div className="mt-2 text-[11px] text-text-secondary">
                        <span className="text-text-tertiary">期望输出: </span>{c.expected_output.length > 100 ? c.expected_output.slice(0, 100) + '...' : c.expected_output}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {previewCases.length === 0 && (
            <div className="text-center py-8 text-text-tertiary text-[12px]">所有样例已删除，请返回重新生成</div>
          )}
        </div>
      )}
    </div>
  )
}

function PreviewCaseEditor({
  caseData,
  onSave,
  onCancel,
}: {
  caseData: TestCase
  onSave: (updated: TestCase) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(caseData.name || '')
  const [description, setDescription] = useState(caseData.description || '')
  const [tags, setTags] = useState((caseData.tags || []).join(', '))
  const [expectedOutput, setExpectedOutput] = useState(caseData.expected_output || '')

  function handleSave() {
    onSave({
      ...caseData,
      name,
      description: description || undefined,
      tags: tags ? tags.split(',').map((t) => t.trim()).filter(Boolean) : undefined,
      expected_output: expectedOutput || undefined,
    })
  }

  return (
    <div className="space-y-3">
      <div className="group">
        <label className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1 group-focus-within:text-accent transition-colors">名称</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full py-1.5 px-2 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
        />
      </div>
      <div className="group">
        <label className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1 group-focus-within:text-accent transition-colors">描述</label>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="w-full py-1.5 px-2 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
        />
      </div>
      <div className="group">
        <label className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1 group-focus-within:text-accent transition-colors">标签</label>
        <input
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          className="w-full py-1.5 px-2 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
        />
      </div>
      <div className="group">
        <label className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1 group-focus-within:text-accent transition-colors">期望输出</label>
        <textarea
          value={expectedOutput}
          onChange={(e) => setExpectedOutput(e.target.value)}
          rows={2}
          className="w-full py-1.5 px-2 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
        />
      </div>
      <div className="flex gap-2">
        <button onClick={handleSave} className="py-1.5 px-3 text-[10px] font-medium rounded-[6px] bg-accent text-white hover:opacity-90 active:scale-[0.97] transition-all">
          保存
        </button>
        <button onClick={onCancel} className="py-1.5 px-3 text-[10px] font-medium rounded-[6px] border border-border text-text-primary hover:border-accent active:scale-[0.97] transition-all">
          取消
        </button>
      </div>
    </div>
  )
}
