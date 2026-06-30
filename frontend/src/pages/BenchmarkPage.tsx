import { Fragment, useId, useState, useRef, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useConfirm, useToast, ExportMenu } from '@/components/ui'
import { projectsApi, benchmarkApi, type BenchmarkCase, type SchemaColumn, type ImportPreview } from '@/services/benchmark'
import { formatApiError, toToastMessage } from '@/lib/errors'

export default function BenchmarkPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const reactId = useId()
  const importCategoryFieldId = `${reactId}-import-category`
  const importFileId = `${reactId}-import-file`
  const newQuestionId = `${reactId}-new-question`
  const newAnswerId = `${reactId}-new-answer`
  const newKeyPointsId = `${reactId}-new-key-points`
  const newNegativePointsId = `${reactId}-new-negative-points`
  const newCategoryFieldId = `${reactId}-new-category`
  const newCategoryNameId = `${reactId}-new-category-name`

  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [showImport, setShowImport] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [editCase, setEditCase] = useState<BenchmarkCase | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [importCategoryId, setImportCategoryId] = useState('')
  // Two-step import: after picking a file we preview it (detected columns +
  // suggested question/answer mapping + samples), let the user confirm or
  // override the mapping, then import.
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null)
  const [importQuestionCol, setImportQuestionCol] = useState('')
  const [importAnswerCol, setImportAnswerCol] = useState('')
  const [importFileName, setImportFileName] = useState('')
  // Hold the picked File in state: the <input type=file> is unmounted once we
  // switch to the preview step (conditional render), so fileRef would be null
  // at confirm time. Keeping the File here makes confirm-import reliable.
  const [importFile, setImportFile] = useState<File | null>(null)
  const [newQuestion, setNewQuestion] = useState('')
  const [newAnswer, setNewAnswer] = useState('')
  const [newKeyPoints, setNewKeyPoints] = useState('')
  const [newNegativePoints, setNewNegativePoints] = useState('')
  const [newCategoryId, setNewCategoryId] = useState('')
  const [showAddCategory, setShowAddCategory] = useState(false)
  const [newCategoryName, setNewCategoryName] = useState('')

  const pageSize = 20

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
  })
  const project = projects?.find(p => p.id === projectId)

  const { data: categories } = useQuery({
    queryKey: ['categories', projectId],
    queryFn: () => projectsApi.getCategories(projectId!).then(r => r.data),
    enabled: !!projectId,
  })

  const { data: casesData, isLoading } = useQuery({
    queryKey: ['benchmark-cases', projectId, page, search, categoryFilter],
    queryFn: () => benchmarkApi.listCases(projectId!, {
      page, page_size: pageSize,
      search: search || undefined,
      category_id: categoryFilter || undefined,
    }).then(r => r.data),
    enabled: !!projectId,
  })

  const { data: categorySchema } = useQuery({
    queryKey: ['category-schema', categoryFilter],
    queryFn: () => benchmarkApi.getCategorySchema(categoryFilter).then(r => r.data),
    enabled: !!categoryFilter,
  })

  const extraColumns = useMemo(() => {
    if (!categorySchema?.schema_config?.columns) return []
    return categorySchema.schema_config.columns.filter(
      (col: SchemaColumn) => col.type === 'mapped' && col.name !== 'question' && col.name !== 'expected_answer'
    )
  }, [categorySchema])

  // Step 1: preview a picked file — detect columns + suggested mapping.
  const previewMutation = useMutation({
    mutationFn: (file: File) =>
      benchmarkApi.importPreview(projectId!, file, { categoryId: importCategoryId || undefined }).then(r => r.data),
    onSuccess: (data) => {
      setImportPreview(data)
      // Default the column pickers to the auto-detected suggestion.
      setImportQuestionCol(data.suggested_mapping.question || '')
      setImportAnswerCol(data.suggested_mapping.reference_answer || '')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '预览失败'),
  })

  // Step 2: import with the (possibly overridden) column mapping.
  const importMutation = useMutation({
    mutationFn: (file: File) => benchmarkApi.importFile(projectId!, file, {
      categoryId: importCategoryId || undefined,
      questionColumn: importQuestionCol || undefined,
      answerColumn: importAnswerCol || undefined,
    }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['benchmark-cases'] })
      closeImport()
      const d = res.data
      const skippedNote = d.skipped ? `，跳过 ${d.skipped} 行（无问题）` : ''
      const dupNote = d.duplicates ? `，跳过 ${d.duplicates} 行（重复）` : ''
      toast.success(
        `${d.imported_to_benchmark} 条入库，${d.pending_in_staging} 条进入暂存区${skippedNote}${dupNote}`,
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
    if (fileRef.current) fileRef.current.value = ''
  }

  const createMutation = useMutation({
    mutationFn: () => benchmarkApi.createCase(projectId!, {
      question: newQuestion,
      reference_answer: newAnswer || undefined,
      key_points: newKeyPoints ? newKeyPoints.split(',').map(s => s.trim()).filter(Boolean) : [],
      negative_points: newNegativePoints ? newNegativePoints.split(',').map(s => s.trim()).filter(Boolean) : [],
      category_id: newCategoryId || undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['benchmark-cases'] })
      setShowCreate(false)
      setNewQuestion('')
      setNewAnswer('')
      setNewKeyPoints('')
      setNewNegativePoints('')
      setNewCategoryId('')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => benchmarkApi.deleteCase(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['benchmark-cases'] }),
  })

  const updateCaseMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<BenchmarkCase> }) => benchmarkApi.updateCase(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['benchmark-cases'] })
      setEditCase(null)
    },
  })

  const addCategoryMutation = useMutation({
    mutationFn: () => projectsApi.createCategory(projectId!, { name: newCategoryName }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['categories', projectId] })
      setShowAddCategory(false)
      setNewCategoryName('')
    },
  })

  const updateCategoryMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => projectsApi.updateCategory(id, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['categories', projectId] })
    },
  })

  const deleteCategoryMutation = useMutation({
    mutationFn: (id: string) => projectsApi.deleteCategory(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['categories', projectId] })
      if (categoryFilter === deleteCategoryMutation.variables) setCategoryFilter('')
    },
    onError: (err: unknown) => {
      const norm = formatApiError(err, { fallbackTitle: '删除失败' })
      toast.error(toToastMessage(norm), '删除失败')
    },
  })

  const cases = casesData?.items ?? []
  const total = casesData?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const getCategoryName = (id: string | null) => {
    if (!id) return '—'
    return categories?.find(c => c.id === id)?.name || '—'
  }

  return (
    <div>
      <Link to="/projects" className="back-link mb-2">
        ← 返回项目
      </Link>
      <header className="mb-6">
        <div className="page-eyebrow">benchmark</div>
        <h1 className="page-title">{project?.name || '基准测试集'}</h1>
        <p className="page-subtitle">{project?.description || '管理样例与类别'}</p>
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
          value={categoryFilter}
          onChange={e => { setCategoryFilter(e.target.value); setPage(1) }}
          className="select-sm"
        >
          <option value="">全部类别</option>
          {categories?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {categoryFilter && (
          <>
            <button
              onClick={() => {
                const cat = categories?.find(c => c.id === categoryFilter)
                const newName = prompt('重命名类别', cat?.name || '')
                if (newName && newName !== cat?.name) {
                  updateCategoryMutation.mutate({ id: categoryFilter, name: newName })
                }
              }}
              className="text-action"
            >
              重命名
            </button>
            <button
              onClick={async () => {
                const ok = await confirm({
                  title: '删除类别',
                  description: '确定删除该类别？仅当类别下无样例时可删除。',
                  confirmText: '删除',
                  danger: true,
                })
                if (ok) deleteCategoryMutation.mutate(categoryFilter)
              }}
              className="text-action-danger"
            >
              删除类别
            </button>
          </>
        )}
        <button
          onClick={() => setShowAddCategory(true)}
          className="text-[11px] text-text-tertiary hover:text-accent transition-colors"
        >
          + 类别
        </button>
        <div className="flex-1" />
        <ExportMenu
          disabled={!projectId}
          onExport={async (format) => {
            if (!projectId) return
            try {
              await benchmarkApi.exportCases(
                projectId,
                { search: search || undefined, category_id: categoryFilter || undefined, status: 'active' },
                format,
              )
            } catch (e) {
              toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '导出失败' })))
            }
          }}
        />
        <Button variant="secondary" size="sm" onClick={() => setShowImport(true)}>
          导入文件
        </Button>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)}>
          新增样例
        </Button>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>问题</th>
              {!categoryFilter && <th className="w-28">类别</th>}
              {extraColumns.map((col: SchemaColumn) => (
                <th key={col.name} title={col.description}>
                  {col.description || col.name}
                </th>
              ))}
              <th className="w-20">有答案</th>
              <th className="w-20">来源</th>
              <th className="w-16 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {cases.map(c => {
              const isOpen = expandedId === c.id
              const colSpan = 3 + (categoryFilter ? 0 : 1) + extraColumns.length
              const expectedTools = (c.extra_fields?.expected_tool_calls ?? []) as Array<Record<string, unknown>>
              return (
              <Fragment key={c.id}>
              <tr
                className="group cursor-pointer"
                onClick={() => setExpandedId(isOpen ? null : c.id)}
                title="点击展开/收起 参考答案与期望工具调用"
              >
                <td className="max-w-[460px]">
                  <span className="inline-block w-3 mr-1 text-text-tertiary">{isOpen ? '▾' : '▸'}</span>
                  <span className="truncate inline-block max-w-[420px] align-middle">{c.question}</span>
                </td>
                {!categoryFilter && <td className="text-text-tertiary">{getCategoryName(c.category_id)}</td>}
                {extraColumns.map((col: SchemaColumn) => (
                  <td key={col.name} className="text-text-tertiary max-w-[200px] truncate">
                    {c.extra_fields?.[col.name] ?? '—'}
                  </td>
                ))}
                <td>
                  <span className={c.reference_answer ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {c.reference_answer ? '有' : '无'}
                  </span>
                </td>
                <td className="text-text-tertiary text-[11px]">{c.source}</td>
                <td className="text-right" onClick={e => e.stopPropagation()}>
                  <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => setEditCase(c)} className="text-action">编辑</button>
                    <button
                      onClick={async () => {
                        const ok = await confirm({
                          title: '删除样例',
                          description: '确定删除该样例？',
                          confirmText: '删除',
                          danger: true,
                        })
                        if (ok) deleteMutation.mutate(c.id)
                      }}
                      className="text-action-danger"
                    >
                      删除
                    </button>
                  </div>
                </td>
              </tr>
              {isOpen && (
                <tr className="bg-fill/5">
                  <td colSpan={colSpan} className="px-3 py-3">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <div className="page-eyebrow mb-1">参考答案</div>
                        {c.reference_answer ? (
                          <pre className="font-mono text-[11px] bg-surface border border-border rounded-md p-2.5 max-h-[260px] overflow-y-auto whitespace-pre-wrap">{c.reference_answer}</pre>
                        ) : (
                          <div className="text-[11px] text-text-tertiary italic">未填写</div>
                        )}
                        {(c.key_points?.length || c.negative_points?.length) ? (
                          <div className="mt-2 text-[11px] space-y-1">
                            {c.key_points?.length ? (
                              <div>
                                <span className="text-text-tertiary mr-1">关键点：</span>
                                <span className="text-positive">{c.key_points.join('、')}</span>
                              </div>
                            ) : null}
                            {c.negative_points?.length ? (
                              <div>
                                <span className="text-text-tertiary mr-1">反向点：</span>
                                <span className="text-negative">{c.negative_points.join('、')}</span>
                              </div>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                      <div>
                        <div className="page-eyebrow mb-1">
                          期望工具调用 {expectedTools.length > 0 && <span className="text-text-tertiary">({expectedTools.length})</span>}
                        </div>
                        {expectedTools.length > 0 ? (
                          <div className="border border-border rounded-md bg-surface overflow-hidden">
                            <table className="w-full text-[11px]">
                              <thead>
                                <tr className="bg-fill/5">
                                  <th className="text-[10px] tracking-[0.08em] uppercase text-text-tertiary text-left py-1.5 px-2 font-medium border-b border-separator">#</th>
                                  <th className="text-[10px] tracking-[0.08em] uppercase text-text-tertiary text-left py-1.5 px-2 font-medium border-b border-separator">工具</th>
                                  <th className="text-[10px] tracking-[0.08em] uppercase text-text-tertiary text-left py-1.5 px-2 font-medium border-b border-separator">参数 / 备注</th>
                                </tr>
                              </thead>
                              <tbody>
                                {expectedTools.map((t, i) => {
                                  const name = (t.tool_name || t.name || '?') as string
                                  const args = t.args ?? t.arguments
                                  return (
                                    <tr key={i} className="border-t border-separator">
                                      <td className="py-1.5 px-2 text-text-tertiary tabular-nums">{i + 1}</td>
                                      <td className="py-1.5 px-2 font-mono">{name}</td>
                                      <td className="py-1.5 px-2 text-text-tertiary">
                                        {args == null ? '—' : (typeof args === 'string' ? args : JSON.stringify(args))}
                                      </td>
                                    </tr>
                                  )
                                })}
                              </tbody>
                            </table>
                          </div>
                        ) : (
                          <div className="text-[11px] text-text-tertiary italic">未指定</div>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              )}
              </Fragment>
              )
            })}
          </tbody>
        </table>
        {cases.length === 0 && !isLoading && (
          <div className="empty-state">暂无样例</div>
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
              支持 CSV、JSON/JSONL、Excel(.xlsx) 格式，可处理大体量文件。上传后会自动识别问题与期望答案列，并允许你手动调整。
            </p>
            <div className="space-y-4">
              <div>
                <label htmlFor={importCategoryFieldId} className="field-label">目标类别</label>
                <select
                  id={importCategoryFieldId}
                  value={importCategoryId}
                  onChange={e => setImportCategoryId(e.target.value)}
                  className="input"
                >
                  <option value="">不指定类别</option>
                  {categories?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor={importFileId} className="field-label">选择文件</label>
                <input id={importFileId} ref={fileRef} type="file" accept=".csv,.json,.jsonl,.xlsx,.xls" className="text-[12px]" />
              </div>
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

      <Dialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        title="新增样例"
        width={520}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowCreate(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              disabled={!newQuestion.trim()}
              loading={createMutation.isPending}
              onClick={() => createMutation.mutate()}
            >
              添加
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label htmlFor={newQuestionId} className="field-label">问题</label>
            <textarea
              id={newQuestionId}
              value={newQuestion}
              onChange={e => setNewQuestion(e.target.value)}
              rows={3}
              placeholder="输入测试问题…"
              className="input resize-y"
            />
          </div>
          <div>
            <label htmlFor={newAnswerId} className="field-label">参考答案</label>
            <textarea
              id={newAnswerId}
              value={newAnswer}
              onChange={e => setNewAnswer(e.target.value)}
              rows={3}
              placeholder="输入参考答案…"
              className="input resize-y"
            />
          </div>
          <div>
            <label htmlFor={newKeyPointsId} className="field-label">关键点（逗号分隔）</label>
            <input
              id={newKeyPointsId}
              value={newKeyPoints}
              onChange={e => setNewKeyPoints(e.target.value)}
              placeholder="要点1, 要点2"
              className="input"
            />
          </div>
          <div>
            <label htmlFor={newNegativePointsId} className="field-label">反向关键点（逗号分隔）</label>
            <input
              id={newNegativePointsId}
              value={newNegativePoints}
              onChange={e => setNewNegativePoints(e.target.value)}
              placeholder="不应出现的内容"
              className="input"
            />
          </div>
          <div>
            <label htmlFor={newCategoryFieldId} className="field-label">类别</label>
            <select
              id={newCategoryFieldId}
              value={newCategoryId}
              onChange={e => setNewCategoryId(e.target.value)}
              className="input"
            >
              <option value="">不指定类别</option>
              {categories?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        </div>
      </Dialog>

      <EditCaseModal
        editCase={editCase}
        setEditCase={setEditCase}
        categories={categories}
        categorySchema={editCase?.category_id === categoryFilter ? categorySchema : undefined}
        onSave={(id, data) => updateCaseMutation.mutate({ id, data })}
        isPending={updateCaseMutation.isPending}
      />

      <Dialog
        open={showAddCategory}
        onClose={() => setShowAddCategory(false)}
        title="新增类别"
        width={380}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowAddCategory(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              disabled={!newCategoryName.trim()}
              loading={addCategoryMutation.isPending}
              onClick={() => addCategoryMutation.mutate()}
            >
              创建
            </Button>
          </>
        }
      >
        <div>
          <label htmlFor={newCategoryNameId} className="field-label">类别名称</label>
          <input
            id={newCategoryNameId}
            value={newCategoryName}
            onChange={e => setNewCategoryName(e.target.value)}
            placeholder="例如：errorcode"
            className="input"
          />
        </div>
      </Dialog>
    </div>
  )
}


function EditCaseModal({
  editCase,
  setEditCase,
  categories,
  categorySchema,
  onSave,
  isPending,
}: {
  editCase: BenchmarkCase | null
  setEditCase: (c: BenchmarkCase | null) => void
  categories: { id: string; name: string }[] | undefined
  categorySchema: { schema_config: { columns?: SchemaColumn[] } | null } | undefined
  onSave: (id: string, data: any) => void
  isPending: boolean
}) {
  const schemaColumns = categorySchema?.schema_config?.columns?.filter(
    (col: SchemaColumn) => col.type === 'mapped' && col.name !== 'question'
  ) || []

  const extraFields = editCase?.extra_fields || {}

  const reactId = useId()
  const questionId = `${reactId}-question`
  const answerId = `${reactId}-answer`
  const keyPointsId = `${reactId}-key-points`
  const negativePointsId = `${reactId}-negative-points`
  const categoryFieldId = `${reactId}-category`

  const updateExtra = (key: string, value: string) => {
    if (!editCase) return
    setEditCase({ ...editCase, extra_fields: { ...extraFields, [key]: value } })
  }

  const handleSave = () => {
    if (!editCase) return
    const data: any = {
      question: editCase.question,
      reference_answer: editCase.reference_answer,
      category_id: editCase.category_id,
    }
    if (schemaColumns.length > 0) {
      data.extra_fields = editCase.extra_fields
    } else {
      data.key_points = editCase.key_points
      data.negative_points = editCase.negative_points
    }
    onSave(editCase.id, data)
  }

  return (
    <Dialog
      open={!!editCase}
      onClose={() => setEditCase(null)}
      title="编辑样例"
      width={540}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={() => setEditCase(null)}>取消</Button>
          <Button
            variant="primary"
            size="md"
            disabled={!editCase?.question?.trim()}
            loading={isPending}
            onClick={handleSave}
          >
            保存
          </Button>
        </>
      }
    >
      {editCase && (
        <div className="space-y-4">
          <div>
            <label htmlFor={questionId} className="field-label">问题</label>
            <textarea
              id={questionId}
              value={editCase.question}
              onChange={e => setEditCase({ ...editCase, question: e.target.value })}
              rows={3}
              className="input resize-y"
            />
          </div>

          {schemaColumns.length > 0 ? (
            <>
              {schemaColumns.map((col: SchemaColumn) => {
                const extraId = `${reactId}-extra-${col.name}`
                return (
                <div key={col.name}>
                  <label htmlFor={extraId} className="field-label">{col.description || col.name}</label>
                  {(col.name.includes('answer') || col.name.includes('response')) ? (
                    <textarea
                      id={extraId}
                      value={extraFields[col.name] || ''}
                      onChange={e => updateExtra(col.name, e.target.value)}
                      rows={3}
                      placeholder={col.description || col.name}
                      className="input resize-y"
                    />
                  ) : (
                    <input
                      id={extraId}
                      value={extraFields[col.name] || ''}
                      onChange={e => updateExtra(col.name, e.target.value)}
                      placeholder={col.description || col.name}
                      className="input"
                    />
                  )}
                </div>
                )
              })}
            </>
          ) : (
            <>
              <div>
                <label htmlFor={answerId} className="field-label">参考答案</label>
                <textarea
                  id={answerId}
                  value={editCase.reference_answer || ''}
                  onChange={e => setEditCase({ ...editCase, reference_answer: e.target.value })}
                  rows={4}
                  placeholder="输入参考答案…"
                  className="input resize-y"
                />
              </div>
              <div>
                <label htmlFor={keyPointsId} className="field-label">关键点（逗号分隔）</label>
                <input
                  id={keyPointsId}
                  value={editCase.key_points?.join(', ') || ''}
                  onChange={e => setEditCase({ ...editCase, key_points: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                  className="input"
                />
              </div>
              <div>
                <label htmlFor={negativePointsId} className="field-label">反向关键点（逗号分隔）</label>
                <input
                  id={negativePointsId}
                  value={editCase.negative_points?.join(', ') || ''}
                  onChange={e => setEditCase({ ...editCase, negative_points: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                  className="input"
                />
              </div>
            </>
          )}

          <div>
            <label htmlFor={categoryFieldId} className="field-label">类别</label>
            <select
              id={categoryFieldId}
              value={editCase.category_id || ''}
              onChange={e => setEditCase({ ...editCase, category_id: e.target.value || null })}
              className="input"
            >
              <option value="">不指定类别</option>
              {categories?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        </div>
      )}
    </Dialog>
  )
}
