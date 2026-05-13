import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { evaluationApi } from '@/services'
import type {
  BuiltinEvaluator, CreateEvaluatorRequest, EvaluatorInstance,
} from '@/types'

export default function EvaluatorsPage() {
  const qc = useQueryClient()

  const listQuery = useQuery({
    queryKey: ['evaluator-instances'],
    queryFn: () => evaluationApi.listEvaluators().then(r => r.data),
  })
  const builtinQuery = useQuery({
    queryKey: ['evaluator-builtin'],
    queryFn: () => evaluationApi.listBuiltinEvaluators().then(r => r.data),
  })

  const [showCreate, setShowCreate] = useState(false)
  const [editing, setEditing] = useState<EvaluatorInstance | null>(null)

  return (
    <div>
      <header className="mb-5 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-light tracking-tight mb-1">评估器</h1>
          <p className="text-[10px] text-text-tertiary tracking-widest uppercase">
            Evaluator instances · reused across runs
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
              <Th>名称</Th><Th>类型</Th><Th>描述</Th><Th>状态</Th><Th>创建时间</Th><Th>操作</Th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={6} className="py-6 text-center text-[12px] text-text-tertiary">加载中…</td></tr>
            )}
            {listQuery.data?.length === 0 && !listQuery.isLoading && (
              <tr><td colSpan={6} className="py-10 text-center text-[12px] text-text-tertiary">
                还没有评估器。点右上角新建一个，才能在评估运行时选用。
              </td></tr>
            )}
            {listQuery.data?.map(e => (
              <tr key={e.id} className="hover:bg-accent-subtle/40">
                <Td><span className="font-medium">{e.name}</span></Td>
                <Td mono>{e.evaluator_type}</Td>
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
          builtins={builtinQuery.data || []}
          onClose={() => { setShowCreate(false); setEditing(null) }}
        />
      )}
    </div>
  )
}


function EvaluatorEditor({
  editing, builtins, onClose,
}: {
  editing: EvaluatorInstance | null
  builtins: BuiltinEvaluator[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [name, setName] = useState(editing?.name || '')
  const [evaluatorType, setEvaluatorType] = useState(editing?.evaluator_type || builtins[0]?.name || 'exact_match')
  const [description, setDescription] = useState(editing?.description || '')
  const [isActive, setIsActive] = useState(editing ? editing.is_active : true)
  const [paramsText, setParamsText] = useState(
    editing ? JSON.stringify(editing.params, null, 2) : '{}'
  )

  const saveMutation = useMutation({
    mutationFn: async () => {
      let params: Record<string, unknown> = {}
      try {
        params = paramsText.trim() ? JSON.parse(paramsText) : {}
      } catch {
        throw new Error('params 必须是合法 JSON')
      }
      if (editing) {
        return evaluationApi.updateEvaluator(editing.id, {
          name, description, params, is_active: isActive,
        }).then(r => r.data)
      }
      const body: CreateEvaluatorRequest = {
        name, evaluator_type: evaluatorType, description, params, is_active: isActive,
      }
      return evaluationApi.createEvaluator(body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
      onClose()
    },
  })

  const currentTmpl = builtins.find(b => b.name === evaluatorType)

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-surface rounded-[8px] border border-border w-[560px] max-h-[90vh] overflow-y-auto p-5"
           onClick={e => e.stopPropagation()}>
        <h3 className="text-[13px] font-medium mb-4">
          {editing ? '编辑评估器' : '新建评估器'}
        </h3>

        <div className="flex flex-col gap-3">
          <Field label="名称（唯一）">
            <input
              type="text" value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. strict-exact / kiro-judge-v1"
              className="input"
            />
          </Field>

          {!editing && (
            <Field label="类型">
              <select value={evaluatorType} onChange={e => setEvaluatorType(e.target.value)} className="input">
                {builtins.map(b => <option key={b.name} value={b.name}>{b.name}</option>)}
              </select>
              {currentTmpl && (
                <div className="mt-1 text-[10px] text-text-tertiary">{currentTmpl.description}</div>
              )}
            </Field>
          )}

          <Field label="描述">
            <input
              type="text" value={description}
              onChange={e => setDescription(e.target.value)}
              className="input"
            />
          </Field>

          <Field label="参数（JSON）">
            <textarea
              value={paramsText} onChange={e => setParamsText(e.target.value)}
              rows={8}
              className="input font-mono text-[11px]"
              placeholder={JSON.stringify(currentTmpl?.params_schema || {}, null, 2)}
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
