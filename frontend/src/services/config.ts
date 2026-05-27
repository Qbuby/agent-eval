import api from './client'
import type {
  AddConfigOptionRequest,
  ConfigItem,
  ConfigUpdateRequest,
  UpdateConfigOptionRequest,
} from '@/types'

export const configApi = {
  list(category?: string) {
    return api.get<ConfigItem[]>('/config', { params: category ? { category } : undefined })
  },
  get(key: string) {
    return api.get<ConfigItem>(`/config/${key}`)
  },
  // Single-value replace (collapses options to a single entry)
  update(key: string, data: ConfigUpdateRequest) {
    return api.put<ConfigItem>(`/config/${key}`, data)
  },
  delete(key: string) {
    return api.delete(`/config/${key}`)
  },
  batchUpdate(items: Record<string, unknown>) {
    return api.post<ConfigItem[]>('/config/batch', { items })
  },

  // ─── Multi-value option management ───
  addOption(key: string, data: AddConfigOptionRequest) {
    return api.post<ConfigItem>(`/config/options/${key}`, data)
  },
  updateOption(key: string, index: number, data: UpdateConfigOptionRequest) {
    return api.put<ConfigItem>(`/config/options/${index}/${key}`, data)
  },
  removeOption(key: string, index: number) {
    return api.delete<ConfigItem>(`/config/options/${index}/${key}`)
  },
  setDefault(key: string, index: number) {
    return api.put<ConfigItem>(`/config/default/${index}/${key}`)
  },
}
