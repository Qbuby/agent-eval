import { ResponsiveContainer } from 'recharts'

// ──────────────────────────────────────────────────────────────────────────
// 仪表盘共享可视化原语。调色板与图表容器对齐 LangfuseMetricsPage 的视觉，
// 让仪表盘的图表与 Tracing 指标页观感一致（同一套品牌色 + 同款卡片外壳）。
// ──────────────────────────────────────────────────────────────────────────

export const COLORS = {
  indigo: '#6366f1',
  amber: '#f59e0b',
  emerald: '#10b981',
  rose: '#f43f5e',
  sky: '#0ea5e9',
  violet: '#8b5cf6',
}

// 图表容器：统一标题 + loading / empty 占位 + ResponsiveContainer。
// 与 LangfuseMetricsPage.ChartCard 同形，但高度可配（仪表盘图更紧凑）。
export function ChartCard({
  title,
  hint,
  loading,
  empty,
  height = 220,
  children,
}: {
  title: string
  hint?: string
  loading: boolean
  empty: boolean
  height?: number
  children: React.ReactElement
}) {
  return (
    <div className="table-card !p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div className="metric-eyebrow">{title}</div>
        {hint && <div className="text-[10px] text-text-tertiary">{hint}</div>}
      </div>
      {loading ? (
        <div
          className="flex items-center justify-center text-[12px] text-text-tertiary"
          style={{ height }}
        >
          加载中…
        </div>
      ) : empty ? (
        <div
          className="flex items-center justify-center text-[12px] text-text-tertiary"
          style={{ height }}
        >
          暂无数据
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          {children}
        </ResponsiveContainer>
      )}
    </div>
  )
}

// KPI 指标卡。to 提供则整卡为可点链接（用 <a>，调用方传 Link 渲染时包裹即可）。
export function MetricCard({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: React.ReactNode
  hint?: string
  tone?: 'default' | 'positive' | 'negative' | 'warning'
}) {
  const toneCls =
    tone === 'positive'
      ? 'text-positive'
      : tone === 'negative'
        ? 'text-negative'
        : tone === 'warning'
          ? 'text-warning'
          : ''
  return (
    <div className="metric-card">
      <div className="metric-eyebrow">{label}</div>
      <div className={`metric-value ${toneCls}`}>{value}</div>
      {hint && <div className="text-[11px] text-text-tertiary mt-1">{hint}</div>}
    </div>
  )
}

// recharts 轴/网格的通用样式。对齐 LangfuseMetricsPage：网格用 currentColor +
// 低透明度，轴 tick 仅设字号（继承父级 currentColor），暗色模式天然自适应，
// 不依赖具体 CSS 变量值，零风险。
export const AXIS_TICK = { fontSize: 11 }
export const GRID_PROPS = { strokeDasharray: '3 3', stroke: 'currentColor', opacity: 0.1 } as const
export const TOOLTIP_STYLE = {
  contentStyle: {
    background: 'rgb(var(--surface))',
    border: '1px solid rgb(var(--border) / 0.6)',
    borderRadius: 8,
    fontSize: 12,
  },
}
