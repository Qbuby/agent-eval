import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { adminApi } from '@/services'
import type { RequestLogEntry } from '@/types'

const REFRESH_INTERVAL_MS = 5000

function statusClass(status: number): string {
  if (status >= 500) return 'text-negative'
  if (status >= 400) return 'text-warning'
  if (status >= 300) return 'text-text-secondary'
  return 'text-positive'
}

function methodClass(method: string): string {
  switch (method) {
    case 'GET': return 'bg-accent-subtle text-accent'
    case 'POST': return 'bg-positive/10 text-positive'
    case 'PUT': case 'PATCH': return 'bg-warning/10 text-warning'
    case 'DELETE': return 'bg-negative/10 text-negative'
    default: return 'bg-accent-subtle text-text-secondary'
  }
}

export default function RequestLogPage() {
  const [statusFilter, setStatusFilter] = useState<'all' | '4xx' | '5xx'>('all')
  const [pathPrefix, setPathPrefix] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [selected, setSelected] = useState<RequestLogEntry | null>(null)

  const status_min = statusFilter === '4xx' ? 400 : statusFilter === '5xx' ? 500 : undefined

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['request-log', statusFilter, pathPrefix],
    queryFn: () =>
      adminApi
        .requestLog({
          limit: 200,
          status_min,
          path_prefix: pathPrefix || undefined,
        })
        .then((r) => r.data),
    refetchInterval: autoRefresh ? REFRESH_INTERVAL_MS : false,
  })

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-lg font-light tracking-tight mb-1">接口日志</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">
          最近 {data?.capacity ?? 512} 条 HTTP 请求 · 进程内环形缓冲（重启清空）
        </p>
      </header>

      <div className="flex gap-3 items-center mb-5 flex-wrap">
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] tracking-wider text-text-tertiary">状态</span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as 'all' | '4xx' | '5xx')}
            className="py-1.5 px-2 text-[11px] border border-border rounded-[3px] bg-surface text-text-secondary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200"
            aria-label="状态筛选"
          >
            <option value="all">全部</option>
            <option value="4xx">≥ 400 (含错误)</option>
            <option value="5xx">≥ 500 (服务端)</option>
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] tracking-wider text-text-tertiary">路径前缀</span>
          <input
            value={pathPrefix}
            onChange={(e) => setPathPrefix(e.target.value)}
            placeholder="/api/eval"
            className="py-1.5 px-2 text-[11px] border border-border rounded-[3px] bg-surface text-text-secondary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200 w-48 font-mono"
            aria-label="路径前缀筛选"
          />
        </div>
        <label className="flex items-center gap-1.5 cursor-pointer text-[11px] text-text-secondary">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            className="accent-accent"
          />
          自动刷新（5s）
        </label>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="py-1.5 px-3 text-[11px] font-medium tracking-wide rounded-[6px] bg-surface text-text-primary border border-border hover:border-accent active:scale-[0.97] disabled:opacity-40 transition-all duration-200"
        >
          {isFetching ? '刷新中…' : '立即刷新'}
        </button>
        <span className="text-[10px] text-text-tertiary ml-auto">
          {data ? `${data.returned} / ${data.capacity}` : ''}
        </span>
      </div>

      {isError && (
        <div className="text-[11px] text-negative mb-3">
          加载失败：{(error as Error).message}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="skeleton h-9 w-full rounded" />
          ))}
        </div>
      ) : (
        <div className="bg-surface border border-border rounded-md overflow-hidden">
          <table className="w-full text-[11px]">
            <thead className="bg-accent-subtle text-text-tertiary tracking-widest uppercase text-[10px]">
              <tr>
                <th className="text-left py-2 px-3 font-medium w-44">时间</th>
                <th className="text-left py-2 px-2 font-medium w-16">方法</th>
                <th className="text-left py-2 px-2 font-medium">路径</th>
                <th className="text-right py-2 px-2 font-medium w-16">状态</th>
                <th className="text-right py-2 px-2 font-medium w-20">耗时</th>
                <th className="text-left py-2 px-2 font-medium w-32">客户端</th>
                <th className="text-left py-2 px-3 font-medium w-32">Request ID</th>
              </tr>
            </thead>
            <tbody>
              {data?.entries.map((entry) => (
                <tr
                  key={entry.request_id + entry.timestamp}
                  onClick={() => setSelected(entry)}
                  className="border-t border-border hover:bg-accent-subtle cursor-pointer transition-colors animate-fade-in"
                >
                  <td className="py-1.5 px-3 font-mono text-text-tertiary text-[10px]">
                    {new Date(entry.timestamp).toLocaleTimeString()}
                  </td>
                  <td className="py-1.5 px-2">
                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium font-mono ${methodClass(entry.method)}`}>
                      {entry.method}
                    </span>
                  </td>
                  <td className="py-1.5 px-2 font-mono text-text-primary truncate max-w-[280px]" title={entry.path + (entry.query ? '?' + entry.query : '')}>
                    {entry.path}
                    {entry.query && <span className="text-text-tertiary">?{entry.query.length > 30 ? entry.query.slice(0, 30) + '…' : entry.query}</span>}
                  </td>
                  <td className={`py-1.5 px-2 text-right font-mono font-medium ${statusClass(entry.status)}`}>
                    {entry.status}
                  </td>
                  <td className="py-1.5 px-2 text-right font-mono text-text-secondary">
                    {entry.latency_ms.toFixed(1)} ms
                  </td>
                  <td className="py-1.5 px-2 font-mono text-text-tertiary text-[10px]">
                    {entry.client}
                  </td>
                  <td className="py-1.5 px-3 font-mono text-text-tertiary text-[10px] truncate" title={entry.request_id}>
                    {entry.request_id}
                  </td>
                </tr>
              ))}
              {data?.entries.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center py-10 text-text-tertiary text-[12px]">
                    暂无符合条件的请求
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <RequestLogDetail entry={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

function RequestLogDetail({ entry, onClose }: { entry: RequestLogEntry; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-end bg-black/30 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-[520px] max-w-full h-full bg-surface border-l border-border overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-[13px] font-medium tracking-tight">请求详情</h2>
          <button
            onClick={onClose}
            className="text-[11px] text-text-tertiary hover:text-text-primary"
            aria-label="关闭详情"
          >
            ✕ 关闭
          </button>
        </div>

        <dl className="space-y-3 text-[11px]">
          <Row label="时间">{new Date(entry.timestamp).toLocaleString()}</Row>
          <Row label="方法 / 路径">
            <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium font-mono mr-1.5 ${methodClass(entry.method)}`}>
              {entry.method}
            </span>
            <span className="font-mono text-text-primary break-all">{entry.path}</span>
          </Row>
          {entry.query && (
            <Row label="Query">
              <span className="font-mono text-text-secondary break-all">{entry.query}</span>
            </Row>
          )}
          <Row label="状态码">
            <span className={`font-mono font-medium ${statusClass(entry.status)}`}>{entry.status}</span>
          </Row>
          <Row label="耗时">
            <span className="font-mono text-text-secondary">{entry.latency_ms.toFixed(1)} ms</span>
          </Row>
          <Row label="客户端">
            <span className="font-mono text-text-secondary">{entry.client}</span>
          </Row>
          <Row label="Request ID">
            <span className="font-mono text-text-secondary break-all">{entry.request_id}</span>
          </Row>
          {entry.error && (
            <Row label="错误">
              <span className="font-mono text-negative break-all">{entry.error}</span>
            </Row>
          )}
          {entry.body_preview && (
            <div>
              <div className="text-[10px] text-text-tertiary tracking-widest uppercase mb-1">
                请求体预览 {entry.body_truncated && <span className="text-warning">(已截断)</span>}
              </div>
              <pre className="bg-accent-subtle rounded p-2 text-[10px] font-mono text-text-primary overflow-x-auto whitespace-pre-wrap break-all">
                {entry.body_preview}
              </pre>
            </div>
          )}
        </dl>
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[100px_1fr] gap-2">
      <dt className="text-[10px] text-text-tertiary tracking-widest uppercase pt-0.5">{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}
