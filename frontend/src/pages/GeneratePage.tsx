import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { generateApi, datasetsApi, candidatesApi } from '@/services'
import { Button, Dialog, useToast } from '@/components/ui'
import { formatApiError, toToastMessage } from '@/lib/errors'
import { useConfigOptions, configOptionToString } from '@/hooks/useConfigOptions'
import type { TestCase, TurnExpectation } from '@/types'

type Category = 'normal' | 'bad_case' | 'edge_case'

interface GenerateForm {
  dataset: string
  scenario: string
  category: Category | ''
  count: number
  context: string
  // 被测 agent 端点：空串 = 用后端 target_agent.endpoint_url 默认；
  // 否则为已配置的 endpoint 预设之一（值即 URL），仅覆盖端点，其余凭据仍取共享配置。
  agentEndpoint: string
  // 勾选后：生成样例时让被测 agent 实跑一遍每个问题，用真实回答覆盖 expected_output
  // （多轮逐轮回填）。默认关，开启会成倍增加耗时。
  runAgent: boolean
}

const CATEGORIES: { value: Category; label: string }[] = [
  { value: 'normal', label: '正常 Case (Normal)' },
  { value: 'bad_case', label: 'Bad Case' },
  { value: 'edge_case', label: 'Edge Case' },
]

function extractErrorMessage(err: unknown): string {
  return toToastMessage(formatApiError(err, { fallbackMessage: '' }))
}

// 取一条样例的首条 user 问句（多轮取第一条 user 消息）。
function firstUserQuestion(c: TestCase): string {
  const msg = (c.input_messages || []).find((m) => m.role === 'user' && m.content)
  return msg?.content || ''
}

export default function GeneratePage() {
  const toast = useToast()
  const [form, setForm] = useState<GenerateForm>({
    dataset: '',
    scenario: '',
    count: 5,
    context: '',
    category: '',
    agentEndpoint: '',
    runAgent: false,
  })
  const [previewCases, setPreviewCases] = useState<TestCase[]>([])
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [phase, setPhase] = useState<'form' | 'preview'>('form')
  const [error, setError] = useState('')

  const { data: datasets } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => datasetsApi.list().then((r) => r.data),
  })

  // 选中数据集的类型：candidate → 落 candidate_cases；conversation → 落 Langfuse。
  const selectedDatasetType =
    datasets?.find((d) => d.name === form.dataset)?.dataset_type ?? 'candidate'

  // 复用评估页的 target_agent.endpoint_url 预设作为可选的被测 agent 列表；
  // 每条 option 的 label 是人给起的名字，value 是端点 URL。
  const endpointOpts = useConfigOptions('target_agent.endpoint_url')

  const generateMutation = useMutation({
    mutationFn: () =>
      generateApi.scenario({
        dataset: form.dataset,
        test_scenario: form.scenario,
        case_category: form.category || 'normal',
        count: form.count,
        context: form.context || undefined,
        agent_endpoint_url: form.agentEndpoint || undefined,
        run_agent: form.runAgent,
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

  // 确认添加：按目标数据集类型路由存储。
  //   - conversation → datasetsApi.addCases（写 Langfuse dataset items，多轮回放读这里）
  //   - candidate    → candidatesApi.batchCreate（写 candidate_cases，备选详情页读这里）
  // 生成页此前恒走 addCases，导致在 candidate 数据集上「确认添加」后详情页看不到。
  const saveMutation = useMutation({
    mutationFn: async () => {
      if (selectedDatasetType === 'conversation') {
        const res = await datasetsApi.addCases(form.dataset, { cases: previewCases })
        return { added: res.data.added ?? previewCases.length }
      }
      // candidate：TestCase → candidate 行（question=首条 user 问句，answer=期望输出）
      const cases = previewCases.map((c) => ({
        question: firstUserQuestion(c),
        answer: c.expected_output || undefined,
        category: c.category || undefined,
        tags: c.tags,
        source: 'generated',
      }))
      const res = await candidatesApi.batchCreate({ dataset_name: form.dataset, cases })
      return { added: res.data.added ?? 0 }
    },
    onSuccess: ({ added }) => {
      if (added > 0) {
        toast.success(`已添加 ${added} 条样例到「${form.dataset}」`, '添加成功')
        setPhase('form')
        setPreviewCases([])
        setError('')
      } else {
        // added===0：入库端点未写入任何行（问题均为空等），明确报错而非静默成功。
        setError('没有样例被添加（可能所有问题为空），请检查生成结果')
      }
    },
    onError: (err: unknown) => {
      const msg = extractErrorMessage(err) || '添加失败'
      setError(msg)
      toast.error(msg, '添加失败')
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
                <option key={ds.id} value={ds.name}>
                  {ds.name}（{ds.dataset_type === 'conversation' ? '多轮对话' : '备选'}）
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="gen-agent" className="field-label">被测智能体（可选）</label>
            <select
              id="gen-agent"
              value={form.agentEndpoint}
              onChange={(e) => setForm({ ...form, agentEndpoint: e.target.value })}
              className="input"
            >
              <option value="">默认（配置里的 target_agent 端点）</option>
              {endpointOpts.options.map((opt, i) => {
                const url = configOptionToString(opt.value)
                return (
                  <option key={i} value={url}>{opt.label ? `${opt.label}（${url}）` : url}</option>
                )
              })}
            </select>
            <p className="mt-1 text-[11px] text-text-tertiary">
              选择已配置的测试目标 agent（SSE）来出题；留空则用默认端点。凭据/超时仍取共享配置
            </p>
          </div>

          <div>
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                id="gen-run-agent"
                type="checkbox"
                checked={form.runAgent}
                onChange={(e) => setForm({ ...form, runAgent: e.target.checked })}
              />
              <span className="field-label !mb-0">让被测智能体实跑一遍，用真实回答作为期望输出</span>
            </label>
            <p className="mt-1 text-[11px] text-text-tertiary">
              勾选后生成的每个问题会真实调用一次被测 agent，把它的实际回答写入 expected_output（多轮逐轮回填）。会成倍增加耗时，默认关闭
            </p>
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
              {selectedDatasetType === 'conversation' ? '（多轮对话集）' : '（备选数据集）'}
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => { setPhase('form'); setPreviewCases([]); setError('') }}
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
                <div className="flex justify-between items-start mb-2">
                  <span className="text-[13px] font-medium text-text-primary">{c.name || `样例 ${idx + 1}`}</span>
                  <div className="flex gap-3">
                    <button onClick={() => setEditingIndex(idx)} className="text-action">
                      编辑
                    </button>
                    <button onClick={() => removePreviewCase(idx)} className="text-action-danger">
                      删除
                    </button>
                  </div>
                </div>
                {c.description && (
                  <p className="text-[12px] text-text-secondary mb-2">{c.description}</p>
                )}
                {c.tags && c.tags.length > 0 && (
                  <div className="flex gap-1 mb-2 flex-wrap">
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
                        <span className="text-text-primary">{msg.content.length > 200 ? msg.content.slice(0, 200) + '…' : msg.content}</span>
                      </div>
                    ))}
                  </div>
                )}
                {c.expected_output && (
                  <div className="mt-2 text-[12px] text-text-secondary">
                    <span className="text-text-tertiary">期望输出：</span>
                    <span className="whitespace-pre-wrap">{c.expected_output.length > 300 ? c.expected_output.slice(0, 300) + '…' : c.expected_output}</span>
                  </div>
                )}
                {c.turn_expectations && c.turn_expectations.length > 0 && (
                  <div className="mt-2 text-[11px] text-text-tertiary">
                    逐轮期望：{c.turn_expectations.filter((t) => t.expected_output).length}/{c.turn_expectations.length} 轮已填
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

      {editingIndex !== null && previewCases[editingIndex] && (
        <PreviewCaseEditorDialog
          caseData={previewCases[editingIndex]}
          onSave={(updated) => updatePreviewCase(editingIndex, updated)}
          onCancel={() => setEditingIndex(null)}
        />
      )}
    </div>
  )
}

function PreviewCaseEditorDialog({
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
  const [messages, setMessages] = useState(
    (caseData.input_messages || []).map((m) => ({ role: m.role, content: m.content })),
  )
  const [expectedOutput, setExpectedOutput] = useState(caseData.expected_output || '')
  const [conversationGoal, setConversationGoal] = useState(caseData.conversation_goal || '')
  const [turnExpectations, setTurnExpectations] = useState<TurnExpectation[]>(
    (caseData.turn_expectations || []).map((t) => ({
      turn_index: t.turn_index,
      criteria: t.criteria,
      expected_output: t.expected_output,
    })),
  )

  const isMultiTurn = messages.filter((m) => m.role === 'user').length > 1 || turnExpectations.length > 0

  function updateMessage(i: number, content: string) {
    setMessages((prev) => prev.map((m, idx) => (idx === i ? { ...m, content } : m)))
  }

  function updateTurnExpected(turnIndex: number, value: string) {
    setTurnExpectations((prev) => {
      const existing = prev.find((t) => t.turn_index === turnIndex)
      if (existing) {
        return prev.map((t) => (t.turn_index === turnIndex ? { ...t, expected_output: value || undefined } : t))
      }
      return [...prev, { turn_index: turnIndex, expected_output: value || undefined }]
    })
  }

  function handleSave() {
    onSave({
      ...caseData,
      name,
      description: description || undefined,
      tags: tags ? tags.split(',').map((t) => t.trim()).filter(Boolean) : undefined,
      input_messages: messages,
      expected_output: expectedOutput || undefined,
      conversation_goal: conversationGoal || undefined,
      turn_expectations: turnExpectations.length > 0 ? turnExpectations : undefined,
    })
  }

  return (
    <Dialog
      open
      onClose={onCancel}
      title="编辑样例"
      width={720}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onCancel}>取消</Button>
          <Button variant="primary" size="md" onClick={handleSave}>保存</Button>
        </>
      }
    >
      <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
        <div>
          <label className="field-label">名称</label>
          <input value={name} onChange={(e) => setName(e.target.value)} className="input" />
        </div>
        <div>
          <label className="field-label">描述</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="input resize-y" />
        </div>
        <div>
          <label className="field-label">标签（逗号分隔）</label>
          <input value={tags} onChange={(e) => setTags(e.target.value)} className="input" />
        </div>

        {isMultiTurn && (
          <div>
            <label className="field-label">会话目标</label>
            <input value={conversationGoal} onChange={(e) => setConversationGoal(e.target.value)} className="input" />
          </div>
        )}

        <div>
          <label className="field-label">
            {isMultiTurn ? '对话消息（逐轮）' : '输入消息'}
          </label>
          <div className="space-y-2">
            {messages.map((msg, i) => {
              // 该 user 消息在 input_messages 里的下标即 turn_index。
              const te = turnExpectations.find((t) => t.turn_index === i)
              return (
                <div key={i} className="border border-border rounded-md p-2.5 space-y-1.5">
                  <div className="flex items-center gap-2">
                    <span className={`badge ${msg.role === 'user' ? 'badge-info' : 'badge-neutral'}`}>{msg.role}</span>
                  </div>
                  <textarea
                    value={msg.content}
                    onChange={(e) => updateMessage(i, e.target.value)}
                    rows={3}
                    className="input resize-y text-[12px]"
                  />
                  {isMultiTurn && msg.role === 'user' && (
                    <div>
                      <label className="text-[11px] text-text-tertiary">本轮期望输出</label>
                      <textarea
                        value={te?.expected_output || ''}
                        onChange={(e) => updateTurnExpected(i, e.target.value)}
                        rows={2}
                        className="input resize-y text-[12px]"
                        placeholder="该轮的标准/期望答案"
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {!isMultiTurn && (
          <div>
            <label className="field-label">期望输出</label>
            <textarea
              value={expectedOutput}
              onChange={(e) => setExpectedOutput(e.target.value)}
              rows={8}
              className="input resize-y"
              placeholder="该样例的标准/期望答案"
            />
          </div>
        )}
      </div>
    </Dialog>
  )
}
