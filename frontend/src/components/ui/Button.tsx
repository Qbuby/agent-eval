import { ButtonHTMLAttributes, forwardRef, ReactNode } from 'react'
import { Spinner } from './Spinner'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
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
}

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-white hover:bg-black active:bg-black border-transparent disabled:bg-text-tertiary',
  secondary:
    'bg-surface text-text-primary hover:bg-accent-subtle active:bg-[#ececec] border-border disabled:text-text-tertiary',
  ghost:
    'bg-transparent text-text-secondary hover:text-text-primary hover:bg-accent-subtle active:bg-[#ececec] border-transparent disabled:text-text-tertiary',
  danger:
    'bg-negative text-white hover:bg-[#a4291e] active:bg-[#902418] border-transparent disabled:bg-text-tertiary',
}

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1 rounded-md',
  md: 'h-8 px-3 text-sm gap-1.5 rounded-md',
  lg: 'h-10 px-4 text-sm gap-2 rounded-md',
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
    disabled,
    children,
    type = 'button',
    ...rest
  },
  ref,
) {
  const base =
    'inline-flex items-center justify-center border font-medium select-none ' +
    'transition-[background-color,color,transform,box-shadow] duration-150 ease-out ' +
    'active:scale-[0.985] focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 ' +
    'disabled:cursor-not-allowed disabled:active:scale-100'
  const cls = `${base} ${VARIANT[variant]} ${SIZE[size]} ${block ? 'w-full' : ''} ${className}`

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
