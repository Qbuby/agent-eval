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
        <div className="skeleton h-5 w-40 rounded mb-6" />
        <div className="grid grid-cols-4 gap-px bg-border border border-border rounded-[3px] overflow-hidden mb-8">
          {[1,2,3,4].map(i => (
            <div key={i} className="bg-surface p-5">
              <div className="skeleton h-2 w-12 rounded mb-2" />
              <div className="skeleton h-6 w-8 rounded" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-lg font-light tracking-tight mb-1">Scheduler Monitor</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">Real-time polling status</p>
      </header>

      <div className="grid grid-cols-4 gap-px bg-border border border-border rounded-[3px] overflow-hidden mb-8">
        <div className="bg-surface p-5 hover:bg-accent-subtle transition-colors">
          <div className="text-[9px] tracking-[0.12em] uppercase text-text-tertiary mb-2">Status</div>
          <div className={`text-[24px] font-light tracking-tight ${status?.running ? 'text-positive' : 'text-negative'}`}>
            {status?.running ? 'Running' : 'Stopped'}
          </div>
        </div>
        <div className="bg-surface p-5 hover:bg-accent-subtle transition-colors">
          <div className="text-[9px] tracking-[0.12em] uppercase text-text-tertiary mb-2">Active</div>
          <div className="text-[24px] font-light tracking-tight text-positive">
            {status?.watches?.filter((w) => w.status === 'active').length ?? 0}
          </div>
        </div>
        <div className="bg-surface p-5 hover:bg-accent-subtle transition-colors">
          <div className="text-[9px] tracking-[0.12em] uppercase text-text-tertiary mb-2">Idle</div>
          <div className="text-[24px] font-light tracking-tight text-text-tertiary">
            {status?.watches?.filter((w) => w.status !== 'active').length ?? 0}
          </div>
        </div>
        <div className="bg-surface p-5 hover:bg-accent-subtle transition-colors">
          <div className="text-[9px] tracking-[0.12em] uppercase text-text-tertiary mb-2">Total</div>
          <div className="text-[24px] font-light tracking-tight">
            {status?.watches?.length ?? 0}
          </div>
        </div>
      </div>

      <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary mb-4 pb-2 border-b border-border">
        Project Polling Status
      </div>

      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal">Project</th>
            <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal">Status</th>
            <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal">Last Poll</th>
          </tr>
        </thead>
        <tbody>
          {status?.watches?.map((w) => (
            <tr key={w.project_name} className="hover:bg-accent-subtle group cursor-default">
              <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-secondary font-medium group-hover:text-text-primary transition-colors">{w.project_name}</td>
              <td className="py-2.5 px-3 border-b border-border text-[12px]">
                <span className={`inline-block px-2 py-0.5 rounded-full text-[9px] tracking-wide font-medium ${
                  w.status === 'active'
                    ? 'bg-[#e6f7ed] text-[#1a6]'
                    : 'bg-[#f5f5f5] text-[#999]'
                }`}>
                  {w.status === 'active' ? 'Running' : 'Idle'}
                </span>
              </td>
              <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-secondary">
                {w.last_poll ? new Date(w.last_poll).toLocaleString() : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {(!status?.watches || status.watches.length === 0) && (
        <div className="text-center py-10 text-text-tertiary text-[12px]">暂无监听项目</div>
      )}
    </div>
  )
}
