import api from './client'
import type { AuditLogList } from '@/types'

export const governanceApi = {
  queryAuditLogs(params?: {
    entity_type?: string
    entity_id?: string
    action?: string
    since?: string
    limit?: number
    offset?: number
  }) {
    return api.get<AuditLogList>('/audit', { params })
  },
}
