import api from './client'
import { triggerExport, type ExportFormat } from '@/lib/download'
import type {
  ListRunsRequest,
  PaginatedRuns,
  RunSummary,
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
  // Export the rows currently loaded in the list (already filtered / sorted
  // on the client). Sends those rows to the backend serializer — it does not
  // re-query LangSmith, so the file mirrors exactly what's on screen.
  exportRuns(rows: RunSummary[], format: ExportFormat) {
    return triggerExport({
      method: 'post',
      url: '/traces/runs/export',
      data: { rows, format },
      format,
      fallbackName: 'traces_runs',
    })
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
