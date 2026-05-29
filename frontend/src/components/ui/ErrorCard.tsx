import { useState } from 'react'
import type { NormalizedError } from '@/lib/errors'

// 标准的错误展示卡片：红边、标题、消息、可选 hint、可选 raw 折叠。
// 用法：
//   const norm = formatApiError(err, { fallbackTitle: '保存失败' })
//   return <ErrorCard error={norm} />
//
// 也可以传一段纯文本（DryRunResponse.error 这种），先包一层
// formatDryRunError() 再用，以便 hint 能正确推断。
//
// 设计理念：所有页面的错误卡片视觉一致，hint 自动从 errors.ts 的分类逻辑
// 里来，避免每个页面重写文案。compact = 内嵌到表格 / drawer 时占位更小。

export interface ErrorCardProps {
  error: NormalizedError | null | undefined
  // 默认 'normal'。'compact' 字号更小、留白更少，适合塞表格行 / drawer 侧栏
  variant?: 'normal' | 'compact'
  // 显示的标题前缀，如 "保存失败：xxx" → 这里设 false 隐藏。默认显示
  showTitle?: boolean
  // 折叠展示原始响应，常见于评估器试跑（raw_content / rendered_messages）
  rawDetails?: { label: string; content: string }
  className?: string
}

export function ErrorCard({
  error,
  variant = 'normal',
  showTitle = true,
  rawDetails,
  className = '',
}: ErrorCardProps) {
  const [open, setOpen] = useState(false)
  if (!error) return null

  const compact = variant === 'compact'
  const padding = compact ? 'p-2' : 'p-3'
  const titleSize = compact ? 'text-[11px]' : 'text-[12px]'
  const msgSize = compact ? 'text-[11px]' : 'text-[12px]'
  const hintSize = compact ? 'text-[10px]' : 'text-[11px]'

  return (
    <div
      role="alert"
      data-error-code={error.code}
      data-error-status={error.status ?? ''}
      className={`rounded-md border border-negative/30 bg-negative/5 ${padding} ${className}`}
    >
      {showTitle && (
        <div className={`flex items-center gap-2 ${titleSize} font-medium text-negative mb-1`}>
          <ErrorIcon />
          <span>{error.title}</span>
          {error.status != null && error.status > 0 && (
            <span className="ml-auto font-mono text-text-tertiary">
              HTTP {error.status}
            </span>
          )}
        </div>
      )}
      <div className={`${msgSize} text-negative whitespace-pre-wrap break-words leading-relaxed`}>
        {error.message}
      </div>
      {error.hint && (
        <div className={`mt-1.5 ${hintSize} text-text-secondary leading-relaxed`}>
          <span className="text-text-tertiary mr-1">→</span>
          {error.hint}
        </div>
      )}
      {rawDetails && rawDetails.content && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setOpen(o => !o)}
            className="text-[10px] text-text-tertiary hover:text-text-secondary cursor-pointer"
          >
            {open ? '隐藏' : '查看'} {rawDetails.label}
          </button>
          {open && (
            <pre className="mt-1.5 rounded bg-surface-2 p-2 text-[10px] font-mono overflow-x-auto whitespace-pre-wrap break-all max-h-60 overflow-y-auto text-text-secondary">
              {rawDetails.content}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

function ErrorIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  )
}
