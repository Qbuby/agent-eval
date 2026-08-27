// 模型价格与成本（纯前端）。
//
// 后端不存价格：三档单价按模型名存浏览器 localStorage，渲染时把评估结果里已有的
// token 计数与单价相乘得出成本。改价立刻反映到所有展示点，不需要重跑评估。
//
// 计价口径（与 Anthropic usage 字段对齐，且与本项目缓存命中率算法同源）：
//   prompt_tokens 已包含 cache_read_tokens 与 cache_creation_tokens，因此
//   计费未命中输入 = prompt_tokens - cache_read_tokens（缓存写入已含在其中）。
//   缓存写入（cache_creation_tokens）按本项目约定归入未命中档计费，UI 单列其量以便核对。
//   成本 = 命中输入 × 命中价 + 未命中输入（含缓存写入）× 未命中价 + 输出 × 输出价。
//
// 单价一律按「每 100 万 token」录入。

const STORAGE_KEY = 'agent-eval-model-pricing'

/** 单价的计价粒度：每 100 万 token。 */
export const PRICE_UNIT_TOKENS = 1_000_000

/** 一个模型的三档单价，单位：货币 / 100 万 token。 */
export interface ModelPrice {
  /** 缓存未命中的输入价（真正重新计算的那部分输入，缓存写入也按此档计费）。 */
  inputMiss: number
  /** 缓存命中的输入价。 */
  inputHit: number
  /** 输出价。 */
  output: number
}

export interface PricingConfig {
  version: 1
  /** 货币符号，仅用于展示，不做换算。 */
  currency: string
  /** key 为规范化后的模型名（小写去空格）。 */
  models: Record<string, ModelPrice>
}

export const EMPTY_PRICE: ModelPrice = { inputMiss: 0, inputHit: 0, output: 0 }

const DEFAULT_CONFIG: PricingConfig = { version: 1, currency: '$', models: {} }

/** 模型名规范化：大小写与首尾空格不参与匹配。 */
export function normalizeModelKey(model: string | null | undefined): string {
  return (model ?? '').trim().toLowerCase()
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function sanitizePrice(raw: unknown): ModelPrice | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const inputMiss = finite(r.inputMiss) ?? 0
  const inputHit = finite(r.inputHit) ?? 0
  const output = finite(r.output) ?? 0
  if (inputMiss < 0 || inputHit < 0 || output < 0) return null
  return { inputMiss, inputHit, output }
}

function sanitizeConfig(raw: unknown): PricingConfig {
  if (!raw || typeof raw !== 'object') return DEFAULT_CONFIG
  const r = raw as Record<string, unknown>
  const models: Record<string, ModelPrice> = {}
  if (r.models && typeof r.models === 'object') {
    for (const [key, value] of Object.entries(r.models as Record<string, unknown>)) {
      const k = normalizeModelKey(key)
      const price = sanitizePrice(value)
      if (k && price) models[k] = price
    }
  }
  const currency = typeof r.currency === 'string' && r.currency.trim() ? r.currency.trim() : DEFAULT_CONFIG.currency
  return { version: 1, currency, models }
}

// ─── 模块级 store（多组件共享同一份配置，改价即时同步） ──────────────────

let cached: PricingConfig | null = null
const listeners = new Set<() => void>()

function readStored(): PricingConfig {
  if (typeof window === 'undefined') return DEFAULT_CONFIG
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_CONFIG
    return sanitizeConfig(JSON.parse(raw))
  } catch {
    return DEFAULT_CONFIG
  }
}

export function getPricingConfig(): PricingConfig {
  if (cached == null) cached = readStored()
  return cached
}

function emit() {
  for (const fn of listeners) fn()
}

export function subscribePricing(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function setPricingConfig(next: PricingConfig): void {
  cached = sanitizeConfig(next)
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cached))
  } catch {
    /* 存储不可用（隐私模式/配额满）时仅保留内存态，不阻断渲染 */
  }
  emit()
}

export function setModelPrice(model: string, price: ModelPrice): void {
  const key = normalizeModelKey(model)
  if (!key) return
  const cfg = getPricingConfig()
  setPricingConfig({ ...cfg, models: { ...cfg.models, [key]: price } })
}

export function removeModelPrice(model: string): void {
  const key = normalizeModelKey(model)
  const cfg = getPricingConfig()
  if (!(key in cfg.models)) return
  const models = { ...cfg.models }
  delete models[key]
  setPricingConfig({ ...cfg, models })
}

export function setCurrency(currency: string): void {
  const cfg = getPricingConfig()
  setPricingConfig({ ...cfg, currency })
}

// 跨标签页同步：另一个标签改了价格，本标签的展示点跟着刷新。
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== null && e.key !== STORAGE_KEY) return
    cached = readStored()
    emit()
  })
}

// ─── 单价查找 ───────────────────────────────────────────────────────────────

/**
 * 取某模型的单价。先精确匹配；再退化为「已配置模型名是实际模型名的前缀」中最长的一个，
 * 这样配一条 `claude-sonnet-4-5` 就能覆盖带日期后缀的 `claude-sonnet-4-5-20250929`。
 * 未配置返回 null——调用方据此显示「未配价」而不是显示 0。
 */
export function resolvePrice(
  model: string | null | undefined,
  config: PricingConfig = getPricingConfig(),
): { price: ModelPrice; matchedKey: string; exact: boolean } | null {
  const key = normalizeModelKey(model)
  if (!key) return null
  const exact = config.models[key]
  if (exact) return { price: exact, matchedKey: key, exact: true }
  let best: string | null = null
  for (const candidate of Object.keys(config.models)) {
    if (!key.startsWith(candidate)) continue
    if (best == null || candidate.length > best.length) best = candidate
  }
  if (best == null) return null
  return { price: config.models[best], matchedKey: best, exact: false }
}

// ─── 成本计算 ───────────────────────────────────────────────────────────────

/** 计价所需的 token 计数，字段名与 EvalResultRow / comparison.agent_b 一致。 */
export interface TokenUsage {
  prompt_tokens?: number | null
  completion_tokens?: number | null
  cache_read_tokens?: number | null
  cache_creation_tokens?: number | null
}

export interface CostBreakdown {
  /** 计费的命中输入 token。 */
  hitInputTokens: number
  /** 计费的未命中输入 token = prompt - 命中（缓存写入含在内，下限 0）。 */
  missInputTokens: number
  /** 缓存写入 token：已含在 missInputTokens 内按未命中档计费，单列供核对。 */
  cacheWriteTokens: number
  /** 计费的输出 token。 */
  outputTokens: number
  hitInputCost: number
  missInputCost: number
  outputCost: number
  /** 总成本；无单价或无 token 数据时为 null。 */
  total: number | null
  /** true 表示 prompt_tokens 缺失，命中/未命中拆分不可靠（未命中按 0 计）。 */
  promptMissing: boolean
}

const ZERO_BREAKDOWN: CostBreakdown = {
  hitInputTokens: 0,
  missInputTokens: 0,
  cacheWriteTokens: 0,
  outputTokens: 0,
  hitInputCost: 0,
  missInputCost: 0,
  outputCost: 0,
  total: null,
  promptMissing: true,
}

/** 把 token 计数拆成三档计费量（不需要单价，供 UI 单独展示口径）。 */
export function splitBillableTokens(usage: TokenUsage): {
  hitInputTokens: number
  missInputTokens: number
  cacheWriteTokens: number
  outputTokens: number
  hasAny: boolean
  promptMissing: boolean
} {
  const prompt = finite(usage.prompt_tokens)
  const hit = finite(usage.cache_read_tokens) ?? 0
  const write = finite(usage.cache_creation_tokens) ?? 0
  const output = finite(usage.completion_tokens) ?? 0
  const miss = prompt == null ? write : Math.max(0, prompt - hit)
  return {
    hitInputTokens: hit,
    missInputTokens: miss,
    cacheWriteTokens: write,
    outputTokens: output,
    hasAny: prompt != null || finite(usage.completion_tokens) != null || finite(usage.cache_read_tokens) != null || finite(usage.cache_creation_tokens) != null,
    promptMissing: prompt == null,
  }
}

/** 单条样例的成本。price 为 null（未配价）时 total 为 null，token 拆分仍返回。 */
export function computeCost(usage: TokenUsage, price: ModelPrice | null): CostBreakdown {
  const split = splitBillableTokens(usage)
  if (!split.hasAny) return { ...ZERO_BREAKDOWN, cacheWriteTokens: split.cacheWriteTokens }
  const base = {
    hitInputTokens: split.hitInputTokens,
    missInputTokens: split.missInputTokens,
    cacheWriteTokens: split.cacheWriteTokens,
    outputTokens: split.outputTokens,
    promptMissing: split.promptMissing,
  }
  if (!price) {
    return { ...base, hitInputCost: 0, missInputCost: 0, outputCost: 0, total: null }
  }
  const hitInputCost = (split.hitInputTokens / PRICE_UNIT_TOKENS) * price.inputHit
  const missInputCost = (split.missInputTokens / PRICE_UNIT_TOKENS) * price.inputMiss
  const outputCost = (split.outputTokens / PRICE_UNIT_TOKENS) * price.output
  return { ...base, hitInputCost, missInputCost, outputCost, total: hitInputCost + missInputCost + outputCost }
}

export interface CostAggregate {
  /** 有单价且有 token 的样例数。 */
  n: number
  /** 缺单价而没能计价的样例数（有 token 但模型未配价）。 */
  unpriced: number
  total: number | null
  mean: number | null
  hitInputTokens: number
  missInputTokens: number
  cacheWriteTokens: number
  outputTokens: number
  /**
   * 三档计费成本的分项合计，口径与 total 一致（未配价样例不计入），三者之和 = total。
   * 供展示层画「钱花在哪一档」的构成图——只有 total 无法回答这个问题。
   */
  hitInputCost: number
  missInputCost: number
  outputCost: number
}

const EMPTY_AGGREGATE: CostAggregate = {
  n: 0,
  unpriced: 0,
  total: null,
  mean: null,
  hitInputTokens: 0,
  missInputTokens: 0,
  cacheWriteTokens: 0,
  outputTokens: 0,
  hitInputCost: 0,
  missInputCost: 0,
  outputCost: 0,
}

/**
 * 汇总一批样例的成本。同一批里允许逐条给不同单价（A/B 双模各按自己的模型价）。
 * 未配价的样例不计入 total/mean，只累加到 unpriced，避免把缺价当成 0 成本。
 */
export function aggregateCost(
  rows: Array<{ usage: TokenUsage; price: ModelPrice | null }>,
): CostAggregate {
  if (rows.length === 0) return EMPTY_AGGREGATE
  let n = 0
  let unpriced = 0
  let total = 0
  const tokens = { hit: 0, miss: 0, write: 0, out: 0 }
  const costs = { hit: 0, miss: 0, out: 0 }
  for (const { usage, price } of rows) {
    const cost = computeCost(usage, price)
    const split = splitBillableTokens(usage)
    if (!split.hasAny) continue
    tokens.hit += cost.hitInputTokens
    tokens.miss += cost.missInputTokens
    tokens.write += cost.cacheWriteTokens
    tokens.out += cost.outputTokens
    if (cost.total == null) {
      unpriced += 1
      continue
    }
    n += 1
    total += cost.total
    // 分项只累加计得出价的样例，与 total 同口径，保证三项之和 = total。
    costs.hit += cost.hitInputCost
    costs.miss += cost.missInputCost
    costs.out += cost.outputCost
  }
  return {
    n,
    unpriced,
    total: n > 0 ? total : null,
    mean: n > 0 ? total / n : null,
    hitInputTokens: tokens.hit,
    missInputTokens: tokens.miss,
    cacheWriteTokens: tokens.write,
    outputTokens: tokens.out,
    hitInputCost: costs.hit,
    missInputCost: costs.miss,
    outputCost: costs.out,
  }
}

// ─── 展示格式 ───────────────────────────────────────────────────────────────

/** 成本金额格式化：金额越小保留越多小数，避免小额被四舍五入成 0.00。 */
export function formatCost(
  value: number | null | undefined,
  currency: string = getPricingConfig().currency,
): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6
  return `${currency}${value.toFixed(digits)}`
}

/** 单价格式化（每 100 万 token）。 */
export function formatUnitPrice(value: number, currency: string = getPricingConfig().currency): string {
  return `${currency}${value}/M`
}

/** 悬浮提示用的口径说明，各展示点复用同一句话。 */
export function costTooltip(breakdown: CostBreakdown, currency: string): string {
  const lines = [
    `命中输入 ${breakdown.hitInputTokens} tok → ${formatCost(breakdown.hitInputCost, currency)}`,
    `未命中输入 ${breakdown.missInputTokens} tok → ${formatCost(breakdown.missInputCost, currency)}`,
    `输出 ${breakdown.outputTokens} tok → ${formatCost(breakdown.outputCost, currency)}`,
    `缓存写入 ${breakdown.cacheWriteTokens} tok（已含在未命中输入内）`,
  ]
  if (breakdown.promptMissing) lines.push('缺少输入 token 计数，未命中部分仅按缓存写入量计')
  return lines.join('\n')
}

export const PRICING_NOTE = '成本按本地配置的模型单价实时计算；缓存写入 token 归入未命中输入档计费。'
