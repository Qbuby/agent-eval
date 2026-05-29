import api from './client'
import type {
  CreateEvaluatorProviderRequest,
  EvaluatorProvider,
  ProviderModelsResponse,
  TestProviderResponse,
  UpdateEvaluatorProviderRequest,
} from '@/types'

export const evaluatorProvidersApi = {
  list(activeOnly?: boolean) {
    return api.get<EvaluatorProvider[]>('/evaluator-providers', {
      params: activeOnly ? { active_only: true } : {},
    })
  },
  create(data: CreateEvaluatorProviderRequest) {
    return api.post<EvaluatorProvider>('/evaluator-providers', data)
  },
  get(id: string) {
    return api.get<EvaluatorProvider>(`/evaluator-providers/${id}`)
  },
  update(id: string, data: UpdateEvaluatorProviderRequest) {
    return api.put<EvaluatorProvider>(`/evaluator-providers/${id}`, data)
  },
  remove(id: string) {
    return api.delete<{ id: string; deleted: boolean }>(`/evaluator-providers/${id}`)
  },
  test(id: string) {
    return api.post<TestProviderResponse>(`/evaluator-providers/${id}/test`)
  },
  listModels(id: string) {
    return api.get<ProviderModelsResponse>(`/evaluator-providers/${id}/models`)
  },
}
