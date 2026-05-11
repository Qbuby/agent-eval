import api from './client'
import type {
  ListRunsRequest,
  PaginatedRuns,
  ExtractRequest,
  ImportTracesRequest,
  PullDatasetRequest,
  RunDetail,
  RunDetailRequest,
  FillModelsRequest,
  FillModelsResponse,
} from '@/types'

export const tracesApi = {
  listRuns(data: ListRunsRequest) {
    return api.post<PaginatedRuns>('/traces/runs', data)
  },
  extract(data: ExtractRequest) {
    return api.post<{ extracted: number; cases: unknown[] }>('/traces/extract', data)
  },
  import(data: ImportTracesRequest) {
    return api.post<{ imported: number; ids: string[] }>('/traces/import', data)
  },
  pull(data: PullDatasetRequest) {
    return api.post<{ pulled: number; saved_to: string; cases: unknown[] }>('/traces/pull', data)
  },
  getDetail(data: RunDetailRequest) {
    return api.post<RunDetail>('/traces/run_detail', data)
  },
  fillModels(data: FillModelsRequest) {
    return api.post<FillModelsResponse>('/traces/fill_models', data)
  },
}
