import { useEffect, useId, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useConfirm, useToast } from '@/components/ui'
import { datasetsApi } from '@/services'
import ConversationView from '@/components/ConversationView'
import ConversationEditor from '@/components/ConversationEditor'
import type { TestCase, CreateDatasetRequest } from '@/types'
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

export default function ConversationDatasetPage() {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const reactId = useId()
  const importFileId = `${reactId}-conv-import-file`

  const [selectedDataset, setSelectedDataset] = useState('')
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<TestCase | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [viewing, setViewing] = useState<TestCase | null>(null)
  const [showImport, setShowImport] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  // 多轮对话集可建自己的独立数据集（dataset_type=conversation），与备选数据集隔离。
  const [showCreateDs, setShowCreateDs] = useState(false)
  const [dsForm, setDsForm] = useState<CreateDatasetRequest>({ name: '', description: '', dataset_type: 'conversation' })

  const pageSize = 20

  // 多轮对话集页只看 conversation 类型，与备选数据集隔离。
  const { data: datasets } = useQuery({
    queryKey: ['datasets', 'conversation'],
    queryFn: () => datasetsApi.list({ type: 'conversation' }).then(r => r.data),
  })

  // 首个数据集兜底选中（在 effect 里 setState，避免渲染期触发更新）
  useEffect(() => {
    if (!selectedDataset && datasets && datasets.length > 0) {
      setSelectedDataset(datasets[0].name)
    }
  }, [selectedDataset, datasets])

  const { data: casesData, isLoading } = useQuery({
    queryKey: ['conv-cases', selectedDataset, page, search],
    queryFn: () => datasetsApi.listCasesPaginated(selectedDataset, {
      page, page_size: pageSize, search: search || undefined,
    }).then(r => r.data),
    enabled: !!selectedDataset,
  })

  const saveMutation = useMutation({
    mutationFn: (c: TestCase) =>
      isNew
        ? datasetsApi.addCases(selectedDataset, { cases: [c] })
        : datasetsApi.updateCase(c.id!, c),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
      setEditing(null)
      toast.success(isNew ? '已添加对话样例' : '已保存')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '保存失败'),
  })

  const importMutation = useMutation({
    mutationFn: (file: File) => datasetsApi.importConversations(selectedDataset, file),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['conv-cases'] })
      setShowImport(false)
      if (fileRef.current) fileRef.current.value = ''
      const d = res.data
      const skip = d.skipped ? `，跳过 ${d.skipped} 行` : ''
      toast.success(`已导入 ${d.added} 条对话样例${skip}`, '导入完成')
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '导入失败'),
  })

  const createDsMutation = useMutation({
    mutationFn: (data: CreateDatasetRequest) => datasetsApi.create(data),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['datasets', 'conversation'] })
      setShowCreateDs(false)
      const created = dsForm.name
      setDsForm({ name: '', description: '', dataset_type: 'conversation' })
      setSelectedDataset(created)
      toast.success(`已创建对话数据集「${res.data.name}」`)
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '创建失败'),
  })

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

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">数据</div>
        <h1 className="page-title">多轮对话集</h1>
        <p className="page-subtitle">构建与管理多轮对话评估样例，固定 thread_id 逐轮调用 agent</p>
      </header>

      <div className="toolbar">
        <select
          value={selectedDataset}
          onChange={e => { setSelectedDataset(e.target.value); setPage(1) }}
          className="select-sm w-[220px]"
        >
          <option value="">选择数据集…</option>
          {(datasets ?? []).map(d => <option key={d.name} value={d.name}>{d.name}</option>)}
        </select>
        <input
          type="text"
          placeholder="搜索名称/描述…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          className="input-sm w-[240px]"
        />
        <div className="flex-1" />
        <Button variant="secondary" size="sm" onClick={() => setShowCreateDs(true)}>
          新建数据集
        </Button>
        <Button variant="secondary" size="sm" disabled={!selectedDataset} onClick={() => setShowImport(true)}>
          导入对话
        </Button>
        <Button variant="primary" size="sm" disabled={!selectedDataset} onClick={openNew}>
          新建对话样例
        </Button>
      </div>

      {!selectedDataset ? (
        <div className="empty-state">
          {(datasets ?? []).length === 0
            ? '还没有多轮对话数据集，点击「新建数据集」创建第一个'
            : '请先选择一个数据集'}
        </div>
      ) : (
        <>
          <div className="table-card">
            <table className="table-base">
              <thead>
                <tr>
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
                          <button onClick={() => openEdit(c)} className="text-action">编辑</button>
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
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            {cases.length === 0 && !isLoading && (
              <div className="empty-state">
                暂无多轮对话样例。点击「新建对话样例」或「导入对话」创建。
                {allCases.length > 0 && '（本页有单轮样例，请到「备选数据集」管理）'}
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
        </>
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

      {/* 导入对话文件 */}
      <Dialog
        open={showImport}
        onClose={() => { setShowImport(false); if (fileRef.current) fileRef.current.value = '' }}
        title="导入多轮对话"
        width={560}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => { setShowImport(false); if (fileRef.current) fileRef.current.value = '' }}>取消</Button>
            <Button
              variant="primary"
              size="md"
              loading={importMutation.isPending}
              onClick={() => { const f = fileRef.current?.files?.[0]; if (f) importMutation.mutate(f) }}
            >
              导入
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-[12px] text-text-secondary">
            支持 CSV / JSON / JSONL / Excel。每行（或每个 JSON 对象）= 一个完整对话样例，
            消息列放消息数组（JSON/JSONL 天然是数组，CSV/Excel 单元格放 JSON 字符串）。
            自动识别 messages / conversation / 对话 等列名，可选 conversation_goal / 对话目标 列。
          </p>
          <div>
            <label htmlFor={importFileId} className="field-label">选择文件</label>
            <input id={importFileId} ref={fileRef} type="file" accept=".csv,.json,.jsonl,.xlsx,.xls" className="text-[12px]" />
          </div>
        </div>
      </Dialog>

      {/* 新建多轮对话数据集（dataset_type=conversation，与备选数据集隔离） */}
      <Dialog
        open={showCreateDs}
        onClose={() => setShowCreateDs(false)}
        title="新建多轮对话数据集"
        width={480}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowCreateDs(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              loading={createDsMutation.isPending}
              disabled={!dsForm.name.trim()}
              onClick={() => createDsMutation.mutate(dsForm)}
            >
              创建
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="field-label">名称</label>
            <input
              value={dsForm.name}
              onChange={e => setDsForm({ ...dsForm, name: e.target.value })}
              placeholder="对话数据集名称"
              className="input"
            />
          </div>
          <div>
            <label className="field-label">描述（可选）</label>
            <input
              value={dsForm.description ?? ''}
              onChange={e => setDsForm({ ...dsForm, description: e.target.value })}
              placeholder="简述用途"
              className="input"
            />
          </div>
        </div>
      </Dialog>
    </div>
  )
}
