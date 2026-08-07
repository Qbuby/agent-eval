// 评估结果「三层语义」的前端统一读取助手。
//
// 后端（#262 起）把一次评估拆成三层，互不混淆：
// - **Agent 执行事实**：执行成功 / 异常 / 未知；
// - **Judge 评分事实**：评分完成 / 跳过 / 异常；
// - **显式验收结论**：仅当运行配置了 acceptance_policy 时才有通过率 / 运行结论。
//
// summary_scores 现以 `facts` / `acceptance` / `cost_scored` /
// `cost_execution_abnormal` 为准（配置验收策略时另有 `cost_accepted` /
// `cost_not_accepted`）。历史 run 经后端 project_stored_summary 已回填
// facts/acceptance；更旧、只有 counts/cost_success 的快照在这里做兜底，
// 保证前端**绝不**把「分数≥0.5」当成通过、也绝不在未配置验收时编造通过率。

export interface EvalFacts {
  total: number
  execution_success: number
  execution_abnormal: number
  execution_unknown: number
  evaluation_completed: number
  evaluation_partial_or_error: number
  scored: number
  skipped: number
}

export interface EvalAcceptance {
  configured: boolean
  decided: number | null
  passed: number | null
  failed: number | null
  undetermined: number | null
  decision_coverage: number | null
  pass_rate: number | null
  run_decision: string | null
}

type SummaryLike = {
  facts?: Partial<EvalFacts> | null
  acceptance?: Partial<EvalAcceptance> | null
  counts?: { total?: number; passed?: number; failed?: number; unreachable?: number } | null
  cost_scored?: Record<string, number | null> | null
  cost_execution_abnormal?: Record<string, number | null> | null
  cost_accepted?: Record<string, number | null> | null
  cost_not_accepted?: Record<string, number | null> | null
  cost_success?: Record<string, number | null> | null
  cost_failure?: Record<string, number | null> | null
} | null | undefined

const EMPTY_ACCEPTANCE: EvalAcceptance = {
  configured: false,
  decided: null,
  passed: null,
  failed: null,
  undetermined: null,
  decision_coverage: null,
  pass_rate: null,
  run_decision: null,
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

/** 读取执行 / 评分事实；旧 counts 快照兜底为近似 facts（不含验收）。 */
export function deriveFacts(summary: SummaryLike): EvalFacts {
  const f = summary?.facts
  if (f && typeof f === 'object') {
    return {
      total: num(f.total),
      execution_success: num(f.execution_success),
      execution_abnormal: num(f.execution_abnormal),
      execution_unknown: num(f.execution_unknown),
      evaluation_completed: num(f.evaluation_completed),
      evaluation_partial_or_error: num(f.evaluation_partial_or_error),
      scored: num(f.scored ?? f.evaluation_completed),
      skipped: num(f.skipped),
    }
  }
  // 兜底：极旧 run 只有 counts.{total,passed,failed,unreachable}。
  // 这些 counts 的 passed/failed 是旧「≥0.5」口径，绝不映射为验收；
  // 只借 total / unreachable 还原执行事实的粗略视图。
  const c = summary?.counts ?? {}
  const total = num(c.total)
  const unreachable = num(c.unreachable)
  const executionSuccess = Math.max(0, total - unreachable)
  return {
    total,
    execution_success: executionSuccess,
    execution_abnormal: unreachable,
    execution_unknown: 0,
    evaluation_completed: 0,
    evaluation_partial_or_error: 0,
    scored: 0,
    skipped: 0,
  }
}

/** 读取显式验收结论；无策略（或旧快照）一律返回 configured=false。 */
export function deriveAcceptance(summary: SummaryLike): EvalAcceptance {
  const a = summary?.acceptance
  if (a && typeof a === 'object' && a.configured) {
    return {
      configured: true,
      decided: a.decided ?? null,
      passed: a.passed ?? null,
      failed: a.failed ?? null,
      undetermined: a.undetermined ?? null,
      decision_coverage: a.decision_coverage ?? null,
      pass_rate: a.pass_rate ?? null,
      run_decision: a.run_decision ?? null,
    }
  }
  return { ...EMPTY_ACCEPTANCE }
}

/** 评分样例成本；新字段 cost_scored 优先，旧 cost_success 兜底。 */
export function deriveCostScored(summary: SummaryLike): Record<string, number> {
  const raw = summary?.cost_scored ?? summary?.cost_success ?? {}
  const out: Record<string, number> = {}
  for (const [k, v] of Object.entries(raw)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v
  }
  return out
}

/** 执行异常样例成本；新字段优先，旧 cost_failure 兜底。 */
export function deriveCostAbnormal(summary: SummaryLike): Record<string, number> {
  const raw = summary?.cost_execution_abnormal ?? summary?.cost_failure ?? {}
  const out: Record<string, number> = {}
  for (const [k, v] of Object.entries(raw)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v
  }
  return out
}

/**
 * 验收通过率的展示文本。未配置验收策略返回 null —— 调用方据此显示
 * 「仅评分」而非 0% / —，杜绝把「没有结论」误读成「全部失败」。
 */
export function acceptancePassRateText(acceptance: EvalAcceptance): string | null {
  if (!acceptance.configured) return null
  if (acceptance.pass_rate == null) return '无数据'
  return `${(acceptance.pass_rate * 100).toFixed(1)}%`
}

const RUN_DECISION_LABELS: Record<string, string> = {
  qualified: '达标',
  unqualified: '不达标',
  undetermined: '待定',
}

export function runDecisionLabel(runDecision: string | null | undefined): string {
  if (!runDecision) return '待定'
  return RUN_DECISION_LABELS[runDecision] ?? runDecision
}

/** 逐样例投影行（对比页子集重算用；字段与后端 EvalResultRow 对齐）。 */
export interface ProjectedRow {
  execution_status?: string
  evaluation_status?: string
  acceptance_decision?: string | null
}

/**
 * 从选中的逐样例投影行重算 facts + acceptance（对比页子集模式）。
 *
 * 与后端 aggregate_semantics 同口径：acceptance 只在存在非空
 * acceptance_decision 时视为「已配置验收」；pass_rate = 通过 / 已决策，
 * 未决策的不摊进分母，绝不把「未配置 / 未决策」当成失败。
 */
export function aggregateProjectedRows(rows: ProjectedRow[]): {
  facts: EvalFacts
  acceptance: EvalAcceptance
} {
  const facts: EvalFacts = {
    total: rows.length,
    execution_success: 0,
    execution_abnormal: 0,
    execution_unknown: 0,
    evaluation_completed: 0,
    evaluation_partial_or_error: 0,
    scored: 0,
    skipped: 0,
  }
  let passed = 0
  let failed = 0
  let undetermined = 0
  let anyDecision = false

  for (const r of rows) {
    const exec = r.execution_status ?? 'unknown'
    if (exec === 'success') facts.execution_success += 1
    else if (exec === 'abnormal') facts.execution_abnormal += 1
    else facts.execution_unknown += 1

    const evalStatus = r.evaluation_status ?? 'unknown'
    if (evalStatus === 'completed') { facts.evaluation_completed += 1; facts.scored += 1 }
    else if (evalStatus === 'skipped') facts.skipped += 1
    else if (evalStatus === 'error' || evalStatus === 'unknown') facts.evaluation_partial_or_error += 1

    const decision = r.acceptance_decision
    if (decision != null) {
      anyDecision = true
      if (decision === 'pass') passed += 1
      else if (decision === 'fail') failed += 1
      else undetermined += 1
    }
  }

  if (!anyDecision) {
    return { facts, acceptance: { ...EMPTY_ACCEPTANCE } }
  }
  const decided = passed + failed
  const total = rows.length
  return {
    facts,
    acceptance: {
      configured: true,
      decided,
      passed,
      failed,
      undetermined,
      decision_coverage: total > 0 ? decided / total : null,
      pass_rate: decided > 0 ? passed / decided : null,
      run_decision: null,
    },
  }
}

// ---------------------------------------------------------------------------
// 「样例异常」快筛判定（详情页 + 对比页共用）
// ---------------------------------------------------------------------------

/**
 * 执行状态本身即异常：agent 跑挂 / 不可达 / 超时。
 *
 * 不含 fail —— fail 是判分未达合格线（跑通了但没答好），属评分事实而非执行异常。
 */
const ABNORMAL_STATUSES = new Set(['error', 'agent_unreachable', 'agent_timeout'])

/**
 * 尚未产出回复的中间态。这些状态下 actual_output 本来就是空，
 * 判空会把「还没跑」误判成「跑出了空回复」，故一律豁免空回复判定。
 */
const PENDING_STATUSES = new Set(['pending', 'running', 'stopping'])

function isBlank(v: unknown): boolean {
  return typeof v !== 'string' || v.trim() === ''
}

/** 空回复判定所需的最小行结构（与后端 EvalResultRow 对齐，除 status 外全可选）。 */
export interface AbnormalRowLike {
  status: string
  actual_output?: string | null
  full_trace?: {
    conversation?: { turns?: Array<{ assistant?: unknown }> } | null
  } | null
  comparison?: {
    agent_b?: {
      output?: string | null
      conversation?: { turns?: Array<{ assistant?: unknown }> } | null
    } | null
    answer_counts?: { a_blank?: number; b_blank?: number } | null
  } | null
}

/**
 * 该行是否含「空回复」——agent 跑通了但没吐出内容。四类 run 各有取法：
 *
 * - 多轮双模：`comparison.answer_counts.{a_blank,b_blank}`（后端已分侧统计）；
 * - 多轮单模：`full_trace.conversation.turns[].assistant` 现数（旧 run 没有
 *   answer_counts，故不读它，直接数轮次，顺带兼容旧数据）；
 * - 单轮双模：A 侧 `actual_output` + B 侧 `comparison.agent_b.output`；
 * - 单轮单模：`actual_output`。
 *
 * 任一侧、任一轮为空即算命中：一次 A/B 对比里 B 侧空了同样是不可用样例。
 * 中间态（pending/running/stopping）一律返回 false。
 */
export function rowHasBlankReply(row: AbnormalRowLike): boolean {
  if (PENDING_STATUSES.has(row.status)) return false

  const cmp = row.comparison
  const aTurns = row.full_trace?.conversation?.turns
  const bTurns = cmp?.agent_b?.conversation?.turns

  // 多轮：以轮次为准（双模两侧各自判）。任一侧有回放轮次即走多轮分支。
  const aMulti = Array.isArray(aTurns) && aTurns.length > 0
  const bMulti = Array.isArray(bTurns) && bTurns.length > 0
  if (aMulti || bMulti) {
    const counts = cmp?.answer_counts
    if (counts && (num(counts.a_blank) > 0 || num(counts.b_blank) > 0)) return true
    if (aMulti && aTurns!.some(t => isBlank(t?.assistant))) return true
    if (bMulti && bTurns!.some(t => isBlank(t?.assistant))) return true
    return false
  }

  // 单轮：A 侧必判；对比 run 另判 B 侧。
  if (isBlank(row.actual_output)) return true
  if (cmp && isBlank(cmp.agent_b?.output)) return true
  return false
}

/**
 * 快筛「异常样例」的统一判定：执行状态异常 **或** 空回复。
 *
 * 两者并入同一口径 —— 对使用者而言都是「这条结果不可用」，无论是 agent 没连上
 * 还是连上了没说话。
 */
export function rowIsAbnormal(row: AbnormalRowLike): boolean {
  return ABNORMAL_STATUSES.has(row.status) || rowHasBlankReply(row)
}
