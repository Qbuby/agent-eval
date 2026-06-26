import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Button, useConfirm, useToast } from '@/components/ui'
import { datasetsApi } from '@/services'
import { useAuthStore } from '@/stores/auth'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { CreateDatasetRequest } from '@/types'

// 多轮对话集列表页：卡片网格列出各 conversation 数据集（与备选数据集 /datasets
// 同一形态）。点击卡片进 /conversations/:name 详情页管理样例。
export default function ConversationDatasetPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const toast = useToast()
  // 内部普通 user 只读：写操作（建/删数据集）仅 admin。删除端点后端亦 admin-only 兜底。
  const isAdmin = useAuthStore((s) => s.isAdmin())
  const [deletingName, setDeletingName] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState<CreateDatasetRequest>({ name: '', description: '', dataset_type: 'conversation' })
  const [search, setSearch] = useState('')

  // 多轮对话集页只看 conversation 类型，与备选数据集隔离。
  const { data: datasets, isLoading, isFetching } = useQuery({
    queryKey: ['datasets', 'conversation'],
    queryFn: () => datasetsApi.list({ type: 'conversation' }).then((r) => r.data),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  })

  const createMutation = useMutation({
    mutationFn: (data: CreateDatasetRequest) => datasetsApi.create(data),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['datasets', 'conversation'] })
      setShowCreate(false)
      const created = form.name
      setForm({ name: '', description: '', dataset_type: 'conversation' })
      toast.success(`已创建对话数据集「${res.data.name || created}」`)
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e)), '创建失败'),
  })

  const deleteMutation = useMutation({
    mutationFn: (name: string) => datasetsApi.delete(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['datasets', 'conversation'] }),
  })

  const filtered = datasets?.filter(d =>
    !search.trim() ||
    d.name.toLowerCase().includes(search.toLowerCase()) ||
    (d.description ?? '').toLowerCase().includes(search.toLowerCase())
  )

  if (isLoading) {
    return (
      <div>
        <header className="mb-6">
          <div className="page-eyebrow">数据</div>
          <h1 className="page-title">多轮对话集</h1>
          <p className="page-subtitle">构建与管理多轮对话评估样例，固定 thread_id 逐轮调用 agent</p>
        </header>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="card p-5">
              <div className="skeleton h-4 w-32 rounded mb-3" />
              <div className="skeleton h-3 w-20 rounded mb-2" />
              <div className="skeleton h-3 w-24 rounded" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">数据</div>
        <h1 className="page-title">多轮对话集</h1>
        <p className="page-subtitle">构建与管理多轮对话评估样例，固定 thread_id 逐轮调用 agent</p>
      </header>

      <div className="toolbar">
        <input
          type="text"
          placeholder="搜索数据集"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input-sm flex-1 max-w-[280px]"
        />
        {isFetching && !isLoading && (
          <span className="text-[10px] text-text-tertiary">刷新中…</span>
        )}
        <div className="flex-1" />
        <Button onClick={() => setShowCreate(true)} variant="primary" size="md">
          新建数据集
        </Button>
      </div>

      {showCreate && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            createMutation.mutate(form)
          }}
          className="card p-5 mb-6 space-y-4 animate-fade-in"
        >
          <div>
            <label className="field-label">名称</label>
            <input
              placeholder="对话数据集名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              className="input max-w-[320px]"
            />
          </div>
          <div>
            <label className="field-label">描述</label>
            <input
              placeholder="描述（可选）"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="input max-w-[320px]"
            />
          </div>
          <div className="flex gap-2 pt-1">
            <Button type="submit" variant="primary" size="md" loading={createMutation.isPending} disabled={!form.name.trim()}>创建</Button>
            <Button type="button" variant="secondary" size="md" onClick={() => setShowCreate(false)}>
              取消
            </Button>
          </div>
        </form>
      )}

      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
        {filtered?.map((ds, i) => (
          <div
            key={ds.id}
            role="link"
            tabIndex={0}
            onClick={() => navigate(`/conversations/${encodeURIComponent(ds.name)}`)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                navigate(`/conversations/${encodeURIComponent(ds.name)}`)
              }
            }}
            className="card p-5 cursor-pointer animate-fade-in transition-[transform,box-shadow,border-color] duration-200 ease-standard hover:-translate-y-0.5 hover:shadow-md hover:border-border-strong focus:outline-none focus-visible:shadow-focus"
            style={{ animationDelay: `${i * 40}ms` }}
          >
            <div className="flex justify-between items-start mb-3 gap-2">
              <span className="text-[15px] font-display font-semibold tracking-[-0.2px] text-text-primary truncate">
                {ds.name}
              </span>
              <span className="badge badge-positive shrink-0">启用中</span>
            </div>
            <div className="space-y-1.5 mb-4">
              <div className="flex justify-between items-center">
                <span className="text-[11px] text-text-tertiary">样例数</span>
                <span className="text-[12px] tabular-nums font-medium text-text-primary">
                  {ds.example_count}
                </span>
              </div>
              <div className="flex justify-between items-center gap-2">
                <span className="text-[11px] text-text-tertiary shrink-0">描述</span>
                <span className="text-[12px] text-text-secondary truncate">{ds.description || '—'}</span>
              </div>
            </div>
            {isAdmin && (
              <button
                onClick={async (e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  const ok = await confirm({
                    title: '删除数据集',
                    description: `确定删除对话数据集 "${ds.name}" 及其全部样例？此操作不可撤销。`,
                    confirmText: '删除',
                    danger: true,
                  })
                  if (!ok) return
                  setDeletingName(ds.name)
                  try {
                    await deleteMutation.mutateAsync(ds.name)
                    toast.success('数据集已删除')
                  } catch (err) {
                    const norm = formatApiError(err, { fallbackTitle: '删除失败' })
                    toast.error(toToastMessage(norm), '删除失败')
                  } finally {
                    setDeletingName(null)
                  }
                }}
                disabled={deletingName === ds.name}
                className="text-[11px] text-text-tertiary hover:text-negative active:opacity-80 transition-colors disabled:opacity-50"
              >
                {deletingName === ds.name ? '删除中…' : '删除'}
              </button>
            )}
          </div>
        ))}
      </div>

      {filtered?.length === 0 && (
        <div className="card border-dashed empty-state mt-6">
          <h3 className="text-[14px] font-medium text-text-primary mb-1">
            {search.trim() ? '没有匹配的数据集' : '暂无对话数据集'}
          </h3>
          <p className="text-[12px] text-text-tertiary max-w-[280px] mx-auto">
            {search.trim() ? '换个关键词试试' : '创建第一个对话数据集开始构建多轮样例'}
          </p>
        </div>
      )}
    </div>
  )
}
