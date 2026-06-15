import { useId, useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useConfirm, useToast, ExportMenu } from '@/components/ui'
import { datasetsApi, candidatesApi, projectsApi } from '@/services'
import type { CandidateCase, ImportPreview } from '@/services/benchmark'
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
  const [categoryFilter, setCategoryFilter] = useState('')
  const [search, setSearch] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showPromote, setShowPromote] = useState(false)
  const [promoteProjectId, setPromoteProjectId] = useState('')
  const [editingCase, setEditingCase] = useState<CandidateCase | null>(null)
  const [editAnswer, setEditAnswer] = useState('')
  const [editCategory, setEditCategory] = useState('')
  const [editKeyPoints, setEditKeyPoints] = useState('')
  const [editNegativePoints, setEditNegativePoints] = useState('')
  const [showAddModal, setShowAddModal] = useState(false)
  const [addQuestion, setAddQuestion] = useState('')
  const [addAnswer, setAddAnswer] = useState('')
  const [addCategory, setAddCategory] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [importCategory, setImportCategory] = useState('')
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

  const pageSize = 20

  const { data: dataset, isLoading: datasetLoading } = useQuery({
    queryKey: ['dataset', name],
    queryFn: () => datasetsApi.get(name!).then(r => r.data),
    enabled: !!name,
  })

  const { data: casesData, isLoading } = useQuery({
    queryKey: ['dataset-candidates', name, page, statusFilter, categoryFilter, search],
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

  function openEdit(c: CandidateCase) {
    setEditingCase(c)
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
        answer: editAnswer || null,
        category: editCategory.trim() || null,
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
              <th className="w-28">类别</th>
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
    </div>
  )
}
