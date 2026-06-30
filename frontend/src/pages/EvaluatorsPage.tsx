import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, useConfirm, useToast } from '@/components/ui'
import EvaluatorEditorDrawer from '@/components/EvaluatorEditorDrawer'
import { evaluationApi } from '@/services'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { EvaluatorInstance } from '@/types'

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
        <p className="page-subtitle">
          标签模板用于触发 Langfuse 端打分；可配置 LLM Judge 直接由本平台调 provider 打分
        </p>
      </header>

      <div className="section-row">
        <div className="page-eyebrow">评估器列表</div>
        <div className="flex gap-2">
          <Link to="/evaluators/compare">
            <Button variant="ghost" size="sm">对比评估器</Button>
          </Link>
          <Button variant="primary" size="sm" onClick={() => { setEditing(null); setShowEditor(true) }}>
            新建评估器
          </Button>
        </div>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>Tag</th>
              <th className="w-24">状态</th>
              <th className="w-44">创建时间</th>
              <th className="w-28 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={6} className="empty-state">加载中…</td></tr>
            )}
            {listQuery.data?.length === 0 && !listQuery.isLoading && (
              <tr><td colSpan={6} className="empty-state">
                还没有评估器。新建一个，运行评估时勾选它即可。
              </td></tr>
            )}
            {listQuery.data?.map(e => (
              <tr key={e.id} className="group">
                <td className="font-medium">{e.name}</td>
                <td>
                  <span className="badge badge-neutral text-[10px]">
                    {e.evaluator_type === 'configurable_judge' ? 'LLM Judge' : '标签模板'}
                  </span>
                </td>
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
                          const norm = formatApiError(err, { fallbackTitle: '删除失败' })
                          toast.error(toToastMessage(norm), '删除失败')
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
        <EvaluatorEditorDrawer
          open={showEditor}
          editing={editing}
          onClose={() => { setShowEditor(false); setEditing(null) }}
        />
      )}
    </div>
  )
}
