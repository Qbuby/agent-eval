// A small "导出" button that opens a popover with CSV / Excel / JSON options.
//
// The UI lib has no generic dropdown/menu, so this is a self-contained
// popover: a Button toggles a positioned list, clicking outside or pressing
// Escape closes it, and picking a format calls `onExport(format)`. While an
// export is in flight the button shows a spinner and the menu is disabled.

import { useEffect, useRef, useState } from 'react'
import { Button } from './Button'
import type { ExportFormat } from '@/lib/download'

interface ExportMenuProps {
  /** Invoked with the chosen format; should resolve when the download starts. */
  onExport: (format: ExportFormat) => Promise<void> | void
  disabled?: boolean
  /** Button label; defaults to "导出". */
  label?: string
  size?: 'sm' | 'md'
}

const OPTIONS: { format: ExportFormat; label: string }[] = [
  { format: 'csv', label: 'CSV (.csv)' },
  { format: 'xlsx', label: 'Excel (.xlsx)' },
  { format: 'json', label: 'JSON (.json)' },
]

export function ExportMenu({ onExport, disabled, label = '导出', size = 'sm' }: ExportMenuProps) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  async function pick(format: ExportFormat) {
    setOpen(false)
    setBusy(true)
    try {
      await onExport(format)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div ref={wrapRef} className="relative inline-block">
      <Button
        variant="secondary"
        size={size}
        loading={busy}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        rightIcon={<span aria-hidden className="text-[10px] opacity-70">▾</span>}
      >
        {label}
      </Button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 min-w-[140px] rounded-md border border-border bg-surface shadow-lg py-1"
        >
          {OPTIONS.map((opt) => (
            <button
              key={opt.format}
              role="menuitem"
              type="button"
              onClick={() => pick(opt.format)}
              className="block w-full px-3 py-1.5 text-left text-[12px] text-text-primary hover:bg-surface-hover transition-colors"
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
