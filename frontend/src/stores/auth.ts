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
  // 内部角色（admin | user）是否有数据集写权限。三类数据集（多轮对话集 /
  // 备选数据集 / 基准测试集）的新建/编辑/导入/删样例等写操作对内部 user 放开，
  // 唯一例外「删除整个数据集」仍限 admin（见各页面单独的 isAdmin gate）。
  // external_customer 不属于内部角色，一律不可写。
  canWrite: () => boolean
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
      canWrite: () => {
        const role = get().user?.role
        return role === 'admin' || role === 'user'
      },
      isExternal: () => get().user?.role === 'external_customer',
      tenantId: () => get().user?.tenant_id ?? null,
    }),
    { name: 'agent-eval-auth' },
  ),
)
