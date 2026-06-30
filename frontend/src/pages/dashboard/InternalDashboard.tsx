import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  Cell,
} from 'recharts'
import { datasetsApi, schedulerApi, routingApi } from '@/services'
import { projectsApi, candidatesApi } from '@/services/benchmark'
import { langfuseMetricsApi } from '@/services/langfuseMetrics'
import { feedbackReviewApi } from '@/services/feedbackReview'
import { useAuthStore } from '@/stores/auth'
import { ChartCard, MetricCard, COLORS, AXIS_TICK, GRID_PROPS, TOOLTIP_STYLE } from './charts'

// ──────────────────────────────────────────────────────────────────────────
// 内部仪表盘（admin + 内部普通 user 共用）。
// 对齐「实际可操作模块」：只展示该角色真正能进的入口。自动采集是 admin 专属，
// 故其指标卡 + 快捷操作仅 isAdmin 时渲染（避免内部 user 点了被 RoleRoute 弹回的死链）。
// 三块可视化：Tracing 运行趋势 / 客户反馈概览 / 数据资产分布 —— 数据源均为
// require_internal 接口，admin 与内部 user 都可访问，不会 403。
// ──────────────────────────────────────────────────────────────────────────

export default function InternalDashboard() {
  const isAdmin = useAuthStore((s) => s.isAdmin)()

  const { data: datasets } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => datasetsApi.list().then((r) => r.data),
  })
  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then((r) => r.data),
  })
  const { data: pendingCandidates } = useQuery({
    queryKey: ['candidates-pending-count'],
    queryFn: () => candidatesApi.list({ status: 'pending', page_size: 1 }).then((r) => r.data),
  })
  // 自动采集相关只有 admin 需要 + 能进入对应页面，故仅 admin 拉取（避免无意义请求）。
  const { data: schedulerStatus } = useQuery({
    queryKey: ['scheduler-status'],
    queryFn: () => schedulerApi.getStatus().then((r) => r.data),
    enabled: isAdmin,
  })
  const { data: rules } = useQuery({
    queryKey: ['routing-rules'],
    queryFn: () => routingApi.listRules().then((r) => r.data),
    enabled: isAdmin,
  })
  // 可视化数据源（内部角色均可访问）
  const { data: lfStats } = useQuery({
    queryKey: ['dash-lf-stats'],
    queryFn: () => langfuseMetricsApi.stats({}).then((r) => r.data),
  })
  const { data: lfTrends, isLoading: lfTrendsLoading } = useQuery({
    queryKey: ['dash-lf-trends'],
    queryFn: () => langfuseMetricsApi.trends({ bucket: 'day' }).then((r) => r.data),
  })
  const { data: fbStats, isLoading: fbStatsLoading } = useQuery({
    queryKey: ['dash-fb-stats'],
    queryFn: () => feedbackReviewApi.stats().then((r) => r.data),
  })

  const datasetCount = datasets?.length ?? 0
  const projectCount = projects?.length ?? 0
  const pendingCount = pendingCandidates?.total ?? 0
  const traceCount = lfStats?.total_traces ?? 0
  const feedbackTotal = fbStats?.total_feedbacks ?? 0
  const isRunning = schedulerStatus?.running ?? false
  const activeWatches = schedulerStatus?.watches?.filter((w) => w.status === 'active').length ?? 0
  const activeRules = rules?.filter((r) => r.is_active).length ?? 0

  // 运行趋势：调用量（面积）+ 错误数（面积），按天。
  const trendData = (lfTrends?.buckets ?? []).map((b) => ({
    date: b.date.slice(5, 10), // MM-DD
    trace_count: b.trace_count,
    error_count: b.error_count,
  }))

  // 反馈概览：按租户覆盖率(%) + 平均分，Top 8。
  const fbData = (fbStats?.rows ?? [])
    .slice(0, 8)
    .map((r) => ({
      name: r.tenant_name,
      coverage: Math.round((r.coverage ?? 0) * 100),
      avg_overall: r.avg_overall ?? 0,
    }))

  // 数据资产分布：备选数据集按样例数 Top 8。
  const assetData = [...(datasets ?? [])]
    .sort((a, b) => (b.example_count ?? 0) - (a.example_count ?? 0))
    .slice(0, 8)
    .map((ds) => ({ name: ds.name, count: ds.example_count ?? 0 }))

  // 快捷操作：自动采集仅 admin 可见（其余对内部角色均可达）。
  const quickActions: Array<{ to: string; title: string; subtitle: string }> = [
    { to: '/datasets', title: '浏览备选数据集', subtitle: '管理备选样例' },
    { to: '/projects', title: '基准测试集', subtitle: '管理评测基准' },
    { to: '/evaluation', title: '运行评估', subtitle: '基准评测与对比' },
    { to: '/tracing-metrics', title: 'Tracing 指标', subtitle: '调用趋势与延迟' },
    { to: '/feedback', title: '客户反馈', subtitle: '查看评审回流' },
    ...(isAdmin
      ? [{ to: '/auto-collect', title: '自动采集配置', subtitle: '调度器与路由规则' }]
      : []),
  ]

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">概览</div>
        <h1 className="page-title">仪表盘</h1>
      </header>

      {/* KPI 指标卡 */}
      <div className="grid grid-cols-5 gap-3 mb-8">
        <Link to="/datasets" className="no-underline block">
          <MetricCard label="备选数据集" value={datasetCount} hint="个数据集" />
        </Link>
        <Link to="/projects" className="no-underline block">
          <MetricCard label="基准测试集" value={projectCount} hint="个项目" />
        </Link>
        <Link to="/projects" className="no-underline block">
          <MetricCard label="暂存区" value={pendingCount} hint="待补全答案" tone="warning" />
        </Link>
        <Link to="/tracing-metrics" className="no-underline block">
          <MetricCard label="调用轨迹" value={traceCount} hint="近 30 天 trace" />
        </Link>
        <Link to="/feedback" className="no-underline block">
          <MetricCard label="客户反馈" value={feedbackTotal} hint="条反馈" />
        </Link>
      </div>

      {/* 可视化区：运行趋势 + 反馈概览 */}
      <div className="grid grid-cols-2 gap-3 mb-3">
        <ChartCard
          title="Tracing 运行趋势"
          hint="按天 · 近 30 天"
          loading={lfTrendsLoading}
          empty={trendData.length === 0}
        >
          <AreaChart data={trendData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <defs>
              <linearGradient id="dashTrace" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.indigo} stopOpacity={0.35} />
                <stop offset="100%" stopColor={COLORS.indigo} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="dashErr" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.rose} stopOpacity={0.35} />
                <stop offset="100%" stopColor={COLORS.rose} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid {...GRID_PROPS} />
            <XAxis dataKey="date" tick={AXIS_TICK} />
            <YAxis tick={AXIS_TICK} allowDecimals={false} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Area
              type="monotone"
              dataKey="trace_count"
              name="调用量"
              stroke={COLORS.indigo}
              strokeWidth={1.8}
              fill="url(#dashTrace)"
            />
            <Area
              type="monotone"
              dataKey="error_count"
              name="错误数"
              stroke={COLORS.rose}
              strokeWidth={1.8}
              fill="url(#dashErr)"
            />
          </AreaChart>
        </ChartCard>

        <ChartCard
          title="客户反馈概览"
          hint="按租户 · 覆盖率与均分"
          loading={fbStatsLoading}
          empty={fbData.length === 0}
        >
          <BarChart data={fbData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid {...GRID_PROPS} />
            <XAxis dataKey="name" tick={AXIS_TICK} interval={0} angle={-15} textAnchor="end" height={48} />
            <YAxis
              yAxisId="left"
              tick={AXIS_TICK}
              domain={[0, 100]}
              tickFormatter={(v) => `${v}%`}
            />
            <YAxis yAxisId="right" orientation="right" tick={AXIS_TICK} domain={[0, 5]} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar yAxisId="left" dataKey="coverage" name="覆盖率 (%)" fill={COLORS.emerald} radius={[3, 3, 0, 0]} />
            <Bar yAxisId="right" dataKey="avg_overall" name="平均分" fill={COLORS.amber} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartCard>
      </div>

      {/* 数据资产分布 */}
      <div className="mb-8">
        <ChartCard
          title="数据资产分布"
          hint="备选数据集 · 按样例数 Top 8"
          loading={!datasets}
          empty={assetData.length === 0}
          height={200}
        >
          <BarChart data={assetData} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 8 }}>
            <CartesianGrid {...GRID_PROPS} horizontal={false} />
            <XAxis type="number" tick={AXIS_TICK} allowDecimals={false} />
            <YAxis type="category" dataKey="name" tick={AXIS_TICK} width={120} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Bar dataKey="count" name="样例数" radius={[0, 3, 3, 0]}>
              {assetData.map((_, i) => (
                <Cell
                  key={i}
                  fill={[COLORS.indigo, COLORS.sky, COLORS.violet, COLORS.emerald][i % 4]}
                />
              ))}
            </Bar>
          </BarChart>
        </ChartCard>
      </div>

      {/* admin 专属：自动采集状态卡 */}
      {isAdmin && (
        <div className="grid grid-cols-4 gap-3 mb-8">
          <Link to="/auto-collect" className="no-underline block">
            <MetricCard
              label="自动采集"
              value={isRunning ? '开启' : '关闭'}
              hint={`${activeWatches} 监听 · ${activeRules} 规则`}
              tone={isRunning ? 'positive' : 'negative'}
            />
          </Link>
        </div>
      )}

      {/* 快捷操作 */}
      <div className="section-row">
        <div className="page-eyebrow">快捷操作</div>
      </div>
      <div className="grid grid-cols-4 gap-3 mb-8">
        {quickActions.map((a) => (
          <Link
            key={a.to}
            to={a.to}
            className="card px-4 py-3.5 no-underline transition-[transform,box-shadow,border-color] duration-200 ease-standard hover:-translate-y-0.5 hover:shadow-sm hover:border-border-strong"
          >
            <div className="text-[12px] font-medium text-text-primary">{a.title}</div>
            <div className="text-[10px] text-text-tertiary mt-1">{a.subtitle}</div>
          </Link>
        ))}
      </div>

      {/* 最近数据集 */}
      <div className="section-row">
        <div className="page-eyebrow">最近数据集</div>
        <Link
          to="/datasets"
          className="text-[11px] text-accent hover:text-accent-hover no-underline transition-colors"
        >
          查看全部
        </Link>
      </div>
      <div className="table-card">
        {datasets && datasets.length > 0 ? (
          <table className="table-base">
            <thead>
              <tr>
                <th>名称</th>
                <th className="w-24 text-right">样例数</th>
                <th>描述</th>
              </tr>
            </thead>
            <tbody>
              {datasets.slice(0, 5).map((ds) => (
                <tr key={ds.id}>
                  <td className="font-medium">
                    <Link
                      to={`/datasets/${ds.name}`}
                      className="text-text-primary hover:text-accent no-underline transition-colors"
                    >
                      {ds.name}
                    </Link>
                  </td>
                  <td className="text-right text-text-secondary tabular-nums">{ds.example_count}</td>
                  <td className="text-text-tertiary truncate max-w-[280px]">{ds.description || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">暂无数据集</div>
        )}
      </div>
    </div>
  )
}
