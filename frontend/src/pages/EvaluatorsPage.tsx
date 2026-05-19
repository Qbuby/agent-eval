import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { evaluationApi } from '@/services'
import type {
  CreateEvaluatorRequest, EvaluatorInstance,
} from '@/types'

// Tag-only mode (since 2026-05-19): an evaluator is just a named template
// for a Langfuse trace tag. Selecting evaluators on a run stamps each
// one's `tag` onto every sample's Langfuse trace, then Langfuse-side
// evaluators bound to those tags pick the trace up and produce scores —
// which we pull back into evaluation_scores. No more local scoring fns.
export default function EvaluatorsPage() {
  const qc = useQueryClient()

  const listQuery = useQuery({
    queryKey: ['evaluator-instances'],
    queryFn: () => evaluationApi.listEvaluators().then(r => r.data),
  })

  const [showCreate, setShowCreate] = useState(false)
  const [editing, setEditing] = useState<EvaluatorInstance | null>(null)

  return (
    <div>
      <header className="mb-5 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-light tracking-tight mb-1">评估器</h1>
          <p className="text-[10px] text-text-tertiary tracking-widest uppercase">
            Tag templates · stamped onto every sample's Langfuse trace
          </p>
        </div>
        <button
          onClick={() => { setEditing(null); setShowCreate(true) }}
          className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90"
        >
          + 新建评估器
        </button>
      </header>

      <div className="border border-border rounded-[3px] overflow-hidden bg-surface">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <Th>名称</Th><Th>Tag (写到 trace)</Th><Th>描述</Th><Th>状态</Th><Th>创建时间</Th><Th>操作</Th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={6} className="py-6 text-center text-[12px] text-text-tertiary">加载中…</td></tr>
            )}
            {listQuery.data?.length === 0 && !listQuery.isLoading && (
              <tr><td colSpan={6} className="py-10 text-center text-[12px] text-text-tertiary">
                还没有评估器。新建一个：填好 tag 后，运行评估时勾选它，
                平台会把这个 tag 加到每条样例的 Langfuse trace 上。
              </td></tr>
            )}
            {listQuery.data?.map(e => (
              <tr key={e.id} className="hover:bg-accent-subtle/40">
                <Td><span className="font-medium">{e.name}</span></Td>
                <Td mono>
                  <span className="text-[10px] px-1.5 py-0.5 rounded border border-blue-300 bg-blue-50 text-blue-800">
                    {e.tag || e.name}
                  </span>
                </Td>
                <Td>{e.description || '—'}</Td>
                <Td>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                    e.is_active ? 'border-green-300 bg-green-50 text-green-700' : 'border-gray-300 bg-gray-50 text-gray-600'
                  }`}>
                    {e.is_active ? 'active' : 'inactive'}
                  </span>
                </Td>
                <Td>{e.created_at ? new Date(e.created_at).toLocaleString() : '—'}</Td>
                <Td>
                  <button
                    onClick={() => { setEditing(e); setShowCreate(true) }}
                    className="text-[11px] text-accent hover:underline mr-2"
                  >
                    编辑
                  </button>
                  <button
                    onClick={async () => {
                      if (!confirm(`删除评估器「${e.name}」？`)) return
                      await evaluationApi.deleteEvaluator(e.id)
                      qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
                    }}
                    className="text-[11px] text-negative hover:underline"
                  >
                    删除
                  </button>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <EvaluatorEditor
          editing={editing}
          onClose={() => { setShowCreate(false); setEditing(null) }}
        />
      )}
    </div>
  )
}


function EvaluatorEditor({
  editing, onClose,
}: {
  editing: EvaluatorInstance | null
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [name, setName] = useState(editing?.name || '')
  // Default tag = name on create. Editing: keep whatever the row has.
  const [tag, setTag] = useState(editing?.tag || '')
  const [description, setDescription] = useState(editing?.description || '')
  const [isActive, setIsActive] = useState(editing ? editing.is_active : true)

  const saveMutation = useMutation({
    mutationFn: async () => {
      const effectiveTag = tag.trim() || name.trim()
      if (editing) {
        return evaluationApi.updateEvaluator(editing.id, {
          name, tag: effectiveTag, description, is_active: isActive,
        }).then(r => r.data)
      }
      const body: CreateEvaluatorRequest = {
        name, tag: effectiveTag, description, is_active: isActive,
      }
      return evaluationApi.createEvaluator(body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
      onClose()
    },
  })

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-surface rounded-[8px] border border-border w-[560px] max-h-[90vh] overflow-y-auto p-5"
           onClick={e => e.stopPropagation()}>
        <h3 className="text-[13px] font-medium mb-4">
          {editing ? '编辑评估器' : '新建评估器'}
        </h3>

        <p className="text-[11px] text-text-secondary mb-4 leading-relaxed">
          每个评估器对应一个 Langfuse trace tag。运行评估时勾选它，平台会把
          <code className="mx-1 px-1 bg-accent-subtle rounded">tag</code>
          加到每条样例的 trace 上 — 这样在 Langfuse 里配置成 target=tag 的评估器
          就会自动处理这些 trace 并打分。打分会被本平台拉回展示在样例旁。
        </p>

        <div className="flex flex-col gap-3">
          <Field label="名称（唯一，UI 展示用）">
            <input
              type="text" value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. 正确性 / Goal Accuracy"
              className="input"
            />
          </Field>

          <Field label="Tag（写到 Langfuse trace 的字符串，留空则用名称）">
            <input
              type="text" value={tag} onChange={e => setTag(e.target.value)}
              placeholder="e.g. agent-eval-correctness"
              className="input font-mono text-[11px]"
            />
            <div className="mt-1 text-[10px] text-text-tertiary">
              每条样例的 Langfuse trace 都会带上这个 tag。Langfuse 端配的同名
              evaluator 会被触发；你也可以多个评估器用相同 tag 让它们共用一份
              Langfuse 配置。
            </div>
          </Field>

          <Field label="描述（可选）">
            <input
              type="text" value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="e.g. 整体回答正确性，由 Langfuse 上的 LLM-judge 给分"
              className="input"
            />
          </Field>

          <label className="inline-flex items-center gap-1.5 text-[12px]">
            <input
              type="checkbox" checked={isActive}
              onChange={e => setIsActive(e.target.checked)}
              className="accent-accent"
            />
            启用（运行时可选）
          </label>
        </div>

        {saveMutation.isError && (
          <p className="text-[11px] text-negative mt-2">
            {(saveMutation.error as Error)?.message || '保存失败'}
          </p>
        )}

        <div className="flex items-center justify-end gap-2 mt-4 pt-3 border-t border-border">
          <button onClick={onClose}
                  className="py-1.5 px-3 text-[11px] rounded-[6px] border border-border hover:border-accent">
            取消
          </button>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={!name.trim() || saveMutation.isPending}
            className="py-1.5 px-3 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent disabled:opacity-40 hover:opacity-90"
          >
            {saveMutation.isPending ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}


function Th({ children }: { children: React.ReactNode }) {
  return <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">{children}</th>
}
function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return <td className={`py-2 px-3 border-b border-border text-[12px] ${mono ? 'font-mono text-[11px]' : ''}`}>{children}</td>
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] tracking-widest uppercase text-text-tertiary">{label}</span>
      {children}
    </label>
  )
}
