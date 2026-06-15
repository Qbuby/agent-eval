import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { Button, Drawer, SkeletonRow, ErrorCard } from '@/components/ui'
import { formatApiError } from '@/lib/errors'
import { langfuseMetricsApi } from '@/services/langfuseMetrics'
import MarkdownView from '@/components/MarkdownView'
import type { LangfuseObservation } from '@/services/langfuseMetrics'

const TRACE_PAGE_SIZE = 20

// 时间范围预设：换算成相对 now 的 from ISO。
const RANGE_OPTIONS = [
  { value: '7d', label: '近 7 天', days: 7 },
  { value: '30d', label: '近 30 天', days: 30 },
] as const
type RangeValue = (typeof RANGE_OPTIONS)[number]['value']

function rangeToFrom(value: RangeValue): string {
  const opt = RANGE_OPTIONS.find((o) => o.value === value) ?? RANGE_OPTIONS[0]
  return new Date(Date.now() - opt.days * 24 * 60 * 60 * 1000).toISOString()
}

// ---- 数值格式化 ----
function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v == null) return '—'
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function fmtSeconds(v: number | null | undefined): string {
  return v == null ? '—' : `${v.toFixed(2)}s`
}

function fmtCost(v: number | null | undefined): string {
  return v == null ? '—' : `$${v.toFixed(4)}`
}

function fmtPct(v: number | null | undefined): string {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`
}

function fmtTime(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

// Langfuse 的 input/output 可能是字符串，也可能是 JSON 对象/数组（LangGraph 等
// agent 框架会塞 messages 结构）。字符串原样返回交给 markdown 渲染；非字符串
// 序列化成 ```json 代码块，让 MarkdownView 以代码块形式美观展示且不再崩溃。
function toDisplayText(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  try {
    return '```json\n' + JSON.stringify(v, null, 2) + '\n```'
  } catch {
    return String(v)
  }
}

export default function LangfuseMetricsPage() {
  const [environment, setEnvironment] = useState('')
  const [range, setRange] = useState<RangeValue>('7d')
  const [tracePage, setTracePage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [polling, setPolling] = useState(false)

  // from 随时间范围变化；to 留空表示到 now。用 range 作 queryKey 的一部分。
  const from = useMemo(() => rangeToFrom(range), [range])
  const queryParams = useMemo(
    () => ({ environment: environment || undefined, from }),
    [environment, from],
  )

  const statsQuery = useQuery({
    queryKey: ['lf-stats', environment, range],
    queryFn: () => langfuseMetricsApi.stats(queryParams).then((r) => r.data),
  })

  const trendsQuery = useQuery({
    queryKey: ['lf-trends', environment, range],
    queryFn: () =>
      langfuseMetricsApi.trends({ ...queryParams, bucket: 'day' }).then((r) => r.data),
  })

  const tracesQuery = useQuery({
    queryKey: ['lf-traces', environment, range, tracePage],
    queryFn: () =>
      langfuseMetricsApi
        .traces({ ...queryParams, page: tracePage, page_size: TRACE_PAGE_SIZE })
        .then((r) => r.data),
  })

  const pollStatusQuery = useQuery({
    queryKey: ['lf-poll-status'],
    queryFn: () => langfuseMetricsApi.pollStatus().then((r) => r.data),
  })

  const stats = statsQuery.data
  const buckets = trendsQuery.data?.buckets ?? []
  const traces = tracesQuery.data?.traces ?? []
  const total = tracesQuery.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / TRACE_PAGE_SIZE))
  const pollStatus = pollStatusQuery.data

  const refreshAll = () => {
    statsQuery.refetch()
    trendsQuery.refetch()
    tracesQuery.refetch()
    pollStatusQuery.refetch()
  }

  const handlePoll = async () => {
    setPolling(true)
    try {
      await langfuseMetricsApi.poll()
      await Promise.all([pollStatusQuery.refetch(), statsQuery.refetch()])
    } catch {
      // 错误状态由 pollStatus 的 last_error 体现，这里静默
      pollStatusQuery.refetch()
    } finally {
      setPolling(false)
    }
  }

  const hasPollFailures = (pollStatus?.consecutive_failures ?? 0) > 0

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">可观测性</div>
        <h1 className="page-title">Tracing 指标</h1>
        <p className="page-subtitle">
          来自 Langfuse 的 trace 级延迟 / token / 成本 / 工具调用指标 · 趋势与明细钻取
        </p>
      </header>

      {/* 工具栏 */}
      <div className="toolbar">
        <select
          value={environment}
          onChange={(e) => {
            setEnvironment(e.target.value)
            setTracePage(1)
          }}
          className="select-sm"
          aria-label="环境筛选"
        >
          <option value="">全部环境</option>
          {(stats?.environments ?? []).map((env) => (
            <option key={env} value={env}>
              {env}
            </option>
          ))}
        </select>
        <select
          value={range}
          onChange={(e) => {
            setRange(e.target.value as RangeValue)
            setTracePage(1)
          }}
          className="select-sm"
          aria-label="时间范围"
        >
          {RANGE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <Button
          variant="secondary"
          size="sm"
          loading={statsQuery.isFetching || trendsQuery.isFetching}
          onClick={refreshAll}
        >
          刷新
        </Button>
        <Button variant="tinted" size="sm" loading={polling} onClick={handlePoll}>
          触发轮询
        </Button>
        <span className="text-[11px] text-text-tertiary ml-auto tabular-nums">
          {pollStatus ? (
            <span className={hasPollFailures ? 'text-negative' : ''}>
              轮询：{pollStatus.status}
              {pollStatus.last_polled_at
                ? ` · ${fmtTime(pollStatus.last_polled_at)}`
                : ''}
              {hasPollFailures
                ? ` · 连续失败 ${pollStatus.consecutive_failures} 次`
                : ''}
            </span>
          ) : (
            '轮询状态加载中…'
          )}
        </span>
      </div>

      {statsQuery.isError && (
        <div className="mb-3">
          <ErrorCard
            error={formatApiError(statsQuery.error, { fallbackTitle: '加载指标失败' })}
          />
        </div>
      )}

      {/* KPI 指标卡 */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        <MetricCard
          label="Trace 总数"
          value={fmtNum(stats?.total_traces)}
          hint="区间内 trace 数"
        />
        <MetricCard
          label="平均响应时间"
          value={fmtSeconds(stats?.avg_latency_s)}
          hint="trace 级 latency"
        />
        <MetricCard label="总成本" value={fmtCost(stats?.total_cost)} hint="区间合计" />
        <MetricCard
          label="平均总 Token"
          value={fmtNum(stats?.avg_total_tokens)}
          hint={`合计 ${fmtNum(stats?.total_tokens_sum)}`}
        />
        <MetricCard
          label="平均首工具调用"
          value={fmtSeconds(stats?.avg_first_tool_call_s)}
          hint="首个 tool call"
        />
        <MetricCard
          label="工具成功率"
          value={fmtPct(stats?.overall_tool_success_rate)}
          hint={`成功 ${fmtNum(stats?.tool_success_sum)} / ${fmtNum(stats?.tool_calls_sum)}`}
        />
        <MetricCard
          label="平均首思考 Token"
          value={fmtSeconds(stats?.avg_first_thinking_token_s)}
          hint="首个 thinking token"
        />
        <MetricCard
          label="平均首答 Token"
          value={fmtSeconds(stats?.avg_first_answer_token_s)}
          hint="首个 answer token"
        />
        <MetricCard
          label="错误 Trace 数"
          value={fmtNum(stats?.error_trace_count)}
          hint="含错误标记"
        />
        <MetricCard label="缓存命中率" value="N/A" hint="暂未采集" />
      </div>

      {/* 趋势图 */}
      <div className="grid grid-cols-2 gap-3 mb-8">
        <div className="table-card !p-4">
          <div className="metric-eyebrow mb-3">每日 Trace 数 / 平均延迟</div>
          {trendsQuery.isLoading ? (
            <div className="h-[240px] flex items-center justify-center text-[12px] text-text-tertiary">
              加载中…
            </div>
          ) : buckets.length === 0 ? (
            <div className="h-[240px] flex items-center justify-center text-[12px] text-text-tertiary">
              暂无趋势数据
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={buckets} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="trace_count"
                  name="Trace 数"
                  stroke="#6366f1"
                  strokeWidth={1.8}
                  dot={false}
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="avg_latency_s"
                  name="平均延迟 (s)"
                  stroke="#f59e0b"
                  strokeWidth={1.8}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
        <div className="table-card !p-4">
          <div className="metric-eyebrow mb-3">每日成本 / Token</div>
          {trendsQuery.isLoading ? (
            <div className="h-[240px] flex items-center justify-center text-[12px] text-text-tertiary">
              加载中…
            </div>
          ) : buckets.length === 0 ? (
            <div className="h-[240px] flex items-center justify-center text-[12px] text-text-tertiary">
              暂无趋势数据
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={buckets} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                <defs>
                  <linearGradient id="lfCost" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Area
                  yAxisId="left"
                  type="monotone"
                  dataKey="total_cost"
                  name="成本 ($)"
                  stroke="#10b981"
                  strokeWidth={1.8}
                  fill="url(#lfCost)"
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="total_tokens"
                  name="Token 数"
                  stroke="#6366f1"
                  strokeWidth={1.8}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {tracesQuery.isError && (
        <div className="mb-3">
          <ErrorCard
            error={formatApiError(tracesQuery.error, { fallbackTitle: '加载 trace 列表失败' })}
          />
        </div>
      )}

      {/* Trace 列表 */}
      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th className="w-40">时间</th>
              <th>Name</th>
              <th className="w-24">环境</th>
              <th className="w-20 text-right">延迟</th>
              <th className="w-20 text-right">总 Token</th>
              <th className="w-20 text-right">成本</th>
              <th className="w-16 text-right">工具数</th>
              <th className="w-20 text-right">成功率</th>
              <th className="w-14 text-center">错误</th>
            </tr>
          </thead>
          <tbody>
            {tracesQuery.isLoading
              ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={9} />)
              : traces.map((t) => (
                  <tr
                    key={t.langfuse_trace_id}
                    onClick={() => setSelectedId(t.langfuse_trace_id)}
                    className="cursor-pointer animate-fade-in"
                  >
                    <td className="font-mono text-text-tertiary text-[11px]">
                      {fmtTime(t.trace_timestamp)}
                    </td>
                    <td
                      className="text-text-primary truncate max-w-[240px]"
                      title={t.name ?? ''}
                    >
                      {t.name ?? '—'}
                    </td>
                    <td className="text-text-secondary truncate">{t.environment ?? '—'}</td>
                    <td className="text-right font-mono tabular-nums text-text-secondary">
                      {fmtSeconds(t.latency_s)}
                    </td>
                    <td className="text-right font-mono tabular-nums text-text-secondary">
                      {fmtNum(t.total_tokens)}
                    </td>
                    <td className="text-right font-mono tabular-nums text-text-secondary">
                      {fmtCost(t.total_cost)}
                    </td>
                    <td className="text-right font-mono tabular-nums text-text-secondary">
                      {fmtNum(t.tool_call_count)}
                    </td>
                    <td className="text-right font-mono tabular-nums text-text-secondary">
                      {fmtPct(t.tool_success_rate)}
                    </td>
                    <td className="text-center">
                      {t.has_error ? (
                        <span className="badge badge-neutral text-negative">错误</span>
                      ) : (
                        <span className="text-text-tertiary">—</span>
                      )}
                    </td>
                  </tr>
                ))}
            {!tracesQuery.isLoading && traces.length === 0 && (
              <tr>
                <td colSpan={9} className="empty-state">
                  该区间暂无 trace
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-3 text-[12px] text-text-secondary">
          <span className="tabular-nums">
            第 {tracePage} / {totalPages} 页 · 共 {total} 条
          </span>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={tracePage <= 1}
              onClick={() => setTracePage((p) => p - 1)}
            >
              上一页
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={tracePage >= totalPages}
              onClick={() => setTracePage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      )}

      {/* 详情 Drawer */}
      <TraceDetailDrawer traceId={selectedId} onClose={() => setSelectedId(null)} />
    </div>
  )
}

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint?: string
}) {
  return (
    <div className="metric-card">
      <div className="metric-eyebrow">{label}</div>
      <div className="metric-value">{value}</div>
      {hint && <div className="text-[11px] text-text-tertiary mt-1 truncate">{hint}</div>}
    </div>
  )
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] text-text-tertiary">{label}</div>
      <div className="text-[13px] font-mono tabular-nums text-text-primary mt-0.5">
        {value}
      </div>
    </div>
  )
}

function TraceDetailDrawer({
  traceId,
  onClose,
}: {
  traceId: string | null
  onClose: () => void
}) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['lf-trace', traceId],
    queryFn: () => langfuseMetricsApi.trace(traceId!).then((r) => r.data),
    enabled: !!traceId,
  })

  const observations: LangfuseObservation[] = data?.observations ?? []

  return (
    <Drawer
      open={!!traceId}
      onClose={onClose}
      title={data?.name ?? 'Trace 明细'}
      subtitle={
        data
          ? `${data.environment ?? '—'} · ${fmtTime(data.trace_timestamp)}`
          : undefined
      }
      width="wide"
    >
      {isError && (
        <div className="mb-3">
          <ErrorCard error={formatApiError(error, { fallbackTitle: '加载 trace 详情失败' })} />
        </div>
      )}
      {isLoading && <div className="text-[12px] text-text-tertiary">加载中…</div>}
      {data && (
        <div className="space-y-5">
          {/* trace 指标网格 */}
          <div className="grid grid-cols-3 gap-3">
            <DetailItem label="延迟" value={fmtSeconds(data.latency_s)} />
            <DetailItem label="总 Token" value={fmtNum(data.total_tokens)} />
            <DetailItem label="成本" value={fmtCost(data.total_cost)} />
            <DetailItem label="工具调用数" value={fmtNum(data.tool_call_count)} />
            <DetailItem label="工具成功率" value={fmtPct(data.tool_success_rate)} />
            <DetailItem label="首工具调用" value={fmtSeconds(data.first_tool_call_s)} />
            <DetailItem label="首思考 Token" value={fmtSeconds(data.first_thinking_token_s)} />
            <DetailItem label="首答 Token" value={fmtSeconds(data.first_answer_token_s)} />
            <DetailItem label="缓存命中率" value="N/A" />
          </div>

          {data.has_error && (
            <div className="text-[12px] text-negative">该 trace 含错误标记</div>
          )}

          {/* input / output —— Langfuse 的 input/output 可能是字符串，也可能是
              JSON 对象/数组（如 LangGraph 的 messages 结构）。非字符串统一序列化
              成 ```json 代码块再交给 MarkdownView，避免把对象当字符串渲染。 */}
          {data.input != null && (
            <div>
              <div className="field-label">Input</div>
              <div className="bg-fill/5 rounded-md p-3">
                <MarkdownView text={toDisplayText(data.input)} />
              </div>
            </div>
          )}
          {data.output != null && (
            <div>
              <div className="field-label">Output</div>
              <div className="bg-fill/5 rounded-md p-3">
                <MarkdownView text={toDisplayText(data.output)} />
              </div>
            </div>
          )}

          {/* observations 明细表 */}
          <div>
            <div className="field-label">Observations（{observations.length}）</div>
            {observations.length === 0 ? (
              <div className="empty-state !py-6">该 trace 暂无 observation</div>
            ) : (
              <div className="table-card">
                <table className="table-base">
                  <thead>
                    <tr>
                      <th className="w-20">类型</th>
                      <th>Name</th>
                      <th className="w-32">Model</th>
                      <th className="w-16 text-right">延迟</th>
                      <th className="w-16 text-right">Token</th>
                      <th className="w-16 text-right">成本</th>
                      <th className="w-16">Level</th>
                      <th className="w-16 text-right">TTFT</th>
                    </tr>
                  </thead>
                  <tbody>
                    {observations.map((o) => (
                      <tr key={o.id} className="animate-fade-in">
                        <td className="text-text-secondary truncate">{o.type ?? '—'}</td>
                        <td
                          className="text-text-primary truncate max-w-[180px]"
                          title={o.name ?? ''}
                        >
                          {o.name ?? '—'}
                        </td>
                        <td className="text-text-secondary truncate" title={o.model ?? ''}>
                          {o.model ?? '—'}
                        </td>
                        <td className="text-right font-mono tabular-nums text-text-secondary">
                          {fmtSeconds(o.latency_s)}
                        </td>
                        <td className="text-right font-mono tabular-nums text-text-secondary">
                          {fmtNum(o.total_tokens)}
                        </td>
                        <td className="text-right font-mono tabular-nums text-text-secondary">
                          {fmtCost(o.calculated_total_cost)}
                        </td>
                        <td
                          className={
                            o.level && o.level.toUpperCase() !== 'DEFAULT'
                              ? 'text-warning'
                              : 'text-text-tertiary'
                          }
                          title={o.status_message ?? ''}
                        >
                          {o.level ?? '—'}
                        </td>
                        <td className="text-right font-mono tabular-nums text-text-secondary">
                          {fmtSeconds(o.time_to_first_token_s)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </Drawer>
  )
}
