import { useTheme, ThemeChoice } from '@/hooks/useTheme'

const OPTIONS: { value: ThemeChoice; label: string; icon: JSX.Element }[] = [
  {
    value: 'light',
    label: '浅色',
    icon: (
      <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <circle cx="8" cy="8" r="3" />
        <path d="M8 1.5v1.5M8 13v1.5M14.5 8H13M3 8H1.5M12.5 3.5l-1 1M4.5 11.5l-1 1M12.5 12.5l-1-1M4.5 4.5l-1-1" />
      </svg>
    ),
  },
  {
    value: 'system',
    label: '跟随',
    icon: (
      <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <rect x="2" y="3" width="12" height="8" rx="1.5" />
        <path d="M5.5 13.5h5M8 11v2.5" />
      </svg>
    ),
  },
  {
    value: 'dark',
    label: '深色',
    icon: (
      <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M13.5 9.2A5.5 5.5 0 1 1 6.8 2.5a4.5 4.5 0 0 0 6.7 6.7z" />
      </svg>
    ),
  },
]

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { choice, setChoice } = useTheme()
  return (
    <div
      role="radiogroup"
      aria-label="外观"
      className="inline-flex items-center gap-0.5 rounded-full bg-fill/10 p-0.5 border border-border/60"
    >
      {OPTIONS.map((opt) => {
        const active = opt.value === choice
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={opt.label}
            title={opt.label}
            onClick={() => setChoice(opt.value)}
            className={`relative inline-flex items-center gap-1 px-2 ${compact ? 'h-6' : 'h-7'} rounded-full text-[11px] font-medium transition-colors duration-150 ease-standard
              ${active
                ? 'bg-surface text-text-primary shadow-sm'
                : 'text-text-secondary hover:text-text-primary'}
            `}
          >
            <span className="inline-flex">{opt.icon}</span>
            {!compact && <span>{opt.label}</span>}
          </button>
        )
      })}
    </div>
  )
}
