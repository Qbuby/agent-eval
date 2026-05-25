import { ReactNode, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

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
  const panelRef = useRef<HTMLDivElement | null>(null)

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
    <div className="fixed inset-0 z-50 flex justify-end" aria-modal="true" role="dialog">
      {/* 遮罩 */}
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-[1px] animate-overlay-in"
        onClick={onClose}
      />
      {/* 面板 */}
      <div
        ref={panelRef}
        className={`relative h-full ${widthClass} bg-surface shadow-lg flex flex-col animate-drawer-in`}
      >
        {(title || subtitle || actions) && (
          <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-border">
            <div className="min-w-0 flex-1">
              {title && (
                <div className="text-base font-semibold text-text-primary truncate">{title}</div>
              )}
              {subtitle && (
                <div className="mt-1 text-xs text-text-tertiary truncate">{subtitle}</div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {actions}
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭"
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-secondary hover:bg-accent-subtle hover:text-text-primary transition-colors"
              >
                <svg viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true">
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
