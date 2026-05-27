import { ButtonHTMLAttributes, forwardRef, ReactNode } from 'react'
import { Spinner } from './Spinner'

type ButtonVariant = 'primary' | 'secondary' | 'tinted' | 'plain' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg'

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  /** 左侧图标 */
  leftIcon?: ReactNode
  /** 右侧图标 */
  rightIcon?: ReactNode
  className?: string
  block?: boolean
  /** HIG capsule (pill) shape — full radius */
  pill?: boolean
}

// HIG button styles:
// - primary  — accent fill ("Filled" / "Prominent" tint), white label
// - secondary— neutral fill on light, hairline border, primary text
// - tinted   — accent at low opacity, accent label (HIG "Tinted")
// - plain    — text-only with hover background (HIG "Plain")
// - ghost    — like plain but tertiary text by default
// - danger   — destructive, red fill
const VARIANT: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-accent-fg border-transparent ' +
    'hover:bg-accent-hover active:opacity-90 ' +
    'disabled:bg-fill/30 disabled:text-text-tertiary',
  secondary:
    'bg-surface text-text-primary border-border ' +
    'hover:bg-surface-hover active:bg-fill/10 ' +
    'disabled:text-text-tertiary disabled:bg-surface',
  tinted:
    'bg-accent/10 text-accent border-transparent ' +
    'hover:bg-accent/20 active:bg-accent/25 ' +
    'disabled:bg-fill/10 disabled:text-text-tertiary',
  plain:
    'bg-transparent text-accent border-transparent ' +
    'hover:bg-accent/10 active:bg-accent/15 ' +
    'disabled:text-text-tertiary',
  ghost:
    'bg-transparent text-text-secondary border-transparent ' +
    'hover:text-text-primary hover:bg-fill/10 active:bg-fill/15 ' +
    'disabled:text-text-tertiary',
  danger:
    'bg-negative text-white border-transparent ' +
    'hover:opacity-90 active:opacity-80 ' +
    'disabled:bg-fill/30 disabled:text-text-tertiary',
}

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-[12px] gap-1',
  md: 'h-8 px-3 text-[13px] gap-1.5',
  lg: 'h-10 px-4 text-[14px] gap-2',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    leftIcon,
    rightIcon,
    className = '',
    block = false,
    pill = false,
    disabled,
    children,
    type = 'button',
    ...rest
  },
  ref,
) {
  const base =
    'inline-flex items-center justify-center border font-medium select-none ' +
    'transition-[background-color,color,box-shadow,transform] duration-150 ease-standard ' +
    'active:scale-[0.985] focus-visible:shadow-focus focus-visible:outline-none ' +
    'disabled:cursor-not-allowed disabled:active:scale-100'
  const radius = pill ? 'rounded-full' : 'rounded-md'
  const cls = `${base} ${VARIANT[variant]} ${SIZE[size]} ${radius} ${block ? 'w-full' : ''} ${className}`

  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={cls}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? (
        <Spinner size={size === 'lg' ? 'sm' : 'xs'} inherit />
      ) : (
        leftIcon && <span className="inline-flex">{leftIcon}</span>
      )}
      {children != null && <span>{children}</span>}
      {rightIcon && !loading && <span className="inline-flex">{rightIcon}</span>}
    </button>
  )
})
