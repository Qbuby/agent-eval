import { CSSProperties } from 'react'

type SpinnerSize = 'xs' | 'sm' | 'md' | 'lg'

interface SpinnerProps {
  size?: SpinnerSize
  className?: string
  /** 让 spinner 用 currentColor，便于在按钮里继承文字色 */
  inherit?: boolean
  label?: string
}

const SIZE_PX: Record<SpinnerSize, number> = {
  xs: 12,
  sm: 14,
  md: 18,
  lg: 28,
}

export function Spinner({ size = 'md', className = '', inherit = false, label }: SpinnerProps) {
  const px = SIZE_PX[size]
  const stroke = size === 'lg' ? 2.5 : 2
  const style: CSSProperties = {
    width: px,
    height: px,
    color: inherit ? 'currentColor' : '#6b6b6b',
  }
  return (
    <span
      role="status"
      aria-label={label || '加载中'}
      className={`inline-flex items-center justify-center ${className}`}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        style={style}
        className="animate-spin"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.18" strokeWidth={stroke} />
        <path
          d="M21 12a9 9 0 0 0-9-9"
          stroke="currentColor"
          strokeWidth={stroke}
          strokeLinecap="round"
        />
      </svg>
    </span>
  )
}

interface LoadingBlockProps {
  text?: string
  className?: string
}

/** 整块占位的"加载中…"，用于替代散落在各页面的纯文字 placeholder */
export function LoadingBlock({ text = '加载中…', className = '' }: LoadingBlockProps) {
  return (
    <div
      className={`flex items-center justify-center gap-2 py-10 text-sm text-text-tertiary ${className}`}
    >
      <Spinner size="sm" />
      <span>{text}</span>
    </div>
  )
}
