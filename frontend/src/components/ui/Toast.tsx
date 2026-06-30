import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'

type ToastTone = 'success' | 'error' | 'info' | 'warning'

export interface ToastOptions {
  title?: ReactNode
  message?: ReactNode
  tone?: ToastTone
  /** ms，0 表示不自动关闭 */
  duration?: number
}

interface Toast extends Required<Pick<ToastOptions, 'tone' | 'duration'>> {
  id: number
  title?: ReactNode
  message?: ReactNode
}

interface ToastApi {
  show: (opts: ToastOptions) => number
  success: (msg: ReactNode, title?: ReactNode) => number
  error: (msg: ReactNode, title?: ReactNode) => number
  info: (msg: ReactNode, title?: ReactNode) => number
  warning: (msg: ReactNode, title?: ReactNode) => number
  dismiss: (id: number) => void
}

const ToastCtx = createContext<ToastApi | null>(null)

const TONE_STYLES: Record<ToastTone, { ring: string; iconBg: string; iconText: string }> = {
  success: { ring: 'ring-positive/20', iconBg: 'bg-positive/10', iconText: 'text-positive' },
  error: { ring: 'ring-negative/20', iconBg: 'bg-negative/10', iconText: 'text-negative' },
  warning: { ring: 'ring-warning/30', iconBg: 'bg-warning/10', iconText: 'text-warning' },
  info: {
    ring: 'ring-text-secondary/20',
    iconBg: 'bg-accent-subtle',
    iconText: 'text-text-secondary',
  },
}

const ICONS: Record<ToastTone, ReactNode> = {
  success: (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M3.5 8.5l3 3 6-7"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
  error: (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  ),
  warning: (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M8 4v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="8" cy="11.5" r="1" fill="currentColor" />
    </svg>
  ),
  info: (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M8 7v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="8" cy="4.5" r="1" fill="currentColor" />
    </svg>
  ),
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const idRef = useRef(0)
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
    const timer = timersRef.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timersRef.current.delete(id)
    }
  }, [])

  const show = useCallback<ToastApi['show']>(
    (opts) => {
      const id = ++idRef.current
      const tone: ToastTone = opts.tone ?? 'info'
      const duration = opts.duration ?? (tone === 'error' ? 5000 : 3200)
      const toast: Toast = {
        id,
        tone,
        duration,
        title: opts.title,
        message: opts.message,
      }
      setToasts((prev) => [...prev, toast])
      if (duration > 0) {
        const timer = setTimeout(() => dismiss(id), duration)
        timersRef.current.set(id, timer)
      }
      return id
    },
    [dismiss],
  )

  useEffect(() => {
    return () => {
      timersRef.current.forEach((t) => clearTimeout(t))
      timersRef.current.clear()
    }
  }, [])

  const api: ToastApi = {
    show,
    success: (message, title) => show({ tone: 'success', message, title }),
    error: (message, title) => show({ tone: 'error', message, title }),
    info: (message, title) => show({ tone: 'info', message, title }),
    warning: (message, title) => show({ tone: 'warning', message, title }),
    dismiss,
  }

  return (
    <ToastCtx.Provider value={api}>
      {children}
      {createPortal(
        <div
          aria-live="polite"
          aria-atomic="true"
          className="fixed top-4 right-4 z-[70] flex flex-col gap-2 pointer-events-none"
        >
          {toasts.map((t) => {
            const tone = TONE_STYLES[t.tone]
            return (
              <div
                key={t.id}
                role="status"
                className={`pointer-events-auto min-w-[280px] max-w-sm rounded-2xl border border-border/60 bg-bg-elevated shadow-lg ring-1 ${tone.ring} animate-toast-in`}
              >
                <div className="flex items-start gap-3 p-3">
                  <span
                    className={`mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full ${tone.iconBg} ${tone.iconText}`}
                  >
                    {ICONS[t.tone]}
                  </span>
                  <div className="min-w-0 flex-1">
                    {t.title && (
                      <div className="text-sm font-semibold text-text-primary">{t.title}</div>
                    )}
                    {t.message && (
                      <div className={`text-sm text-text-secondary ${t.title ? 'mt-0.5' : ''}`}>
                        {t.message}
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => dismiss(t.id)}
                    className="text-text-tertiary hover:text-text-primary transition-colors"
                    aria-label="关闭通知"
                  >
                    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
                      <path
                        d="M4 4l8 8M12 4l-8 8"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                      />
                    </svg>
                  </button>
                </div>
              </div>
            )
          })}
        </div>,
        document.body,
      )}
    </ToastCtx.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx)
  if (!ctx) {
    // 没挂 provider 时退化为 console，避免阻塞
    const fallback: ToastApi = {
      show: (o) => {
        // eslint-disable-next-line no-console
        console.info('[toast]', o)
        return 0
      },
      success: (m) => {
        // eslint-disable-next-line no-console
        console.info('[toast:success]', m)
        return 0
      },
      error: (m) => {
        // eslint-disable-next-line no-console
        console.error('[toast:error]', m)
        return 0
      },
      info: (m) => {
        // eslint-disable-next-line no-console
        console.info('[toast:info]', m)
        return 0
      },
      warning: (m) => {
        // eslint-disable-next-line no-console
        console.warn('[toast:warning]', m)
        return 0
      },
      dismiss: () => undefined,
    }
    return fallback
  }
  return ctx
}
