// 多轮维度折叠：把逐轮维度 key 折回评估器级 base 维度。
//
// 多轮 run 的 score / dimension_averages key 形如 `<评估器名>.turn<N>` 或
// `<评估器名>.conversation`。汇总可视化、报告导出一律按**评估器**聚合，不按
// 轮次（不同样例轮次无固定规律、轮次间不可比）。新 run 的 summary 已在后端
// 折叠；前端再折叠一次兼容旧 run（其 summary 仍是轮次级）。
//
// 从 EvaluationRunDetailPage 提取到此 lib，供该页 + reportExport 共用，避免重复。

const _TURN_SUFFIX = /^(turn\d+|conversation)$/

/** 把逐轮/会话维度 key 折回 base（`correctness.turn3` → `correctness`）。 */
export function collapseScoreKey(key: string): string {
  const idx = key.lastIndexOf('.')
  if (idx <= 0) return key
  return _TURN_SUFFIX.test(key.slice(idx + 1)) ? key.slice(0, idx) : key
}

// 把轮次级维度均分折叠回评估器级：同一评估器的各轮/会话分数求简单平均。
// 旧 run 的 dimension_averages 无 count 信息，退化为对各轮均值再平均，作为
// 近似展示足够（精确值以新 run 为准）。
export function collapseDimAvg(dimAvg: Record<string, number>): Record<string, number> {
  const acc: Record<string, { sum: number; n: number }> = {}
  for (const [k, v] of Object.entries(dimAvg)) {
    if (typeof v !== 'number') continue
    const dim = collapseScoreKey(k)
    if (!acc[dim]) acc[dim] = { sum: 0, n: 0 }
    acc[dim].sum += v
    acc[dim].n += 1
  }
  const out: Record<string, number> = {}
  for (const [dim, { sum, n }] of Object.entries(acc)) {
    out[dim] = n ? Math.round((sum / n) * 1000) / 1000 : 0
  }
  return out
}
