import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { Button, Dialog, Drawer, SkeletonRow, ErrorCard, useToast } from '@/components/ui'
import { formatApiError, toToastMessage } from '@/lib/errors'
import { langfuseMetricsApi } from '@/services/langfuseMetrics'
import { datasetsApi } from '@/services'
import { candidatesApi } from '@/services/benchmark'
import MarkdownView from '@/components/MarkdownView'
import { CotTimeline, ToolCallsTable } from '@/components/TraceTimeline'
import type { CotStep } from '@/types'
import type { LangfuseObservation } from '@/services/langfuseMetrics'

const TRACE_PAGE_SIZE = 20

// 时间范围预设：换算成相对 now 的天数。custom 走自定义起止。
const RANGE_OPTIONS = [
  { value: '1d', label: '近 24 小时', days: 1 },
  { value: '7d', label: '近 7 天', days: 7 },
  { value: '30d', label: '近 30 天', days: 30 },
  { value: 'custom', label: '自定义', days: 0 },
] as const
type RangeValue = (typeof RANGE_OPTIONS)[number]['value']

// 分桶粒度。auto 按区间跨度自适应：≤2 天→hour，≤45 天→day，否则 week。
const BUCKET_OPTIONS = [
  { value: 'auto', label: '自动' },
  { value: 'hour', label: '按小时' },
  { value: 'day', label: '按天' },
  { value: 'week', label: '按周' },
] as const
type BucketValue = (typeof BUCKET_OPTIONS)[number]['value']

function autoBucket(fromMs: number, toMs: number): 'hour' | 'day' | 'week' {
  const days = (toMs - fromMs) / (24 * 60 * 60 * 1000)
  if (days <= 2) return 'hour'
  if (days <= 45) return 'day'
  return 'week'
}

// <input type="datetime-local"> 需要 "YYYY-MM-DDTHH:mm" 本地格式。
function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
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

// 趋势图 X 轴：把 ISO 时间按分桶粒度精简显示（小时显示时:分，天/周显示月-日）。
function fmtBucketTick(iso: string, bucket: 'hour' | 'day' | 'week'): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  if (bucket === 'hour') return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:00`
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
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

// 图表通用配色
const COLORS = {
  indigo: '#6366f1',
  amber: '#f59e0b',
  emerald: '#10b981',
  rose: '#f43f5e',
  sky: '#0ea5e9',
  violet: '#8b5cf6',
}

export default function LangfuseMetricsPage() {
  const [environment, setEnvironment] = useState('')
  const [range, setRange] = useState<RangeValue>('7d')
  const [bucketMode, setBucketMode] = useState<BucketValue>('auto')
  // 自定义起止（datetime-local 字符串）；仅 range==='custom' 时生效。
  const [customFrom, setCustomFrom] = useState(() =>
    toLocalInput(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)),
  )
  const [customTo, setCustomTo] = useState(() => toLocalInput(new Date()))
  const [tracePage, setTracePage] = useState(1)
  // 搜索框即时值 + debounced 值（debounced 才进 queryKey，避免每次击键打请求）。
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [errorOnly, setErrorOnly] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [polling, setPolling] = useState(false)

  // search 输入 debounce 400ms
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput.trim())
      setTracePage(1)
    }, 400)
    return () => clearTimeout(t)
  }, [searchInput])

  // 解析时间窗 [fromIso, toIso]。预设走相对 now；custom 走输入框。
  const { fromIso, toIso, fromMs, toMs } = useMemo(() => {
    if (range === 'custom') {
      const f = new Date(customFrom)
      const t = new Date(customTo)
      const fm = isNaN(f.getTime()) ? Date.now() - 7 * 86400000 : f.getTime()
      const tm = isNaN(t.getTime()) ? Date.now() : t.getTime()
      return {
        fromIso: new Date(fm).toISOString(),
        toIso: new Date(tm).toISOString(),
        fromMs: fm,
        toMs: tm,
      }
    }
    const opt = RANGE_OPTIONS.find((o) => o.value === range) ?? RANGE_OPTIONS[1]
    const tm = Date.now()
    const fm = tm - opt.days * 24 * 60 * 60 * 1000
    return {
      fromIso: new Date(fm).toISOString(),
      toIso: new Date(tm).toISOString(),
      fromMs: fm,
      toMs: tm,
    }
  }, [range, customFrom, customTo])

  // 有效分桶粒度：auto 时按跨度自适应，否则用手选值。
  const effectiveBucket: 'hour' | 'day' | 'week' = useMemo(
    () => (bucketMode === 'auto' ? autoBucket(fromMs, toMs) : (bucketMode as 'hour' | 'day' | 'week')),
    [bucketMode, fromMs, toMs],
  )

  // 公共时间窗参数（stats/trends/traces 共用，含 to 上界）。
  const windowParams = useMemo(
    () => ({ environment: environment || undefined, from: fromIso, to: toIso }),
    [environment, fromIso, toIso],
  )

  const statsQuery = useQuery({
    queryKey: ['lf-stats', environment, fromIso, toIso],
    queryFn: () => langfuseMetricsApi.stats(windowParams).then((r) => r.data),
  })

  const trendsQuery = useQuery({
    queryKey: ['lf-trends', environment, fromIso, toIso, effectiveBucket],
    queryFn: () =>
      langfuseMetricsApi.trends({ ...windowParams, bucket: effectiveBucket }).then((r) => r.data),
  })

  const tracesQuery = useQuery({
    queryKey: ['lf-traces', environment, fromIso, toIso, tracePage, search, errorOnly],
    queryFn: () =>
      langfuseMetricsApi
        .traces({
          ...windowParams,
          page: tracePage,
          page_size: TRACE_PAGE_SIZE,
          name: search || undefined,
          has_error: errorOnly ? true : undefined,
        })
        .then((r) => r.data),
  })

  const pollStatusQuery = useQuery({
    queryKey: ['lf-poll-status'],
    queryFn: () => langfuseMetricsApi.pollStatus().then((r) => r.data),
  })

  const stats = statsQuery.data
  const rawBuckets = trendsQuery.data?.buckets ?? []
  // 给每个桶预算一个精简的 X 轴标签
  const buckets = useMemo(
    () => rawBuckets.map((b) => ({ ...b, tick: fmtBucketTick(b.date, effectiveBucket) })),
    [rawBuckets, effectiveBucket],
  )
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
      await Promise.all([pollStatusQuery.refetch(), statsQuery.refetch(), trendsQuery.refetch()])
    } catch {
      pollStatusQuery.refetch()
    } finally {
      setPolling(false)
    }
  }

  const hasPollFailures = (pollStatus?.consecutive_failures ?? 0) > 0
  const chartEmpty = !trendsQuery.isLoading && buckets.length === 0

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">可观测性</div>
        <h1 className="page-title">Tracing 指标</h1>
        <p className="page-subtitle">
          来自 Langfuse 的 trace 级延迟 / token / 成本 / 工具调用指标 · 趋势与明细钻取
        </p>
      </header>

      {/* 工具栏：环境 / 时间范围 / 分桶 / 刷新 / 轮询 */}
      <div className="toolbar flex-wrap">
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
        {range === 'custom' && (
          <>
            <input
              type="datetime-local"
              value={customFrom}
              max={customTo}
              onChange={(e) => {
                setCustomFrom(e.target.value)
                setTracePage(1)
              }}
              className="input-sm"
              aria-label="起始时间"
            />
            <span className="text-[11px] text-text-tertiary">至</span>
            <input
              type="datetime-local"
              value={customTo}
              min={customFrom}
              onChange={(e) => {
                setCustomTo(e.target.value)
                setTracePage(1)
              }}
              className="input-sm"
              aria-label="结束时间"
            />
          </>
        )}
        <select
          value={bucketMode}
          onChange={(e) => setBucketMode(e.target.value as BucketValue)}
          className="select-sm"
          aria-label="分桶粒度"
          title={bucketMode === 'auto' ? `自动（当前：${effectiveBucket}）` : undefined}
        >
          {BUCKET_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.value === 'auto' ? `自动 · ${effectiveBucket}` : o.label}
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
              {pollStatus.last_polled_at ? ` · ${fmtTime(pollStatus.last_polled_at)}` : ''}
              {hasPollFailures ? ` · 连续失败 ${pollStatus.consecutive_failures} 次` : ''}
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
      <div className="grid grid-cols-5 gap-3 mb-6">
        <MetricCard label="Trace 总数" value={fmtNum(stats?.total_traces)} hint="区间内 trace 数" />
        <MetricCard label="平均响应时间" value={fmtSeconds(stats?.avg_latency_s)} hint="trace 级 latency" />
        <MetricCard label="总成本" value={fmtCost(stats?.total_cost)} hint="区间合计" />
        <MetricCard
          label="平均总 Token"
          value={fmtNum(stats?.avg_total_tokens)}
          hint={`合计 ${fmtNum(stats?.total_tokens_sum)}`}
        />
        <MetricCard label="平均首工具调用" value={fmtSeconds(stats?.avg_first_tool_call_s)} hint="首个 tool call" />
        <MetricCard
          label="工具成功率"
          value={fmtPct(stats?.overall_tool_success_rate)}
          hint={`成功 ${fmtNum(stats?.tool_success_sum)} / ${fmtNum(stats?.tool_calls_sum)}`}
        />
        <MetricCard label="平均首思考 Token" value={fmtSeconds(stats?.avg_first_thinking_token_s)} hint="首个 thinking token" />
        <MetricCard label="平均首答 Token" value={fmtSeconds(stats?.avg_first_answer_token_s)} hint="首个 answer token" />
        <MetricCard label="错误 Trace 数" value={fmtNum(stats?.error_trace_count)} hint="含错误标记" />
        <MetricCard label="缓存命中率" value="N/A" hint="暂未采集" />
      </div>

      {/* 趋势图：4 张 2x2 */}
      <div className="grid grid-cols-2 gap-3 mb-8">
        <ChartCard title="Trace 数 / 平均延迟" loading={trendsQuery.isLoading} empty={chartEmpty}>
          <LineChart data={buckets} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
            <XAxis dataKey="tick" tick={{ fontSize: 11 }} />
            <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
            <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Line yAxisId="left" type="monotone" dataKey="trace_count" name="Trace 数" stroke={COLORS.indigo} strokeWidth={1.8} dot={false} />
            <Line yAxisId="right" type="monotone" dataKey="avg_latency_s" name="平均延迟 (s)" stroke={COLORS.amber} strokeWidth={1.8} dot={false} />
          </LineChart>
        </ChartCard>

        <ChartCard title="成本 / Token" loading={trendsQuery.isLoading} empty={chartEmpty}>
          <AreaChart data={buckets} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <defs>
              <linearGradient id="lfCost" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={COLORS.emerald} stopOpacity={0.3} />
                <stop offset="95%" stopColor={COLORS.emerald} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
            <XAxis dataKey="tick" tick={{ fontSize: 11 }} />
            <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
            <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Area yAxisId="left" type="monotone" dataKey="total_cost" name="成本 ($)" stroke={COLORS.emerald} strokeWidth={1.8} fill="url(#lfCost)" />
            <Line yAxisId="right" type="monotone" dataKey="total_tokens" name="Token 数" stroke={COLORS.indigo} strokeWidth={1.8} dot={false} />
          </AreaChart>
        </ChartCard>

        <ChartCard title="工具成功率 / 错误数" loading={trendsQuery.isLoading} empty={chartEmpty}>
          <BarChart data={buckets} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
            <XAxis dataKey="tick" tick={{ fontSize: 11 }} />
            <YAxis yAxisId="left" orientation="left" tick={{ fontSize: 11 }} domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
            <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} allowDecimals={false} />
            <Tooltip formatter={(v: any, n: any) => (n === '成功率' ? `${(Number(v) * 100).toFixed(1)}%` : v)} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar yAxisId="right" dataKey="error_count" name="错误数" fill={COLORS.rose} radius={[2, 2, 0, 0]} maxBarSize={28} />
            <Line yAxisId="left" type="monotone" dataKey="tool_success_rate" name="成功率" stroke={COLORS.emerald} strokeWidth={1.8} dot={false} />
          </BarChart>
        </ChartCard>

        <ChartCard title="首 Token 时间趋势" loading={trendsQuery.isLoading} empty={chartEmpty}>
          <LineChart data={buckets} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
            <XAxis dataKey="tick" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}s`} />
            <Tooltip formatter={(v: any) => `${typeof v === 'number' ? v.toFixed(2) : v}s`} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Line type="monotone" dataKey="avg_first_tool_call_s" name="首工具" stroke={COLORS.sky} strokeWidth={1.8} dot={false} connectNulls />
            <Line type="monotone" dataKey="avg_first_thinking_token_s" name="首思考" stroke={COLORS.violet} strokeWidth={1.8} dot={false} connectNulls />
            <Line type="monotone" dataKey="avg_first_answer_token_s" name="首答" stroke={COLORS.amber} strokeWidth={1.8} dot={false} connectNulls />
          </LineChart>
        </ChartCard>
      </div>

      {/* Trace 列表工具栏：搜索 + 错误过滤 */}
      <div className="toolbar">
        <input
          type="text"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="搜索 trace name…"
          className="input-sm w-[260px]"
          aria-label="搜索 trace"
        />
        <label className="flex items-center gap-1.5 text-[12px] text-text-secondary cursor-pointer select-none">
          <input
            type="checkbox"
            checked={errorOnly}
            onChange={(e) => {
              setErrorOnly(e.target.checked)
              setTracePage(1)
            }}
            className="accent-accent"
          />
          仅看错误
        </label>
        {(search || errorOnly) && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setSearchInput('')
              setSearch('')
              setErrorOnly(false)
              setTracePage(1)
            }}
          >
            清除筛选
          </Button>
        )}
        <span className="text-[11px] text-text-tertiary ml-auto tabular-nums">共 {total} 条</span>
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
              <th className="w-44">Name</th>
              <th>Input</th>
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
              ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={10} />)
              : traces.map((t) => (
                  <tr
                    key={t.langfuse_trace_id}
                    onClick={() => setSelectedId(t.langfuse_trace_id)}
                    className="cursor-pointer animate-fade-in"
                  >
                    <td className="font-mono text-text-tertiary text-[11px]">
                      {fmtTime(t.trace_timestamp)}
                    </td>
                    <td className="text-text-primary truncate max-w-[180px]" title={t.name ?? ''}>
                      {t.name ?? '—'}
                    </td>
                    <td
                      className="text-text-secondary truncate max-w-[320px] text-[12px]"
                      title={t.input_preview ?? ''}
                    >
                      {t.input_preview ?? '—'}
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
                <td colSpan={10} className="empty-state">
                  {search || errorOnly ? '无匹配的 trace' : '该区间暂无 trace'}
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
            <Button variant="secondary" size="sm" disabled={tracePage <= 1} onClick={() => setTracePage((p) => p - 1)}>
              上一页
            </Button>
            <Button variant="secondary" size="sm" disabled={tracePage >= totalPages} onClick={() => setTracePage((p) => p + 1)}>
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

function MetricCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-eyebrow">{label}</div>
      <div className="metric-value">{value}</div>
      {hint && <div className="text-[11px] text-text-tertiary mt-1 truncate">{hint}</div>}
    </div>
  )
}

// 趋势图容器：统一标题 + loading / empty 占位 + ResponsiveContainer。
function ChartCard({
  title,
  loading,
  empty,
  children,
}: {
  title: string
  loading: boolean
  empty: boolean
  children: React.ReactElement
}) {
  return (
    <div className="table-card !p-4">
      <div className="metric-eyebrow mb-3">{title}</div>
      {loading ? (
        <div className="h-[240px] flex items-center justify-center text-[12px] text-text-tertiary">
          加载中…
        </div>
      ) : empty ? (
        <div className="h-[240px] flex items-center justify-center text-[12px] text-text-tertiary">
          暂无趋势数据
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          {children}
        </ResponsiveContainer>
      )}
    </div>
  )
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] text-text-tertiary">{label}</div>
      <div className="text-[13px] font-mono tabular-nums text-text-primary mt-0.5">{value}</div>
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
  const toast = useToast()
  const [importing, setImporting] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [importDataset, setImportDataset] = useState('')
  const [importCategory, setImportCategory] = useState('')
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['lf-trace', traceId],
    queryFn: () => langfuseMetricsApi.trace(traceId!).then((r) => r.data),
    enabled: !!traceId,
  })

  // 备选数据集下拉（type=candidate，与 /datasets 备选数据集页同源）+ 所选数据集
  // 下已出现过的自由文本类别下拉（candidatesApi.categories）。
  const { data: candidateDatasets } = useQuery({
    queryKey: ['datasets', 'candidate'],
    queryFn: () => datasetsApi.list({ type: 'candidate' }).then((r) => r.data),
  })
  const { data: datasetCategories } = useQuery({
    queryKey: ['candidate-categories', importDataset],
    queryFn: () => candidatesApi.categories({ dataset_name: importDataset }).then((r) => r.data.categories),
    enabled: !!importDataset,
  })

  const observations: LangfuseObservation[] = data?.observations ?? []

  // 导入本条 trace 到备选数据集（连同答案 / 思维链 / 工具链 / 来源快照 + 目标数据集/类别）。
  const handleImport = async () => {
    if (!traceId) return
    if (!importDataset) {
      toast.error('请选择目标备选数据集')
      return
    }
    setImporting(true)
    try {
      const res = await langfuseMetricsApi.importToCandidates({
        trace_ids: [traceId],
        dataset_name: importDataset,
        category: importCategory.trim() || undefined,
      })
      const { imported, skipped } = res.data
      if (imported > 0) {
        toast.success('已导入到备选数据集')
        setShowImport(false)
      } else {
        toast.error(skipped ? '该 trace 无可用问题，已跳过' : '未导入任何样例')
      }
    } catch (e) {
      toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '导入失败' })))
    } finally {
      setImporting(false)
    }
  }

  return (
    <Drawer
      open={!!traceId}
      onClose={onClose}
      title={data?.name ?? 'Trace 明细'}
      subtitle={data ? `${data.environment ?? '—'} · ${fmtTime(data.trace_timestamp)}` : undefined}
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

          {/* 导入到备选数据集：弹窗选目标项目 + 类别后落库（含答案 / 思维链 / 工具链 / 来源快照）。 */}
          <div className="flex justify-end">
            <Button variant="primary" size="sm" onClick={() => setShowImport(true)}>
              导入到备选数据集
            </Button>
          </div>

          {data.has_error && <div className="text-[12px] text-negative">该 trace 含错误标记</div>}

          {/* 语义执行链（思维链 + 工具调用）：服务端从 observations 归一化。优先展示，
              让用户先看执行过程；只在有可识别内容时渲染，无则跳过。 */}
          {Array.isArray(data.semantic_trace?.steps) && data.semantic_trace!.steps!.length > 0 && (
            <div>
              <div className="field-label">思维链（{data.semantic_trace!.steps!.length} 步）</div>
              <CotTimeline steps={data.semantic_trace!.steps as CotStep[]} />
            </div>
          )}
          {Array.isArray(data.semantic_trace?.tool_calls) && data.semantic_trace!.tool_calls!.length > 0 && (
            <div>
              <div className="field-label">工具调用（{data.semantic_trace!.tool_calls!.length}）</div>
              <ToolCallsTable calls={data.semantic_trace!.tool_calls as Array<Record<string, unknown>>} />
            </div>
          )}

          {/* Input —— 非字符串统一序列化成 ```json 代码块再交给 MarkdownView。 */}
          {data.input != null && (
            <div>
              <div className="field-label">Input</div>
              <div className="bg-fill/5 rounded-md p-3">
                <MarkdownView text={toDisplayText(data.input)} />
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
                        <td className="text-text-primary truncate max-w-[180px]" title={o.name ?? ''}>
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

          {/* Output 元数据较冗长（含 todos/messages 全量结构），放到最后展示，
              让语义化的思维链 / 工具链 / observations 优先呈现。 */}
          {data.output != null && (
            <div>
              <div className="field-label">Output</div>
              <div className="bg-fill/5 rounded-md p-3">
                <MarkdownView text={toDisplayText(data.output)} />
              </div>
            </div>
          )}
        </div>
      )}

      {/* 导入到备选数据集：选目标项目 + 类别后落库（含答案/思维链/工具链/来源快照）。 */}
      <Dialog
        open={showImport}
        onClose={() => setShowImport(false)}
        title="导入到备选数据集"
        description="将本条 trace 连同答案、思维链、工具调用链和来源 trace_id 快照导入备选数据集。"
        width={460}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowImport(false)}>取消</Button>
            <Button variant="primary" size="md" loading={importing} onClick={handleImport}>
              确认导入
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="field-label">目标备选数据集</label>
            <select
              value={importDataset}
              onChange={(e) => { setImportDataset(e.target.value); setImportCategory('') }}
              className="input"
            >
              <option value="">选择备选数据集…</option>
              {candidateDatasets?.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
            </select>
          </div>
          <div>
            <label className="field-label">类别（可选）</label>
            {importDataset && (datasetCategories?.length ?? 0) > 0 ? (
              <select
                value={importCategory}
                onChange={(e) => setImportCategory(e.target.value)}
                className="input"
              >
                <option value="">不指定类别</option>
                {datasetCategories?.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            ) : (
              <input
                value={importCategory}
                onChange={(e) => setImportCategory(e.target.value)}
                placeholder="自由文本类别名（可空）"
                className="input"
              />
            )}
            <p className="text-[11px] text-text-tertiary mt-1">
              有参考答案的样例状态为「待导入」，无答案的进入暂存区。
            </p>
          </div>
        </div>
      </Dialog>
    </Drawer>
  )
}
