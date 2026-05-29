import { NavLink, Outlet } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth'
import { useNavigate } from 'react-router-dom'
import { clsx } from 'clsx'
import { ThemeToggle } from '@/components/ui'

type NavItem = { to: string; label: string; icon: string }
type NavGroup = { title?: string; items: NavItem[] }

// HIG sidebar: items grouped by purpose, with section captions in
// SF "Footnote" style (uppercase tracking, tertiary color).
const NAV_GROUPS: NavGroup[] = [
  {
    items: [{ to: '/dashboard', label: '仪表盘', icon: 'grid' }],
  },
  {
    title: '数据',
    items: [
      { to: '/datasets', label: '备选数据集', icon: 'list' },
      { to: '/projects', label: '基准测试集', icon: 'target' },
      { to: '/generate', label: '样例生成', icon: 'sparkle' },
    ],
  },
  {
    title: '运行',
    items: [
      { to: '/traces', label: '调用轨迹', icon: 'activity' },
      { to: '/evaluators', label: '评估器', icon: 'beaker' },
      { to: '/evaluation', label: '评估', icon: 'gauge' },
      { to: '/auto-collect', label: '自动采集', icon: 'route' },
    ],
  },
  {
    title: '系统',
    items: [
      { to: '/evaluator-providers', label: 'Judge Providers', icon: 'key' },
      { to: '/config', label: '配置', icon: 'settings' },
      { to: '/audit', label: '审计日志', icon: 'file' },
      { to: '/request-log', label: '接口日志', icon: 'pulse' },
    ],
  },
]

function NavIcon({ name }: { name: string }) {
  const common = 'w-[17px] h-[17px]'
  const props = {
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.4,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className: common,
  }
  switch (name) {
    case 'grid':
      return (
        <svg {...props}>
          <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" />
          <rect x="9" y="2.5" width="4.5" height="4.5" rx="1" />
          <rect x="2.5" y="9" width="4.5" height="4.5" rx="1" />
          <rect x="9" y="9" width="4.5" height="4.5" rx="1" />
        </svg>
      )
    case 'target':
      return (
        <svg {...props}>
          <circle cx="8" cy="8" r="5.5" />
          <circle cx="8" cy="8" r="2.8" />
          <circle cx="8" cy="8" r="0.6" fill="currentColor" />
        </svg>
      )
    case 'list':
      return (
        <svg {...props}>
          <path d="M3 4h10M3 8h10M3 12h6" />
        </svg>
      )
    case 'sparkle':
      return (
        <svg {...props}>
          <path d="M9.5 2.5l1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1z" />
          <path d="M4 9l.6 1.4L6 11l-1.4.6L4 13l-.6-1.4L2 11l1.4-.6z" />
        </svg>
      )
    case 'activity':
      return (
        <svg {...props}>
          <path d="M2 8h2.5L6 4.5l3 7L11 7h3" />
        </svg>
      )
    case 'route':
      return (
        <svg {...props}>
          <circle cx="3.5" cy="3.5" r="1.5" />
          <circle cx="12.5" cy="12.5" r="1.5" />
          <path d="M3.5 5v2.5a3 3 0 0 0 3 3h3a3 3 0 0 1 3 3" />
        </svg>
      )
    case 'gauge':
      return (
        <svg {...props}>
          <path d="M2.5 11a5.5 5.5 0 1 1 11 0" />
          <path d="M8 11l3-3" />
          <circle cx="8" cy="11" r="0.7" fill="currentColor" />
        </svg>
      )
    case 'beaker':
      return (
        <svg {...props}>
          <path d="M6 2v4L3 12.5a1.5 1.5 0 0 0 1.4 2.1h7.2A1.5 1.5 0 0 0 13 12.5L10 6V2" />
          <path d="M5.5 2h5" />
          <path d="M4.5 9h7" />
        </svg>
      )
    case 'settings':
      return (
        <svg {...props}>
          <circle cx="8" cy="8" r="2" />
          <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M3.5 12.5l1.4-1.4M11.1 4.9l1.4-1.4" />
        </svg>
      )
    case 'file':
      return (
        <svg {...props}>
          <path d="M4 2h5l3 3v9H4z" />
          <path d="M9 2v3h3" />
        </svg>
      )
    case 'pulse':
      return (
        <svg {...props}>
          <path d="M1.5 8h2.5L5.5 4.5l3 7L10 7h4" />
        </svg>
      )
    case 'key':
      return (
        <svg {...props}>
          <circle cx="5" cy="11" r="2.5" />
          <path d="M7 9.5l5.5-5.5M11 6l1.5 1.5M9.5 7.5L11 9" />
        </svg>
      )
    default:
      return null
  }
}

export default function Layout() {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const initial = (user?.username || 'U').slice(0, 1).toUpperCase()

  return (
    <div className="grid grid-cols-[240px_1fr] min-h-screen bg-bg">
      <aside className="material border-r border-separator flex flex-col h-screen sticky top-0">
        {/* Brand */}
        <div className="flex items-center gap-2.5 px-5 pt-5 pb-4">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-accent to-accent-hover flex items-center justify-center shadow-sm">
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2.5 11a5.5 5.5 0 1 1 11 0" />
              <path d="M8 11l3-3" />
            </svg>
          </div>
          <div className="leading-tight">
            <div className="text-[14px] font-display font-semibold tracking-[-0.2px] text-text-primary">Agent‑Eval</div>
            <div className="text-[10px] text-text-tertiary tracking-[0.04em]">智能体评估</div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-2.5 pb-3" aria-label="主导航">
          {NAV_GROUPS.map((group, gi) => (
            <div key={gi} className={gi === 0 ? '' : 'mt-4'}>
              {group.title && (
                <div className="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-text-tertiary">
                  {group.title}
                </div>
              )}
              <div className="flex flex-col gap-px">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      clsx(
                        'group relative flex items-center gap-2.5 px-2.5 h-8 rounded-md text-[13px] transition-colors duration-150 ease-standard focus-visible:shadow-focus focus-visible:outline-none',
                        isActive
                          ? 'bg-accent text-accent-fg font-medium shadow-sm'
                          : 'text-text-secondary hover:bg-fill/10 hover:text-text-primary',
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span
                          className={clsx(
                            'inline-flex',
                            isActive ? 'text-accent-fg' : 'text-text-tertiary group-hover:text-text-primary',
                          )}
                        >
                          <NavIcon name={item.icon} />
                        </span>
                        <span className="truncate">{item.label}</span>
                      </>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* Footer — theme + user */}
        <div className="px-3 pt-3 pb-4 border-t border-separator">
          <div className="flex items-center justify-center pb-3">
            <ThemeToggle compact />
          </div>
          <div className="flex items-center gap-2.5 px-2">
            <div className="w-7 h-7 rounded-full bg-fill/15 flex items-center justify-center text-[12px] font-semibold text-text-primary">
              {initial}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-[12px] font-medium text-text-primary truncate">{user?.username || 'User'}</div>
              <div className="text-[10px] text-text-tertiary truncate">{user?.role || ''}</div>
            </div>
            <button
              onClick={handleLogout}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary hover:text-negative hover:bg-fill/10 transition-colors duration-150 ease-standard focus-visible:shadow-focus focus-visible:outline-none"
              aria-label="退出登录"
              title="退出登录"
            >
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9.5 2.5h-5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h5" />
                <path d="M7 8h7M11 5l3 3-3 3" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      <main className="min-w-0 animate-fade-in">
        <div className="px-10 py-8 max-w-[1280px] mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
