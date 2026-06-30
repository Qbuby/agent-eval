import api from './client'
import type { RequestLogResponse } from '@/types'

export const adminApi = {
  requestLog(params: { limit?: number; status_min?: number; path_prefix?: string } = {}) {
    return api.get<RequestLogResponse>('/admin/request-log', { params })
  },
}
