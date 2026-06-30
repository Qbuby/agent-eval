import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { generateApi, datasetsApi } from '@/services'
import { Button } from '@/components/ui'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { TestCase } from '@/types'

type Category = 'normal' | 'bad_case' | 'edge_case'

interface GenerateForm {
  dataset: string
  scenario: string
  category: Category | ''
  count: number
  context: string
}

const CATEGORIES: { value: Category; label: string }[] = [
  { value: 'normal', label: '正常 Case (Normal)' },
  { value: 'bad_case', label: 'Bad Case' },
  { value: 'edge_case', label: 'Edge Case' },
]

function extractErrorMessage(err: unknown): string {
  return toToastMessage(formatApiError(err, { fallbackMessage: '' }))
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
      <header className="mb-6">
        <div className="page-eyebrow">数据集</div>
        <h1 className="page-title">样例生成</h1>
        <p className="page-subtitle">按测试场景与类别批量生成样例，预览后入库</p>
      </header>

      {phase === 'form' && (
        <form onSubmit={handlePreview} className="card p-5 max-w-[560px] space-y-4">
          <div>
            <label htmlFor="gen-dataset" className="field-label">目标数据集</label>
            <select
              id="gen-dataset"
              value={form.dataset}
              onChange={(e) => setForm({ ...form, dataset: e.target.value })}
              required
              className="input"
            >
              <option value="">选择数据集</option>
              {datasets?.map((ds) => (
                <option key={ds.id} value={ds.name}>{ds.name}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="gen-scenario" className="field-label">测试场景 / 主题（可选）</label>
            <input
              id="gen-scenario"
              type="text"
              value={form.scenario}
              onChange={(e) => setForm({ ...form, scenario: e.target.value })}
              placeholder="留空则由智能体围绕其核心领域能力自由出题"
              className="input"
            />
            <p className="mt-1 text-[11px] text-text-tertiary">
              样例由被测智能体基于自身知识图谱生成，填写场景可聚焦特定主题
            </p>
          </div>

          <div>
            <label htmlFor="gen-category" className="field-label">样例类别</label>
            <select
              id="gen-category"
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value as Category })}
              required
              className="input"
            >
              <option value="">选择类别</option>
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="gen-count" className="field-label">生成数量</label>
            <input
              id="gen-count"
              type="number"
              min={1}
              max={20}
              value={form.count}
              onChange={(e) => setForm({ ...form, count: Number(e.target.value) })}
              className="input w-28"
            />
          </div>

          <div>
            <label htmlFor="gen-context" className="field-label">上下文（可选）</label>
            <textarea
              id="gen-context"
              value={form.context}
              onChange={(e) => setForm({ ...form, context: e.target.value })}
              rows={3}
              className="input resize-y"
            />
          </div>

          {error && <p className="text-[12px] text-negative">{error}</p>}

          <div className="pt-2">
            <Button type="submit" variant="primary" size="md" loading={generateMutation.isPending}>
              生成预览
            </Button>
          </div>
        </form>
      )}

      {phase === 'preview' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-[12px] text-text-secondary">
              已生成 {previewCases.length} 条样例，可编辑或删除后确认添加
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => { setPhase('form'); setPreviewCases([]) }}
              >
                返回修改
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={previewCases.length === 0}
                loading={saveMutation.isPending}
                onClick={() => saveMutation.mutate()}
              >
                确认添加 ({previewCases.length})
              </Button>
            </div>
          </div>

          {error && <p className="text-[12px] text-negative">{error}</p>}

          <div className="space-y-3">
            {previewCases.map((c, idx) => (
              <div key={idx} className="card p-4">
                {editingIndex === idx ? (
                  <PreviewCaseEditor
                    caseData={c}
                    onSave={(updated) => updatePreviewCase(idx, updated)}
                    onCancel={() => setEditingIndex(null)}
                  />
                ) : (
                  <div>
                    <div className="flex justify-between items-start mb-2">
                      <span className="text-[13px] font-medium text-text-primary">{c.name || `样例 ${idx + 1}`}</span>
                      <div className="flex gap-3">
                        <button
                          onClick={() => setEditingIndex(idx)}
                          className="text-action"
                        >
                          编辑
                        </button>
                        <button
                          onClick={() => removePreviewCase(idx)}
                          className="text-action-danger"
                        >
                          删除
                        </button>
                      </div>
                    </div>
                    {c.description && (
                      <p className="text-[12px] text-text-secondary mb-2">{c.description}</p>
                    )}
                    {c.tags && c.tags.length > 0 && (
                      <div className="flex gap-1 mb-2">
                        {c.tags.map((tag) => (
                          <span key={tag} className="badge badge-neutral">{tag}</span>
                        ))}
                      </div>
                    )}
                    {c.input_messages && c.input_messages.length > 0 && (
                      <div className="mt-2 space-y-1">
                        {c.input_messages.map((msg, mi) => (
                          <div key={mi} className="text-[11px] font-mono bg-fill/5 rounded-md px-2.5 py-1.5">
                            <span className="text-text-tertiary">{msg.role}:</span>{' '}
                            <span className="text-text-primary">{msg.content.length > 120 ? msg.content.slice(0, 120) + '…' : msg.content}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {c.expected_output && (
                      <div className="mt-2 text-[12px] text-text-secondary">
                        <span className="text-text-tertiary">期望输出：</span>{c.expected_output.length > 100 ? c.expected_output.slice(0, 100) + '…' : c.expected_output}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {previewCases.length === 0 && (
            <div className="empty-state">所有样例已删除，请返回重新生成</div>
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
      <div>
        <label className="field-label">名称</label>
        <input value={name} onChange={(e) => setName(e.target.value)} className="input" />
      </div>
      <div>
        <label className="field-label">描述</label>
        <input value={description} onChange={(e) => setDescription(e.target.value)} className="input" />
      </div>
      <div>
        <label className="field-label">标签</label>
        <input value={tags} onChange={(e) => setTags(e.target.value)} className="input" />
      </div>
      <div>
        <label className="field-label">期望输出</label>
        <textarea
          value={expectedOutput}
          onChange={(e) => setExpectedOutput(e.target.value)}
          rows={2}
          className="input resize-y"
        />
      </div>
      <div className="flex gap-2 pt-1">
        <Button variant="primary" size="sm" onClick={handleSave}>保存</Button>
        <Button variant="secondary" size="sm" onClick={onCancel}>取消</Button>
      </div>
    </div>
  )
}
