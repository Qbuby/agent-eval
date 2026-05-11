import api from './client'
import type { GenerateScenarioRequest, GenerateMutateRequest } from '@/types'

export const generateApi = {
  scenario(data: GenerateScenarioRequest) {
    return api.post<{ generated: number; saved: boolean; cases: unknown[] }>('/generate/scenario', data)
  },
  mutate(data: GenerateMutateRequest) {
    return api.post<{ generated: number; saved: boolean; cases: unknown[] }>('/generate/mutate', data)
  },
}
