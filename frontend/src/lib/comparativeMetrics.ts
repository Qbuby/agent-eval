import type {
  Comparison,
  ComparisonEvaluatorSummary,
  ComparisonEvaluatorVerdict,
  ComparisonSummary,
  ComparisonVerdict,
  EvalResultRow,
} from '@/types'

export interface NormalizedComparisonVerdict extends ComparisonEvaluatorVerdict {
  evaluatorKey: string
  legacy: boolean
}

export interface NormalizedComparisonSummary extends ComparisonEvaluatorSummary {
  evaluator_key: string
  legacy: boolean
}

function evaluatorKey(entry: {
  evaluator_version_id?: string | null
  evaluator_id?: string | null
  tag?: string | null
  label?: string | null
}): string {
  return String(
    entry.evaluator_version_id
      || entry.evaluator_id
      || entry.tag
      || entry.label
      || 'legacy',
  )
}

/** New evaluator_verdicts always wins, including an explicitly empty array. */
export function normalizeComparisonVerdicts(
  comparison: Comparison | null | undefined,
): NormalizedComparisonVerdict[] {
  if (!comparison) return []
  if (Array.isArray(comparison.evaluator_verdicts)) {
    return comparison.evaluator_verdicts.map((entry) => ({
      ...entry,
      evaluatorKey: evaluatorKey(entry),
      legacy: false,
    }))
  }
  if (!comparison.verdict) return []
  return [{
    evaluatorKey: 'legacy',
    evaluator_id: null,
    evaluator_version_id: null,
    label: '历史结果',
    tag: 'legacy',
    status: 'scored',
    verdict: comparison.verdict,
    error: null,
    legacy: true,
  }]
}

/** New evaluators always wins; old top-level summary becomes one identity-less group. */
export function normalizeComparisonSummary(
  summary: ComparisonSummary | null | undefined,
): NormalizedComparisonSummary[] {
  if (!summary) return []
  if (Array.isArray(summary.evaluators)) {
    return summary.evaluators.map((entry) => ({
      ...entry,
      evaluator_key: entry.evaluator_key || evaluatorKey(entry),
      legacy: entry.legacy === true,
    }))
  }
  const hasLegacySummary = [summary.total, summary.a_wins, summary.b_wins, summary.ties]
    .some(value => typeof value === 'number')
    || summary.per_dimension != null
  if (!hasLegacySummary) return []
  const total = summary.total ?? 0
  return [{
    evaluator_key: 'legacy',
    evaluator_id: null,
    evaluator_version_id: null,
    label: '历史结果',
    tag: 'legacy',
    legacy: true,
    total,
    scored: total,
    evaluation_errors: 0,
    a_wins: summary.a_wins ?? 0,
    b_wins: summary.b_wins ?? 0,
    ties: summary.ties ?? 0,
    per_dimension: summary.per_dimension ?? {},
  }]
}

export type ComparativeResourceMetricKey =
  | 'promptTokens'
  | 'completionTokens'
  | 'totalTokens'
  | 'cacheCreationTokens'
  | 'cacheReadTokens'
  | 'toolCalls'
  | 'attempts'

export type ComparativePerformanceMetricKey =
  | 'latencyMs'
  | 'firstThinkingTokenMs'
  | 'firstAnswerTokenMs'

export interface SumMeanMetric {
  sum: number | null
  mean: number | null
  n: number
}

export interface MeanMetric {
  mean: number | null
  n: number
}

export interface DeltaMetric {
  value: number | null
  percent: number | null
}

export interface ResourceMetricComparison {
  a: SumMeanMetric
  b: SumMeanMetric
  sumDelta: DeltaMetric
  meanDelta: DeltaMetric
}

export interface PerformanceMetricComparison {
  a: MeanMetric
  b: MeanMetric
  meanDelta: DeltaMetric
}

export interface CacheHitRateMetric {
  value: number | null
  promptN: number
  cacheReadN: number
}

export interface ComparativeResourceAggregate {
  totalRows: number
  resources: Record<ComparativeResourceMetricKey, ResourceMetricComparison>
  performance: Record<ComparativePerformanceMetricKey, PerformanceMetricComparison>
  cacheHitRate: { a: CacheHitRateMetric; b: CacheHitRateMetric; delta: DeltaMetric }
}

export const comparativeResourceMetricLabels: Record<ComparativeResourceMetricKey, string> = {
  promptTokens: '输入 token',
  completionTokens: '输出 token',
  totalTokens: '总 token',
  cacheCreationTokens: '缓存写入 token',
  cacheReadTokens: '缓存命中 token',
  toolCalls: '工具调用数',
  attempts: '尝试次数',
}

export const comparativePerformanceMetricLabels: Record<ComparativePerformanceMetricKey, string> = {
  latencyMs: '总时延',
  firstThinkingTokenMs: '首思考 token 时延',
  firstAnswerTokenMs: '首回答 token 时延',
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function sumMean(values: unknown[]): SumMeanMetric {
  const valid = values.map(finite).filter((value): value is number => value != null)
  if (valid.length === 0) return { sum: null, mean: null, n: 0 }
  const sum = valid.reduce((acc, value) => acc + value, 0)
  return { sum, mean: sum / valid.length, n: valid.length }
}

function meanOnly(values: unknown[]): MeanMetric {
  const metric = sumMean(values)
  return { mean: metric.mean, n: metric.n }
}

function delta(b: number | null, a: number | null): DeltaMetric {
  if (a == null || b == null) return { value: null, percent: null }
  const value = b - a
  return { value, percent: a !== 0 ? value / a : null }
}

function compareResource(aValues: unknown[], bValues: unknown[]): ResourceMetricComparison {
  const a = sumMean(aValues)
  const b = sumMean(bValues)
  return {
    a,
    b,
    sumDelta: delta(b.sum, a.sum),
    meanDelta: delta(b.mean, a.mean),
  }
}

function comparePerformance(aValues: unknown[], bValues: unknown[]): PerformanceMetricComparison {
  const a = meanOnly(aValues)
  const b = meanOnly(bValues)
  return { a, b, meanDelta: delta(b.mean, a.mean) }
}

function cacheHitRate(prompt: SumMeanMetric, cacheRead: SumMeanMetric): CacheHitRateMetric {
  return {
    value: prompt.sum != null && prompt.sum > 0 && cacheRead.sum != null
      ? cacheRead.sum / prompt.sum
      : null,
    promptN: prompt.n,
    cacheReadN: cacheRead.n,
  }
}

/**
 * Aggregate actual A/B executions, regardless of judge success. Missing values stay missing and
 * each metric receives its own coverage n. A is the result row; B is comparison.agent_b.
 */
export function aggregateComparativeResources(items: EvalResultRow[]): ComparativeResourceAggregate {
  const bRows = items.map(item => item.comparison?.agent_b)
  const resources: ComparativeResourceAggregate['resources'] = {
    promptTokens: compareResource(items.map(r => r.prompt_tokens), bRows.map(r => r?.prompt_tokens)),
    completionTokens: compareResource(items.map(r => r.completion_tokens), bRows.map(r => r?.completion_tokens)),
    totalTokens: compareResource(items.map(r => r.total_tokens), bRows.map(r => r?.total_tokens)),
    cacheCreationTokens: compareResource(items.map(r => r.cache_creation_tokens), bRows.map(r => r?.cache_creation_tokens)),
    cacheReadTokens: compareResource(items.map(r => r.cache_read_tokens), bRows.map(r => r?.cache_read_tokens)),
    toolCalls: compareResource(
      items.map(r => r.tool_call_count),
      bRows.map(r => Array.isArray(r?.tool_calls) ? r.tool_calls.length : null),
    ),
    attempts: compareResource(items.map(r => r.attempts_made), bRows.map(r => r?.attempts_made)),
  }
  const performance: ComparativeResourceAggregate['performance'] = {
    latencyMs: comparePerformance(items.map(r => r.latency_ms), bRows.map(r => r?.latency_ms)),
    firstThinkingTokenMs: comparePerformance(
      items.map(r => r.first_thinking_token_ms),
      bRows.map(r => r?.first_thinking_token_ms),
    ),
    firstAnswerTokenMs: comparePerformance(
      items.map(r => r.first_answer_token_ms),
      bRows.map(r => r?.first_answer_token_ms),
    ),
  }
  const cacheA = cacheHitRate(resources.promptTokens.a, resources.cacheReadTokens.a)
  const cacheB = cacheHitRate(resources.promptTokens.b, resources.cacheReadTokens.b)
  return {
    totalRows: items.length,
    resources,
    performance,
    cacheHitRate: { a: cacheA, b: cacheB, delta: delta(cacheB.value, cacheA.value) },
  }
}

export function evaluatorDisplayName(entry: {
  label?: string | null
  tag?: string | null
  legacy?: boolean
}): string {
  if (entry.legacy) return '历史结果（评估器身份不可恢复）'
  return entry.label || entry.tag || '未命名评估器'
}

export function firstScoredVerdict(
  comparison: Comparison | null | undefined,
): ComparisonVerdict | null {
  return normalizeComparisonVerdicts(comparison)
    .find(entry => entry.status === 'scored' && entry.verdict)?.verdict ?? null
}
