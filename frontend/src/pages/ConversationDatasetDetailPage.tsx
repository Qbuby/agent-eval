import { useId, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, ExportMenu, useConfirm, useToast } from '@/components/ui'
import { datasetsApi } from '@/services'
import { useAuthStore } from '@/stores/auth'
import type {
  ConversationImportPreview,
  ConversationColumnMap,
  ConversationInspectResult,
} from '@/services/datasets'
import ConversationView from '@/components/ConversationView'
import ConversationEditor from '@/components/ConversationEditor'
import type { TestCase } from '@/types'
import { formatApiError, toToastMessage } from '@/lib/errors'

// 多轮对话样例：input_messages 含多条消息，或带 conversation_goal / turn_expectations。
// 单轮老样例（仅 1 条 user 消息且无会话级字段）在此页过滤掉，避免与备选数据集页职责重叠。
function isConversation(c: TestCase): boolean {
  if (c.conversation_goal) return true
  if (c.turn_expectations && c.turn_expectations.length > 0) return true
  return (c.input_messages?.length ?? 0) > 1
}

// 拍平多行布局的字段映射项：语义字段 key + 展示标签 + 说明。用户在「字段映射」
// 步为每项指定源列（下拉），覆盖别名自动识别。仅 question 为必填。
const IMPORT_FIELD_DEFS: { key: keyof ConversationColumnMap; label: string; hint: string; required?: boolean }[] = [
  { key: 'question', label: '用户问句', hint: '每行的用户输入（必填）', required: true },
  { key: 'answer', label: '助手回复', hint: '智能体实际回复，存为 assistant 消息' },
  { key: 'expected_output', label: '期望答案', hint: '标准/参考答案，写入该轮期望输出' },
  { key: 'criteria', label: '评分点', hint: '检查点/要点，写入该轮评分标准' },
  { key: 'conversation_id', label: '会话 ID', hint: '同值的多行聚合成一段对话（如 session_id）' },
  { key: 'turn_no', label: '轮次序号', hint: '同一会话内的排序依据' },
  { key: 'goal', label: '对话目标', hint: '会话级目标/场景' },
  { key: 'name', label: '对话名称', hint: '样例名，缺省用会话 ID' },
]

function emptyCase(): TestCase {
  return {
    name: '',
    description: '',
    source: 'manual',
    input_messages: [{ role: 'user', content: '' }],
    conversation_goal: '',
    turn_expectations: [],
    category: '',
  }
}

export default function ConversationDatasetDetailPage() {
  const { name = '' } = useParams<{ name: string }>()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const toast = useToast()
  // 内部角色（admin | user）均可写：新建/编辑/导入/删样例/类别管理全放开，用 canWrite。
  // 唯一例外「删除整个数据集」（本页底部按钮）仍限 admin，单独用 isAdmin gate。
  const canWrite = useAuthStore((s) => s.canWrite)()
  const isAdmin = useAuthStore((s) => s.isAdmin)()
  const reactId = useId()
  const importFileId = `${reactId}-conv-import-file`

  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  // 类别筛选（空串=全部）。经后端 category query 过滤，覆盖全量而非仅当前页。
  const [categoryFilter, setCategoryFilter] = useState('')
  const [showAddCategory, setShowAddCategory] = useState(false)
  const [newCategoryName, setNewCategoryName] = useState('')
  const [editing, setEditing] = useState<TestCase | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [viewing, setViewing] = useState<TestCase | null>(null)
  const [showImport, setShowImport] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  // 批量选择（example_id 集合）。
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // 三步式导入：选文件 → 字段映射（拍平多行布局才需要）→ 解析预览 → 确认导入。
  // step 驱动弹窗内容：'file' 选文件、'map' 映射列、'preview' 看解析结果。
  const [importStep, setImportStep] = useState<'file' | 'map' | 'preview'>('file')
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importInspect, setImportInspect] = useState<ConversationInspectResult | null>(null)
  const [columnMap, setColumnMap] = useState<ConversationColumnMap>({})
  const [importPreview, setImportPreview] = useState<ConversationImportPreview | null>(null)
  // 导入时为整批样例统一指定的类别（空串=不指定）。
  const [importCategory, setImportCategory] = useState('')

  const pageSize = 20

  const { data: dataset, isLoading: datasetLoading } = useQuery({
    queryKey: ['dataset', name],
    queryFn: () => datasetsApi.get(name).then(r => r.data),
    enabled: !!name,
  })

  const { data: casesData, isLoading } = useQuery({
    queryKey: ['conv-cases', name, page, search, categoryFilter],
    queryFn: () => datasetsApi.listCasesPaginated(name, {
      page, page_size: pageSize, search: search || undefined,
      category: categoryFilter || undefined,
    }).then(r => r.data),
    enabled: !!name,
  })

  // 受管类别列表（实体存 Postgres，对齐基准测试集）。下拉筛选 + 编辑 select 共用。
  const { data: categories } = useQuery({
    queryKey: ['conv-categories', name],
    queryFn: () => datasetsApi.listConvCategories(name).then(r => r.data),
    enabled: !!name,
  })

  const saveMutation = useMutation({
    mutationFn: (c: TestCase) =>
      isNew
        ? datasetsApi.addCases(name, { cases: [c] })
        : datasetsApi.updateCase(c.id!, c),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
      queryClient.invalidateQueries({ queryKey: ['conv-categories', name] })
      setEditing(null)
      toast.success(isNew ? '已添加对话样例' : '已保存')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '保存失败'),
  })

  // 三步式导入第一步：inspect 文件结构（列头 + 样例值 + 自动建议映射）。
  // is_structured=true（行内已带对话数组，布局 A/B）时无需列映射，直接跳预览。
  const inspectMutation = useMutation({
    mutationFn: (file: File) =>
      datasetsApi.inspectConversationFile(name, file).then(r => r.data),
    onSuccess: (data, file) => {
      setImportInspect(data)
      if (data.is_structured) {
        // 结构自解释，跳过映射步直接预览。
        previewMutation.mutate(file)
      } else {
        // 用自动建议初始化映射，进映射步让用户确认/纠正。
        setColumnMap(data.suggested || {})
        setImportStep('map')
      }
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '解析文件失败'),
  })

  // 第二步（或结构化文件的第一步）：预览解析结果（不写库），带列映射。
  const previewMutation = useMutation({
    mutationFn: (file: File) =>
      datasetsApi.previewConversations(name, file, {
        category: importCategory || undefined,
        columnMap: Object.keys(columnMap).length > 0 ? columnMap : undefined,
      }).then(r => r.data),
    onSuccess: (data) => { setImportPreview(data); setImportStep('preview') },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '预览失败'),
  })

  // 第二步：确认导入（按名 upsert，重复样例按最新导入更新字段）。整批可统一指定类别。
  const importMutation = useMutation({
    mutationFn: (file: File) =>
      datasetsApi.importConversations(name, file, {
        category: importCategory || undefined,
        columnMap: Object.keys(columnMap).length > 0 ? columnMap : undefined,
      }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
      queryClient.invalidateQueries({ queryKey: ['conv-categories', name] })
      closeImport()
      const d = res.data
      const parts: string[] = []
      if (d.updated) parts.push(`更新 ${d.updated}`)
      if (d.skipped) parts.push(`跳过 ${d.skipped} 行`)
      const suffix = parts.length ? `（${parts.join(' / ')}）` : ''
      // 保留「已导入 N 条对话样例」前缀（e2e 断言依赖）。N = 新增 + 更新。
      toast.success(`已导入 ${d.added + d.updated} 条对话样例${suffix}`, '导入完成')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '导入失败'),
  })

  // 删除整个对话数据集（软删，复用通用端点）。删除后回列表页。
  const deleteDsMutation = useMutation({
    mutationFn: () => datasetsApi.delete(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['datasets', 'conversation'] })
      toast.success('数据集已删除')
      navigate('/conversations')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '删除失败'),
  })

  // 批量删除选中样例。
  const batchDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => datasetsApi.batchDeleteCases(ids),
    onSuccess: (_res, ids) => {
      queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
      queryClient.invalidateQueries({ queryKey: ['conv-categories', name] })
      setSelectedIds(new Set())
      toast.success(`已删除 ${ids.length} 条样例`)
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '删除失败'),
  })

  // ── 受管类别 CRUD（对齐基准测试集）──
  const addCategoryMutation = useMutation({
    mutationFn: (cat: string) => datasetsApi.createCategory(name, { name: cat }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conv-categories', name] })
      setShowAddCategory(false)
      setNewCategoryName('')
      toast.success('类别已新建')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '新建类别失败'),
  })

  const renameCategoryMutation = useMutation({
    mutationFn: (args: { id: string; name: string }) =>
      datasetsApi.updateCategory(args.id, { name: args.name }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['conv-categories', name] })
      // 重命名会把样例的 metadata.category 批量同步成新名，需刷新样例列表。
      queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
      const synced = res.data?.synced_cases
      toast.success(synced ? `类别已重命名，同步 ${synced} 条样例` : '类别已重命名')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '重命名失败'),
  })

  const deleteCategoryMutation = useMutation({
    mutationFn: (id: string) => datasetsApi.deleteCategory(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conv-categories', name] })
      if (categoryFilter) { setCategoryFilter(''); setPage(1) }
      toast.success('类别已删除')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '删除类别失败'),
  })

  function closeImport() {
    setShowImport(false)
    setImportStep('file')
    setImportFile(null)
    setImportInspect(null)
    setColumnMap({})
    setImportPreview(null)
    setImportCategory('')
    if (fileRef.current) fileRef.current.value = ''
  }

  function toggleSelect(id: string) {
    setSelectedIds(prev => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }

  // LangSmith case 列表无法按类型服务端过滤，这里前端筛多轮样例。
  const allCases = casesData?.items ?? []
  const cases = allCases.filter(isConversation)
  const total = casesData?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  function openNew() {
    setIsNew(true)
    setEditing(emptyCase())
  }
  function openEdit(c: TestCase) {
    setIsNew(false)
    setEditing(c)
  }

  if (datasetLoading) {
    return (
      <div>
        <div className="skeleton h-5 w-48 rounded mb-4" />
        <div className="skeleton h-3 w-32 rounded mb-6" />
      </div>
    )
  }

  return (
    <div>
      <Link to="/conversations" className="back-link mb-2">
        ← 返回
      </Link>
      <header className="mb-6">
        <div className="page-eyebrow">多轮对话集</div>
        <h1 className="page-title">{dataset?.name || name}</h1>
        <p className="page-subtitle">{dataset?.description || '构建与管理多轮对话评估样例，固定 thread_id 逐轮调用 agent'}</p>
      </header>

      <div className="toolbar">
        <input
          type="text"
          placeholder="搜索名称/描述…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          className="input-sm w-[240px]"
        />
        <select
          value={categoryFilter}
          onChange={e => { setCategoryFilter(e.target.value); setPage(1) }}
          className="input-sm w-[160px]"
        >
          <option value="">全部类别</option>
          {(categories ?? []).map(c => <option key={c.id} value={c.name}>{c.name}</option>)}
        </select>
        {canWrite && categoryFilter && (() => {
          const cat = (categories ?? []).find(c => c.name === categoryFilter)
          if (!cat) return null
          return (
            <>
              <button
                className="text-action text-[12px]"
                onClick={() => {
                  const next = window.prompt('重命名类别', cat.name)?.trim()
                  if (next && next !== cat.name) renameCategoryMutation.mutate({ id: cat.id, name: next })
                }}
              >重命名</button>
              <button
                className="text-action-danger text-[12px]"
                onClick={async () => {
                  const ok = await confirm({
                    title: '删除类别',
                    description: '确定删除该类别？仅当类别下无样例时可删除。',
                    confirmText: '删除', danger: true,
                  })
                  if (ok) deleteCategoryMutation.mutate(cat.id)
                }}
              >删除类别</button>
            </>
          )
        })()}
        {canWrite && (
          <button className="text-action text-[12px]" onClick={() => setShowAddCategory(true)}>+ 类别</button>
        )}
        <div className="flex-1" />
        {canWrite && selectedIds.size > 0 && (
          <Button
            variant="danger"
            size="sm"
            loading={batchDeleteMutation.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: '批量删除样例',
                description: `确定删除选中的 ${selectedIds.size} 条样例？此操作不可撤销。`,
                confirmText: '删除', danger: true,
              })
              if (ok) batchDeleteMutation.mutate(Array.from(selectedIds))
            }}
          >
            批量删除 ({selectedIds.size})
          </Button>
        )}
        <ExportMenu
          onExport={async (format) => {
            try {
              await datasetsApi.exportConversations(name, format)
            } catch (e) {
              toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '导出失败' })))
            }
          }}
        />
        {canWrite && (
          <>
            <Button variant="secondary" size="sm" onClick={() => setShowImport(true)}>
              导入对话
            </Button>
            <Button variant="primary" size="sm" onClick={openNew}>
              新建对话样例
            </Button>
          </>
        )}
        {/* 删除整个数据集：唯一保留 admin 专属的写操作（内部 user 不可）。 */}
        {isAdmin && (
          <Button
            variant="secondary"
            size="sm"
            loading={deleteDsMutation.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: '删除数据集',
                description: `确定删除对话数据集「${name}」及其全部样例？此操作不可撤销。`,
                confirmText: '删除', danger: true,
              })
              if (ok) deleteDsMutation.mutate()
            }}
          >
            删除数据集
          </Button>
        )}
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              {canWrite && (
                <th className="w-10 text-center">
                  <input
                    type="checkbox"
                    checked={cases.length > 0 && cases.every(c => c.id && selectedIds.has(c.id))}
                    onChange={() => {
                      const ids = cases.map(c => c.id).filter(Boolean) as string[]
                      const allSelected = ids.length > 0 && ids.every(id => selectedIds.has(id))
                      setSelectedIds(allSelected ? new Set() : new Set(ids))
                    }}
                    className="accent-accent"
                  />
                </th>
              )}
              <th>名称</th>
              <th className="w-20 text-center">轮数</th>
              <th>会话目标</th>
              {!categoryFilter && <th className="w-32">类别</th>}
              <th className="w-24 text-center">逐轮期望</th>
              <th className="w-28 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {cases.map(c => {
              const userTurns = (c.input_messages ?? []).filter(m => m.role === 'user').length
              return (
                <tr key={c.id} className="group">
                  {canWrite && (
                    <td className="text-center">
                      <input
                        type="checkbox"
                        checked={!!c.id && selectedIds.has(c.id)}
                        onChange={() => c.id && toggleSelect(c.id)}
                        className="accent-accent"
                      />
                    </td>
                  )}
                  <td className="max-w-[260px]">
                    <div className="truncate font-medium">{c.name || '(未命名)'}</div>
                    {c.description && <div className="text-[11px] text-text-tertiary truncate">{c.description}</div>}
                  </td>
                  <td className="text-center">
                    <span className="badge badge-info">{userTurns} 轮</span>
                  </td>
                  <td className="max-w-[320px]">
                    <div className="truncate text-text-secondary">{c.conversation_goal || '—'}</div>
                  </td>
                  {!categoryFilter && (
                    <td className="max-w-[160px]">
                      {c.category ? (
                        <span className="badge badge-neutral">{c.category}</span>
                      ) : (
                        <span className="text-text-tertiary">—</span>
                      )}
                    </td>
                  )}
                  <td className="text-center text-text-secondary">
                    {c.turn_expectations?.length || 0}
                  </td>
                  <td className="text-right">
                    <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onClick={() => setViewing(c)} className="text-action">查看</button>
                      {canWrite && <button onClick={() => openEdit(c)} className="text-action">编辑</button>}
                      {canWrite && (
                      <button
                        onClick={async () => {
                          const ok = await confirm({
                            title: '删除对话样例', description: '确定删除该样例？此操作不可撤销。',
                            confirmText: '删除', danger: true,
                          })
                          if (!ok || !c.id) return
                          try {
                            await datasetsApi.deleteCase(c.id)
                            queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
      queryClient.invalidateQueries({ queryKey: ['conv-categories', name] })
                            toast.success('样例已删除')
                          } catch (err) {
                            toast.error(toToastMessage(formatApiError(err)), '删除失败')
                          }
                        }}
                        className="text-action-danger"
                      >删除</button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {cases.length === 0 && !isLoading && (
          <div className="empty-state">
            {canWrite ? '暂无多轮对话样例。点击「新建对话样例」或「导入对话」创建。' : '暂无多轮对话样例。'}
            {allCases.length > 0 && '（本页有单轮样例,请到「备选数据集」管理）'}
          </div>
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

      {/* 查看对话 */}
      <Dialog
        open={!!viewing}
        onClose={() => setViewing(null)}
        title={viewing?.name || '对话样例'}
        width={680}
        footer={<Button variant="secondary" size="md" onClick={() => setViewing(null)}>关闭</Button>}
      >
        {viewing && <ConversationView testCase={viewing} />}
      </Dialog>

      {/* 新建 / 编辑对话 */}
      <Dialog
        open={!!editing}
        onClose={() => setEditing(null)}
        title={isNew ? '新建对话样例' : '编辑对话样例'}
        width={760}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setEditing(null)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              loading={saveMutation.isPending}
              disabled={!editing?.name?.trim() || !(editing?.input_messages?.some(m => m.content.trim()))}
              onClick={() => editing && saveMutation.mutate(editing)}
            >
              {isNew ? '添加' : '保存'}
            </Button>
          </>
        }
      >
        {editing && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="field-label">名称 <span className="text-action-danger">*</span></label>
                <input
                  value={editing.name}
                  onChange={e => setEditing({ ...editing, name: e.target.value })}
                  placeholder="如：退货咨询多轮对话"
                  className="input"
                />
              </div>
              <div>
                <label className="field-label">描述（可选）</label>
                <input
                  value={editing.description ?? ''}
                  onChange={e => setEditing({ ...editing, description: e.target.value })}
                  placeholder="简述此对话场景"
                  className="input"
                />
              </div>
            </div>
            <div>
              <label className="field-label">类别（可选）</label>
              <select
                value={editing.category || ''}
                onChange={e => setEditing({ ...editing, category: e.target.value || null })}
                className="input"
              >
                <option value="">不指定类别</option>
                {(categories ?? []).map(c => <option key={c.id} value={c.name}>{c.name}</option>)}
              </select>
              {(categories ?? []).length === 0 && (
                <p className="text-[11px] text-text-tertiary mt-1">
                  还没有类别。先用工具栏「+ 类别」新建，再回来归类。
                </p>
              )}
            </div>
            <ConversationEditor value={editing} onChange={setEditing} />
          </div>
        )}
      </Dialog>

      {/* 导入对话文件（两步式：选文件 → 解析结果预览 → 确认导入） */}
      <Dialog
        open={showImport}
        onClose={closeImport}
        title="导入多轮对话"
        width={640}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={closeImport}>取消</Button>
            {importStep === 'file' && (
              <Button
                variant="primary"
                size="md"
                loading={inspectMutation.isPending}
                onClick={() => {
                  const f = fileRef.current?.files?.[0]
                  if (f) { setImportFile(f); inspectMutation.mutate(f) }
                }}
              >
                下一步：识别字段
              </Button>
            )}
            {importStep === 'map' && (
              <Button
                variant="primary"
                size="md"
                disabled={!importFile || !columnMap.question}
                loading={previewMutation.isPending}
                onClick={() => { if (importFile) previewMutation.mutate(importFile) }}
              >
                下一步：解析预览
              </Button>
            )}
            {importStep === 'preview' && importPreview && (
              <Button
                variant="primary"
                size="md"
                disabled={!importFile || importPreview.total === 0}
                loading={importMutation.isPending}
                onClick={() => { if (importFile) importMutation.mutate(importFile) }}
              >
                确认导入（{importPreview.total} 段）
              </Button>
            )}
          </>
        }
      >
        {importStep === 'file' && (
          <div className="space-y-4">
            <p className="text-[12px] text-text-secondary">
              支持 CSV / JSON / JSONL / Excel。两种布局都支持：①「消息数组」列
              （messages / conversation / 对话，单元格放 JSON 数组）；②「拍平多行」——
              每行一个 turn，同一 conversation_id（或 session_id）的多行聚合成一段多轮对话。
              下一步可手动指定问句 / 期望答案 / 评分点等字段对应哪一列。同名样例按最新导入更新（去重）。
            </p>
            <div>
              <label htmlFor={importFileId} className="field-label">选择文件</label>
              <input id={importFileId} ref={fileRef} type="file" accept=".csv,.json,.jsonl,.xlsx,.xls" className="text-[12px]" />
            </div>
            <div>
              <label className="field-label">统一指定类别（可选）</label>
              <select
                value={importCategory}
                onChange={e => setImportCategory(e.target.value)}
                className="input"
              >
                <option value="">不指定类别</option>
                {(categories ?? []).map(c => <option key={c.id} value={c.name}>{c.name}</option>)}
              </select>
              <p className="text-[11px] text-text-tertiary mt-1">
                选中后，本次导入的全部样例统一归到该类别。需先在工具栏「+ 类别」建好类别。
              </p>
            </div>
          </div>
        )}

        {importStep === 'map' && importInspect && (
          <div className="space-y-4">
            <p className="text-[12px] text-text-secondary">
              为每个字段指定源文件的列（已按列名自动匹配，可手动纠正）。
              <span className="text-action-danger">问句列必填</span>；
              「期望答案」写入每轮的标准答案，「评分点」写入每轮 criteria，二者独立可选。
            </p>
            <div className="space-y-2">
              {IMPORT_FIELD_DEFS.map(fd => (
                <div key={fd.key} className="grid grid-cols-[120px_1fr] items-center gap-2">
                  <label className="text-[12px]">
                    {fd.label}
                    {fd.required && <span className="text-action-danger"> *</span>}
                  </label>
                  <select
                    className="input text-[12px]"
                    value={columnMap[fd.key] ?? ''}
                    onChange={e => setColumnMap(prev => {
                      const next = { ...prev }
                      if (e.target.value) next[fd.key] = e.target.value
                      else delete next[fd.key]
                      return next
                    })}
                  >
                    <option value="">（不映射）</option>
                    {importInspect.columns.map(col => (
                      <option key={col} value={col}>
                        {col}
                        {importInspect.samples[col]?.length
                          ? ` — 例: ${importInspect.samples[col][0].slice(0, 30)}`
                          : ''}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
            <div>
              <div className="text-[12px] mb-1 text-text-secondary">列预览（前 3 行样例）</div>
              <div className="border border-border rounded-md overflow-auto max-h-[220px]">
                <table className="w-full text-[11px]">
                  <thead className="bg-fill/5 sticky top-0">
                    <tr>
                      {importInspect.columns.map(col => {
                        const role = (Object.keys(columnMap) as (keyof ConversationColumnMap)[])
                          .find(k => columnMap[k] === col)
                        const label = IMPORT_FIELD_DEFS.find(fd => fd.key === role)?.label
                        const cls = role === 'question' ? 'text-action-primary font-medium'
                          : role === 'expected_output' ? 'text-action-success font-medium'
                          : role ? 'text-text-primary font-medium' : 'text-text-tertiary'
                        return (
                          <th key={col} className={`text-left px-2 py-1 whitespace-nowrap ${cls}`}>
                            {col}{label ? ` · ${label}` : ''}
                          </th>
                        )
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {[0, 1, 2].map(rowIdx => (
                      <tr key={rowIdx} className="border-t border-separator">
                        {importInspect.columns.map(col => (
                          <td key={col} className="px-2 py-1 align-top max-w-[220px] truncate text-text-secondary">
                            {importInspect.samples[col]?.[rowIdx] ?? ''}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            {!columnMap.question && (
              <p className="text-[11px] text-action-danger">请先指定「问句」列才能继续。</p>
            )}
            <button
              type="button"
              onClick={() => { setImportStep('file'); setImportInspect(null); setColumnMap({}) }}
              className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
            >
              ‹ 重新选择文件
            </button>
          </div>
        )}

        {importStep === 'preview' && importPreview && (
          <div className="space-y-4">
            <div className="flex gap-2 flex-wrap text-[12px]">
              <span className="badge badge-info">共 {importPreview.total} 段</span>
              <span className="badge badge-positive">新增 {importPreview.new}</span>
              <span className="badge badge-warning">更新 {importPreview.updated}</span>
              {importPreview.skipped > 0 && (
                <span className="badge badge-neutral">跳过 {importPreview.skipped} 行</span>
              )}
            </div>
            {importPreview.total === 0 ? (
              <p className="text-[12px] text-action-danger">
                未解析到任何多轮对话样例，请返回上一步检查字段映射或换一个文件。
              </p>
            ) : (
              <div>
                <div className="field-label mb-1">解析结果预览（前 {importPreview.samples.length} 段）</div>
                <div className="border border-border rounded-md overflow-auto max-h-[300px]">
                  <table className="w-full text-[11px]">
                    <thead className="bg-fill/5 sticky top-0">
                      <tr>
                        <th className="text-left px-2 py-1 font-medium">名称</th>
                        <th className="text-center px-2 py-1 font-medium w-14">轮数</th>
                        <th className="text-left px-2 py-1 font-medium">首句</th>
                        <th className="text-center px-2 py-1 font-medium w-14">要点</th>
                        <th className="text-center px-2 py-1 font-medium w-16">期望答案</th>
                        <th className="text-center px-2 py-1 font-medium w-16">动作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {importPreview.samples.map((s, i) => (
                        <tr key={i} className="border-t border-separator">
                          <td className="px-2 py-1 align-top max-w-[160px] truncate">{s.name}</td>
                          <td className="px-2 py-1 text-center align-top">{s.turns}</td>
                          <td className="px-2 py-1 align-top max-w-[200px] truncate text-text-secondary">{s.first_user || '—'}</td>
                          <td className="px-2 py-1 text-center align-top text-text-secondary">{s.checkpoints}</td>
                          <td className="px-2 py-1 text-center align-top text-text-secondary">{s.expected_answers}</td>
                          <td className="px-2 py-1 text-center align-top">
                            <span className={s.action === 'update' ? 'badge badge-warning' : 'badge badge-positive'}>
                              {s.action === 'update' ? '更新' : '新增'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            <button
              type="button"
              onClick={() => {
                // 回到映射步（结构化文件无映射步，回到选文件步）。
                if (importInspect && !importInspect.is_structured) {
                  setImportStep('map'); setImportPreview(null)
                } else {
                  setImportStep('file'); setImportPreview(null); setImportInspect(null)
                  setImportFile(null); if (fileRef.current) fileRef.current.value = ''
                }
              }}
              className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
            >
              ‹ 上一步
            </button>
          </div>
        )}
      </Dialog>

      {/* 新增类别 */}
      <Dialog
        open={showAddCategory}
        onClose={() => { setShowAddCategory(false); setNewCategoryName('') }}
        title="新增类别"
        width={420}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => { setShowAddCategory(false); setNewCategoryName('') }}>取消</Button>
            <Button
              variant="primary"
              size="md"
              loading={addCategoryMutation.isPending}
              disabled={!newCategoryName.trim()}
              onClick={() => addCategoryMutation.mutate(newCategoryName.trim())}
            >
              创建
            </Button>
          </>
        }
      >
        <div>
          <label className="field-label">类别名称</label>
          <input
            value={newCategoryName}
            onChange={e => setNewCategoryName(e.target.value)}
            placeholder="如：故障诊断"
            className="input"
            autoFocus
          />
        </div>
      </Dialog>
    </div>
  )
}
