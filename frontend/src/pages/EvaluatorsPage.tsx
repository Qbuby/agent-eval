import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useConfirm, useToast } from '@/components/ui'
import { evaluationApi } from '@/services'
import type {
  CreateEvaluatorRequest, EvaluatorInstance,
} from '@/types'

export default function EvaluatorsPage() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['evaluator-instances'],
    queryFn: () => evaluationApi.listEvaluators().then(r => r.data),
  })

  const [showEditor, setShowEditor] = useState(false)
  const [editing, setEditing] = useState<EvaluatorInstance | null>(null)

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">评估</div>
        <h1 className="page-title">评估器</h1>
        <p className="page-subtitle">Tag 模板 — 运行时把它打到每条样例的 Langfuse trace 上</p>
      </header>

      <div className="section-row">
        <div className="page-eyebrow">评估器列表</div>
        <Button variant="primary" size="sm" onClick={() => { setEditing(null); setShowEditor(true) }}>
          新建评估器
        </Button>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>名称</th>
              <th>Tag</th>
              <th className="w-24">状态</th>
              <th className="w-44">创建时间</th>
              <th className="w-28 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={5} className="empty-state">加载中…</td></tr>
            )}
            {listQuery.data?.length === 0 && !listQuery.isLoading && (
              <tr><td colSpan={5} className="empty-state">
                还没有评估器。新建一个，运行评估时勾选它，平台会把这个 tag 加到每条样例的 Langfuse trace 上。
              </td></tr>
            )}
            {listQuery.data?.map(e => (
              <tr key={e.id} className="group">
                <td className="font-medium">{e.name}</td>
                <td>
                  <span className="font-mono text-[11px] text-text-secondary">{e.tag || e.name}</span>
                </td>
                <td>
                  <span className={e.is_active ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {e.is_active ? '启用' : '停用'}
                  </span>
                </td>
                <td className="text-text-tertiary text-[11px]">
                  {e.created_at ? new Date(e.created_at).toLocaleString() : '—'}
                </td>
                <td className="text-right">
                  <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => { setEditing(e); setShowEditor(true) }}
                      className="text-action"
                    >
                      编辑
                    </button>
                    <button
                      onClick={async () => {
                        const ok = await confirm({
                          title: '删除评估器',
                          description: `删除评估器"${e.name}"？`,
                          confirmText: '删除',
                          danger: true,
                        })
                        if (!ok) return
                        setDeletingId(e.id)
                        try {
                          await evaluationApi.deleteEvaluator(e.id)
                          qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
                          toast.success('评估器已删除')
                        } catch (err) {
                          const msg = (err as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail
                            || (err as Error)?.message || '未知错误'
                          toast.error(msg, '删除失败')
                        } finally {
                          setDeletingId(null)
                        }
                      }}
                      disabled={deletingId === e.id}
                      className="text-action-danger"
                    >
                      {deletingId === e.id ? '删除中…' : '删除'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showEditor && (
        <EvaluatorEditor
          open={showEditor}
          editing={editing}
          onClose={() => { setShowEditor(false); setEditing(null) }}
        />
      )}
    </div>
  )
}


function EvaluatorEditor({
  open, editing, onClose,
}: {
  open: boolean
  editing: EvaluatorInstance | null
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [name, setName] = useState(editing?.name || '')
  const [tag, setTag] = useState(editing?.tag || '')
  const [isActive, setIsActive] = useState(editing ? editing.is_active : true)
  const reactId = useId()
  const nameId = `${reactId}-name`
  const tagId = `${reactId}-tag`
  const activeId = `${reactId}-active`

  const saveMutation = useMutation({
    mutationFn: async () => {
      const effectiveTag = tag.trim() || name.trim()
      if (editing) {
        return evaluationApi.updateEvaluator(editing.id, {
          name, tag: effectiveTag, is_active: isActive,
        }).then(r => r.data)
      }
      const body: CreateEvaluatorRequest = {
        name, tag: effectiveTag, is_active: isActive,
      }
      return evaluationApi.createEvaluator(body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
      onClose()
    },
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={editing ? '编辑评估器' : '新建评估器'}
      width={520}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            size="md"
            disabled={!name.trim()}
            loading={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            保存
          </Button>
        </>
      }
    >
      <p className="text-[12px] text-text-secondary mb-4 leading-relaxed">
        每个评估器对应一个 Langfuse trace tag。运行评估时勾选它，平台会把
        <code className="mx-1 px-1.5 py-0.5 bg-fill/10 rounded font-mono text-[11px]">tag</code>
        加到每条样例的 trace 上 — 在 Langfuse 里配置成 target=tag 的评估器会自动处理这些 trace 并打分，结果会被本平台拉回展示。
      </p>

      <div className="space-y-4">
        <div>
          <label htmlFor={nameId} className="field-label">名称（唯一，UI 展示用）</label>
          <input
            id={nameId}
            type="text" value={name} onChange={e => setName(e.target.value)}
            placeholder="例如：正确性 / Goal Accuracy"
            className="input"
          />
        </div>

        <div>
          <label htmlFor={tagId} className="field-label">Tag（写到 Langfuse trace，留空则用名称）</label>
          <input
            id={tagId}
            type="text" value={tag} onChange={e => setTag(e.target.value)}
            placeholder="例如：agent-eval-correctness"
            className="input font-mono"
          />
          <div className="mt-1.5 text-[10px] text-text-tertiary">
            每条样例的 Langfuse trace 都会带上这个 tag。Langfuse 端配的同名 evaluator 会被触发；你也可以让多个评估器用相同 tag。
          </div>
        </div>

        <label htmlFor={activeId} className="inline-flex items-center gap-2 text-[12px] cursor-pointer">
          <input
            id={activeId}
            type="checkbox" checked={isActive}
            onChange={e => setIsActive(e.target.checked)}
            className="accent-accent"
          />
          启用（运行时可选）
        </label>

        {saveMutation.isError && (
          <p className="text-[12px] text-negative">
            {(saveMutation.error as Error)?.message || '保存失败'}
          </p>
        )}
      </div>
    </Dialog>
  )
}
