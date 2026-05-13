import { NavLink, Outlet } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth'
import { useNavigate } from 'react-router-dom'
import { clsx } from 'clsx'

const navItems = [
  { to: '/dashboard', label: 'Dashboard', icon: 'grid' },
  { to: '/datasets', label: '备选数据集', icon: 'list' },
  { to: '/projects', label: '基准测试集', icon: 'target' },
  { to: '/generate', label: '生成', icon: 'sparkle' },
  { to: '/traces', label: 'Traces', icon: 'activity' },
  { to: '/evaluation', label: '评估', icon: 'gauge' },
  { to: '/auto-collect', label: '自动采集', icon: 'route' },
  { to: '/config', label: '配置', icon: 'settings' },
  { to: '/audit', label: '审计日志', icon: 'file' },
]

function NavIcon({ name }: { name: string }) {
  const icons: Record<string, React.ReactNode> = {
    grid: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <rect x="2" y="2" width="5" height="5" rx="1"/>
        <rect x="9" y="2" width="5" height="5" rx="1"/>
        <rect x="2" y="9" width="5" height="5" rx="1"/>
        <rect x="9" y="9" width="5" height="5" rx="1"/>
      </svg>
    ),
    target: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="3"/><circle cx="8" cy="8" r="0.5" fill="currentColor"/>
      </svg>
    ),
    inbox: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <path d="M2 10l2-6h8l2 6"/><path d="M2 10v3h12v-3"/><path d="M2 10h3a1 1 0 0 1 1 1v0a1 1 0 0 0 1 1h2a1 1 0 0 0 1-1v0a1 1 0 0 1 1-1h3"/>
      </svg>
    ),
    list: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <path d="M3 4h10M3 8h10M3 12h6"/>
      </svg>
    ),
    sparkle: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <path d="M8 2v4M8 10v4M2 8h4M10 8h4M4 4l2 2M10 10l2 2M4 12l2-2M10 4l2 2"/>
      </svg>
    ),
    activity: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <path d="M2 8h4l2-4 2 6 2-2h2"/>
      </svg>
    ),
    route: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <path d="M3 13V8a4 4 0 0 1 4-4h6M10 1l3 3-3 3"/>
      </svg>
    ),
    gauge: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <path d="M2 11a6 6 0 1 1 12 0"/>
        <path d="M8 11l3-3"/>
        <circle cx="8" cy="11" r="0.6" fill="currentColor"/>
      </svg>
    ),
    clock: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <circle cx="8" cy="8" r="5.5"/>
        <path d="M8 5v3l2 2"/>
      </svg>
    ),
    settings: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <circle cx="8" cy="8" r="2"/>
        <path d="M8 2v2M8 12v2M2 8h2M12 8h2M3.8 3.8l1.4 1.4M10.8 10.8l1.4 1.4M3.8 12.2l1.4-1.4M10.8 5.2l1.4-1.4"/>
      </svg>
    ),
    file: (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-4 h-4">
        <path d="M4 2h5l3 3v9H4V2z"/>
        <path d="M9 2v3h3"/>
      </svg>
    ),
  }
  return <>{icons[name] || null}</>
}

export default function Layout() {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="grid grid-cols-[220px_1fr] min-h-screen">
      <aside className="bg-surface border-r border-border flex flex-col py-5 px-4">
        <div className="flex items-center gap-2.5 mb-8 px-2">
          <div className="w-5 h-5 bg-accent rounded-sm" />
          <span className="text-[14px] font-semibold tracking-tight text-text-primary">Agent-Eval</span>
        </div>

        <nav className="flex flex-col gap-0.5 flex-1" aria-label="主导航">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                clsx(
                  'group relative flex items-center gap-2.5 px-2.5 py-2 rounded-sm text-[13px]',
                  isActive
                    ? 'bg-accent-subtle text-text-primary font-medium'
                    : 'text-text-secondary hover:bg-accent-subtle hover:text-text-primary hover:translate-x-0.5',
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-4 bg-accent rounded-r-full" />
                  )}
                  <span className="group-hover:scale-110 transition-transform duration-150">
                    <NavIcon name={item.icon} />
                  </span>
                  {item.label}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="pt-4 border-t border-border mt-auto">
          <div className="flex items-center justify-between px-2">
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-positive animate-pulse" />
              <span className="text-[11px] text-text-tertiary">{user?.username || 'User'}</span>
            </div>
            <button
              onClick={handleLogout}
              className="text-[11px] text-text-tertiary hover:text-negative hover:scale-105 active:scale-95 focus:outline-none focus:ring-1 focus:ring-accent/20 rounded px-1.5 py-0.5"
            >
              退出
            </button>
          </div>
        </div>
      </aside>

      <main className="py-8 px-10 max-w-[1200px] animate-fade-in">
        <Outlet />
      </main>
    </div>
  )
}
