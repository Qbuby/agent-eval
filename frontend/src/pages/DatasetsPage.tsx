import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useConfirm, useToast } from '@/components/ui'
import { datasetsApi } from '@/services'
import type { CreateDatasetRequest } from '@/types'

export default function DatasetsPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const toast = useToast()
  const [deletingName, setDeletingName] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState<CreateDatasetRequest>({ name: '', description: '', source_project: '' })

  const { data: datasets, isLoading, isFetching } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => datasetsApi.list().then((r) => r.data),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  })

  const createMutation = useMutation({
    mutationFn: (data: CreateDatasetRequest) => datasetsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['datasets'] })
      setShowCreate(false)
      setForm({ name: '', description: '', source_project: '' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (name: string) => datasetsApi.delete(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['datasets'] }),
  })

  if (isLoading) {
    return (
      <div>
        <div className="skeleton h-6 w-48 rounded mb-6" />
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
          {[1,2,3].map(i => (
            <div key={i} className="bg-surface border border-border rounded-md p-5">
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
        <h1 className="text-xl font-semibold tracking-tight mb-1">备选数据集</h1>
        <p className="text-[13px] text-text-secondary">管理评测数据集、样本和质量指标</p>
      </header>

      <div className="flex gap-3 items-center mb-6">
        <input
          type="text"
          placeholder="搜索数据集..."
          className="flex-1 max-w-[280px] py-2 px-3 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 placeholder:text-text-tertiary transition-all duration-200"
        />
        {isFetching && !isLoading && (
          <span className="text-[10px] text-text-tertiary">刷新中…</span>
        )}
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-1.5 py-2 px-3.5 text-[11px] font-medium tracking-wide rounded-[6px] bg-accent text-white border border-accent cursor-pointer hover:opacity-90 hover:scale-[1.02] active:scale-[0.97] focus:outline-none focus:ring-2 focus:ring-accent/20 transition-all duration-200"
        >
          + 新建数据集
        </button>
      </div>

      {showCreate && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            createMutation.mutate(form)
          }}
          className="bg-surface border border-border rounded-md p-5 mb-6 space-y-3 animate-fade-in"
        >
          <div className="group">
            <label className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1 group-focus-within:text-accent transition-colors">名称</label>
            <input
              placeholder="数据集名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              className="w-full max-w-[280px] py-2 px-3 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            />
          </div>
          <div className="group">
            <label className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1 group-focus-within:text-accent transition-colors">描述</label>
            <input
              placeholder="描述（可选）"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="w-full max-w-[280px] py-2 px-3 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            />
          </div>
          <div className="group">
            <label className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1 group-focus-within:text-accent transition-colors">数据源（LangSmith Project）</label>
            <input
              placeholder="绑定后系统会自动从该 project 增量同步新样例"
              value={form.source_project}
              onChange={(e) => setForm({ ...form, source_project: e.target.value })}
              className="w-full max-w-[280px] py-2 px-3 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            />
          </div>
          <div className="flex gap-2 pt-1">
            <button type="submit" className="py-1.5 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 active:scale-[0.97] transition-all duration-200">
              创建
            </button>
            <button
              type="button"
              onClick={() => setShowCreate(false)}
              className="py-1.5 px-3.5 text-[11px] font-medium rounded-[6px] bg-surface text-text-primary border border-border hover:border-accent active:scale-[0.97] transition-all duration-200"
            >
              取消
            </button>
          </div>
        </form>
      )}

      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
        {datasets?.map((ds, i) => (
          <div
            key={ds.id}
            role="link"
            tabIndex={0}
            onClick={() => navigate(`/datasets/${ds.name}`)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                navigate(`/datasets/${ds.name}`)
              }
            }}
            className="block bg-surface border border-border rounded-md p-5 cursor-pointer hover:-translate-y-0.5 hover:shadow-md hover:border-accent/20 transition-all duration-200 animate-fade-in focus:outline-none focus:ring-2 focus:ring-accent/30"
            style={{ animationDelay: `${i * 40}ms` }}
          >
            <div className="flex justify-between items-start mb-3">
              <span className="text-[14px] font-semibold text-text-primary">
                {ds.name}
              </span>
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-[#ecfdf5] text-positive">
                启用中
              </span>
            </div>
            <div className="space-y-1.5 mb-3">
              <div className="flex justify-between items-center">
                <span className="text-[10px] tracking-wider text-text-tertiary">样例数</span>
                <span className="text-[12px] font-medium">{ds.example_count}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-[10px] tracking-wider text-text-tertiary">描述</span>
                <span className="text-[12px] text-text-secondary truncate max-w-[140px]">{ds.description || '—'}</span>
              </div>
            </div>
            <button
              onClick={async (e) => {
                e.preventDefault()
                e.stopPropagation()
                const ok = await confirm({
                  title: '删除数据集',
                  description: `确定删除数据集 "${ds.name}"？`,
                  confirmText: '删除',
                  danger: true,
                })
                if (!ok) return
                setDeletingName(ds.name)
                try {
                  await deleteMutation.mutateAsync(ds.name)
                  toast.success('数据集已删除')
                } catch (err) {
                  const msg = (err as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail
                    || (err as Error)?.message || '未知错误'
                  toast.error(msg, '删除失败')
                } finally {
                  setDeletingName(null)
                }
              }}
              disabled={deletingName === ds.name}
              className="text-[10px] text-text-tertiary hover:text-negative active:scale-95 transition-all tracking-wide disabled:opacity-50"
            >
              {deletingName === ds.name ? '删除中…' : '删除'}
            </button>
          </div>
        ))}
      </div>

      {datasets?.length === 0 && (
        <div className="bg-surface border border-dashed border-border rounded-md py-12 px-8 text-center mt-6">
          <h3 className="text-[14px] font-medium mb-1">暂无数据集</h3>
          <p className="text-[12px] text-text-tertiary max-w-[280px] mx-auto">
            创建第一个数据集开始评测。
          </p>
        </div>
      )}
    </div>
  )
}
