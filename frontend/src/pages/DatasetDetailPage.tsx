import { useId, useMemo, useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useConfirm, useToast, ExportMenu } from '@/components/ui'
import { datasetsApi, candidatesApi, projectsApi, agentRepliesApi, keyPointsApi } from '@/services'
import AgentReplyGenerateDialog from '@/components/AgentReplyGenerateDialog'
import AgentReplyBatchVersionDialog from '@/components/AgentReplyBatchVersionDialog'
import CaseCategoryBatchDialog from '@/components/CaseCategoryBatchDialog'
import { AgentReplyVersionsDrawer } from '@/components/AgentReplyVersionsDrawer'
import KeyPointsExtractDialog from '@/components/KeyPointsExtractDialog'
import { SelectionBar } from '@/components/SelectionBar'
import QuestionContentEditor, { questionFilled } from '@/components/QuestionContentEditor'
import { AttachmentStrip } from '@/components/MessageContentView'
import type { CandidateCase, ImportPreview } from '@/services/benchmark'
import { contentToText } from '@/lib/contentBlocks'
import type { MessageContent } from '@/lib/contentBlocks'
import { formatApiError, toToastMessage } from '@/lib/errors'
import { addIds, collectAllIds, pageSelectionState, togglePageIds } from '@/lib/batchSelection'

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
  const editQuestionId = `${reactId}-edit-question`
  const editAnswerId = `${reactId}-edit-answer`
  const editKeyPointsId = `${reactId}-edit-key-points`
  const editNegativePointsId = `${reactId}-edit-negative-points`
  const promoteProjectFieldId = `${reactId}-promote-project`
  const addQuestionId = `${reactId}-add-question`
  const addAnswerId = `${reactId}-add-answer`

  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [search, setSearch] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showPromote, setShowPromote] = useState(false)
  const [promoteProjectId, setPromoteProjectId] = useState('')
  const [editingCase, setEditingCase] = useState<CandidateCase | null>(null)
  // 问题从「只读展示」改成可编辑的 canonical content：带附件样例要能改图，
  // 纯文本样例 buildContent 回落成字符串，提交 payload 与改造前一致。
  const [editQuestion, setEditQuestion] = useState<MessageContent>('')
  const [editAnswer, setEditAnswer] = useState('')
  const [editCategory, setEditCategory] = useState('')
  const [editKeyPoints, setEditKeyPoints] = useState('')
  const [editNegativePoints, setEditNegativePoints] = useState('')
  const [showAddModal, setShowAddModal] = useState(false)
  const [addQuestion, setAddQuestion] = useState<MessageContent>('')
  // 编辑器内部存着「空附件行」草稿，无法从 addQuestion 派生；提交成功后靠换 key 重挂来清空。
  const [addAttachKey, setAddAttachKey] = useState(0)
  const [addAnswer, setAddAnswer] = useState('')
  const [addCategory, setAddCategory] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [importCategory, setImportCategory] = useState('')
  // agent 生成答案：genSingleId 非空 = 只重跑这一条，否则用当前勾选集
  const [genOpen, setGenOpen] = useState(false)
  const [genSingleId, setGenSingleId] = useState<string | null>(null)
  // 批量切换当前版本：按「最新 / vN / 版本备注」在每个样例各自的版本链里解析
  const [batchVerOpen, setBatchVerOpen] = useState(false)
  // 批量改类别：备选集的 category 是自由文本，弹窗允许填新名
  const [batchCatOpen, setBatchCatOpen] = useState(false)
  const [versionsCaseRef, setVersionsCaseRef] = useState<string | null>(null)
  // 提炼关键点：勾选了就只提炼勾选的，没勾选就全量扫待提炼样例。
  const [extractOpen, setExtractOpen] = useState(false)
  // 编辑弹窗里的单条 AI 提炼：结果只回填输入框，用户可改后再保存。
  const [extractingOne, setExtractingOne] = useState(false)
  // 两步式文件导入：选文件 → 预览（识别列 + 建议问题/答案列 + 样例）→ 确认导入。
  const fileRef = useRef<HTMLInputElement>(null)
  const importFileId = `${reactId}-import-file`
  const [showImport, setShowImport] = useState(false)
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null)
  const [importQuestionCol, setImportQuestionCol] = useState('')
  const [importAnswerCol, setImportAnswerCol] = useState('')
  const [importFileName, setImportFileName] = useState('')
  // <input type=file> 在切到预览步骤后被卸载，故把 File 存进 state 保证确认时可用。
  const [importFile, setImportFile] = useState<File | null>(null)

  const [pageSize, setPageSize] = useState(20)
  // 跨页全选（逐页拉 id）进行中
  const [selectingAll, setSelectingAll] = useState(false)

  const { data: dataset, isLoading: datasetLoading } = useQuery({
    queryKey: ['dataset', name],
    queryFn: () => datasetsApi.get(name!).then(r => r.data),
    enabled: !!name,
  })

  const { data: casesData, isLoading } = useQuery({
    queryKey: ['dataset-candidates', name, page, pageSize, statusFilter, categoryFilter, search],
    queryFn: () => candidatesApi.list({
      page,
      page_size: pageSize,
      dataset_name: name,
      status: statusFilter || undefined,
      category: categoryFilter || undefined,
      search: search || undefined,
    }).then(r => r.data),
    enabled: !!name,
  })

  // 该数据集下已有的类别名（去重），供筛选下拉 + 导入/添加时的 datalist 建议。
  const { data: categoryOptions } = useQuery({
    queryKey: ['dataset-candidate-categories', name],
    queryFn: () => candidatesApi.categories({ dataset_name: name }).then(r => r.data.categories),
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
      category: addCategory.trim() || undefined,
      dataset_name: name!,
      source: 'manual',
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
      queryClient.invalidateQueries({ queryKey: ['dataset-candidate-categories'] })
      setShowAddModal(false)
      setAddQuestion('')
      setAddAttachKey(k => k + 1)
      setAddAnswer('')
      setAddCategory('')
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

  // 第一步：预览选中文件，识别列 + 建议映射。
  const previewMutation = useMutation({
    mutationFn: (file: File) =>
      candidatesApi.importPreview(file).then(r => r.data),
    onSuccess: (data) => {
      setImportPreview(data)
      setImportQuestionCol(data.suggested_mapping.question || '')
      setImportAnswerCol(data.suggested_mapping.reference_answer || '')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '预览失败'),
  })

  // 第二步：按（可能被覆盖的）列映射导入。
  const importMutation = useMutation({
    mutationFn: (file: File) => candidatesApi.importFile(file, {
      datasetName: name || undefined,
      category: importCategory.trim() || undefined,
      questionColumn: importQuestionCol || undefined,
      answerColumn: importAnswerCol || undefined,
    }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
      closeImport()
      const d = res.data
      const skippedNote = d.skipped ? `，跳过 ${d.skipped} 行（无问题）` : ''
      const dupNote = d.duplicates ? `，跳过 ${d.duplicates} 行（重复）` : ''
      toast.success(
        `${d.imported_to_benchmark} 条待导入，${d.pending_in_staging} 条进入暂存区${skippedNote}${dupNote}`,
        '导入完成',
      )
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '导入失败'),
  })

  const closeImport = () => {
    setShowImport(false)
    setImportPreview(null)
    setImportQuestionCol('')
    setImportAnswerCol('')
    setImportFileName('')
    setImportFile(null)
    setImportCategory('')
    if (fileRef.current) fileRef.current.value = ''
  }

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

  // 表头 checkbox 只反映当页；跨页累积的总数走 SelectionBar。
  const pageSel = pageSelectionState(selectedIds, caseIdsOnPage)

  function refreshReplyStates() {
    queryClient.invalidateQueries({ queryKey: ['agent-reply-states'] })
  }

  function openEdit(c: CandidateCase) {
    setEditingCase(c)
    // 带附件样例回填 blocks（question_content），纯文本样例回填 question 字符串。
    setEditQuestion(c.question_content ?? c.question ?? '')
    setEditAnswer(c.answer || '')
    setEditCategory(c.category || '')
    setEditKeyPoints((c.key_points || []).join(', '))
    setEditNegativePoints((c.negative_points || []).join(', '))
  }

  function saveEdit() {
    if (!editingCase) return
    updateMutation.mutate({
      id: editingCase.id,
      data: {
        // 后端 split_question_content 会把它拆成 question 文本投影 + question_content
        question: editQuestion,
        answer: editAnswer || null,
        category: editCategory.trim() || null,
        key_points: editKeyPoints ? editKeyPoints.split(',').map(s => s.trim()).filter(Boolean) : null,
        negative_points: editNegativePoints ? editNegativePoints.split(',').map(s => s.trim()).filter(Boolean) : null,
      },
    })
  }

  // 编辑弹窗里的单条提炼：只回填输入框，用户确认（可改）后随表单一起保存，不落库。
  async function extractOneKeyPoints() {
    if (!editAnswer.trim()) {
      toast.error('请先填写参考答案')
      return
    }
    setExtractingOne(true)
    try {
      const res = await keyPointsApi.extractOne({
        answer: editAnswer,
        question: editingCase?.question || undefined,
      })
      setEditKeyPoints(res.data.points.join(', '))
      toast.success(`提炼出 ${res.data.points.length} 个关键点，确认后点保存`)
    } catch (e) {
      toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '提炼失败' })))
    } finally {
      setExtractingOne(false)
    }
  }

  function toggleSelect(id: string) {
    setSelectedIds(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })
  }

  // 「选择全部 N 条」：按当前筛选逐页拉 id 并入选中集合。
  async function selectAllMatching() {
    setSelectingAll(true)
    try {
      const ids = await collectAllIds(async (p, size) => {
        const r = await candidatesApi.list({
          page: p, page_size: size,
          dataset_name: name,
          status: statusFilter || undefined,
          category: categoryFilter || undefined,
          search: search || undefined,
        })
        return { ids: r.data.items.map(c => c.id), total: r.data.total }
      })
      setSelectedIds(prev => addIds(prev, ids))
    } catch (e) {
      toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '全选失败' })))
    } finally {
      setSelectingAll(false)
    }
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
        <select
          value={categoryFilter}
          onChange={e => { setCategoryFilter(e.target.value); setPage(1) }}
          className="select-sm"
        >
          <option value="">全部类别</option>
          {(categoryOptions ?? []).map(cat => (
            <option key={cat} value={cat}>{cat}</option>
          ))}
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
        <Button variant="secondary" size="sm" onClick={() => setShowImport(true)}>
          导入文件
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
        {/* 勾选样例后先让 agent 跑出答案存成版本，评估时可直接消费、不再实时连 agent。 */}
        <Button
          variant="secondary"
          size="sm"
          onClick={() => { setGenSingleId(null); setGenOpen(true) }}
          disabled={selectedIds.size === 0}
          title={selectedIds.size === 0 ? '请先勾选样例' : undefined}
        >
          agent生成答案{selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}
        </Button>
        {/* 批量切换这些样例的当前版本：评估「使用已有回复」消费的就是当前版本 */}
        <Button
          onClick={() => setBatchVerOpen(true)}
          variant="secondary"
          size="sm"
          disabled={selectedIds.size === 0}
          title={selectedIds.size === 0 ? '请先勾选样例' : '把这些样例的当前版本批量切到同一标识'}
        >
          批量切换版本{selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}
        </Button>
        {/* 批量改类别：备选集的类别是自由文本，允许直接写一个新名字 */}
        <Button
          onClick={() => setBatchCatOpen(true)}
          variant="secondary"
          size="sm"
          disabled={selectedIds.size === 0}
          title={selectedIds.size === 0 ? '请先勾选样例' : '把这些样例的类别批量改成同一个，或清空'}
        >
          批量改类别{selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}
        </Button>
        {/* 从参考答案提炼关键点：勾选了就只提炼这几条，没勾选就全量扫「有答案但关键点为空」的。 */}
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setExtractOpen(true)}
          title="用大模型从参考答案提炼关键点，供 judge 逐条核对"
        >
          提炼关键点{selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}
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

      <SelectionBar
        selectedCount={selectedIds.size}
        total={total}
        pageCount={cases.length}
        pageSelectedCount={pageSel.count}
        onSelectAll={selectAllMatching}
        onClear={() => setSelectedIds(new Set())}
        selectingAll={selectingAll}
        pageSize={pageSize}
        onPageSizeChange={size => { setPageSize(size); setPage(1) }}
      />

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th className="w-10 text-center">
                <input
                  type="checkbox"
                  checked={pageSel.all}
                  ref={el => { if (el) el.indeterminate = pageSel.some }}
                  onChange={() => setSelectedIds(prev => togglePageIds(prev, caseIdsOnPage))}
                  aria-label="全选当前页"
                  className="accent-accent"
                />
              </th>
              <th>问题</th>
              <th className="w-28">类别</th>
              <th className="w-20">有答案</th>
              <th className="w-24">状态</th>
              <th className="w-24">来源</th>
              <th className="w-28">agent回复</th>
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
                  {/* 带附件时问题文本用后端的纯文本投影（含 [图片] 占位），
                      再挂一排缩略图，列表里也能一眼看出这条是带图样例。 */}
                  <div className="truncate">{contentToText(c.question_content ?? c.question)}</div>
                  <AttachmentStrip content={c.question_content ?? undefined} />
                  {c.answer && <div className="text-[11px] text-text-tertiary mt-0.5 truncate">答：{c.answer.slice(0, 80)}</div>}
                </td>
                <td>
                  {c.category
                    ? <span className="badge badge-neutral">{c.category}</span>
                    : <span className="text-text-tertiary text-[11px]">—</span>}
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
              <label htmlFor={editQuestionId} className="field-label">问题</label>
              {/* key 绑样例 id：切换样例时重置编辑器内部的空附件行 draft */}
              <QuestionContentEditor
                key={editingCase.id}
                textareaId={editQuestionId}
                value={editQuestion}
                onChange={setEditQuestion}
                rows={3}
              />
            </div>
            <div>
              <label htmlFor={editAnswerId} className="field-label">参考答案</label>
              <textarea id={editAnswerId} value={editAnswer} onChange={e => setEditAnswer(e.target.value)} rows={4} className="input resize-y" />
            </div>
            <div>
              <label className="field-label">类别（可选，进入基准时按名同步）</label>
              <input
                value={editCategory}
                onChange={e => setEditCategory(e.target.value)}
                list="candidate-category-options"
                placeholder="如：规格参数 / 故障处理"
                className="input"
              />
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label htmlFor={editKeyPointsId} className="field-label">关键点（逗号分隔）</label>
                {/* 提炼结果只回填输入框，用户核对（可改）后随表单一起保存 */}
                <button
                  type="button"
                  onClick={extractOneKeyPoints}
                  disabled={extractingOne || !editAnswer.trim()}
                  className="text-action text-[11px] disabled:opacity-40"
                  title={editAnswer.trim() ? '用大模型从参考答案提炼关键点' : '请先填写参考答案'}
                >
                  {extractingOne ? '提炼中…' : 'AI 提炼'}
                </button>
              </div>
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
              disabled={!questionFilled(addQuestion)}
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
            {/* key 随每次提交自增：控件内部的空附件行 draft 无法从 value 派生，
                靠重挂载来清空，否则下一条新增会带着上一条的空行。 */}
            <QuestionContentEditor
              key={addAttachKey}
              textareaId={addQuestionId}
              value={addQuestion}
              onChange={setAddQuestion}
              rows={3}
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
          <div>
            <label className="field-label">类别（可选，进入基准时按名同步）</label>
            <input
              value={addCategory}
              onChange={e => setAddCategory(e.target.value)}
              list="candidate-category-options"
              placeholder="如：规格参数 / 故障处理"
              className="input"
            />
          </div>
        </div>
      </Dialog>

      <Dialog
        open={showImport}
        onClose={closeImport}
        title="导入文件"
        width={720}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={closeImport}>取消</Button>
            {!importPreview ? (
              <Button
                variant="primary"
                size="md"
                loading={previewMutation.isPending}
                onClick={() => { const f = fileRef.current?.files?.[0]; if (f) { setImportFile(f); setImportFileName(f.name); previewMutation.mutate(f) } }}
              >
                下一步：识别字段
              </Button>
            ) : (
              <Button
                variant="primary"
                size="md"
                disabled={!importQuestionCol || !importFile}
                loading={importMutation.isPending}
                onClick={() => { if (importFile) importMutation.mutate(importFile) }}
              >
                确认并导入（{importPreview.total_rows} 行）
              </Button>
            )}
          </>
        }
      >
        {!importPreview ? (
          <>
            <p className="text-[12px] text-text-secondary mb-4">
              支持 CSV、JSON/JSONL、Excel(.xlsx) 格式，可处理大体量文件。上传后会自动识别问题与期望答案列，并允许你手动调整。有答案的样例进入待导入，无答案进入暂存区。
            </p>
            <div>
              <label htmlFor={importFileId} className="field-label">选择文件</label>
              <input id={importFileId} ref={fileRef} type="file" accept=".csv,.json,.jsonl,.xlsx,.xls" className="text-[12px]" />
            </div>
          </>
        ) : (
          <div className="space-y-4">
            <p className="text-[12px] text-text-secondary">
              文件 <span className="font-medium text-text-primary">{importFileName}</span> 共 {importPreview.total_rows} 行，
              识别到 {importPreview.source_headers.length} 列。请确认问题列与期望答案列。
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="field-label">问题列 <span className="text-action-danger">*</span></label>
                <select value={importQuestionCol} onChange={e => setImportQuestionCol(e.target.value)} className="input">
                  <option value="">— 选择列 —</option>
                  {importPreview.source_headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
              <div>
                <label className="field-label">期望答案列（可选）</label>
                <select value={importAnswerCol} onChange={e => setImportAnswerCol(e.target.value)} className="input">
                  <option value="">— 不指定 —</option>
                  {importPreview.source_headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
            </div>
            {!importQuestionCol && (
              <p className="text-[11px] text-action-danger">未能自动识别问题列，请手动选择。</p>
            )}
            <div>
              <label className="field-label">类别（可选，统一套用到本次导入的样例）</label>
              <input
                value={importCategory}
                onChange={e => setImportCategory(e.target.value)}
                list="candidate-category-options"
                placeholder="如：规格参数 / 故障处理；文件含类别列时此处留空则按行识别"
                className="input"
              />
            </div>
            <div>
              <div className="field-label mb-1">列预览（前 3 行样例）</div>
              <div className="border border-border rounded-md overflow-auto max-h-[260px]">
                <table className="w-full text-[11px]">
                  <thead className="bg-fill/5 sticky top-0">
                    <tr>
                      {importPreview.source_headers.map(h => (
                        <th key={h} className={`text-left px-2 py-1 font-medium whitespace-nowrap ${
                          h === importQuestionCol ? 'text-accent' : h === importAnswerCol ? 'text-positive' : 'text-text-secondary'
                        }`}>
                          {h}
                          {h === importQuestionCol && ' · 问题'}
                          {h === importAnswerCol && ' · 答案'}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[0, 1, 2].map(i => (
                      <tr key={i} className="border-t border-separator">
                        {importPreview.source_headers.map(h => (
                          <td key={h} className="px-2 py-1 align-top text-text-secondary max-w-[200px] truncate">
                            {importPreview.sample_values[h]?.[i] ?? ''}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <button
              type="button"
              onClick={() => { setImportPreview(null); setImportQuestionCol(''); setImportAnswerCol(''); setImportFile(null); if (fileRef.current) fileRef.current.value = '' }}
              className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
            >
              ‹ 重新选择文件
            </button>
          </div>
        )}
      </Dialog>

      {/* 共享类别建议选项：手动添加 / 编辑 / 导入 的类别输入框 datalist 复用 */}
      <datalist id="candidate-category-options">
        {(categoryOptions ?? []).map(cat => <option key={cat} value={cat} />)}
      </datalist>

      {/* agent 生成答案：genSingleId 非空 = 只重跑这一条，否则用当前勾选集 */}
      <AgentReplyGenerateDialog
        open={genOpen}
        onClose={() => { setGenOpen(false); setGenSingleId(null) }}
        datasetType="candidate"
        caseIds={genSingleId ? [genSingleId] : Array.from(selectedIds)}
        onFinished={refreshReplyStates}
      />

      <AgentReplyBatchVersionDialog
        open={batchVerOpen}
        onClose={() => setBatchVerOpen(false)}
        datasetType="candidate"
        caseRefs={Array.from(selectedIds)}
        onDone={refreshReplyStates}
      />

      {/* 批量改类别：备选集的类别是自由文本，允许直接写一个新名字 */}
      <CaseCategoryBatchDialog
        open={batchCatOpen}
        onClose={() => setBatchCatOpen(false)}
        datasetType="candidate"
        caseRefs={Array.from(selectedIds)}
        datasetName={name}
        onDone={() => {
          queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })
          queryClient.invalidateQueries({ queryKey: ['candidates'] })
          // 自由文本类别可能是刚新建的，筛选下拉与类别建议 datalist 都要跟着刷。
          queryClient.invalidateQueries({ queryKey: ['dataset-candidate-categories'] })
        }}
      />

      {/* 批量提炼关键点：勾选集为空则由弹窗全量扫待提炼样例 */}
      <KeyPointsExtractDialog
        open={extractOpen}
        onClose={() => setExtractOpen(false)}
        target="candidate"
        caseIds={Array.from(selectedIds)}
        onFinished={() => queryClient.invalidateQueries({ queryKey: ['dataset-candidates'] })}
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
