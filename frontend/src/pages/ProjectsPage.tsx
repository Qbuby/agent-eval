import { useId, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Button, Dialog } from '@/components/ui'
import { projectsApi, type Project } from '@/services/benchmark'

export default function ProjectsPage() {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const reactId = useId()
  const nameId = `${reactId}-name`
  const descId = `${reactId}-desc`

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
      <header className="mb-6">
        <div className="page-eyebrow">管理</div>
        <h1 className="page-title">基准测试集</h1>
        <p className="page-subtitle">为不同业务场景管理可复用的评测项目</p>
      </header>

      <div className="toolbar">
        <Button onClick={() => setShowCreate(true)} variant="primary" size="md">
          新建项目
        </Button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => <div key={i} className="skeleton h-32 rounded-xl" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects?.map((p: Project) => (
            <Link
              key={p.id}
              to={`/benchmark/${p.id}`}
              className="card p-5 transition-[transform,box-shadow,border-color] duration-200 ease-standard hover:-translate-y-0.5 hover:shadow-md hover:border-border-strong focus:outline-none focus-visible:shadow-focus no-underline"
            >
              <div className="text-[15px] font-display font-semibold tracking-[-0.2px] text-text-primary mb-1 truncate">
                {p.name}
              </div>
              <div className="text-[12px] text-text-secondary mb-4 line-clamp-2 min-h-[36px]">
                {p.description || '无描述'}
              </div>
              <div className="text-[11px] text-text-tertiary">
                创建于 {new Date(p.created_at).toLocaleDateString()}
              </div>
            </Link>
          ))}
          {projects?.length === 0 && (
            <div className="col-span-full card border-dashed empty-state">
              <div className="text-[14px] font-medium text-text-primary mb-1">暂无项目</div>
              <div className="text-[12px] text-text-tertiary">创建一个项目来开始管理基准测试集</div>
            </div>
          )}
        </div>
      )}

      <Dialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        title="新建项目"
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowCreate(false)}>
              取消
            </Button>
            <Button
              variant="primary"
              size="md"
              onClick={() => createMutation.mutate()}
              disabled={!name.trim() || createMutation.isPending}
              loading={createMutation.isPending}
            >
              创建
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label htmlFor={nameId} className="field-label">项目名称</label>
            <input
              id={nameId}
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="例如：ep-agent"
              className="input"
            />
          </div>
          <div>
            <label htmlFor={descId} className="field-label">描述</label>
            <textarea
              id={descId}
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="项目用途描述…"
              rows={3}
              className="input resize-y"
            />
          </div>
        </div>
      </Dialog>
    </div>
  )
}
