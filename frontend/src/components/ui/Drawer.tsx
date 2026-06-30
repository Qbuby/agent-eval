import { ReactNode, useEffect, useId } from 'react'
import { createPortal } from 'react-dom'
import { useDialogFocus } from '../../hooks/useDialogFocus'

interface DrawerProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  /** 副标题/小字标识，比如 case ID */
  subtitle?: ReactNode
  /** 头部右侧自定义动作区 */
  actions?: ReactNode
  /** 内容区是否自带 padding（默认 true） */
  padded?: boolean
  /** 宽度档：default=560, wide=720（仅 >1600px 屏幕生效）。<1024 自动全屏 */
  width?: 'default' | 'wide'
  children: ReactNode
}

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  actions,
  padded = true,
  width = 'default',
  children,
}: DrawerProps) {
  const panelRef = useDialogFocus<HTMLDivElement>(open)
  const reactId = useId()
  const titleId = `drawer-title-${reactId}`
  const subtitleId = `drawer-subtitle-${reactId}`

  // ESC 关闭
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  // 锁定 body 滚动
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [open])

  if (!open) return null

  const widthClass =
    width === 'wide'
      ? 'w-full lg:w-[640px] xl:w-[720px] 2xl:w-[840px]'
      : 'w-full lg:w-[560px] xl:w-[600px] 2xl:w-[720px]'

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Overlay — softer in light, heavier in dark, with HIG-style blur */}
      <div
        className="absolute inset-0 bg-black/25 dark:bg-black/55 backdrop-blur-[6px] animate-overlay-in"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* Panel — vibrancy material, hairline left border */}
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-describedby={subtitle ? subtitleId : undefined}
        tabIndex={-1}
        className={`relative h-full ${widthClass} bg-bg-elevated border-l border-border/60 shadow-xl flex flex-col animate-drawer-in outline-none`}
      >
        {(title || subtitle || actions) && (
          <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-separator">
            <div className="min-w-0 flex-1">
              {title && (
                <div id={titleId} className="text-[17px] font-display font-semibold tracking-[-0.4px] text-text-primary truncate">{title}</div>
              )}
              {subtitle && (
                <div id={subtitleId} className="mt-1 text-[12px] text-text-tertiary truncate">{subtitle}</div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {actions}
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭"
                className="inline-flex h-8 w-8 items-center justify-center rounded-full text-text-secondary hover:bg-fill/10 hover:text-text-primary transition-colors duration-150 ease-standard focus-visible:shadow-focus focus-visible:outline-none"
              >
                <svg viewBox="0 0 20 20" width="14" height="14" fill="none" aria-hidden="true">
                  <path
                    d="M5 5l10 10M15 5L5 15"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>
          </div>
        )}
        <div className={`flex-1 min-h-0 overflow-auto ${padded ? 'px-6 py-5' : ''}`}>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}
