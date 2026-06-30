import api from './client'
import { triggerExport, type ExportFormat } from '@/lib/download'
import type {
  Dataset,
  CreateDatasetRequest,
  DatasetStats,
  DatasetVersion,
  TestCase,
  AddCasesRequest,
  DuplicateInfo,
  QualityReport,
  CapacityInfo,
} from '@/types'

// 多轮对话两步式导入预览：解析结果 + 与现有同名样例的新增/更新比对。
export interface ConversationImportPreview {
  total: number
  new: number
  updated: number
  skipped: number
  samples: {
    name: string
    turns: number
    first_user: string
    has_assistant: boolean
    checkpoints: number
    goal: string
    action: 'new' | 'update'
  }[]
}

export const datasetsApi = {
  list(params?: { filter?: string; type?: string }) {
    return api.get<Dataset[]>('/datasets', { params })
  },
  get(name: string) {
    return api.get<Dataset>(`/datasets/${name}`)
  },
  create(data: CreateDatasetRequest) {
    return api.post<{ id: string; name: string }>('/datasets', data)
  },
  delete(name: string) {
    return api.delete(`/datasets/${name}`)
  },
  getStats(name: string, params?: { split?: string; tag?: string[] }) {
    return api.get<DatasetStats>(`/datasets/${name}/stats`, { params })
  },
  export(name: string, params?: { split?: string; tag?: string[]; as_of?: string }) {
    return api.get(`/datasets/${name}/export`, { params })
  },
  listVersions(name: string) {
    return api.get<DatasetVersion[]>(`/datasets/${name}/versions`)
  },
  listCases(name: string, params?: { split?: string; tag?: string[]; as_of?: string; limit?: number }) {
    return api.get<TestCase[]>(`/datasets/${name}/cases`, { params })
  },
  listCasesPaginated(name: string, params?: { page?: number; page_size?: number; search?: string; tag?: string; category?: string }) {
    return api.get<{ items: TestCase[]; total: number; page: number; page_size: number }>(`/datasets/${name}/cases`, { params })
  },
  // ── 多轮对话集的受管类别（对齐基准测试集的类别 CRUD）──
  // 后端：list/create 带 dataset name（作用域）；update/delete 用全局 category_id。
  listConvCategories(name: string) {
    return api.get<{ id: string; name: string; description: string | null; created_at: string }[]>(
      `/datasets/${name}/categories`,
    )
  },
  createCategory(name: string, data: { name: string; description?: string }) {
    return api.post<{ id: string; name: string }>(`/datasets/${name}/categories`, data)
  },
  updateCategory(categoryId: string, data: { name?: string; description?: string }) {
    return api.put<{ id: string; name: string; synced_cases: number }>(
      `/datasets/categories/${categoryId}`,
      data,
    )
  },
  deleteCategory(categoryId: string) {
    return api.delete(`/datasets/categories/${categoryId}`)
  },
  addCases(name: string, data: AddCasesRequest) {
    return api.post<{ added: number; ids: string[] }>(`/datasets/${name}/cases`, data)
  },
  importConversations(
    name: string,
    file: File,
    opts?: { split?: string; messagesColumn?: string; goalColumn?: string; category?: string },
  ) {
    const form = new FormData()
    form.append('file', file)
    const params: Record<string, string> = {}
    if (opts?.split) params.split = opts.split
    if (opts?.messagesColumn) params.messages_column = opts.messagesColumn
    if (opts?.goalColumn) params.goal_column = opts.goalColumn
    if (opts?.category) params.category = opts.category
    return api.post<{ added: number; updated: number; skipped: number; ids: string[] }>(
      `/datasets/${name}/cases/import-conversations`,
      form,
      { params, headers: { 'Content-Type': 'multipart/form-data' } },
    )
  },
  // 两步式导入第一步：解析文件但不写库，返回解析结果预览 + 新增/更新比对。
  previewConversations(
    name: string,
    file: File,
    opts?: { messagesColumn?: string; goalColumn?: string; category?: string },
  ) {
    const form = new FormData()
    form.append('file', file)
    const params: Record<string, string> = {}
    if (opts?.messagesColumn) params.messages_column = opts.messagesColumn
    if (opts?.goalColumn) params.goal_column = opts.goalColumn
    if (opts?.category) params.category = opts.category
    return api.post<ConversationImportPreview>(
      `/datasets/${name}/cases/import-conversations/preview`,
      form,
      { params, headers: { 'Content-Type': 'multipart/form-data' } },
    )
  },
  exportConversations(name: string, format: ExportFormat) {
    return triggerExport({
      url: `/datasets/${name}/cases/export-conversations`,
      params: { format },
      format,
      fallbackName: `conversations_${name.slice(0, 20)}`,
    })
  },
  updateCase(exampleId: string, data: TestCase) {
    return api.put(`/cases/${exampleId}`, data)
  },
  deleteCase(exampleId: string) {
    return api.delete(`/cases/${exampleId}`)
  },
  batchDeleteCases(exampleIds: string[]) {
    return api.post('/cases/batch-delete', { example_ids: exampleIds })
  },
  getDuplicates(name: string) {
    return api.get<DuplicateInfo[]>(`/datasets/${name}/duplicates`)
  },
  deduplicate(name: string, strategy?: string) {
    return api.post(`/datasets/${name}/deduplicate`, strategy ? { strategy } : undefined)
  },
  getQuality(name: string) {
    return api.get<QualityReport>(`/datasets/${name}/quality`)
  },
  getCapacity(name: string) {
    return api.get<CapacityInfo>(`/datasets/${name}/capacity`)
  },
  archive(name: string) {
    return api.post(`/datasets/${name}/archive`)
  },
  activate(name: string) {
    return api.post(`/datasets/${name}/activate`)
  },
}
