import { useEffect, useId, useRef, useState } from 'react'
import { configOptionToString } from '@/hooks/useConfigOptions'
import type { ConfigOption } from '@/types'

// Dropdown trigger that lists pre-configured options for a config key.
// Renders an absolutely-positioned chevron button — its parent must be
// `relative`, and the sibling input should reserve space with `pr-9`.
// Returns null when no options exist so unconfigured keys stay invisible.
//
// Fluent-Design notes: chevron rotates on open (motion), the popover floats
// with layered shadow + faint border (depth), the focused row has a 2px
// accent leading bar (light/selection), and ↑↓ Enter Esc work for keyboard
// users.
export function OptionPicker({ options, currentValue, onPick, maskValues }: {
  options: ConfigOption[]
  currentValue: string
  onPick: (value: unknown) => void
  maskValues?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [focusIdx, setFocusIdx] = useState<number>(-1)
  const wrapRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const reactId = useId()
  const listboxId = `optionpicker-list-${reactId}`
  const optionId = (i: number) => `${listboxId}-opt-${i}`

  // When opening, focus the currently-selected option (or first).
  useEffect(() => {
    if (!open) { setFocusIdx(-1); return }
    const sel = options.findIndex(o => configOptionToString(o.value) === currentValue)
    setFocusIdx(sel >= 0 ? sel : 0)
  }, [open, options, currentValue])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); return }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setFocusIdx(i => (i + 1) % options.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setFocusIdx(i => (i <= 0 ? options.length - 1 : i - 1))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        const opt = options[focusIdx]
        if (opt) { onPick(opt.value); setOpen(false) }
      } else if (e.key === 'Home') {
        e.preventDefault(); setFocusIdx(0)
      } else if (e.key === 'End') {
        e.preventDefault(); setFocusIdx(options.length - 1)
      }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, options, focusIdx, onPick])

  // Keep the focused row in view.
  useEffect(() => {
    if (!open || focusIdx < 0) return
    const node = listRef.current?.querySelector<HTMLButtonElement>(`[data-idx="${focusIdx}"]`)
    node?.scrollIntoView({ block: 'nearest' })
  }, [open, focusIdx])

  if (!options || options.length === 0) return null

  const display = (v: unknown) => {
    const s = configOptionToString(v)
    if (maskValues && s) return s.length <= 6 ? '••••' : `••••${s.slice(-4)}`
    return s.length > 60 ? s.slice(0, 60) + '…' : s
  }

  return (
    <div ref={wrapRef} className="absolute right-1 top-1.5">
      <button
        type="button"
        tabIndex={-1}
        onClick={() => setOpen(o => !o)}
        aria-label="选择预设值"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listboxId : undefined}
        title="选择预设值"
        className={`inline-flex items-center justify-center w-6 h-6 rounded-md border transition-[color,background-color,border-color,box-shadow] duration-150 ease-standard ${
          open
            ? 'border-accent text-accent bg-accent/10 shadow-sm'
            : 'border-transparent text-text-tertiary hover:border-border hover:bg-fill/10 hover:text-text-primary'
        }`}
      >
        <svg
          viewBox="0 0 12 12"
          width="10"
          height="10"
          aria-hidden="true"
          className={`transition-transform duration-200 ease-standard ${open ? 'rotate-180' : ''}`}
        >
          <path d="M2.5 4.5l3.5 3.5 3.5-3.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-label="预设值"
          aria-activedescendant={focusIdx >= 0 ? optionId(focusIdx) : undefined}
          className="absolute right-0 top-[calc(100%+4px)] z-20 min-w-[260px] max-w-[380px] bg-bg-elevated border border-border rounded-lg shadow-lg overflow-hidden animate-popover-in origin-top-right"
        >
          <div className="px-3 py-1.5 flex items-center justify-between border-b border-separator bg-fill/5">
            <span className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary font-medium">预设值</span>
            <span className="text-[10px] text-text-tertiary tabular-nums">{options.length}</span>
          </div>
          <div ref={listRef} className="max-h-64 overflow-auto py-1">
            {options.map((opt, i) => {
              const valueStr = configOptionToString(opt.value)
              const active = valueStr === currentValue
              const focused = i === focusIdx
              return (
                <button
                  key={i}
                  id={optionId(i)}
                  type="button"
                  data-idx={i}
                  role="option"
                  aria-selected={active}
                  onMouseEnter={() => setFocusIdx(i)}
                  onClick={() => { onPick(opt.value); setOpen(false) }}
                  title={maskValues ? opt.label || `选项 #${i}` : valueStr}
                  className={`relative w-full text-left pl-6 pr-3 py-1.5 text-[11px] flex flex-col gap-0.5 transition-colors duration-150 ease-standard ${
                    focused ? 'bg-fill/10' : ''
                  } ${active ? 'text-text-primary' : 'text-text-secondary'}`}
                >
                  {active && (
                    <span className="absolute left-1 top-1 bottom-1 w-[2px] rounded-full bg-accent" aria-hidden="true" />
                  )}
                  {active && (
                    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-accent">
                      <path d="M2.5 6.2l2.4 2.4L9.5 3.6" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                  <span className="truncate font-medium">{opt.label ? opt.label : display(opt.value)}</span>
                  {opt.label && (
                    <span className="text-text-tertiary text-[10px] truncate font-mono">{display(opt.value)}</span>
                  )}
                </button>
              )
            })}
          </div>
          <div className="px-3 py-1 border-t border-separator bg-fill/5 text-[10px] text-text-tertiary flex items-center gap-2">
            <kbd className="font-mono px-1 py-px rounded border border-border bg-surface">↑↓</kbd>
            <span>导航</span>
            <kbd className="font-mono px-1 py-px rounded border border-border bg-surface">Enter</kbd>
            <span>选择</span>
            <kbd className="font-mono px-1 py-px rounded border border-border bg-surface">Esc</kbd>
            <span>关闭</span>
          </div>
        </div>
      )}
    </div>
  )
}
