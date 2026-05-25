import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { datasetsApi, schedulerApi, routingApi } from '@/services'
import { projectsApi, candidatesApi } from '@/services/benchmark'

export default function DashboardPage() {
  const { data: datasets } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => datasetsApi.list().then(r => r.data),
  })

  const { data: schedulerStatus } = useQuery({
    queryKey: ['scheduler-status'],
    queryFn: () => schedulerApi.getStatus().then(r => r.data),
  })

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
  })

  const { data: pendingCandidates } = useQuery({
    queryKey: ['candidates-pending-count'],
    queryFn: () => candidatesApi.list({ status: 'pending', page_size: 1 }).then(r => r.data),
  })

  const { data: rules } = useQuery({
    queryKey: ['routing-rules'],
    queryFn: () => routingApi.listRules().then(r => r.data),
  })

  const datasetCount = datasets?.length ?? 0
  const projectCount = projects?.length ?? 0
  const pendingCount = pendingCandidates?.total ?? 0
  const activeWatches = schedulerStatus?.watches?.filter(w => w.status === 'active').length ?? 0
  const isRunning = schedulerStatus?.running ?? false
  const activeRules = rules?.filter(r => r.is_active).length ?? 0

  return (
    <div>
      <header className="mb-8">
        <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary">概览</div>
        <h1 className="text-xl font-medium tracking-tight">仪表盘</h1>
      </header>

      {/* 统计卡片 */}
      <div className="grid grid-cols-4 gap-3 mb-8">
        <Link to="/datasets" className="p-4 border border-border rounded-lg bg-surface hover:-translate-y-0.5 hover:shadow-sm transition-all no-underline">
          <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">备选数据集</div>
          <div className="text-2xl font-medium tracking-tight">{datasetCount}</div>
          <div className="text-[11px] text-text-tertiary mt-1">个数据集</div>
        </Link>
        <Link to="/projects" className="p-4 border border-border rounded-lg bg-surface hover:-translate-y-0.5 hover:shadow-sm transition-all no-underline">
          <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">基准测试集</div>
          <div className="text-2xl font-medium tracking-tight">{projectCount}</div>
          <div className="text-[11px] text-text-tertiary mt-1">个项目</div>
        </Link>
        <div className="p-4 border border-border rounded-lg bg-surface hover:-translate-y-0.5 hover:shadow-sm transition-all">
          <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">暂存区</div>
          <div className="text-2xl font-medium tracking-tight">{pendingCount}</div>
          <div className="text-[11px] text-warning mt-1">待补全答案</div>
        </div>
        <Link to="/auto-collect" className="p-4 border border-border rounded-lg bg-surface hover:-translate-y-0.5 hover:shadow-sm transition-all no-underline">
          <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">自动采集</div>
          <div className={`text-2xl font-medium tracking-tight ${isRunning ? 'text-positive' : 'text-negative'}`}>
            {isRunning ? '开启' : '关闭'}
          </div>
          <div className="text-[11px] text-text-tertiary mt-1">{activeWatches} 监听 · {activeRules} 规则</div>
        </Link>
      </div>

      {/* 快捷操作 */}
      <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary mb-3 pb-2 border-b border-border">快捷操作</div>
      <div className="grid grid-cols-4 gap-3 mb-8">
        <Link to="/datasets" className="p-4 bg-surface border border-border rounded-lg text-center no-underline hover:-translate-y-0.5 hover:shadow-sm hover:border-accent/20 transition-all">
          <div className="text-[12px] font-medium text-text-primary">浏览备选数据集</div>
          <div className="text-[10px] text-text-tertiary mt-1">管理备选样例</div>
        </Link>
        <Link to="/projects" className="p-4 bg-surface border border-border rounded-lg text-center no-underline hover:-translate-y-0.5 hover:shadow-sm hover:border-accent/20 transition-all">
          <div className="text-[12px] font-medium text-text-primary">基准测试集</div>
          <div className="text-[10px] text-text-tertiary mt-1">管理评测基准</div>
        </Link>
        <Link to="/traces" className="p-4 bg-surface border border-border rounded-lg text-center no-underline hover:-translate-y-0.5 hover:shadow-sm hover:border-accent/20 transition-all">
          <div className="text-[12px] font-medium text-text-primary">导入调用轨迹</div>
          <div className="text-[10px] text-text-tertiary mt-1">从 LangSmith 拉取</div>
        </Link>
        <Link to="/auto-collect" className="p-4 bg-surface border border-border rounded-lg text-center no-underline hover:-translate-y-0.5 hover:shadow-sm hover:border-accent/20 transition-all">
          <div className="text-[12px] font-medium text-text-primary">自动采集配置</div>
          <div className="text-[10px] text-text-tertiary mt-1">调度器与路由规则</div>
        </Link>
      </div>

      {/* 最近数据集 */}
      <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary mb-3 pb-2 border-b border-border">最近数据集</div>
      <div className="border border-border rounded-[6px] overflow-hidden bg-surface">
        {datasets && datasets.length > 0 ? (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">名称</th>
                <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-24">样例数</th>
                <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-32">描述</th>
              </tr>
            </thead>
            <tbody>
              {datasets.slice(0, 5).map(ds => (
                <tr key={ds.id} className="hover:bg-accent-subtle">
                  <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-primary font-medium">
                    <Link to={`/datasets/${ds.name}`} className="no-underline text-text-primary hover:text-accent">{ds.name}</Link>
                  </td>
                  <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-secondary">{ds.example_count}</td>
                  <td className="py-2.5 px-3 border-b border-border text-[11px] text-text-tertiary truncate max-w-[200px]">{ds.description || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-center py-10 text-text-tertiary text-[12px]">暂无数据集</div>
        )}
      </div>
    </div>
  )
}
