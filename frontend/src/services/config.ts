import api from './client'
import type { ConfigItem, ConfigUpdateRequest } from '@/types'

export const configApi = {
  list(category?: string) {
    return api.get<ConfigItem[]>('/config', { params: category ? { category } : undefined })
  },
  get(key: string) {
    return api.get<ConfigItem>(`/config/${key}`)
  },
  update(key: string, data: ConfigUpdateRequest) {
    return api.put<ConfigItem>(`/config/${key}`, data)
  },
  delete(key: string) {
    return api.delete(`/config/${key}`)
  },
  batchUpdate(items: Record<string, unknown>) {
    return api.post<ConfigItem[]>('/config/batch', { items })
  },
}
