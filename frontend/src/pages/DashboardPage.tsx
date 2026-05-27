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

  const QUICK_ACTIONS: Array<{ to: string; title: string; subtitle: string }> = [
    { to: '/datasets', title: '浏览备选数据集', subtitle: '管理备选样例' },
    { to: '/projects', title: '基准测试集', subtitle: '管理评测基准' },
    { to: '/traces', title: '导入调用轨迹', subtitle: '从 LangSmith 拉取' },
    { to: '/auto-collect', title: '自动采集配置', subtitle: '调度器与路由规则' },
  ]

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">概览</div>
        <h1 className="page-title">仪表盘</h1>
      </header>

      <div className="grid grid-cols-4 gap-3 mb-8">
        <Link to="/datasets" className="metric-card no-underline block">
          <div className="metric-eyebrow">备选数据集</div>
          <div className="metric-value">{datasetCount}</div>
          <div className="text-[11px] text-text-tertiary mt-1">个数据集</div>
        </Link>
        <Link to="/projects" className="metric-card no-underline block">
          <div className="metric-eyebrow">基准测试集</div>
          <div className="metric-value">{projectCount}</div>
          <div className="text-[11px] text-text-tertiary mt-1">个项目</div>
        </Link>
        <div className="metric-card">
          <div className="metric-eyebrow">暂存区</div>
          <div className="metric-value">{pendingCount}</div>
          <div className="text-[11px] text-warning mt-1">待补全答案</div>
        </div>
        <Link to="/auto-collect" className="metric-card no-underline block">
          <div className="metric-eyebrow">自动采集</div>
          <div className={`metric-value ${isRunning ? 'text-positive' : 'text-negative'}`}>
            {isRunning ? '开启' : '关闭'}
          </div>
          <div className="text-[11px] text-text-tertiary mt-1">{activeWatches} 监听 · {activeRules} 规则</div>
        </Link>
      </div>

      <div className="section-row">
        <div className="page-eyebrow">快捷操作</div>
      </div>
      <div className="grid grid-cols-4 gap-3 mb-8">
        {QUICK_ACTIONS.map(a => (
          <Link
            key={a.to}
            to={a.to}
            className="card px-4 py-3.5 no-underline transition-[transform,box-shadow,border-color] duration-200 ease-standard hover:-translate-y-0.5 hover:shadow-sm hover:border-border-strong"
          >
            <div className="text-[12px] font-medium text-text-primary">{a.title}</div>
            <div className="text-[10px] text-text-tertiary mt-1">{a.subtitle}</div>
          </Link>
        ))}
      </div>

      <div className="section-row">
        <div className="page-eyebrow">最近数据集</div>
        <Link to="/datasets" className="text-[11px] text-accent hover:text-accent-hover no-underline transition-colors">查看全部</Link>
      </div>

      <div className="table-card">
        {datasets && datasets.length > 0 ? (
          <table className="table-base">
            <thead>
              <tr>
                <th>名称</th>
                <th className="w-24 text-right">样例数</th>
                <th>描述</th>
              </tr>
            </thead>
            <tbody>
              {datasets.slice(0, 5).map(ds => (
                <tr key={ds.id}>
                  <td className="font-medium">
                    <Link to={`/datasets/${ds.name}`} className="text-text-primary hover:text-accent no-underline transition-colors">
                      {ds.name}
                    </Link>
                  </td>
                  <td className="text-right text-text-secondary tabular-nums">{ds.example_count}</td>
                  <td className="text-text-tertiary truncate max-w-[280px]">{ds.description || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">暂无数据集</div>
        )}
      </div>
    </div>
  )
}
