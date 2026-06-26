import { useId, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, ExportMenu, useConfirm, useToast } from '@/components/ui'
import { datasetsApi } from '@/services'
import { useAuthStore } from '@/stores/auth'
import type { ConversationImportPreview } from '@/services/datasets'
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

function emptyCase(): TestCase {
  return {
    name: '',
    description: '',
    source: 'manual',
    input_messages: [{ role: 'user', content: '' }],
    conversation_goal: '',
    turn_expectations: [],
  }
}

export default function ConversationDatasetDetailPage() {
  const { name = '' } = useParams<{ name: string }>()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const toast = useToast()
  const isAdmin = useAuthStore((s) => s.isAdmin)()
  const reactId = useId()
  const importFileId = `${reactId}-conv-import-file`

  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<TestCase | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [viewing, setViewing] = useState<TestCase | null>(null)
  const [showImport, setShowImport] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  // 批量选择（example_id 集合）。
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // 两步式导入：选文件 → 预览（解析结果 + 新增/更新比对）→ 确认导入。
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importPreview, setImportPreview] = useState<ConversationImportPreview | null>(null)

  const pageSize = 20

  const { data: dataset, isLoading: datasetLoading } = useQuery({
    queryKey: ['dataset', name],
    queryFn: () => datasetsApi.get(name).then(r => r.data),
    enabled: !!name,
  })

  const { data: casesData, isLoading } = useQuery({
    queryKey: ['conv-cases', name, page, search],
    queryFn: () => datasetsApi.listCasesPaginated(name, {
      page, page_size: pageSize, search: search || undefined,
    }).then(r => r.data),
    enabled: !!name,
  })

  const saveMutation = useMutation({
    mutationFn: (c: TestCase) =>
      isNew
        ? datasetsApi.addCases(name, { cases: [c] })
        : datasetsApi.updateCase(c.id!, c),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
      setEditing(null)
      toast.success(isNew ? '已添加对话样例' : '已保存')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '保存失败'),
  })

  // 两步式导入第一步：预览解析结果（不写库）。
  const previewMutation = useMutation({
    mutationFn: (file: File) => datasetsApi.previewConversations(name, file).then(r => r.data),
    onSuccess: (data) => setImportPreview(data),
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '预览失败'),
  })

  // 第二步：确认导入（按名 upsert，重复样例按最新导入更新字段）。
  const importMutation = useMutation({
    mutationFn: (file: File) => datasetsApi.importConversations(name, file),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
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
      setSelectedIds(new Set())
      toast.success(`已删除 ${ids.length} 条样例`)
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '删除失败'),
  })

  function closeImport() {
    setShowImport(false)
    setImportFile(null)
    setImportPreview(null)
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
        <div className="flex-1" />
        {isAdmin && selectedIds.size > 0 && (
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
        {isAdmin && (
          <>
            <Button variant="secondary" size="sm" onClick={() => setShowImport(true)}>
              导入对话
            </Button>
            <Button variant="primary" size="sm" onClick={openNew}>
              新建对话样例
            </Button>
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
          </>
        )}
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              {isAdmin && (
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
              <th className="w-24 text-center">逐轮期望</th>
              <th className="w-28 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {cases.map(c => {
              const userTurns = (c.input_messages ?? []).filter(m => m.role === 'user').length
              return (
                <tr key={c.id} className="group">
                  {isAdmin && (
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
                  <td className="text-center text-text-secondary">
                    {c.turn_expectations?.length || 0}
                  </td>
                  <td className="text-right">
                    <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onClick={() => setViewing(c)} className="text-action">查看</button>
                      {isAdmin && <button onClick={() => openEdit(c)} className="text-action">编辑</button>}
                      {isAdmin && (
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
            {isAdmin ? '暂无多轮对话样例。点击「新建对话样例」或「导入对话」创建。' : '暂无多轮对话样例。'}
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
            {!importPreview ? (
              <Button
                variant="primary"
                size="md"
                loading={previewMutation.isPending}
                onClick={() => {
                  const f = fileRef.current?.files?.[0]
                  if (f) { setImportFile(f); previewMutation.mutate(f) }
                }}
              >
                下一步：解析预览
              </Button>
            ) : (
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
        {!importPreview ? (
          <div className="space-y-4">
            <p className="text-[12px] text-text-secondary">
              支持 CSV / JSON / JSONL / Excel。每行（或每个 JSON 对象）= 一个完整对话样例，
              消息列放消息数组（JSON/JSONL 天然是数组，CSV/Excel 单元格放 JSON 字符串）。
              自动识别 messages / conversation / 对话 等列名，可选 conversation_goal / 对话目标 列。
              同名样例会按最新导入更新（去重），不会重复新增。
            </p>
            <div>
              <label htmlFor={importFileId} className="field-label">选择文件</label>
              <input id={importFileId} ref={fileRef} type="file" accept=".csv,.json,.jsonl,.xlsx,.xls" className="text-[12px]" />
            </div>
          </div>
        ) : (
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
                未解析到任何多轮对话样例，请检查文件格式或换一个文件。
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
              onClick={() => { setImportPreview(null); setImportFile(null); if (fileRef.current) fileRef.current.value = '' }}
              className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
            >
              ‹ 重新选择文件
            </button>
          </div>
        )}
      </Dialog>
    </div>
  )
}
