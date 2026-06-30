import { useQuery } from '@tanstack/react-query'
import { schedulerApi } from '@/services'

export default function SchedulerPage() {
  const { data: status, isLoading } = useQuery({
    queryKey: ['scheduler-status'],
    queryFn: () => schedulerApi.getStatus().then((r) => r.data),
    refetchInterval: 10000,
  })

  if (isLoading) {
    return (
      <div>
        <header className="mb-6">
          <h1 className="page-title">调度监控</h1>
          <p className="page-subtitle">实时轮询状态</p>
        </header>
        <div className="grid grid-cols-4 gap-3 mb-8">
          {[1, 2, 3, 4].map(i => (
            <div key={i} className="metric-card">
              <div className="skeleton h-2 w-12 rounded mb-2" />
              <div className="skeleton h-6 w-16 rounded" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  const active = status?.watches?.filter(w => w.status === 'active').length ?? 0
  const idle = status?.watches?.filter(w => w.status !== 'active').length ?? 0
  const total = status?.watches?.length ?? 0

  return (
    <div>
      <header className="mb-6">
        <h1 className="page-title">调度监控</h1>
        <p className="page-subtitle">实时轮询状态</p>
      </header>

      <div className="grid grid-cols-4 gap-3 mb-8">
        <div className="metric-card">
          <div className="metric-eyebrow">运行状态</div>
          <div className={`metric-value ${status?.running ? 'text-positive' : 'text-negative'}`}>
            {status?.running ? '运行中' : '已停止'}
          </div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">活跃</div>
          <div className="metric-value text-positive">{active}</div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">空闲</div>
          <div className="metric-value text-text-tertiary">{idle}</div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">总数</div>
          <div className="metric-value">{total}</div>
        </div>
      </div>

      <div className="section-row">
        <div className="page-eyebrow">项目轮询状态</div>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>项目</th>
              <th className="w-28">状态</th>
              <th className="w-44">最近轮询</th>
            </tr>
          </thead>
          <tbody>
            {status?.watches?.map((w) => (
              <tr key={w.project_name}>
                <td className="font-medium">{w.project_name}</td>
                <td>
                  <span className={w.status === 'active' ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {w.status === 'active' ? '运行中' : '空闲'}
                  </span>
                </td>
                <td className="text-text-secondary">
                  {w.last_poll ? new Date(w.last_poll).toLocaleString() : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(!status?.watches || status.watches.length === 0) && (
          <div className="empty-state">暂无监听项目</div>
        )}
      </div>
    </div>
  )
}
