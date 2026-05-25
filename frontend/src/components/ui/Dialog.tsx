import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'
import { Button } from './Button'

// ──────────────────────────────────────────────────────────────────────────
// 通用 Dialog 原语
// ──────────────────────────────────────────────────────────────────────────

interface DialogProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  description?: ReactNode
  /** 底部操作区，留空则不渲染 footer */
  footer?: ReactNode
  /** 主体内容 */
  children?: ReactNode
  /** 点击遮罩是否关闭，默认 true */
  closeOnOverlayClick?: boolean
  /** ESC 是否关闭，默认 true */
  closeOnEsc?: boolean
  /** 宽度 px，默认 420 */
  width?: number
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  footer,
  children,
  closeOnOverlayClick = true,
  closeOnEsc = true,
  width = 420,
}: DialogProps) {
  useEffect(() => {
    if (!open || !closeOnEsc) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, closeOnEsc, onClose])

  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [open])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div
        className="absolute inset-0 bg-black/35 backdrop-blur-[1px] animate-overlay-in"
        onClick={() => closeOnOverlayClick && onClose()}
      />
      <div
        className="relative bg-surface rounded-lg shadow-lg border border-border animate-dialog-in"
        style={{ width: `min(${width}px, calc(100vw - 32px))` }}
      >
        {(title || description) && (
          <div className="px-5 pt-5 pb-3">
            {title && (
              <div className="text-base font-semibold text-text-primary">{title}</div>
            )}
            {description && (
              <div className="mt-1 text-sm text-text-secondary leading-relaxed whitespace-pre-line">
                {description}
              </div>
            )}
          </div>
        )}
        {children && <div className="px-5 pb-3 text-sm text-text-secondary">{children}</div>}
        {footer && (
          <div className="flex items-center justify-end gap-2 px-5 pb-5 pt-2">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  )
}

// ──────────────────────────────────────────────────────────────────────────
// useConfirm —— 替代 window.confirm
// ──────────────────────────────────────────────────────────────────────────

export interface ConfirmOptions {
  title?: ReactNode
  description?: ReactNode
  confirmText?: string
  cancelText?: string
  /** 危险操作时把确认按钮变红 */
  danger?: boolean
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>

const ConfirmCtx = createContext<ConfirmFn | null>(null)

interface ActiveConfirm extends ConfirmOptions {
  resolve: (v: boolean) => void
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<ActiveConfirm | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)

  const confirm = useCallback<ConfirmFn>((opts) => {
    return new Promise<boolean>((resolve) => {
      setSubmitting(false)
      submittingRef.current = false
      setActive({ ...opts, resolve })
    })
  }, [])

  const close = useCallback(
    (result: boolean) => {
      if (submittingRef.current) return
      if (active) {
        active.resolve(result)
      }
      setActive(null)
    },
    [active],
  )

  // ESC / 遮罩走 close(false)
  const onClose = useCallback(() => close(false), [close])

  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      <Dialog
        open={!!active}
        onClose={onClose}
        title={active?.title || '请确认'}
        description={active?.description}
        footer={
          <>
            <Button variant="ghost" size="md" onClick={() => close(false)} disabled={submitting}>
              {active?.cancelText || '取消'}
            </Button>
            <Button
              variant={active?.danger ? 'danger' : 'primary'}
              size="md"
              onClick={() => close(true)}
              loading={submitting}
            >
              {active?.confirmText || '确定'}
            </Button>
          </>
        }
      />
    </ConfirmCtx.Provider>
  )
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmCtx)
  if (!ctx) {
    // 没挂 Provider 时退化为原生 confirm，避免抛错
    return async (opts) => {
      const text = [opts.title, opts.description].filter(Boolean).join('\n\n')
      return window.confirm(typeof text === 'string' && text.length > 0 ? text : '请确认')
    }
  }
  return ctx
}

// 便利组合：让组件内 confirm-then-await-mutation 更顺手
export function useConfirmThen() {
  const confirm = useConfirm()
  return useMemo(
    () =>
      async <T,>(opts: ConfirmOptions, run: () => Promise<T> | T): Promise<T | undefined> => {
        const ok = await confirm(opts)
        if (!ok) return undefined
        return run()
      },
    [confirm],
  )
}
