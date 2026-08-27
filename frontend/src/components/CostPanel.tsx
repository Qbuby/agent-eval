import { useMemo } from 'react'
import { usePricing } from '@/hooks/usePricing'
import {
  aggregateCost,
  formatCost,
  formatUnitPrice,
  PRICING_NOTE,
  type CostAggregate,
  type TokenUsage,
} from '@/lib/pricing'
import type { EvalResultRow } from '@/types'
import { PricingButton } from '@/components/ModelPricingDrawer'

/** 从结果行取 A 侧 token 计数。 */
function usageOfA(row: EvalResultRow): TokenUsage {
  return {
    prompt_tokens: row.prompt_tokens,
    completion_tokens: row.completion_tokens,
    cache_read_tokens: row.cache_read_tokens,
    cache_creation_tokens: row.cache_creation_tokens,
  }
}

/** 从结果行取 B 侧 token 计数（对比 run 才有）。 */
function usageOfB(row: EvalResultRow): TokenUsage | null {
  const b = row.comparison?.agent_b
  if (!b) return null
  return {
    prompt_tokens: b.prompt_tokens,
    completion_tokens: b.completion_tokens,
    cache_read_tokens: b.cache_read_tokens,
    cache_creation_tokens: b.cache_creation_tokens,
  }
}

function TokenRow({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex justify-between border-b border-separator pb-1" title={hint}>
      <span className="text-text-tertiary">{label}</span>
      <span className="font-mono text-text-primary">{value}</span>
    </div>
  )
}

// ─── 可视化 ─────────────────────────────────────────────────────────────────
//
// 三档计费的颜色跟「实体」走（单 run 内 A=accent 绿 / B=info 蓝，与全站 A/B 语义色
// 一致；跨 run 对比时由调用方给每个运行指定色，与该页圆点/图表同色），
// 档位靠同色系深浅区分——切勿给三档另配一组独立色，那会和实体色抢语义。
// 深浅按单价档递进：命中输入（最便宜）最浅 → 未命中输入 → 输出（最贵）最深。

/** 单个 run 内部的两侧。跨 run 对比不用这个，直接给 rgbVar。 */
type Side = 'a' | 'b'

const SIDE_RGB: Record<Side, string> = { a: 'var(--accent)', b: 'var(--info)' }
const TIER_ALPHA = [0.32, 0.6, 1] as const

/** rgbVar 为未包 rgb() 的原始 CSS 变量（如 `var(--accent)`），才能按档叠 alpha。 */
const tierFill = (rgbVar: string, tier: number) => `rgb(${rgbVar} / ${TIER_ALPHA[tier]})`

const sharePct = (value: number, total: number) =>
  total > 0 ? `${((value / total) * 100).toFixed(0)}%` : '—'

/**
 * 单条水平堆叠：段宽按占比，相邻段留 2px 表面缝隙分隔（非描边），与
 * EvaluationRunDetailPage 的 WinRateStackBar 同形态。
 * 数值不入段内——各段深浅不同，段内文字对比度不可控，统一由图例承载。
 */
function StackBar({ label, segments, ariaLabel }: {
  label: string
  segments: Array<{ key: string; value: number; fill: string; title: string }>
  ariaLabel: string
}) {
  const drawn = segments.filter(s => s.value > 0)
  const total = drawn.reduce((sum, s) => sum + s.value, 0)
  if (total <= 0) return null
  return (
    <div>
      <div className="text-[10px] text-text-tertiary mb-1">{label}</div>
      <div className="flex h-5 w-full overflow-hidden rounded" role="img" aria-label={ariaLabel}>
        {drawn.map((s, i) => (
          <div
            key={s.key}
            style={{
              width: `${(s.value / total) * 100}%`,
              backgroundColor: s.fill,
              marginLeft: i > 0 ? 2 : 0,
            }}
            title={s.title}
          />
        ))}
      </div>
    </div>
  )
}

/**
 * token 构成 vs 成本构成对照。两条同色同序的堆叠条上下并排——同一档在两条里
 * 宽度错开，就是「token 多不等于花钱多」的直接证据（缓存命中常占掉大半 token
 * 却只摊到一小半成本）。未配价时成本条无意义，只画 token 条。
 *
 * 口径：缓存写入 token 已含在未命中输入内（见 pricing.ts 的计价口径），故不单列
 * 成段，否则同一批 token 会被数两次、占比失真。
 */
function CostShareBars({ agg, currency, rgbVar }: {
  agg: CostAggregate
  currency: string
  rgbVar: string
}) {
  const tiers = [
    { key: 'hit', label: '命中输入', tokens: agg.hitInputTokens, cost: agg.hitInputCost },
    { key: 'miss', label: '未命中输入', tokens: agg.missInputTokens, cost: agg.missInputCost },
    { key: 'out', label: '输出', tokens: agg.outputTokens, cost: agg.outputCost },
  ].map((t, i) => ({ ...t, fill: tierFill(rgbVar, i) }))

  const tokTotal = tiers.reduce((sum, t) => sum + t.tokens, 0)
  if (tokTotal <= 0) return null
  const costTotal = agg.total != null && agg.total > 0 ? agg.total : null

  const describe = (pick: (t: typeof tiers[number]) => number, total: number) =>
    tiers.map(t => `${t.label} ${sharePct(pick(t), total)}`).join('，')

  return (
    <div className="space-y-2 mb-3">
      <StackBar
        label="token 构成"
        segments={tiers.map(t => ({
          key: t.key,
          value: t.tokens,
          fill: t.fill,
          title: `${t.label}：${t.tokens.toLocaleString()} tok（${sharePct(t.tokens, tokTotal)}）`,
        }))}
        ariaLabel={`token 构成：${describe(t => t.tokens, tokTotal)}`}
      />
      {costTotal != null && (
        <StackBar
          label="成本构成"
          segments={tiers.map(t => ({
            key: t.key,
            value: t.cost,
            fill: t.fill,
            title: `${t.label}：${formatCost(t.cost, currency)}（${sharePct(t.cost, costTotal)}）`,
          }))}
          ariaLabel={`成本构成：${describe(t => t.cost, costTotal)}`}
        />
      )}
      <div className="grid grid-cols-3 gap-x-3 text-[10px]">
        {tiers.map(t => (
          <div key={t.key} className="min-w-0">
            <div className="flex items-center gap-1 text-text-secondary">
              <span
                className="inline-block h-2 w-2 rounded-sm shrink-0"
                style={{ backgroundColor: t.fill }}
              />
              <span className="truncate">{t.label}</span>
            </div>
            <div className="pl-3 mt-0.5 text-text-tertiary tabular-nums">
              tok {sharePct(t.tokens, tokTotal)}
              {costTotal != null && ` · 钱 ${sharePct(t.cost, costTotal)}`}
            </div>
            {costTotal != null && (
              <div className="pl-3 font-mono text-text-secondary">{formatCost(t.cost, currency)}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * 总成本同标尺对比：N 条按同一最大值缩放，条长直接可比——表格里几个数字要来回读
 * 才知道差多少，条长是一眼的事。颜色跟实体走（单 run 内 A=accent / B=info；跨 run
 * 由调用方按运行指定，与该页圆点/图表同色）。
 * 条内不放文字，金额固定在右侧列——避免短条上的数字被裁掉。
 *
 * caption 必须画在屏幕上：多组条上下相邻时，条内标签都是实体名，唯一能区分口径
 * （总成本 / 单样例均成本）的就是组标题——样例数相同的两个运行会让两组条长完全
 * 一样，只把口径写进 aria-label 等于视觉上没写。
 */
function CostScaleBars({ rows, currency, labelWidthClass = 'w-14', caption = '总成本对比' }: {
  rows: Array<{ key: string; label: string; value: number; rgbVar: string }>
  currency: string
  /** 标签列宽。跨 run 的 `id · model` 比「A 侧」长，需要更宽。 */
  labelWidthClass?: string
  /** 可见组标题，同时作为 aria 前缀——口径只维护一份，不会图文不一致。 */
  caption?: string
}) {
  const max = Math.max(0, ...rows.map(r => r.value))
  if (max <= 0) return null
  return (
    /* mt-3 > 组内 space-y-1：组间距明显大于行间距，四条才不会读成一组。 */
    <div className="mt-3">
      <div className="text-[10px] font-medium text-text-secondary mb-1">{caption}</div>
      <div
        className="space-y-1"
        role="img"
        aria-label={`${caption}：${rows.map(r => `${r.label} ${formatCost(r.value, currency)}`).join('，')}`}
      >
        {rows.map(r => (
          <div key={r.key} className="flex items-center gap-2 text-[10px]">
            <span className={`${labelWidthClass} shrink-0 text-text-tertiary truncate`} title={r.label}>{r.label}</span>
            <div className="flex-1 h-3 rounded-sm bg-fill/10 overflow-hidden">
              <div
                className="h-full rounded-sm"
                style={{ width: `${(r.value / max) * 100}%`, backgroundColor: tierFill(r.rgbVar, 2) }}
              />
            </div>
            <span className="w-20 shrink-0 text-right font-mono text-text-secondary tabular-nums">
              {formatCost(r.value, currency)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * 单侧成本卡：总成本 / 均成本 / 三档计费 token。
 * 单价来自本地价格配置，未配价时明示「未配价」而不是显示 0。
 */
function CostColumn({
  title,
  model,
  agg,
  currency,
  rgbVar,
}: {
  title: string
  model: string | null
  agg: CostAggregate
  currency: string
  /** 该列实体的图形色，未包 rgb() 的原始 CSS 变量（单 run 内用 SIDE_RGB，跨 run 由调用方指定）。 */
  rgbVar: string
}) {
  const { priceOf, matchOf } = usePricing()
  const price = priceOf(model)
  const match = matchOf(model)

  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="page-eyebrow">{title}</h3>
        {model && (
          <span className="font-mono text-[10px] text-text-tertiary truncate max-w-[45%]" title={model}>
            {model}
          </span>
        )}
      </div>

      <div className="flex items-baseline gap-3 mb-3">
        <div>
          <div className="metric-eyebrow">总成本</div>
          <div className="metric-value">{formatCost(agg.total, currency)}</div>
        </div>
        <div>
          <div className="metric-eyebrow">单样例均值</div>
          <div className="metric-value">{formatCost(agg.mean, currency)}</div>
        </div>
      </div>

      {!price && (
        <div className="text-[11px] text-warning mb-2">
          该模型未配价，无法计算成本。点右上「模型价格」填入单价。
        </div>
      )}
      {price && match && !match.exact && (
        <div className="text-[11px] text-text-tertiary mb-2">
          按前缀匹配到 <span className="font-mono">{match.matchedKey}</span> 的单价。
        </div>
      )}
      {price && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] mb-3">
          <TokenRow label="命中输入单价" value={formatUnitPrice(price.inputHit, currency)} />
          <TokenRow label="未命中输入单价" value={formatUnitPrice(price.inputMiss, currency)} />
          <TokenRow label="输出单价" value={formatUnitPrice(price.output, currency)} />
        </div>
      )}

      {/* 构成条放在单价与 token 明细之间：先知道单价，再看量与钱分别落在哪一档。 */}
      <CostShareBars agg={agg} currency={currency} rgbVar={rgbVar} />

      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
        <TokenRow label="计价样例数" value={String(agg.n)} />
        <TokenRow
          label="未配价样例"
          value={String(agg.unpriced)}
          hint="有 token 数据但模型未配价，未计入总成本"
        />
        <TokenRow label="命中输入 tok" value={agg.hitInputTokens.toLocaleString()} />
        <TokenRow label="未命中输入 tok" value={agg.missInputTokens.toLocaleString()} />
        <TokenRow label="输出 tok" value={agg.outputTokens.toLocaleString()} />
        <TokenRow
          label="缓存写入 tok"
          value={agg.cacheWriteTokens.toLocaleString()}
          hint="缓存写入按未命中输入档计费，已含在上方未命中输入 tok 内"
        />
      </div>
    </div>
  )
}

/**
 * run 的实算成本区。单模一列，对比 run 出 A/B 两列并给出差额。
 * 改价后本区随 usePricing 订阅即时重算，无需刷新页面。
 */
export function RunCostSection({
  items,
  modelA,
  modelB,
  comparative = false,
}: {
  items: EvalResultRow[]
  modelA: string | null
  modelB?: string | null
  comparative?: boolean
}) {
  const { currency, priceOf } = usePricing()

  const aggA = useMemo(
    () => aggregateCost(items.map(row => ({ usage: usageOfA(row), price: priceOf(modelA) }))),
    [items, modelA, priceOf],
  )
  const aggB = useMemo(() => {
    if (!comparative) return null
    const rows = items
      .map(row => usageOfB(row))
      .filter((u): u is TokenUsage => u != null)
      .map(usage => ({ usage, price: priceOf(modelB) }))
    return aggregateCost(rows)
  }, [comparative, items, modelB, priceOf])

  const delta =
    aggA.total != null && aggB?.total != null ? aggB.total - aggA.total : null
  const deltaPct =
    delta != null && aggA.total != null && aggA.total > 0 ? (delta / aggA.total) * 100 : null

  return (
    <section className="mb-5">
      <div className="section-row mb-2">
        <div className="page-eyebrow">实际成本</div>
        <PricingButton
          suggestModels={[modelA, modelB ?? null].filter((m): m is string => Boolean(m))}
        />
      </div>

      <div className={comparative ? 'grid grid-cols-2 gap-3' : ''}>
        <CostColumn
          title={comparative ? 'A 侧成本' : '本次运行成本'}
          model={modelA}
          agg={aggA}
          currency={currency}
          rgbVar={SIDE_RGB.a}
        />
        {comparative && aggB && (
          <CostColumn
            title="B 侧成本"
            model={modelB ?? null}
            agg={aggB}
            currency={currency}
            rgbVar={SIDE_RGB.b}
          />
        )}
      </div>

      {delta != null && (
        <div className="mt-2 text-[11px] text-text-secondary">
          B 相对 A {delta >= 0 ? '多花' : '省下'} {formatCost(Math.abs(delta), currency)}
          {deltaPct != null && `（${deltaPct >= 0 ? '+' : ''}${deltaPct.toFixed(1)}%）`}
        </div>
      )}

      {/* 差额文字之后紧跟同标尺条：文字给差多少，条给差多大。 */}
      {aggA.total != null && aggB?.total != null && (
        <CostScaleBars
          rows={[
            { key: 'a', label: 'A 侧', value: aggA.total, rgbVar: SIDE_RGB.a },
            { key: 'b', label: 'B 侧', value: aggB.total, rgbVar: SIDE_RGB.b },
          ]}
          currency={currency}
        />
      )}

      <div className="mt-2 text-[10px] text-text-tertiary">{PRICING_NOTE}</div>
    </section>
  )
}

/** 跨运行成本区的单个运行入参。rgbVar 必须是未包 rgb() 的裸变量。 */
export interface CrossRunCostEntry {
  id: string
  /** 与该页圆点/图表一致的运行标签。 */
  label: string
  model: string | null
  items: EvalResultRow[]
  /** 该运行在本页的实体色，形如 `var(--accent)`。传已包 rgb() 的值会生成非法 CSS。 */
  rgbVar: string
}

/**
 * N 个运行的实算成本区。每个运行一列（总成本 / 均成本 / 三档构成条），
 * 下方给同标尺对比条：总成本按批量比，均成本按单样例比——两个运行样例数不同时，
 * 只看总成本会把「样例更多」误读成「更贵」。
 *
 * 颜色跟运行实体走，由调用方按运行传入，与该页概览圆点、图表同色；勾选子集
 * 重算时颜色不变（色不跟排名走）。
 */
export function CrossRunCostSection({ entries, subsetNote }: {
  entries: CrossRunCostEntry[]
  /** 子集模式提示，例如「· 子集（12 个样例）」。 */
  subsetNote?: string
}) {
  const { currency, priceOf } = usePricing()

  const priced = useMemo(
    () => entries.map(e => ({
      ...e,
      agg: aggregateCost(e.items.map(row => ({ usage: usageOfA(row), price: priceOf(e.model) }))),
    })),
    [entries, priceOf],
  )

  if (priced.length === 0) return null

  const totalRows = priced
    .filter(p => p.agg.total != null)
    .map(p => ({ key: p.id, label: p.label, value: p.agg.total as number, rgbVar: p.rgbVar }))
  const meanRows = priced
    .filter(p => p.agg.mean != null)
    .map(p => ({ key: p.id, label: p.label, value: p.agg.mean as number, rgbVar: p.rgbVar }))

  // 极差用均成本口径：样例数不同的运行之间，只有单样例成本才可比。
  const cheapest = meanRows.length >= 2
    ? meanRows.reduce((min, r) => (r.value < min.value ? r : min))
    : null
  const dearest = meanRows.length >= 2
    ? meanRows.reduce((max, r) => (r.value > max.value ? r : max))
    : null
  const spreadPct = cheapest && dearest && cheapest.value > 0
    ? ((dearest.value - cheapest.value) / cheapest.value) * 100
    : null

  return (
    <section className="mb-5">
      <div className="section-row mb-2">
        <div className="page-eyebrow">实算成本（按运行）{subsetNote}</div>
        <PricingButton
          suggestModels={priced.map(p => p.model).filter((m): m is string => Boolean(m))}
        />
      </div>

      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))' }}
      >
        {priced.map(p => (
          <CostColumn
            key={p.id}
            title={p.label}
            model={p.model}
            agg={p.agg}
            currency={currency}
            rgbVar={p.rgbVar}
          />
        ))}
      </div>

      {cheapest && dearest && cheapest.key !== dearest.key && (
        <div className="mt-2 text-[11px] text-text-secondary">
          单样例最省 <span className="font-mono">{cheapest.label}</span>（{formatCost(cheapest.value, currency)}），
          最贵 <span className="font-mono">{dearest.label}</span>（{formatCost(dearest.value, currency)}）
          {spreadPct != null && `，贵 ${spreadPct.toFixed(1)}%`}
        </div>
      )}

      {/* 文字给差多少，条给差多大；总成本与均成本各一条，避免样例数差异误读。 */}
      {totalRows.length >= 2 && (
        <CostScaleBars rows={totalRows} currency={currency} labelWidthClass="w-32" caption="总成本对比" />
      )}
      {meanRows.length >= 2 && (
        <CostScaleBars rows={meanRows} currency={currency} labelWidthClass="w-32" caption="单样例均成本对比" />
      )}

      <div className="mt-2 text-[10px] text-text-tertiary">{PRICING_NOTE}</div>
    </section>
  )
}
