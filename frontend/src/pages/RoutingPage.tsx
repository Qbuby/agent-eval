import { useQuery } from '@tanstack/react-query'
import { routingApi } from '@/services'

export default function RoutingPage() {
  const { data: rules, isLoading } = useQuery({
    queryKey: ['routing-rules'],
    queryFn: () => routingApi.listRules().then((r) => r.data),
  })

  const { data: stats } = useQuery({
    queryKey: ['routing-stats'],
    queryFn: () => routingApi.getStats().then((r) => r.data),
  })

  if (isLoading) {
    return (
      <div>
        <header className="mb-6">
          <div className="page-eyebrow">自动化</div>
          <h1 className="page-title">路由规则</h1>
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

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">自动化</div>
        <h1 className="page-title">路由规则</h1>
        <p className="page-subtitle">规则按优先级匹配 trace，路由到对应数据集</p>
      </header>

      {stats && stats.length > 0 && (
        <div className="grid grid-cols-4 gap-3 mb-8">
          {stats.map((s, i) => (
            <div key={i} className="metric-card">
              <div className="metric-eyebrow">{s.rule_id?.slice(0, 8) || '全局'}</div>
              <div className="metric-value">{s.total}</div>
              <div className="flex gap-3 mt-1">
                <span className="text-[11px] text-positive">{s.routed} 已路由</span>
                <span className="text-[11px] text-negative">{s.failed} 失败</span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="section-row">
        <div className="page-eyebrow">规则列表</div>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>名称</th>
              <th>来源</th>
              <th>目标</th>
              <th className="w-20 text-right">优先级</th>
              <th className="w-20">状态</th>
            </tr>
          </thead>
          <tbody>
            {rules?.map((rule) => (
              <tr key={rule.id}>
                <td className="font-medium">{rule.name}</td>
                <td className="text-text-secondary">{rule.source_project}</td>
                <td className="text-text-secondary">{rule.target_dataset}</td>
                <td className="text-right text-text-tertiary tabular-nums">{rule.priority}</td>
                <td>
                  <span className={rule.is_active ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {rule.is_active ? '启用' : '禁用'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rules?.length === 0 && (
          <div className="empty-state">暂无路由规则</div>
        )}
      </div>
    </div>
  )
}
