import { Fragment, useState, useRef, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi, benchmarkApi, type BenchmarkCase, type SchemaColumn } from '@/services/benchmark'

export default function BenchmarkPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)

  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [showImport, setShowImport] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [editCase, setEditCase] = useState<BenchmarkCase | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [importCategoryId, setImportCategoryId] = useState('')
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

  const importMutation = useMutation({
    mutationFn: (file: File) => benchmarkApi.importFile(projectId!, file, importCategoryId || undefined),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['benchmark-cases'] })
      setShowImport(false)
      alert(`导入完成：${res.data.imported_to_benchmark} 条入库，${res.data.pending_in_staging} 条进入暂存区`)
    },
  })

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
    onError: (err: any) => {
      alert(err?.response?.data?.detail || '删除失败')
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
      <Link to="/projects" className="inline-flex items-center gap-1 text-[11px] text-text-tertiary hover:text-text-primary transition-all mb-2 no-underline">
        ← 返回项目
      </Link>
      <header className="mb-6">
        <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary">benchmark</div>
        <h1 className="text-xl font-medium tracking-tight">{project?.name || '基准测试集'}</h1>
        <p className="text-[12px] text-text-tertiary mt-0.5">{project?.description}</p>
      </header>

      <div className="flex items-center gap-3 mb-5 flex-wrap">
        <input
          type="text"
          placeholder="搜索问题..."
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          className="flex-1 max-w-[240px] py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
        />
        <select
          value={categoryFilter}
          onChange={e => { setCategoryFilter(e.target.value); setPage(1) }}
          className="py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
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
              className="py-1.5 px-2 text-[10px] text-text-secondary hover:text-accent transition-all"
              title="重命名类别"
            >
              重命名
            </button>
            <button
              onClick={() => {
                if (confirm('确定删除该类别？仅当类别下无样例时可删除。')) {
                  deleteCategoryMutation.mutate(categoryFilter)
                }
              }}
              className="py-1.5 px-2 text-[10px] text-text-secondary hover:text-negative transition-all"
              title="删除类别"
            >
              删除类别
            </button>
          </>
        )}
        <button
          onClick={() => setShowAddCategory(true)}
          className="py-2 px-2 text-[11px] text-text-tertiary hover:text-accent transition-all"
          title="新增类别"
        >
          + 类别
        </button>
        <div className="flex-1" />
        <button
          onClick={() => setShowImport(true)}
          className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-surface text-text-primary border border-border hover:border-accent active:scale-[0.97] transition-all"
        >
          导入文件
        </button>
        <button
          onClick={() => setShowCreate(true)}
          className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 active:scale-[0.97] transition-all"
        >
          + 新增样例
        </button>
      </div>

      <div className="border border-border rounded-[6px] overflow-hidden bg-surface">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">问题</th>
              {!categoryFilter && <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-24">类别</th>}
              {extraColumns.map((col: SchemaColumn) => (
                <th key={col.name} className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle" title={col.description}>
                  {col.description || col.name}
                </th>
              ))}
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-20">有答案</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-20">来源</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-right py-2 px-3 border-b border-border font-normal bg-accent-subtle w-16">操作</th>
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
                className="hover:bg-accent-subtle group cursor-pointer"
                onClick={() => setExpandedId(isOpen ? null : c.id)}
                title="点击展开 / 收起 参考答案与期望工具调用"
              >
                <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-primary max-w-[400px]">
                  <span className="inline-block w-3 mr-1 text-text-tertiary">{isOpen ? '▾' : '▸'}</span>
                  <span className="truncate inline-block max-w-[370px] align-middle">{c.question}</span>
                </td>
                {!categoryFilter && <td className="py-2.5 px-3 border-b border-border text-[11px] text-text-tertiary">{getCategoryName(c.category_id)}</td>}
                {extraColumns.map((col: SchemaColumn) => (
                  <td key={col.name} className="py-2.5 px-3 border-b border-border text-[11px] text-text-tertiary max-w-[180px] truncate">
                    {c.extra_fields?.[col.name] ?? '—'}
                  </td>
                ))}
                <td className="py-2.5 px-3 border-b border-border text-[11px]">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-medium ${c.reference_answer ? 'bg-[#e6f7ed] text-[#1a6]' : 'bg-[#f5f5f5] text-[#999]'}`}>
                    {c.reference_answer ? 'Y' : 'N'}
                  </span>
                </td>
                <td className="py-2.5 px-3 border-b border-border text-[11px] text-text-tertiary">{c.source}</td>
                <td className="py-2.5 px-3 border-b border-border text-right" onClick={e => e.stopPropagation()}>
                  <div className="flex gap-2 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => setEditCase(c)} className="text-[10px] text-text-secondary hover:text-accent">编辑</button>
                    <button onClick={() => { if (confirm('确定删除？')) deleteMutation.mutate(c.id) }} className="text-[10px] text-text-secondary hover:text-negative">删除</button>
                  </div>
                </td>
              </tr>
              {isOpen && (
                <tr className="bg-accent-subtle/30">
                  <td colSpan={colSpan} className="px-3 py-3 border-b border-border">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">参考答案</div>
                        {c.reference_answer ? (
                          <pre className="font-mono text-[11px] bg-white border border-border rounded-[3px] p-2 max-h-[260px] overflow-y-auto whitespace-pre-wrap">{c.reference_answer}</pre>
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
                        <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">
                          期望工具调用 {expectedTools.length > 0 && <span className="text-text-tertiary">({expectedTools.length})</span>}
                        </div>
                        {expectedTools.length > 0 ? (
                          <div className="border border-border rounded-[3px] bg-white">
                            <table className="w-full text-[11px]">
                              <thead>
                                <tr className="bg-accent-subtle/60">
                                  <th className="text-[9px] tracking-widest uppercase text-text-tertiary text-left py-1 px-2 font-normal">#</th>
                                  <th className="text-[9px] tracking-widest uppercase text-text-tertiary text-left py-1 px-2 font-normal">工具</th>
                                  <th className="text-[9px] tracking-widest uppercase text-text-tertiary text-left py-1 px-2 font-normal">参数 / 备注</th>
                                </tr>
                              </thead>
                              <tbody>
                                {expectedTools.map((t, i) => {
                                  const name = (t.tool_name || t.name || '?') as string
                                  const args = t.args ?? t.arguments
                                  return (
                                    <tr key={i} className="border-t border-border/40">
                                      <td className="py-1 px-2 text-text-tertiary tabular-nums">{i + 1}</td>
                                      <td className="py-1 px-2 font-mono">{name}</td>
                                      <td className="py-1 px-2 text-text-tertiary">
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
          <div className="text-center py-10 text-text-tertiary text-[12px]">暂无样例</div>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-[11px] text-text-tertiary">共 {total} 条，第 {page}/{totalPages} 页</span>
          <div className="flex gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="py-1 px-2.5 text-[11px] border border-border rounded-[4px] hover:border-accent disabled:opacity-30 transition-all">上一页</button>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="py-1 px-2.5 text-[11px] border border-border rounded-[4px] hover:border-accent disabled:opacity-30 transition-all">下一页</button>
          </div>
        </div>
      )}

      {showImport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowImport(false)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[440px] shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-[14px] font-medium">导入文件</h2>
              <button onClick={() => setShowImport(false)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
            </div>
            <p className="text-[11px] text-text-secondary mb-4">支持 CSV、JSON、XLSX 格式。需包含 question 列，可选 reference_answer、key_points、negative_points 列。</p>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">目标类别</label>
                <select
                  value={importCategoryId}
                  onChange={e => setImportCategoryId(e.target.value)}
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent"
                >
                  <option value="">不指定类别</option>
                  {categories?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">选择文件</label>
                <input ref={fileRef} type="file" accept=".csv,.json,.jsonl,.xlsx,.xls" className="text-[12px]" />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setShowImport(false)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button
                  onClick={() => { const f = fileRef.current?.files?.[0]; if (f) importMutation.mutate(f) }}
                  disabled={importMutation.isPending}
                  className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all"
                >
                  {importMutation.isPending ? '导入中...' : '开始导入'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 新增样例弹窗 */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowCreate(false)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[500px] max-h-[85vh] overflow-y-auto shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-[14px] font-medium">新增样例</h2>
              <button onClick={() => setShowCreate(false)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">问题</label>
                <textarea
                  value={newQuestion}
                  onChange={e => setNewQuestion(e.target.value)}
                  rows={3}
                  placeholder="输入测试问题..."
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
                />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">参考答案</label>
                <textarea
                  value={newAnswer}
                  onChange={e => setNewAnswer(e.target.value)}
                  rows={3}
                  placeholder="输入参考答案..."
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
                />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">关键点（逗号分隔）</label>
                <input
                  value={newKeyPoints}
                  onChange={e => setNewKeyPoints(e.target.value)}
                  placeholder="要点1, 要点2, ..."
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">反向关键点（逗号分隔）</label>
                <input
                  value={newNegativePoints}
                  onChange={e => setNewNegativePoints(e.target.value)}
                  placeholder="不应出现的内容..."
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">类别</label>
                <select
                  value={newCategoryId}
                  onChange={e => setNewCategoryId(e.target.value)}
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                >
                  <option value="">不指定类别</option>
                  {categories?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setShowCreate(false)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button
                  onClick={() => createMutation.mutate()}
                  disabled={!newQuestion.trim() || createMutation.isPending}
                  className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all"
                >
                  添加
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 编辑样例弹窗 */}
      {editCase && (
        <EditCaseModal
          editCase={editCase}
          setEditCase={setEditCase}
          categories={categories}
          categorySchema={editCase.category_id === categoryFilter ? categorySchema : undefined}
          onSave={(id, data) => updateCaseMutation.mutate({ id, data })}
          isPending={updateCaseMutation.isPending}
        />
      )}

      {/* 新增类别弹窗 */}
      {showAddCategory && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowAddCategory(false)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[360px] shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-[14px] font-medium">新增类别</h2>
              <button onClick={() => setShowAddCategory(false)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">类别名称</label>
                <input
                  value={newCategoryName}
                  onChange={e => setNewCategoryName(e.target.value)}
                  placeholder="e.g. errorcode"
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setShowAddCategory(false)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button
                  onClick={() => addCategoryMutation.mutate()}
                  disabled={!newCategoryName.trim() || addCategoryMutation.isPending}
                  className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all"
                >
                  创建
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
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
  editCase: BenchmarkCase
  setEditCase: (c: BenchmarkCase | null) => void
  categories: { id: string; name: string }[] | undefined
  categorySchema: { schema_config: { columns?: SchemaColumn[] } | null } | undefined
  onSave: (id: string, data: any) => void
  isPending: boolean
}) {
  const schemaColumns = categorySchema?.schema_config?.columns?.filter(
    (col: SchemaColumn) => col.type === 'mapped' && col.name !== 'question'
  ) || []

  const extraFields = editCase.extra_fields || {}

  const updateExtra = (key: string, value: string) => {
    setEditCase({ ...editCase, extra_fields: { ...extraFields, [key]: value } })
  }

  const handleSave = () => {
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setEditCase(null)}>
      <div className="bg-surface border border-border rounded-lg p-6 w-[500px] max-h-[85vh] overflow-y-auto shadow-lg" onClick={e => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-5">
          <h2 className="text-[14px] font-medium">编辑样例</h2>
          <button onClick={() => setEditCase(null)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
        </div>
        <div className="space-y-4">
          <div>
            <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">问题</label>
            <textarea
              value={editCase.question}
              onChange={e => setEditCase({ ...editCase, question: e.target.value })}
              rows={3}
              className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
            />
          </div>

          {schemaColumns.length > 0 ? (
            <>
              {schemaColumns.map((col: SchemaColumn) => (
                <div key={col.name}>
                  <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">
                    {col.description || col.name}
                  </label>
                  {(col.name.includes('answer') || col.name.includes('response')) ? (
                    <textarea
                      value={extraFields[col.name] || ''}
                      onChange={e => updateExtra(col.name, e.target.value)}
                      rows={3}
                      placeholder={col.description || col.name}
                      className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
                    />
                  ) : (
                    <input
                      value={extraFields[col.name] || ''}
                      onChange={e => updateExtra(col.name, e.target.value)}
                      placeholder={col.description || col.name}
                      className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                    />
                  )}
                </div>
              ))}
            </>
          ) : (
            <>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">参考答案</label>
                <textarea
                  value={editCase.reference_answer || ''}
                  onChange={e => setEditCase({ ...editCase, reference_answer: e.target.value })}
                  rows={4}
                  placeholder="输入参考答案..."
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
                />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">关键点（逗号分隔）</label>
                <input
                  value={editCase.key_points?.join(', ') || ''}
                  onChange={e => setEditCase({ ...editCase, key_points: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">反向关键点（逗号分隔）</label>
                <input
                  value={editCase.negative_points?.join(', ') || ''}
                  onChange={e => setEditCase({ ...editCase, negative_points: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                />
              </div>
            </>
          )}

          <div>
            <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">类别</label>
            <select
              value={editCase.category_id || ''}
              onChange={e => setEditCase({ ...editCase, category_id: e.target.value || null })}
              className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
            >
              <option value="">不指定类别</option>
              {categories?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>

          <div className="flex gap-2 justify-end pt-2">
            <button onClick={() => setEditCase(null)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
            <button
              onClick={handleSave}
              disabled={!editCase.question.trim() || isPending}
              className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all"
            >
              {isPending ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
