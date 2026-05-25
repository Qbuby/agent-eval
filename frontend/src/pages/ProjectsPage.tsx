import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { projectsApi, type Project } from '@/services/benchmark'

export default function ProjectsPage() {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
  })

  const createMutation = useMutation({
    mutationFn: () => projectsApi.create({ name, description }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setShowCreate(false)
      setName('')
      setDescription('')
    },
  })

  return (
    <div>
      <header className="mb-8">
        <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary">管理</div>
        <h1 className="text-xl font-medium tracking-tight">基准测试集</h1>
      </header>

      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => setShowCreate(true)}
          className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 active:scale-[0.97] transition-all"
        >
          + 新建项目
        </button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => <div key={i} className="skeleton h-28 rounded-lg" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects?.map((p: Project) => (
            <Link
              key={p.id}
              to={`/benchmark/${p.id}`}
              className="block p-5 bg-surface border border-border rounded-lg hover:-translate-y-0.5 hover:shadow-sm hover:border-accent/20 transition-all no-underline"
            >
              <div className="text-[14px] font-medium text-text-primary mb-1">{p.name}</div>
              <div className="text-[11px] text-text-tertiary mb-3">{p.description || '无描述'}</div>
              <div className="text-[10px] text-text-tertiary">
                创建于 {new Date(p.created_at).toLocaleDateString()}
              </div>
            </Link>
          ))}
          {projects?.length === 0 && (
            <div className="col-span-full text-center py-12 border border-dashed border-border rounded-lg">
              <div className="text-[14px] font-medium mb-1">暂无项目</div>
              <div className="text-[12px] text-text-tertiary">创建一个项目来开始管理基准测试集</div>
            </div>
          )}
        </div>
      )}

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowCreate(false)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[400px] shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-[14px] font-medium">新建项目</h2>
              <button onClick={() => setShowCreate(false)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">项目名称</label>
                <input
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder="例如：ep-agent"
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">描述</label>
                <textarea
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  placeholder="项目用途描述..."
                  rows={3}
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent resize-y transition-all"
                />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setShowCreate(false)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button
                  onClick={() => createMutation.mutate()}
                  disabled={!name.trim() || createMutation.isPending}
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
