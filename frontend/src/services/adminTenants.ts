import api from './client'

// ──────────────────────────────────────────────────────────────────────────
// 内部 admin 多租户开户 API client。
// 对接后端 routers/admin_tenants.py（设计文档 §6.1），全部需要 admin 角色。
// 类型定义就近写在本文件，避免改共享的 types/index.ts（归 frontend-wire 摊）。
// ──────────────────────────────────────────────────────────────────────────

export interface Tenant {
  id: string
  name: string
  slug: string
  is_active: boolean
  user_count: number
  created_at: string
  updated_at: string
}

export interface CreateTenantRequest {
  name: string
  slug: string
}

export interface UpdateTenantRequest {
  name?: string
  is_active?: boolean
}

export interface CreateTenantUserRequest {
  username: string
  email: string
  password: string
}

// admin 视角下的用户行（比公共 User 多 tenant_id / is_superadmin）。
export interface AdminUser {
  id: string
  username: string
  email: string
  role: string
  tenant_id: string
  is_superadmin: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface UpdateUserRequest {
  role?: string
  is_active?: boolean
  password?: string
}

export const adminTenantsApi = {
  listTenants() {
    return api.get<Tenant[]>('/admin/tenants')
  },
  createTenant(body: CreateTenantRequest) {
    return api.post<Tenant>('/admin/tenants', body)
  },
  updateTenant(id: string, body: UpdateTenantRequest) {
    return api.patch<Tenant>(`/admin/tenants/${id}`, body)
  },
  createTenantUser(tenantId: string, body: CreateTenantUserRequest) {
    return api.post<AdminUser>(`/admin/tenants/${tenantId}/users`, body)
  },
  listUsers(params: { tenant_id?: string } = {}) {
    return api.get<AdminUser[]>('/admin/users', { params })
  },
  updateUser(id: string, body: UpdateUserRequest) {
    return api.patch<AdminUser>(`/admin/users/${id}`, body)
  },
}
