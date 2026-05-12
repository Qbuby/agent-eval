import api from './client'
import type {
  RoutingRule,
  CreateRuleRequest,
  PaginatedRoutingLogs,
  RoutingStats,
} from '@/types'

export const routingApi = {
  listRules() {
    return api.get<RoutingRule[]>('/routing/rules')
  },
  createRule(data: CreateRuleRequest) {
    return api.post<RoutingRule>('/routing/rules', data)
  },
  updateRule(ruleId: string, data: Partial<CreateRuleRequest>) {
    return api.put<RoutingRule>(`/routing/rules/${ruleId}`, data)
  },
  deleteRule(ruleId: string) {
    return api.delete(`/routing/rules/${ruleId}`)
  },
  testRule(ruleId: string, data: { run: Record<string, unknown>; project_name: string }) {
    return api.post<{ matched: boolean; rule_id: string; target_dataset: string | null }>(
      `/routing/rules/${ruleId}/test`,
      data,
    )
  },
  listLogs(params?: { source_project?: string; target_dataset?: string; status?: string; limit?: number; offset?: number }) {
    return api.get<PaginatedRoutingLogs>('/routing/logs', { params })
  },
  getStats() {
    return api.get<RoutingStats[]>('/routing/stats')
  },
}
