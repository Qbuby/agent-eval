interface SkeletonProps {
  className?: string
  width?: string | number
  height?: string | number
  /** 圆形头像/icon 用 */
  circle?: boolean
}

export function Skeleton({ className = '', width, height, circle = false }: SkeletonProps) {
  const style: React.CSSProperties = {
    width,
    height,
    backgroundImage:
      'linear-gradient(90deg, rgba(0,0,0,0.04) 0%, rgba(0,0,0,0.10) 50%, rgba(0,0,0,0.04) 100%)',
    backgroundSize: '200% 100%',
  }
  return (
    <div
      aria-hidden="true"
      style={style}
      className={`animate-shimmer ${circle ? 'rounded-full' : 'rounded-md'} ${className}`}
    />
  )
}

interface SkeletonTextProps {
  lines?: number
  className?: string
}

export function SkeletonText({ lines = 3, className = '' }: SkeletonTextProps) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          height={12}
          width={i === lines - 1 ? '60%' : '100%'}
          className="rounded"
        />
      ))}
    </div>
  )
}

export function SkeletonRow({ cols = 5 }: { cols?: number }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-3 py-3">
          <Skeleton height={12} width={i === 0 ? '80%' : i === cols - 1 ? '40%' : '60%'} />
        </td>
      ))}
    </tr>
  )
}
