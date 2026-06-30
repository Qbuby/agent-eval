import api from './client'

// ──────────────────────────────────────────────────────────────────────────
// 内部 admin 入口码管理 API client。
// 对接后端 routers/admin_entry_codes.py（入口码功能），全部需要 admin 角色。
// 类型就近写在本文件，避免改共享的 types/index.ts（与 adminTenants.ts 同惯例）。
// ──────────────────────────────────────────────────────────────────────────

export interface EntryCode {
  id: string
  code: string
  tenant_id: string
  role: string
  label: string | null
  is_active: boolean
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface CreateEntryCodeRequest {
  code: string
  tenant_id: string
  role: string
  label?: string | null
}

export interface UpdateEntryCodeRequest {
  code?: string
  role?: string
  label?: string | null
  is_active?: boolean
}

export const entryCodesApi = {
  list() {
    return api.get<EntryCode[]>('/admin/entry-codes')
  },
  create(body: CreateEntryCodeRequest) {
    return api.post<EntryCode>('/admin/entry-codes', body)
  },
  update(id: string, body: UpdateEntryCodeRequest) {
    return api.patch<EntryCode>(`/admin/entry-codes/${id}`, body)
  },
  remove(id: string) {
    return api.delete(`/admin/entry-codes/${id}`)
  },
}
