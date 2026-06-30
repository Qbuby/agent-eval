import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { governanceApi } from '@/services'

const ACTION_DOT: Record<string, string> = {
  delete: 'bg-negative',
  update: 'bg-warning',
  create: 'bg-positive',
  import: 'bg-accent',
}

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
      <header className="mb-6">
        <h1 className="page-title">审计日志</h1>
        <p className="page-subtitle">系统活动 · 变更历史</p>
      </header>

      <div className="toolbar">
        <select
          value={entityType}
          onChange={(e) => setEntityType(e.target.value)}
          className="select-sm"
          aria-label="实体类型筛选"
        >
          <option value="">全部实体</option>
          <option value="dataset">数据集</option>
          <option value="example">样例</option>
          <option value="rule">规则</option>
        </select>
        <select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          className="select-sm"
          aria-label="操作类型筛选"
        >
          <option value="">全部操作</option>
          <option value="create">创建</option>
          <option value="update">更新</option>
          <option value="delete">删除</option>
          <option value="import">导入</option>
        </select>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4].map((i) => <div key={i} className="skeleton h-14 rounded-lg" />)}
        </div>
      ) : (
        <div className="relative pl-6">
          <div className="absolute left-[8px] top-3 bottom-3 w-px bg-separator" />
          {data?.items.map((log, i) => {
            const dot = ACTION_DOT[log.action] || 'bg-fill/40'
            return (
              <div
                key={log.id}
                className="relative py-3 pl-4 pr-3 rounded-lg hover:bg-fill/5 -mx-2 transition-colors animate-fade-in"
                style={{ animationDelay: `${i * 30}ms` }}
              >
                <span className={`absolute left-[-3px] top-[18px] w-[7px] h-[7px] rounded-full ring-2 ring-bg ${dot}`} />
                <div className="text-[10px] text-text-tertiary tracking-wider font-mono mb-0.5">
                  {new Date(log.created_at).toLocaleString()}
                </div>
                <div className="text-[12px] text-text-secondary leading-relaxed">
                  <span className="text-text-primary font-medium">{log.entity_type}</span>
                  <span className="mx-1.5 text-text-tertiary">—</span>
                  <span className="badge badge-neutral !text-[10px]">{log.action}</span>
                  <span className="font-mono text-[11px] text-text-tertiary ml-2">{log.entity_id}</span>
                  {log.details && (
                    <span className="text-text-tertiary ml-1.5 text-[11px] block mt-0.5 truncate">
                      {JSON.stringify(log.details).slice(0, 100)}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
          {data?.items.length === 0 && (
            <div className="empty-state pl-0">暂无审计日志</div>
          )}
        </div>
      )}
    </div>
  )
}
