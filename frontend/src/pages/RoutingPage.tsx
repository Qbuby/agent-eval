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
        <div className="skeleton h-5 w-32 rounded mb-6" />
        <div className="skeleton h-24 w-full rounded mb-6" />
      </div>
    )
  }

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-lg font-light tracking-tight mb-1">Routes</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">Routing rules &middot; traffic distribution</p>
      </header>

      {stats && stats.length > 0 && (
        <div className="grid grid-cols-4 gap-px bg-border border border-border rounded-[3px] overflow-hidden mb-8">
          {stats.map((s, i) => (
            <div key={i} className="bg-surface p-5 hover:bg-accent-subtle transition-colors">
              <div className="text-[9px] tracking-[0.12em] uppercase text-text-tertiary mb-2">
                {s.rule_id?.slice(0, 8) || 'Global'}
              </div>
              <div className="text-[24px] font-light tracking-tight">{s.total}</div>
              <div className="flex gap-2.5 mt-1.5">
                <span className="text-[10px] text-positive">{s.routed} routed</span>
                <span className="text-[10px] text-negative">{s.failed} failed</span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary mb-4 pb-2 border-b border-border">
        Active Rules
      </div>

      <div className="border border-border rounded-[3px] overflow-hidden bg-surface">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">Name</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">Source</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">Target</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">Priority</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">Status</th>
            </tr>
          </thead>
          <tbody>
            {rules?.map((rule) => (
              <tr key={rule.id} className="hover:bg-accent-subtle group cursor-default">
                <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-primary font-medium">{rule.name}</td>
                <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-secondary">{rule.source_project}</td>
                <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-secondary">{rule.target_dataset}</td>
                <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-secondary">{rule.priority}</td>
                <td className="py-2.5 px-3 border-b border-border text-[12px]">
                  <span className={`inline-block px-2 py-0.5 rounded-full text-[9px] tracking-wide font-medium ${
                    rule.is_active
                      ? 'bg-[#e6f7ed] text-[#1a6]'
                      : 'bg-[#f5f5f5] text-[#999]'
                  }`}>
                    {rule.is_active ? '启用' : '禁用'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rules?.length === 0 && (
          <div className="text-center py-10 text-text-tertiary text-[12px]">暂无路由规则</div>
        )}
      </div>
    </div>
  )
}
