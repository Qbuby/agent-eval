import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, Cell } from 'recharts'
import { portalApi } from '@/services/portal'
import { ChartCard, MetricCard, COLORS, AXIS_TICK, GRID_PROPS, TOOLTIP_STYLE } from './charts'

// ──────────────────────────────────────────────────────────────────────────
// 外部客户仪表盘。数据源仅 GET /portal/stats（本租户聚合 + 本人评审进度），
// 天然租户过滤，外部客户只看到自己的数据。展示评审进度（已评 vs 总样例）+
// 快捷入口到样例评审。不触碰任何内部接口，零 403。
// ──────────────────────────────────────────────────────────────────────────

export default function ExternalDashboard() {
  const { data: stats, isLoading } = useQuery({
    queryKey: ['portal-stats'],
    queryFn: () => portalApi.stats().then((r) => r.data),
  })

  const batchCount = stats?.batch_count ?? 0
  const sampleCount = stats?.sample_count ?? 0
  const ratedCount = stats?.rated_count ?? 0
  const pendingCount = Math.max(0, sampleCount - ratedCount)
  const coveragePct = Math.round((stats?.coverage ?? 0) * 100)
  const avgOverall = stats?.avg_overall

  // 评审进度：每批次「已评 vs 待评」堆叠柱。
  const progressData = (stats?.by_batch ?? []).map((b) => ({
    name: b.name.length > 16 ? b.name.slice(0, 15) + '…' : b.name,
    rated: b.rated_count,
    pending: Math.max(0, b.sample_count - b.rated_count),
  }))

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">概览</div>
        <h1 className="page-title">仪表盘</h1>
      </header>

      {/* KPI 指标卡 */}
      <div className="grid grid-cols-4 gap-3 mb-8">
        <Link to="/portal" className="no-underline block">
          <MetricCard label="样例集" value={batchCount} hint="个批次" />
        </Link>
        <MetricCard label="总样例" value={sampleCount} hint="条待评审" />
        <MetricCard label="我已评" value={ratedCount} hint={`覆盖率 ${coveragePct}%`} tone="positive" />
        <MetricCard
          label="待评审"
          value={pendingCount}
          hint={avgOverall != null ? `我的均分 ${avgOverall}` : '尚未评分'}
          tone={pendingCount > 0 ? 'warning' : 'default'}
        />
      </div>

      {/* 评审进度图 */}
      <div className="mb-8">
        <ChartCard
          title="评审进度"
          hint="按样例集 · 已评 vs 待评"
          loading={isLoading}
          empty={progressData.length === 0}
          height={260}
        >
          <BarChart data={progressData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid {...GRID_PROPS} />
            <XAxis dataKey="name" tick={AXIS_TICK} interval={0} angle={-15} textAnchor="end" height={48} />
            <YAxis tick={AXIS_TICK} allowDecimals={false} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="rated" name="已评" stackId="a" fill={COLORS.emerald} radius={[0, 0, 0, 0]} />
            <Bar dataKey="pending" name="待评" stackId="a" fill={COLORS.amber} radius={[3, 3, 0, 0]}>
              {progressData.map((_, i) => (
                <Cell key={i} fill={COLORS.amber} />
              ))}
            </Bar>
          </BarChart>
        </ChartCard>
      </div>

      {/* 快捷操作 */}
      <div className="section-row">
        <div className="page-eyebrow">快捷操作</div>
      </div>
      <div className="grid grid-cols-4 gap-3">
        <Link
          to="/portal"
          className="card px-4 py-3.5 no-underline transition-[transform,box-shadow,border-color] duration-200 ease-standard hover:-translate-y-0.5 hover:shadow-sm hover:border-border-strong"
        >
          <div className="text-[12px] font-medium text-text-primary">样例评审</div>
          <div className="text-[10px] text-text-tertiary mt-1">逐条打分并提交反馈</div>
        </Link>
      </div>
    </div>
  )
}
