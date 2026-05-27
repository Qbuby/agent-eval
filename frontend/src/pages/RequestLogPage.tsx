import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Drawer, SkeletonRow } from '@/components/ui'
import { adminApi } from '@/services'
import type { RequestLogEntry } from '@/types'

const REFRESH_INTERVAL_MS = 5000

function statusClass(status: number): string {
  if (status >= 500) return 'text-negative'
  if (status >= 400) return 'text-warning'
  if (status >= 300) return 'text-text-secondary'
  return 'text-positive'
}

function methodBadge(method: string): string {
  switch (method) {
    case 'GET': return 'badge badge-info'
    case 'POST': return 'badge badge-positive'
    case 'PUT':
    case 'PATCH': return 'badge badge-warning'
    case 'DELETE': return 'badge badge-negative'
    default: return 'badge badge-neutral'
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
      <header className="mb-6">
        <h1 className="page-title">接口日志</h1>
        <p className="page-subtitle">
          最近 {data?.capacity ?? 512} 条 HTTP 请求 · 进程内环形缓冲（重启清空）
        </p>
      </header>

      <div className="toolbar">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as 'all' | '4xx' | '5xx')}
          className="select-sm"
          aria-label="状态筛选"
        >
          <option value="all">全部状态</option>
          <option value="4xx">≥ 400 (含错误)</option>
          <option value="5xx">≥ 500 (服务端)</option>
        </select>
        <input
          value={pathPrefix}
          onChange={(e) => setPathPrefix(e.target.value)}
          placeholder="/api/eval"
          className="input-sm w-48 font-mono"
          aria-label="路径前缀筛选"
        />
        <label className="flex items-center gap-1.5 cursor-pointer text-[12px] text-text-secondary select-none">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            className="accent-accent w-3.5 h-3.5"
          />
          自动刷新（5s）
        </label>
        <Button variant="secondary" size="sm" loading={isFetching} onClick={() => refetch()}>
          {isFetching ? '刷新中' : '立即刷新'}
        </Button>
        <span className="text-[11px] text-text-tertiary ml-auto tabular-nums">
          {data ? `${data.returned} / ${data.capacity}` : ''}
        </span>
      </div>

      {isError && (
        <div className="text-[12px] text-negative mb-3">
          加载失败：{(error as Error).message}
        </div>
      )}

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th className="w-44">时间</th>
              <th className="w-20">方法</th>
              <th>路径</th>
              <th className="w-16 text-right">状态</th>
              <th className="w-20 text-right">耗时</th>
              <th className="w-32">客户端</th>
              <th className="w-32">Request ID</th>
            </tr>
          </thead>
          <tbody>
            {isLoading
              ? Array.from({ length: 6 }).map((_, i) => <SkeletonRow key={i} cols={7} />)
              : data?.entries.map((entry) => (
                <tr
                  key={entry.request_id + entry.timestamp}
                  onClick={() => setSelected(entry)}
                  className="cursor-pointer animate-fade-in"
                >
                  <td className="font-mono text-text-tertiary text-[11px]">
                    {new Date(entry.timestamp).toLocaleTimeString()}
                  </td>
                  <td>
                    <span className={methodBadge(entry.method) + ' font-mono !px-2'}>
                      {entry.method}
                    </span>
                  </td>
                  <td className="font-mono text-text-primary truncate max-w-[280px]" title={entry.path + (entry.query ? '?' + entry.query : '')}>
                    {entry.path}
                    {entry.query && <span className="text-text-tertiary">?{entry.query.length > 30 ? entry.query.slice(0, 30) + '…' : entry.query}</span>}
                  </td>
                  <td className={`text-right font-mono font-medium tabular-nums ${statusClass(entry.status)}`}>
                    {entry.status}
                  </td>
                  <td className="text-right font-mono text-text-secondary tabular-nums">
                    {entry.latency_ms.toFixed(1)} ms
                  </td>
                  <td className="font-mono text-text-tertiary text-[11px]">{entry.client}</td>
                  <td className="font-mono text-text-tertiary text-[11px] truncate" title={entry.request_id}>
                    {entry.request_id}
                  </td>
                </tr>
              ))}
            {!isLoading && data?.entries.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">暂无符合条件的请求</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title="请求详情"
        subtitle={selected?.request_id}
      >
        {selected && (
          <dl className="space-y-3 text-[12px]">
            <Row label="时间">{new Date(selected.timestamp).toLocaleString()}</Row>
            <Row label="方法 / 路径">
              <span className={methodBadge(selected.method) + ' font-mono !px-2 mr-1.5'}>
                {selected.method}
              </span>
              <span className="font-mono text-text-primary break-all">{selected.path}</span>
            </Row>
            {selected.query && (
              <Row label="Query">
                <span className="font-mono text-text-secondary break-all">{selected.query}</span>
              </Row>
            )}
            <Row label="状态码">
              <span className={`font-mono font-medium ${statusClass(selected.status)}`}>{selected.status}</span>
            </Row>
            <Row label="耗时">
              <span className="font-mono text-text-secondary">{selected.latency_ms.toFixed(1)} ms</span>
            </Row>
            <Row label="客户端">
              <span className="font-mono text-text-secondary">{selected.client}</span>
            </Row>
            <Row label="Request ID">
              <span className="font-mono text-text-secondary break-all">{selected.request_id}</span>
            </Row>
            {selected.error && (
              <Row label="错误">
                <span className="font-mono text-negative break-all">{selected.error}</span>
              </Row>
            )}
            {selected.body_preview && (
              <div>
                <div className="field-label">
                  请求体预览 {selected.body_truncated && <span className="text-warning">(已截断)</span>}
                </div>
                <pre className="bg-fill/5 rounded-md p-3 text-[11px] font-mono text-text-primary overflow-x-auto whitespace-pre-wrap break-all">
                  {selected.body_preview}
                </pre>
              </div>
            )}
          </dl>
        )}
      </Drawer>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[100px_1fr] gap-2 items-start">
      <dt className="field-label !mb-0 pt-0.5">{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}
