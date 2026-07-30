import { useId, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useToast, useConfirm, ExportMenu } from '@/components/ui'
import { candidatesApi, projectsApi, type CandidateCase } from '@/services/benchmark'
import { agentRepliesApi } from '@/services'
import AgentReplyGenerateDialog from '@/components/AgentReplyGenerateDialog'
import { AgentReplyVersionsDrawer } from '@/components/AgentReplyVersionsDrawer'
import { formatApiError, toToastMessage } from '@/lib/errors'

const STATUS_BADGE: Record<string, string> = {
  pending: 'badge badge-warning',
  ready: 'badge badge-positive',
  imported: 'badge badge-info',
  rejected: 'badge badge-negative',
}
const STATUS_LABEL: Record<string, string> = {
  pending: '暂存',
  ready: '待导入',
  imported: '已导入',
  rejected: '已拒绝',
}

export default function CandidatesPage() {
  const queryClient = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const reactId = useId()
  const editAnswerId = `${reactId}-edit-answer`
  const editKeyPointsId = `${reactId}-edit-key-points`
  const editNegativePointsId = `${reactId}-edit-negative-points`
  const promoteProjectFieldId = `${reactId}-promote-project`
  const lsDatasetNameId = `${reactId}-ls-dataset-name`
  const lsProjectFieldId = `${reactId}-ls-project`
  const lsLimitId = `${reactId}-ls-limit`
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('pending')
  const [search, setSearch] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editAnswer, setEditAnswer] = useState('')
  const [editKeyPoints, setEditKeyPoints] = useState('')
  const [editNegativePoints, setEditNegativePoints] = useState('')
  const [showPromote, setShowPromote] = useState(false)
  const [promoteProjectId, setPromoteProjectId] = useState('')
  const [promoteCategoryId, _setPromoteCategoryId] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  // agent 生成答案：勾选样例后开弹窗；null = 用当前勾选集，非空 = 只重跑这一条。
  const [genOpen, setGenOpen] = useState(false)
  const [genSingleId, setGenSingleId] = useState<string | null>(null)
  const [versionsCaseRef, setVersionsCaseRef] = useState<string | null>(null)

  const pageSize = 20

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
  })

  const { data: casesData, isLoading } = useQuery({
    queryKey: ['candidates', page, statusFilter, search],
    queryFn: () => candidatesApi.list({
      page, page_size: pageSize,
      status: statusFilter || undefined,
      search: search || undefined,
    }).then(r => r.data),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) => candidatesApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['candidates'] })
      setEditingId(null)
    },
  })

  const reviewMutation = useMutation({
    mutationFn: ({ ids, action }: { ids: string[]; action: 'approve' | 'reject' }) => candidatesApi.batchReview(ids, action),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['candidates'] })
      setSelectedIds(new Set())
    },
  })

  const promoteMutation = useMutation({
    mutationFn: () => candidatesApi.promote(Array.from(selectedIds), promoteProjectId, promoteCategoryId || undefined),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['candidates'] })
      queryClient.invalidateQueries({ queryKey: ['benchmark-cases'] })
      setSelectedIds(new Set())
      setShowPromote(false)
      toast.success(`成功导入 ${res.data.promoted} 条到基准测试集`)
    },
  })

  const [showLangSmithImport, setShowLangSmithImport] = useState(false)
  const [lsDatasetName, setLsDatasetName] = useState('')
  const [lsProjectId, setLsProjectId] = useState('')
  const [lsLimit, setLsLimit] = useState('')

  const langsmithImportMutation = useMutation({
    mutationFn: () => candidatesApi.importFromLangSmith({
      dataset_name: lsDatasetName,
      project_id: lsProjectId || undefined,
      limit: lsLimit ? parseInt(lsLimit) : undefined,
    }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['candidates'] })
      setShowLangSmithImport(false)
      setLsDatasetName('')
      toast.success(`成功从 LangSmith 导入 ${res.data.imported} 条样例`)
    },
  })

  const cases = casesData?.items ?? []
  const total = casesData?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  // 当前页样例的「agent 生成答案」状态，一次批量查，给每行打标记。
  const caseIdsOnPage = useMemo(() => cases.map(c => c.id), [cases])
  const { data: replyStates } = useQuery({
    queryKey: ['agent-reply-states', 'candidate', caseIdsOnPage],
    queryFn: () => agentRepliesApi.listStates('candidate', caseIdsOnPage).then(r => r.data),
    enabled: caseIdsOnPage.length > 0,
  })
  const replyStateMap = new Map((replyStates ?? []).map(s => [s.case_ref, s]))

  function refreshReplyStates() {
    queryClient.invalidateQueries({ queryKey: ['agent-reply-states'] })
  }

  function startEdit(c: CandidateCase) {
    setEditingId(c.id)
    setEditAnswer(c.answer || '')
    setEditKeyPoints((c.key_points || []).join(', '))
    setEditNegativePoints((c.negative_points || []).join(', '))
  }

  function saveEdit() {
    if (!editingId) return
    updateMutation.mutate({
      id: editingId,
      data: {
        answer: editAnswer || null,
        key_points: editKeyPoints ? editKeyPoints.split(',').map(s => s.trim()).filter(Boolean) : null,
        negative_points: editNegativePoints ? editNegativePoints.split(',').map(s => s.trim()).filter(Boolean) : null,
      },
    })
  }

  function toggleSelect(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">staging</div>
        <h1 className="page-title">备选数据集</h1>
        <p className="page-subtitle">管理暂存区样例，补全答案后可导入基准测试集</p>
      </header>

      <div className="toolbar">
        <input
          type="text"
          placeholder="搜索问题"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          className="input-sm flex-1 max-w-[260px]"
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
          onExport={async (format) => {
            try {
              await candidatesApi.exportCases(
                { status: statusFilter || undefined, search: search || undefined },
                format,
              )
            } catch (e) {
              toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '导出失败' })))
            }
          }}
        />
        <Button onClick={() => setShowLangSmithImport(true)} variant="secondary" size="md">
          从 LangSmith 导入
        </Button>
        {/* 勾选样例后先让 agent 跑出答案存成版本，评估时可直接消费、不再实时连 agent。 */}
        <Button
          onClick={() => { setGenSingleId(null); setGenOpen(true) }}
          variant="secondary"
          size="md"
          disabled={selectedIds.size === 0}
          title={selectedIds.size === 0 ? '请先勾选样例' : undefined}
        >
          agent生成答案{selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}
        </Button>
        {selectedIds.size > 0 && statusFilter === 'ready' && (
          <Button onClick={() => setShowPromote(true)} variant="primary" size="md">
            导入基准测试集 ({selectedIds.size})
          </Button>
        )}
        {selectedIds.size > 0 && statusFilter === 'pending' && (
          <>
            <Button
              onClick={() => reviewMutation.mutate({ ids: Array.from(selectedIds), action: 'approve' })}
              variant="primary"
              size="md"
            >
              批准 ({selectedIds.size})
            </Button>
            <Button
              onClick={() => reviewMutation.mutate({ ids: Array.from(selectedIds), action: 'reject' })}
              variant="danger"
              size="md"
            >
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
                  className="accent-accent w-3.5 h-3.5"
                />
              </th>
              <th>问题</th>
              <th className="w-24">来源</th>
              <th className="w-24">状态</th>
              <th className="w-28">agent回复</th>
              <th className="w-40 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {cases.map(c => (
              <tr key={c.id} className="group">
                <td className="text-center">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(c.id)}
                    onChange={() => toggleSelect(c.id)}
                    className="accent-accent w-3.5 h-3.5"
                  />
                </td>
                <td className="max-w-[420px]">
                  <div className="truncate text-text-primary">{c.question}</div>
                  {c.answer && (
                    <div className="text-[11px] text-text-tertiary mt-0.5 truncate">
                      答：{c.answer.slice(0, 80)}
                    </div>
                  )}
                </td>
                <td className="text-[11px] text-text-secondary">{c.source}</td>
                <td>
                  <span className={STATUS_BADGE[c.status] || 'badge badge-neutral'}>
                    {STATUS_LABEL[c.status] || c.status}
                  </span>
                </td>
                <td>
                  {/* 点开版本抽屉：可切换当前版本 / 手工修订 / 删除 / 重新生成 */}
                  {(() => {
                    const st = replyStateMap.get(c.id)
                    if (!st?.has_reply) {
                      return <span className="text-[11px] text-text-tertiary">未生成</span>
                    }
                    return (
                      <button
                        onClick={() => setVersionsCaseRef(c.id)}
                        className="text-action text-[11px]"
                        title="查看 / 回溯 agent 生成的回复版本"
                      >
                        v{st.current_version_number ?? '—'}
                        {st.version_count > 1 ? ` · 共${st.version_count}版` : ''}
                      </button>
                    )
                  })()}
                </td>
                <td className="text-right">
                  <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => startEdit(c)}
                      className="text-action"
                    >
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
                          queryClient.invalidateQueries({ queryKey: ['candidates'] })
                          queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
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
          <div className="empty-state">暂无数据</div>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-[11px] text-text-tertiary">共 {total} 条，第 {page}/{totalPages} 页</span>
          <div className="flex gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="pager-btn">
              上一页
            </button>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="pager-btn">
              下一页
            </button>
          </div>
        </div>
      )}

      <Dialog
        open={!!editingId}
        onClose={() => setEditingId(null)}
        title="编辑样例"
        width={520}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setEditingId(null)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              onClick={saveEdit}
              loading={updateMutation.isPending}
            >
              保存
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="field-label">问题</label>
            <div className="px-3 py-2 text-[13px] border border-border rounded-md bg-fill/5 text-text-secondary">
              {cases.find(c => c.id === editingId)?.question}
            </div>
          </div>
          <div>
            <label htmlFor={editAnswerId} className="field-label">参考答案</label>
            <textarea
              id={editAnswerId}
              value={editAnswer}
              onChange={e => setEditAnswer(e.target.value)}
              rows={4}
              className="input resize-y"
            />
          </div>
          <div>
            <label htmlFor={editKeyPointsId} className="field-label">关键点（逗号分隔）</label>
            <input
              id={editKeyPointsId}
              value={editKeyPoints}
              onChange={e => setEditKeyPoints(e.target.value)}
              placeholder="要点1, 要点2, …"
              className="input"
            />
          </div>
          <div>
            <label htmlFor={editNegativePointsId} className="field-label">反向关键点（逗号分隔）</label>
            <input
              id={editNegativePointsId}
              value={editNegativePoints}
              onChange={e => setEditNegativePoints(e.target.value)}
              placeholder="不应出现的内容…"
              className="input"
            />
          </div>
        </div>
      </Dialog>

      <Dialog
        open={showPromote}
        onClose={() => setShowPromote(false)}
        title="导入基准测试集"
        description={`将 ${selectedIds.size} 条样例导入到基准测试集。`}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowPromote(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              onClick={() => promoteMutation.mutate()}
              disabled={!promoteProjectId}
              loading={promoteMutation.isPending}
            >
              确认导入
            </Button>
          </>
        }
      >
        <div>
          <label htmlFor={promoteProjectFieldId} className="field-label">目标项目</label>
          <select
            id={promoteProjectFieldId}
            value={promoteProjectId}
            onChange={e => setPromoteProjectId(e.target.value)}
            className="input"
          >
            <option value="">选择项目…</option>
            {projects?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </Dialog>

      <Dialog
        open={showLangSmithImport}
        onClose={() => setShowLangSmithImport(false)}
        title="从 LangSmith 导入"
        description='从 LangSmith 数据集导入样例到备选数据集。有参考答案的状态为"待导入"，无答案的进入暂存区。'
        width={460}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowLangSmithImport(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              onClick={() => langsmithImportMutation.mutate()}
              disabled={!lsDatasetName.trim()}
              loading={langsmithImportMutation.isPending}
            >
              开始导入
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label htmlFor={lsDatasetNameId} className="field-label">LangSmith 数据集名称</label>
            <input
              id={lsDatasetNameId}
              value={lsDatasetName}
              onChange={e => setLsDatasetName(e.target.value)}
              placeholder="例如：noble-agent-dataset-test"
              className="input"
            />
          </div>
          <div>
            <label htmlFor={lsProjectFieldId} className="field-label">关联项目（可选）</label>
            <select
              id={lsProjectFieldId}
              value={lsProjectId}
              onChange={e => setLsProjectId(e.target.value)}
              className="input"
            >
              <option value="">不关联项目</option>
              {projects?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={lsLimitId} className="field-label">最大数量（可选）</label>
            <input
              id={lsLimitId}
              value={lsLimit}
              onChange={e => setLsLimit(e.target.value)}
              type="number"
              placeholder="不限"
              className="input"
            />
          </div>
        </div>
      </Dialog>

      {/* agent 生成答案：genSingleId 非空 = 只重跑这一条，否则用当前勾选集 */}
      <AgentReplyGenerateDialog
        open={genOpen}
        onClose={() => { setGenOpen(false); setGenSingleId(null) }}
        datasetType="candidate"
        caseIds={genSingleId ? [genSingleId] : Array.from(selectedIds)}
        onFinished={refreshReplyStates}
      />

      <AgentReplyVersionsDrawer
        open={!!versionsCaseRef}
        onClose={() => setVersionsCaseRef(null)}
        datasetType="candidate"
        caseRef={versionsCaseRef}
        caseTitle={cases.find(c => c.id === versionsCaseRef)?.question?.slice(0, 60) ?? null}
        onRetryCase={ref => { setVersionsCaseRef(null); setGenSingleId(ref); setGenOpen(true) }}
        onChanged={refreshReplyStates}
      />
    </div>
  )
}
