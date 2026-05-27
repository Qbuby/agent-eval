import { memo, useState } from 'react'
import type { RunChildMeta, RunDetail } from '@/types'

export interface NodeState {
  data?: RunDetail
  loading: boolean
  error?: string
}

export type NodeCache = Record<string, NodeState>

const RUN_TYPE_COLORS: Record<string, { border: string; badge: string }> = {
  llm: { border: 'rgb(var(--accent))', badge: 'bg-accent/10 text-accent' },
  tool: { border: 'rgb(var(--positive))', badge: 'bg-positive/10 text-positive' },
  chain: { border: 'rgb(var(--info))', badge: 'bg-info/15 text-info' },
  retriever: { border: 'rgb(var(--warning))', badge: 'bg-warning/15 text-warning' },
  prompt: { border: 'rgb(var(--negative))', badge: 'bg-negative/10 text-negative' },
}
const DEFAULT_TYPE_COLOR = { border: 'rgb(var(--fill) / 0.6)', badge: 'bg-fill/10 text-text-secondary' }

interface RunNodeRowProps {
  meta: RunChildMeta
  depth: number
  projectName: string
  isOpen: boolean
  state: NodeState | undefined
  nodeCache: NodeCache
  expanded: Set<string>
  onToggle: (id: string) => void
  onRetry: (id: string) => void
}

export const RunNodeRow = memo(function RunNodeRow({
  meta, depth, projectName, isOpen, state, nodeCache, expanded, onToggle, onRetry,
}: RunNodeRowProps) {
  const color = RUN_TYPE_COLORS[meta.run_type] || DEFAULT_TYPE_COLOR
  const canExpand = meta.has_children

  return (
    <div>
      <div
        className="flex items-center gap-2 py-1.5 border-b border-separator hover:bg-fill/5 cursor-default text-[11px] transition-colors duration-150 ease-standard"
        style={{ paddingLeft: 12 + depth * 16, borderLeft: `2px solid ${color.border}` }}
      >
        <button
          type="button"
          onClick={() => canExpand && onToggle(meta.id)}
          className={`w-4 text-center select-none ${canExpand ? 'text-text-secondary hover:text-accent cursor-pointer' : 'text-transparent'}`}
        >
          {isOpen ? '▾' : '▸'}
        </button>
        <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] tracking-wide uppercase ${color.badge}`}>
          {meta.run_type || '—'}
        </span>
        <span className="flex-1 truncate text-text-primary font-medium">{meta.name || '—'}</span>
        {meta.error && <span className="w-1.5 h-1.5 rounded-full bg-negative" title={meta.error} />}
        <span className="text-text-tertiary tabular-nums">
          {meta.latency_s != null ? `${meta.latency_s.toFixed(2)}s` : '—'}
        </span>
        <span className="text-text-tertiary tabular-nums w-16 text-right">
          {meta.total_tokens != null ? `${meta.total_tokens} tok` : '—'}
        </span>
      </div>

      {isOpen && (
        <div style={{ paddingLeft: 12 + depth * 16 }} className="border-b border-separator">
          {state?.loading && !state.data && <div className="py-3 px-3 text-[11px] text-text-tertiary">加载中…</div>}
          {state?.error && !state.data && (
            <div className="py-3 px-3 text-[11px] text-negative">
              {state.error}
              <button onClick={() => onRetry(meta.id)} className="ml-3 underline">重试</button>
            </div>
          )}
          {state?.data && (
            <div className="py-3 px-3 space-y-3 bg-fill/5">
              <RunDetailBody detail={state.data} compact />
              {state.data.children.length > 0 && (
                <div className="mt-3">
                  <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">
                    Children ({state.data.children.length}) {state.data.children_truncated && <span className="ml-2 text-warning">已截断</span>}
                  </div>
                  <div className="border border-border rounded-md bg-surface">
                    {state.data.children.map(c => (
                      <RunNodeRow
                        key={c.id}
                        meta={c}
                        depth={depth + 1}
                        projectName={projectName}
                        isOpen={expanded.has(c.id)}
                        state={nodeCache[c.id]}
                        nodeCache={nodeCache}
                        expanded={expanded}
                        onToggle={onToggle}
                        onRetry={onRetry}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}, (prev, next) =>
  prev.meta === next.meta &&
  prev.depth === next.depth &&
  prev.projectName === next.projectName &&
  prev.isOpen === next.isOpen &&
  prev.state === next.state &&
  prev.onToggle === next.onToggle &&
  prev.onRetry === next.onRetry
)

export const RunDetailBody = memo(function RunDetailBody({ detail, compact }: { detail: RunDetail; compact?: boolean }) {
  const size = compact ? 'text-[11px]' : 'text-[12px]'
  return (
    <div className={`space-y-4 ${size}`}>
      <div className="grid grid-cols-2 gap-3">
        <PreviewField label="Name" value={detail.name} />
        <PreviewField label="Run Type" value={detail.run_type || '—'} />
        <PreviewField label="Status" value={detail.status} />
        <PreviewField label="ID" value={detail.id} mono />
        <PreviewField label="Latency" value={detail.latency_s != null ? `${detail.latency_s.toFixed(3)}s` : '—'} />
        <PreviewField label="Tokens" value={formatTokens(detail)} />
        <PreviewField label="Start" value={detail.start_time ? new Date(detail.start_time).toLocaleString() : '—'} />
        <PreviewField label="End" value={detail.end_time ? new Date(detail.end_time).toLocaleString() : '—'} />
      </div>
      {detail.tags.length > 0 && <PreviewField label="Tags" value={detail.tags.join(', ')} />}
      {detail.error && <PreviewField label="Error" value={detail.error} error />}
      <JsonField label="Inputs" value={detail.inputs} />
      <JsonField label="Outputs" value={detail.outputs} />
      {detail.metadata && <JsonField label="Metadata" value={detail.metadata} collapsed />}
      {detail.extra && <JsonField label="Extra" value={detail.extra} collapsed />}
    </div>
  )
})

function formatTokens(detail: RunDetail): string {
  const { prompt_tokens, completion_tokens, total_tokens } = detail
  if (total_tokens == null && prompt_tokens == null && completion_tokens == null) return '—'
  const parts: string[] = []
  if (prompt_tokens != null) parts.push(`prompt ${prompt_tokens}`)
  if (completion_tokens != null) parts.push(`completion ${completion_tokens}`)
  if (total_tokens != null) parts.push(`total ${total_tokens}`)
  return parts.join(' · ')
}

function JsonField({ label, value, collapsed }: { label: string; value: unknown; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed)
  if (value == null || (typeof value === 'object' && value !== null && !Array.isArray(value) && Object.keys(value as object).length === 0)) {
    return <PreviewField label={label} value="—" />
  }
  const text = JSON.stringify(value, null, 2)
  return (
    <div>
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1 text-[10px] tracking-widest uppercase text-text-tertiary mb-1 hover:text-accent"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>{label}</span>
        <span className="normal-case tracking-normal text-[9px] opacity-60">({text.length} chars)</span>
      </button>
      {open && (
        <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-all p-2.5 rounded-md border border-border bg-fill/5 text-text-primary font-mono max-h-80 overflow-y-auto">
          {text}
        </pre>
      )}
    </div>
  )
}

function PreviewField({ label, value, mono, error }: { label: string; value: string | null | undefined; mono?: boolean; error?: boolean }) {
  const text = value || '—'
  return (
    <div>
      <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">{label}</div>
      <div className={`text-[12px] leading-relaxed whitespace-pre-wrap break-all p-2.5 rounded-md border border-border bg-fill/5 ${mono ? 'font-mono text-[11px]' : ''} ${error ? 'text-negative bg-negative/5 border-negative/30' : 'text-text-primary'}`}>
        {text}
      </div>
    </div>
  )
}
