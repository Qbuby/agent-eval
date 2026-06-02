import { useId, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useConfirm, useToast, ExportMenu } from '@/components/ui'
import { datasetsApi, candidatesApi, projectsApi } from '@/services'
import type { CandidateCase } from '@/services/benchmark'
import { formatApiError, toToastMessage } from '@/lib/errors'

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  pending: { label: '暂存', cls: 'badge badge-warning' },
  ready: { label: '待导入', cls: 'badge badge-positive' },
  imported: { label: '已导入', cls: 'badge badge-info' },
  rejected: { label: '已拒绝', cls: 'badge badge-negative' },
}

export default function DatasetDetailPage() {
  const { name } = useParams<{ name: string }>()
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const reactId = useId()
  const editAnswerId = `${reactId}-edit-answer`
  const editKeyPointsId = `${reactId}-edit-key-points`
  const editNegativePointsId = `${reactId}-edit-negative-points`
  const promoteProjectFieldId = `${reactId}-promote-project`
  const addQuestionId = `${reactId}-add-question`
  const addAnswerId = `${reactId}-add-answer`

  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [search, setSearch] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showPromote, setShowPromote] = useState(false)
  const [promoteProjectId, setPromoteProjectId] = useState('')
  const [editingCase, setEditingCase] = useState<CandidateCase | null>(null)
  const [editAnswer, setEditAnswer] = useState('')
  const [editKeyPoints, setEditKeyPoints] = useState('')
  const [editNegativePoints, setEditNegativePoints] = useState('')
  const [showAddModal, setShowAddModal] = useState(false)
  const [addQuestion, setAddQuestion] = useState('')
  const [addAnswer, setAddAnswer] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const pageSize = 20

  const { data: dataset, isLoading: datasetLoading } = useQuery({
    queryKey: ['dataset', name],
    queryFn: () => datasetsApi.get(name!).then(r => r.data),
    enabled: !!name,
  })

  const { data: casesData, isLoading } = useQuery({
    queryKey: ['dataset-candidates', name, page, statusFilter, search],
    queryFn: () => candidatesApi.list({
      page,
      page_size: pageSize,
      dataset_name: name,
      status: statusFilter || undefined,
      search: search || undefined,
    }).then(r => r.data),
    enabled: !!name,
  })

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
    enabled: showPromote,
  })

  const syncMutation = useMutation({
    mutationFn: () => candidatesApi.importFromLangSmith({ dataset_name: name! }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
      toast.success(`已同步 ${res.data.imported} 条样例`)
    },
  })

  const addMutation = useMutation({
    mutationFn: () => candidatesApi.create({
      question: addQuestion,
      answer: addAnswer || undefined,
      dataset_name: name!,
      source: 'manual',
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
      setShowAddModal(false)
      setAddQuestion('')
      setAddAnswer('')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) => candidatesApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
      setEditingCase(null)
    },
  })

  const reviewMutation = useMutation({
    mutationFn: ({ ids, action }: { ids: string[]; action: 'approve' | 'reject' }) => candidatesApi.batchReview(ids, action),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
      setSelectedIds(new Set())
    },
  })

  const promoteMutation = useMutation({
    mutationFn: () => candidatesApi.promote(Array.from(selectedIds), promoteProjectId),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
      setSelectedIds(new Set())
      setShowPromote(false)
      toast.success(`成功导入 ${res.data.promoted} 条到基准测试集`)
    },
  })

  const cases = casesData?.items ?? []
  const total = casesData?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  function openEdit(c: CandidateCase) {
    setEditingCase(c)
    setEditAnswer(c.answer || '')
    setEditKeyPoints((c.key_points || []).join(', '))
    setEditNegativePoints((c.negative_points || []).join(', '))
  }

  function saveEdit() {
    if (!editingCase) return
    updateMutation.mutate({
      id: editingCase.id,
      data: {
        answer: editAnswer || null,
        key_points: editKeyPoints ? editKeyPoints.split(',').map(s => s.trim()).filter(Boolean) : null,
        negative_points: editNegativePoints ? editNegativePoints.split(',').map(s => s.trim()).filter(Boolean) : null,
      },
    })
  }

  function toggleSelect(id: string) {
    setSelectedIds(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })
  }

  if (datasetLoading) {
    return (
      <div>
        <div className="skeleton h-5 w-48 rounded mb-4" />
        <div className="skeleton h-3 w-32 rounded mb-6" />
      </div>
    )
  }
  if (!dataset) return <div className="empty-state">数据集未找到</div>

  return (
    <div>
      <Link to="/datasets" className="back-link mb-2">
        ← 返回
      </Link>
      <header className="mb-6">
        <div className="page-eyebrow">数据集</div>
        <h1 className="page-title">{dataset.name}</h1>
        <p className="page-subtitle">{dataset.description || '无描述'}</p>
      </header>

      <div className="toolbar">
        <input
          type="text"
          placeholder="搜索问题…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          className="input-sm w-[240px]"
        />
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
          className="select-sm"
        >
          <option value="">全部状态</option>
          <option value="pending">暂存区</option>
          <option value="ready">待导入</option>
          <option value="imported">已导入</option>
          <option value="rejected">已拒绝</option>
        </select>
        <div className="flex-1" />
        <ExportMenu
          disabled={!name}
          onExport={async (format) => {
            if (!name) return
            try {
              await candidatesApi.exportCases(
                { dataset_name: name, status: statusFilter || undefined, search: search || undefined },
                format,
              )
            } catch (e) {
              toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '导出失败' })))
            }
          }}
        />
        <Button variant="secondary" size="sm" onClick={() => setShowAddModal(true)}>
          手动添加
        </Button>
        <Button
          variant="secondary"
          size="sm"
          loading={syncMutation.isPending}
          onClick={async () => {
            const ok = await confirm({
              title: '同步样例',
              description: `从 LangSmith 同步 "${name}" 的样例到本地？`,
              confirmText: '同步',
            })
            if (ok) syncMutation.mutate()
          }}
        >
          从 LangSmith 同步
        </Button>
        {selectedIds.size > 0 && (statusFilter === '' || statusFilter === 'ready') && (
          <Button variant="primary" size="sm" onClick={() => setShowPromote(true)}>
            导入基准 ({selectedIds.size})
          </Button>
        )}
        {selectedIds.size > 0 && (statusFilter === '' || statusFilter === 'pending') && (
          <>
            <Button variant="primary" size="sm" onClick={() => reviewMutation.mutate({ ids: Array.from(selectedIds), action: 'approve' })}>
              批准 ({selectedIds.size})
            </Button>
            <Button variant="danger" size="sm" onClick={() => reviewMutation.mutate({ ids: Array.from(selectedIds), action: 'reject' })}>
              拒绝
            </Button>
          </>
        )}
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th className="w-10 text-center">
                <input
                  type="checkbox"
                  checked={cases.length > 0 && selectedIds.size === cases.length}
                  onChange={() => {
                    if (selectedIds.size === cases.length) setSelectedIds(new Set())
                    else setSelectedIds(new Set(cases.map(c => c.id)))
                  }}
                  className="accent-accent"
                />
              </th>
              <th>问题</th>
              <th className="w-20">有答案</th>
              <th className="w-24">状态</th>
              <th className="w-24">来源</th>
              <th className="w-28 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {cases.map(c => (
              <tr key={c.id} className="group">
                <td className="text-center">
                  <input type="checkbox" checked={selectedIds.has(c.id)} onChange={() => toggleSelect(c.id)} className="accent-accent" />
                </td>
                <td className="max-w-[460px]">
                  <div className="truncate">{c.question}</div>
                  {c.answer && <div className="text-[11px] text-text-tertiary mt-0.5 truncate">答：{c.answer.slice(0, 80)}</div>}
                </td>
                <td>
                  <span className={c.answer ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {c.answer ? '有' : '无'}
                  </span>
                </td>
                <td>
                  <span className={STATUS_BADGE[c.status]?.cls || 'badge badge-neutral'}>
                    {STATUS_BADGE[c.status]?.label || c.status}
                  </span>
                </td>
                <td className="text-text-tertiary text-[11px]">{c.source}</td>
                <td className="text-right">
                  <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => openEdit(c)} className="text-action">
                      编辑
                    </button>
                    <button
                      onClick={async () => {
                        const ok = await confirm({
                          title: '删除样例',
                          description: '确定删除该样例？此操作不可撤销。',
                          confirmText: '删除',
                          danger: true,
                        })
                        if (!ok) return
                        setDeletingId(c.id)
                        try {
                          await candidatesApi.delete(c.id)
                          queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
                          queryClient.invalidateQueries({ queryKey: ['candidates'] })
                          toast.success('样例已删除')
                        } catch (err) {
                          const norm = formatApiError(err, { fallbackTitle: '删除失败' })
                          toast.error(toToastMessage(norm), '删除失败')
                        } finally {
                          setDeletingId(null)
                        }
                      }}
                      disabled={deletingId === c.id}
                      className="text-action-danger"
                    >
                      {deletingId === c.id ? '删除中…' : '删除'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {cases.length === 0 && !isLoading && (
          <div className="empty-state">暂无样例，点击"从 LangSmith 同步"导入</div>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-[11px] text-text-tertiary">共 {total} 条 · 第 {page} / {totalPages} 页</span>
          <div className="flex gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="pager-btn">上一页</button>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="pager-btn">下一页</button>
          </div>
        </div>
      )}

      <Dialog
        open={!!editingCase}
        onClose={() => setEditingCase(null)}
        title="编辑样例"
        width={560}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setEditingCase(null)}>取消</Button>
            <Button variant="primary" size="md" loading={updateMutation.isPending} onClick={saveEdit}>保存</Button>
          </>
        }
      >
        {editingCase && (
          <div className="space-y-4">
            <div>
              <label className="field-label">问题</label>
              <div className="py-2 px-3 text-[12px] border border-border rounded-md bg-fill/5 text-text-secondary">{editingCase.question}</div>
            </div>
            <div>
              <label htmlFor={editAnswerId} className="field-label">参考答案</label>
              <textarea id={editAnswerId} value={editAnswer} onChange={e => setEditAnswer(e.target.value)} rows={4} className="input resize-y" />
            </div>
            <div>
              <label htmlFor={editKeyPointsId} className="field-label">关键点（逗号分隔）</label>
              <input id={editKeyPointsId} value={editKeyPoints} onChange={e => setEditKeyPoints(e.target.value)} placeholder="要点1, 要点2" className="input" />
            </div>
            <div>
              <label htmlFor={editNegativePointsId} className="field-label">反向关键点（逗号分隔）</label>
              <input id={editNegativePointsId} value={editNegativePoints} onChange={e => setEditNegativePoints(e.target.value)} placeholder="不应出现的内容" className="input" />
            </div>
          </div>
        )}
      </Dialog>

      <Dialog
        open={showPromote}
        onClose={() => setShowPromote(false)}
        title="导入基准测试集"
        width={420}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowPromote(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              disabled={!promoteProjectId}
              loading={promoteMutation.isPending}
              onClick={() => promoteMutation.mutate()}
            >
              确认导入
            </Button>
          </>
        }
      >
        <p className="text-[12px] text-text-secondary mb-4">将 {selectedIds.size} 条样例导入到基准测试集。</p>
        <div>
          <label htmlFor={promoteProjectFieldId} className="field-label">目标项目</label>
          <select id={promoteProjectFieldId} value={promoteProjectId} onChange={e => setPromoteProjectId(e.target.value)} className="input">
            <option value="">选择项目…</option>
            {projects?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </Dialog>

      <Dialog
        open={showAddModal}
        onClose={() => setShowAddModal(false)}
        title="手动添加样例"
        width={500}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowAddModal(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              disabled={!addQuestion.trim()}
              loading={addMutation.isPending}
              onClick={() => addMutation.mutate()}
            >
              添加
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label htmlFor={addQuestionId} className="field-label">问题</label>
            <textarea
              id={addQuestionId}
              value={addQuestion}
              onChange={e => setAddQuestion(e.target.value)}
              rows={3}
              placeholder="输入测试问题…"
              className="input resize-y"
            />
          </div>
          <div>
            <label htmlFor={addAnswerId} className="field-label">参考答案（可选，留空则进入暂存区）</label>
            <textarea
              id={addAnswerId}
              value={addAnswer}
              onChange={e => setAddAnswer(e.target.value)}
              rows={3}
              placeholder="输入参考答案…"
              className="input resize-y"
            />
          </div>
        </div>
      </Dialog>
    </div>
  )
}
