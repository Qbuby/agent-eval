import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { governanceApi } from '@/services'

export default function AuditPage() {
  const [entityType, setEntityType] = useState('')
  const [action, setAction] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['audit-logs', entityType, action],
    queryFn: () =>
      governanceApi
        .queryAuditLogs({
          entity_type: entityType || undefined,
          action: action || undefined,
          limit: 50,
        })
        .then((r) => r.data),
  })

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-lg font-light tracking-tight mb-1">审计日志</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">SYSTEM ACTIVITY · CHANGE HISTORY</p>
      </header>

      <div className="flex gap-3 items-center mb-5 flex-wrap">
        <div className="flex items-center gap-1.5">
          <span className="text-[9px] tracking-[0.08em] uppercase text-text-tertiary">Type</span>
          <select
            value={entityType}
            onChange={(e) => setEntityType(e.target.value)}
            className="py-1.5 px-2 text-[11px] border border-border rounded-[3px] bg-surface text-text-secondary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            aria-label="实体类型筛选"
          >
            <option value="">All</option>
            <option value="dataset">Dataset</option>
            <option value="example">Example</option>
            <option value="rule">Rule</option>
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[9px] tracking-[0.08em] uppercase text-text-tertiary">Action</span>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="py-1.5 px-2 text-[11px] border border-border rounded-[3px] bg-surface text-text-secondary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            aria-label="操作类型筛选"
          >
            <option value="">All</option>
            <option value="create">Create</option>
            <option value="update">Update</option>
            <option value="delete">Delete</option>
            <option value="import">Import</option>
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[1,2,3,4].map(i => <div key={i} className="skeleton h-12 w-full rounded" />)}
        </div>
      ) : (
        <div className="relative pl-5">
          <div className="absolute left-[5px] top-2 bottom-2 w-px bg-border" />
          {data?.items.map((log, i) => (
            <div
              key={log.id}
              className="relative py-3 pl-5 hover:bg-accent-subtle -mx-2 px-7 rounded-sm transition-colors animate-fade-in"
              style={{ animationDelay: `${i * 30}ms` }}
            >
              <div className={`absolute left-[-15px] top-[18px] w-[6px] h-[6px] rounded-full transition-transform hover:scale-150 ${
                log.action === 'delete' ? 'bg-negative' :
                log.action === 'update' ? 'bg-warning' : 'bg-accent'
              }`} />
              <div className="text-[10px] text-text-tertiary tracking-wide font-mono mb-0.5">
                {new Date(log.created_at).toLocaleString()}
              </div>
              <div className="text-[12px] text-text-secondary leading-relaxed">
                <span className="text-text-primary font-medium">{log.entity_type}</span>
                {' — '}
                <span className="px-1.5 py-0.5 bg-accent-subtle rounded text-[10px]">{log.action}</span>
                {' '}
                <span className="font-mono text-[10px]">{log.entity_id}</span>
                {log.details && (
                  <span className="text-text-tertiary ml-1.5 text-[10px]">
                    {JSON.stringify(log.details).slice(0, 60)}
                  </span>
                )}
              </div>
            </div>
          ))}
          {data?.items.length === 0 && (
            <div className="text-center py-10 text-text-tertiary text-[12px] pl-0">暂无审计日志</div>
          )}
        </div>
      )}
    </div>
  )
}
