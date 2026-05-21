// Per-dimension semantic metadata.
//
// All scores in evaluation_scores.score are stored on a 0-1 scale, but the
// *meaning* of "high score" varies by evaluator: Correctness 1.0 means the
// answer was right; Hallucination 1.0 (in some templates) means the answer
// was hallucinated. The UI can't pick the right color or threshold without
// knowing direction + cutoff.
//
// Rather than make every component re-derive this, we centralize it here.
// Lookup is case-insensitive on the dimension name; the `langfuse:` prefix
// is stripped before matching so a Langfuse-pulled and a locally-computed
// score with the same name share the same semantics.

export type ScoreDirection = 'higher_better' | 'lower_better'

export interface ScoreMeta {
  label: string
  direction: ScoreDirection
  // Threshold above (or below, when lower_better) which a single sample
  // counts as "passing" this dimension. Half-open: pass means score >=
  // threshold for higher_better, score <= threshold for lower_better.
  threshold: number
  description: string
}

const DEFAULT_META: ScoreMeta = {
  label: '',
  direction: 'higher_better',
  threshold: 0.5,
  description: '分数越高越好。0.5 为合格线（默认）。',
}

// Match by case-folded, prefix-stripped dimension name. Add entries here
// as new evaluators come online.
const REGISTRY: Record<string, Omit<ScoreMeta, 'label'> & { label?: string }> = {
  correctness: {
    label: '正确性',
    direction: 'higher_better',
    threshold: 0.5,
    description: '回答与参考答案的事实一致程度。1=完全正确，0=完全错误。',
  },
  hallucination: {
    label: '幻觉',
    direction: 'lower_better',
    threshold: 0.3,
    description: '回答中超出参考事实的虚构内容比例。0=没有幻觉，1=全是虚构。分数越低越好。',
  },
  helpfulness: {
    label: '有用性',
    direction: 'higher_better',
    threshold: 0.5,
    description: '是否真正解决了用户的问题。',
  },
  conciseness: {
    label: '简洁度',
    direction: 'higher_better',
    threshold: 0.5,
    description: '在不损失信息的前提下表达的紧凑程度。',
  },
  toxicity: {
    label: '毒性',
    direction: 'lower_better',
    threshold: 0.2,
    description: '冒犯性 / 有害内容比例。分数越低越好。',
  },
  relevance: {
    label: '相关性',
    direction: 'higher_better',
    threshold: 0.5,
    description: '回答与问题主题的契合度。',
  },
  context_relevance: {
    label: '上下文相关性',
    direction: 'higher_better',
    threshold: 0.5,
    description: '检索到的上下文与问题的相关程度（RAG 用）。',
  },
  faithfulness: {
    label: '忠实度',
    direction: 'higher_better',
    threshold: 0.6,
    description: '回答是否完全基于给定上下文（不引入外部知识）。',
  },
  tool_sequence_match: {
    label: '工具调用顺序',
    direction: 'higher_better',
    threshold: 0.7,
    description: '实际工具调用序列与期望前缀的匹配率。',
  },
  exact_match: {
    label: '精确匹配',
    direction: 'higher_better',
    threshold: 1.0,
    description: '与期望输出完全一致才算 1，否则 0。',
  },
}

function normalizeKey(name: string): string {
  return name.replace(/^langfuse:/i, '').trim().toLowerCase()
}

export function getScoreMeta(dimension: string): ScoreMeta {
  const key = normalizeKey(dimension)
  const hit = REGISTRY[key]
  if (hit) return { ...DEFAULT_META, ...hit, label: hit.label || dimension }
  return { ...DEFAULT_META, label: dimension }
}

// Whether this score value passes its threshold.
export function isPassing(dimension: string, score: number): boolean {
  const meta = getScoreMeta(dimension)
  return meta.direction === 'higher_better'
    ? score >= meta.threshold
    : score <= meta.threshold
}

// Shorter direction arrow + tone for chips/labels.
export function directionMark(meta: ScoreMeta): string {
  return meta.direction === 'higher_better' ? '↑越高越好' : '↓越低越好'
}

// Color tone: returns 'good' | 'bad' for use in CSS classes.
export function tone(dimension: string, score: number): 'good' | 'bad' {
  return isPassing(dimension, score) ? 'good' : 'bad'
}
