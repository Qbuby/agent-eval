import { useState } from 'react'
import type { CotStep } from '@/types'

// 思维链 / 工具链的共享展示组件。原本内联在 EvaluationRunDetailPage.tsx，
// 抽到这里供评估详情页与 tracing 详情 Modal 复用（行为保持一致）。
//
// 口径约束：只渲染来源显式标记的 thought/answer/tool_call；不从普通模型输出
// 臆造思维链（后端 semantic_trace 已保证 type 仅在有据可依时才为 thought）。

export function CotTimeline({ steps }: { steps: CotStep[] }) {
  return (
    <div className="border border-border rounded-md bg-surface overflow-hidden">
      {steps.map((step, i) => (
        <CotStepRow key={i} step={step} index={i} last={i === steps.length - 1} />
      ))}
    </div>
  )
}

export function CotStepRow({ step, index, last }: { step: CotStep; index: number; last: boolean }) {
  const [open, setOpen] = useState(step.type !== 'thought')
  const dur = step.duration_ms != null ? `${step.duration_ms}ms` : null
  const border = last ? '' : 'border-b border-separator'

  if (step.type === 'thought' || step.type === 'answer') {
    const isAnswer = step.type === 'answer'
    const tagCls = isAnswer ? 'badge badge-positive' : 'badge badge-neutral'
    const tagLabel = isAnswer ? '答复' : '思考'
    const text = step.content || ''
    const long = text.length > 200
    const preview = long && !open ? `${text.slice(0, 200)}…` : text
    return (
      <div className={`px-3 py-2 ${border} ${isAnswer ? 'bg-positive/5' : ''}`}>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] text-text-tertiary tabular-nums w-5 text-right">{index + 1}</span>
          <span className={tagCls}>{tagLabel}</span>
          {dur && <span className="text-[10px] text-text-tertiary tabular-nums">{dur}</span>}
          {long && (
            <button
              type="button"
              onClick={() => setOpen(o => !o)}
              className="ml-auto text-[10px] text-accent hover:text-accent-hover transition-colors"
            >
              {open ? '收起' : '展开'}
            </button>
          )}
        </div>
        <pre className="font-mono text-[11px] whitespace-pre-wrap text-text-primary">{preview || '（空）'}</pre>
      </div>
    )
  }

  const argsStr =
    step.args == null ? '' : typeof step.args === 'string' ? step.args : JSON.stringify(step.args, null, 2)
  const outStr =
    step.output == null ? '' : typeof step.output === 'string' ? step.output : JSON.stringify(step.output, null, 2)
  const failed = isToolCallError(step)
  return (
    <div className={`px-3 py-2 ${failed ? 'bg-negative/5' : 'bg-warning/5'} ${border}`}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[10px] text-text-tertiary tabular-nums w-5 text-right">{index + 1}</span>
        <span className="badge badge-warning">工具</span>
        <span className="text-[11px] font-mono">{step.tool_name || '?'}</span>
        <ToolResultBadge failed={failed} />
        {dur && <span className="text-[10px] text-text-tertiary tabular-nums">{dur}</span>}
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          className="ml-auto text-[10px] text-accent hover:text-accent-hover transition-colors"
        >
          {open ? '收起' : '展开'}
        </button>
      </div>
      {open && (
        <div className="space-y-1.5 pl-7">
          {argsStr && (
            <div>
              <div className="text-[10px] text-text-tertiary mb-0.5">参数</div>
              <pre className="font-mono text-[10px] bg-surface border border-border rounded-md p-1.5 max-h-[140px] overflow-auto whitespace-pre-wrap">{argsStr}</pre>
            </div>
          )}
          {outStr && (
            <div>
              <div className="text-[10px] text-text-tertiary mb-0.5">输出</div>
              <pre className="font-mono text-[10px] bg-surface border border-border rounded-md p-1.5 max-h-[160px] overflow-auto whitespace-pre-wrap">{outStr}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// 判定单次工具调用是否失败。口径对齐后端 evaluation.py / langfuse_runner.py：
// output 为 dict 时看 error / isError 是否 truthy；为字符串时看是否以 "error" 开头。
export function isToolCallError(call: { output?: unknown }): boolean {
  const out = call.output
  if (out && typeof out === 'object' && !Array.isArray(out)) {
    const o = out as Record<string, unknown>
    return Boolean(o.error) || Boolean(o.isError)
  }
  if (typeof out === 'string') {
    return out.trim().toLowerCase().startsWith('error')
  }
  return false
}

export function ToolResultBadge({ failed }: { failed: boolean }) {
  return failed
    ? <span className="badge badge-negative" title="工具调用失败">失败</span>
    : <span className="badge badge-positive" title="工具调用成功">成功</span>
}

export function ToolCallsTable({ calls }: { calls: Array<Record<string, unknown>> }) {
  const grouped: Record<string, { count: number; errors: number }> = {}
  for (const c of calls) {
    const name = (c.tool_name || c.name || 'unknown') as string
    const slot = grouped[name] ?? (grouped[name] = { count: 0, errors: 0 })
    slot.count++
    if (isToolCallError(c)) slot.errors++
  }
  const entries = Object.entries(grouped).sort((a, b) => b[1].count - a[1].count)

  return (
    <div className="table-card">
      <table className="table-base">
        <thead>
          <tr>
            <th>工具</th>
            <th className="text-right w-20">次数</th>
            <th className="text-right w-20">失败</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, { count, errors }]) => (
            <tr key={name}>
              <td className="font-mono text-[11px]">{name}</td>
              <td className="text-right tabular-nums">{count}</td>
              <td className={`text-right tabular-nums ${errors > 0 ? 'text-negative' : ''}`}>
                {errors || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {calls.length > 5 && (
        <details className="px-2 py-1 border-t border-separator">
          <summary className="text-[10px] text-text-tertiary cursor-pointer">展开全部调用详情</summary>
          <div className="mt-1 max-h-[200px] overflow-y-auto">
            {calls.map((c, i) => (
              <div key={i} className="flex items-center gap-2 py-0.5 border-b border-separator last:border-0">
                <span className="text-[10px] text-text-tertiary w-4 text-right">{i + 1}</span>
                <ToolResultBadge failed={isToolCallError(c)} />
                <span className="font-mono text-[10px]">{(c.tool_name || c.name || '?') as string}</span>
                {c.args != null && (
                  <span className="text-[10px] text-text-tertiary truncate max-w-[200px]">
                    {typeof c.args === 'string' ? c.args : JSON.stringify(c.args).slice(0, 80)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
