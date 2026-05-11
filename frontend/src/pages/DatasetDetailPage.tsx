import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { datasetsApi, candidatesApi, projectsApi } from '@/services'
import type { CandidateCase } from '@/services/benchmark'

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: '暂存', color: 'bg-[#fff3e0] text-[#e65100]' },
  ready: { label: '待导入', color: 'bg-[#e8f5e9] text-[#2e7d32]' },
  imported: { label: '已导入', color: 'bg-[#e3f2fd] text-[#1565c0]' },
  rejected: { label: '已拒绝', color: 'bg-[#fde8e8] text-[#b33]' },
}

export default function DatasetDetailPage() {
  const { name } = useParams<{ name: string }>()
  const queryClient = useQueryClient()

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
      alert(`已同步 ${res.data.imported} 条样例`)
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
      alert(`成功导入 ${res.data.promoted} 条到基准测试集`)
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
  if (!dataset) return <div className="text-text-tertiary text-[12px]">数据集未找到</div>

  return (
    <div>
      <Link to="/datasets" className="inline-flex items-center gap-1 text-[11px] text-text-tertiary hover:text-text-primary transition-all mb-2 no-underline">
        ← 返回
      </Link>
      <header className="mb-6">
        <h1 className="text-xl font-medium tracking-tight">{dataset.name}</h1>
        <p className="text-[12px] text-text-tertiary mt-0.5">{dataset.description || '无描述'}</p>
      </header>

      {/* 工具栏 */}
      <div className="flex items-center gap-3 mb-5 flex-wrap">
        <input
          type="text"
          placeholder="搜索问题..."
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          className="flex-1 max-w-[240px] py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
        />
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
          className="py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
        >
          <option value="">全部状态</option>
          <option value="pending">暂存区</option>
          <option value="ready">待导入</option>
          <option value="imported">已导入</option>
          <option value="rejected">已拒绝</option>
        </select>
        <div className="flex-1" />
        <button
          onClick={() => setShowAddModal(true)}
          className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 active:scale-[0.97] transition-all"
        >
          + 手动添加
        </button>
        <button
          onClick={() => { if (confirm(`从 LangSmith 同步 "${name}" 的样例到本地？`)) syncMutation.mutate() }}
          disabled={syncMutation.isPending}
          className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-surface text-text-primary border border-border hover:border-accent active:scale-[0.97] transition-all"
        >
          {syncMutation.isPending ? '同步中...' : '从 LangSmith 同步'}
        </button>
        {selectedIds.size > 0 && (statusFilter === '' || statusFilter === 'ready') && (
          <button
            onClick={() => setShowPromote(true)}
            className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-[#1a6] text-white border border-[#1a6] hover:opacity-90 active:scale-[0.97] transition-all"
          >
            导入基准测试集 ({selectedIds.size})
          </button>
        )}
        {selectedIds.size > 0 && (statusFilter === '' || statusFilter === 'pending') && (
          <>
            <button
              onClick={() => reviewMutation.mutate({ ids: Array.from(selectedIds), action: 'approve' })}
              className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 transition-all"
            >
              批准 ({selectedIds.size})
            </button>
            <button
              onClick={() => reviewMutation.mutate({ ids: Array.from(selectedIds), action: 'reject' })}
              className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-surface text-negative border border-negative/30 hover:bg-negative/5 transition-all"
            >
              拒绝
            </button>
          </>
        )}
      </div>

      {/* 样例表格 */}
      <div className="border border-border rounded-[6px] overflow-hidden bg-surface">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="w-8 text-center py-2 px-2 border-b border-border bg-accent-subtle">
                <input type="checkbox" checked={cases.length > 0 && selectedIds.size === cases.length} onChange={() => {
                  if (selectedIds.size === cases.length) setSelectedIds(new Set())
                  else setSelectedIds(new Set(cases.map(c => c.id)))
                }} className="w-3 h-3 accent-accent" />
              </th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">问题</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-20">有答案</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-20">状态</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-20">来源</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-right py-2 px-3 border-b border-border font-normal bg-accent-subtle w-16">操作</th>
            </tr>
          </thead>
          <tbody>
            {cases.map(c => (
              <tr key={c.id} className="hover:bg-accent-subtle group">
                <td className="text-center py-2.5 px-2 border-b border-border">
                  <input type="checkbox" checked={selectedIds.has(c.id)} onChange={() => toggleSelect(c.id)} className="w-3 h-3 accent-accent" />
                </td>
                <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-primary max-w-[400px]">
                  <div className="truncate">{c.question}</div>
                  {c.answer && <div className="text-[10px] text-text-tertiary mt-0.5 truncate">答: {c.answer.slice(0, 80)}</div>}
                </td>
                <td className="py-2.5 px-3 border-b border-border text-[11px]">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-medium ${c.answer ? 'bg-[#e6f7ed] text-[#1a6]' : 'bg-[#f5f5f5] text-[#999]'}`}>
                    {c.answer ? 'Y' : 'N'}
                  </span>
                </td>
                <td className="py-2.5 px-3 border-b border-border">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-medium ${STATUS_MAP[c.status]?.color || 'bg-[#f5f5f5] text-[#999]'}`}>
                    {STATUS_MAP[c.status]?.label || c.status}
                  </span>
                </td>
                <td className="py-2.5 px-3 border-b border-border text-[11px] text-text-tertiary">{c.source}</td>
                <td className="py-2.5 px-3 border-b border-border text-right">
                  <button onClick={() => openEdit(c)} className="text-[10px] text-text-secondary hover:text-accent opacity-0 group-hover:opacity-100 transition-all">编辑</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {cases.length === 0 && !isLoading && (
          <div className="text-center py-10 text-text-tertiary text-[12px]">暂无样例，点击"从 LangSmith 同步"导入</div>
        )}
      </div>

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-[11px] text-text-tertiary">共 {total} 条，第 {page}/{totalPages} 页</span>
          <div className="flex gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="py-1 px-2.5 text-[11px] border border-border rounded-[4px] hover:border-accent disabled:opacity-30 transition-all">上一页</button>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="py-1 px-2.5 text-[11px] border border-border rounded-[4px] hover:border-accent disabled:opacity-30 transition-all">下一页</button>
          </div>
        </div>
      )}

      {/* 编辑弹窗 */}
      {editingCase && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setEditingCase(null)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[520px] max-h-[85vh] overflow-y-auto shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-[14px] font-medium">编辑样例</h2>
              <button onClick={() => setEditingCase(null)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">问题</label>
                <div className="py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-accent-subtle text-text-secondary">{editingCase.question}</div>
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">参考答案</label>
                <textarea value={editAnswer} onChange={e => setEditAnswer(e.target.value)} rows={4} className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all" />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">关键点（逗号分隔）</label>
                <input value={editKeyPoints} onChange={e => setEditKeyPoints(e.target.value)} placeholder="要点1, 要点2" className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all" />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">反向关键点（逗号分隔）</label>
                <input value={editNegativePoints} onChange={e => setEditNegativePoints(e.target.value)} placeholder="不应出现的内容" className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all" />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setEditingCase(null)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button onClick={saveEdit} disabled={updateMutation.isPending} className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all">保存</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 导入基准测试集弹窗 */}
      {showPromote && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowPromote(false)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[400px] shadow-lg" onClick={e => e.stopPropagation()}>
            <h2 className="text-[14px] font-medium mb-4">导入基准测试集</h2>
            <p className="text-[11px] text-text-secondary mb-4">将 {selectedIds.size} 条样例导入到基准测试集。</p>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">目标项目</label>
                <select value={promoteProjectId} onChange={e => setPromoteProjectId(e.target.value)} className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent">
                  <option value="">选择项目...</option>
                  {projects?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setShowPromote(false)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button onClick={() => promoteMutation.mutate()} disabled={!promoteProjectId || promoteMutation.isPending}
                  className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all">确认导入</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 手动添加弹窗 */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowAddModal(false)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[480px] shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-[14px] font-medium">手动添加样例</h2>
              <button onClick={() => setShowAddModal(false)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">问题</label>
                <textarea
                  value={addQuestion}
                  onChange={e => setAddQuestion(e.target.value)}
                  rows={3}
                  placeholder="输入测试问题..."
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
                />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">参考答案（可选，留空则进入暂存区）</label>
                <textarea
                  value={addAnswer}
                  onChange={e => setAddAnswer(e.target.value)}
                  rows={3}
                  placeholder="输入参考答案..."
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
                />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setShowAddModal(false)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button
                  onClick={() => addMutation.mutate()}
                  disabled={!addQuestion.trim() || addMutation.isPending}
                  className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all"
                >
                  添加
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
