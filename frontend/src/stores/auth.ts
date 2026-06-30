import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { User } from '@/types'

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  user: User | null
  setTokens: (access: string, refresh: string) => void
  setUser: (user: User) => void
  logout: () => void
  isAuthenticated: () => boolean
  role: () => string | null
  isAdmin: () => boolean
  // 是否外部客户（external_customer 角色）—— 入口反转用来决定落地页与导航分组
  isExternal: () => boolean
  // 当前用户所属租户 id（未登录返回 null）
  tenantId: () => string | null
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      setTokens: (access, refresh) => set({ accessToken: access, refreshToken: refresh }),
      setUser: (user) => set({ user }),
      logout: () => set({ accessToken: null, refreshToken: null, user: null }),
      isAuthenticated: () => !!get().accessToken,
      role: () => get().user?.role ?? null,
      isAdmin: () => get().user?.role === 'admin',
      isExternal: () => get().user?.role === 'external_customer',
      tenantId: () => get().user?.tenant_id ?? null,
    }),
    { name: 'agent-eval-auth' },
  ),
)
