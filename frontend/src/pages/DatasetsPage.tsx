import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { datasetsApi } from '@/services'
import type { CreateDatasetRequest } from '@/types'

export default function DatasetsPage() {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState<CreateDatasetRequest>({ name: '', description: '', source_project: '' })

  const { data: datasets, isLoading } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => datasetsApi.list().then((r) => r.data),
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
        <h1 className="text-xl font-semibold tracking-tight mb-1">Dataset Management</h1>
        <p className="text-[13px] text-text-secondary">管理评测数据集、样本和质量指标</p>
      </header>

      <div className="flex gap-3 items-center mb-6">
        <input
          type="text"
          placeholder="Search datasets..."
          className="flex-1 max-w-[280px] py-2 px-3 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 placeholder:text-text-tertiary transition-all duration-200"
        />
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-1.5 py-2 px-3.5 text-[11px] font-medium tracking-wide rounded-[6px] bg-accent text-white border border-accent cursor-pointer hover:opacity-90 hover:scale-[1.02] active:scale-[0.97] focus:outline-none focus:ring-2 focus:ring-accent/20 transition-all duration-200"
        >
          + New Dataset
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
            className="bg-surface border border-border rounded-md p-5 hover:-translate-y-0.5 hover:shadow-md hover:border-accent/20 transition-all duration-200 animate-fade-in"
            style={{ animationDelay: `${i * 40}ms` }}
          >
            <div className="flex justify-between items-start mb-3">
              <Link to={`/datasets/${ds.name}`} className="text-[14px] font-semibold text-text-primary no-underline hover:text-accent transition-colors">
                {ds.name}
              </Link>
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-[#ecfdf5] text-positive">
                Active
              </span>
            </div>
            <div className="space-y-1.5 mb-3">
              <div className="flex justify-between items-center">
                <span className="text-[10px] tracking-widest uppercase text-text-tertiary">Samples</span>
                <span className="text-[12px] font-medium">{ds.example_count}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-[10px] tracking-widest uppercase text-text-tertiary">Description</span>
                <span className="text-[12px] text-text-secondary truncate max-w-[140px]">{ds.description || '—'}</span>
              </div>
            </div>
            <button
              onClick={() => {
                if (confirm(`确定删除数据集 "${ds.name}"？`)) deleteMutation.mutate(ds.name)
              }}
              className="text-[10px] text-text-tertiary hover:text-negative active:scale-95 transition-all tracking-wide"
            >
              删除
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
