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
    expected_answers: number
    goal: string
    action: 'new' | 'update'
  }[]
}

// 语义字段 → 源列名的映射。拍平多行布局（同 session_id 多行=多轮）下，用户可
// 手动指定每个字段对应哪一列，覆盖别名自动识别。所有字段可选，缺失回退别名。
export interface ConversationColumnMap {
  question?: string        // 用户问句列（必需，缺失该行跳过）
  answer?: string          // 助手回复列 → assistant 消息（存档实际回复）
  expected_output?: string // 期望答案/标准答案列 → 该轮 expected_output
  criteria?: string        // 评分点/检查点列 → 该轮 criteria
  conversation_id?: string // 会话聚合键（如 session_id）
  turn_no?: string         // 轮次序号列（排序用）
  goal?: string            // 对话目标列
  name?: string            // 对话名列
}

// 导入前的文件结构自省：列头 + 每列样例值 + 自动建议映射。
export interface ConversationInspectResult {
  columns: string[]
  samples: Record<string, string[]>
  suggested: ConversationColumnMap
  is_structured: boolean  // true=行内已带对话数组（布局A/B），无需列映射
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
  // 三步式导入第一步：自省文件列结构，返回列头/样例/建议映射（不写库）。
  inspectConversationFile(name: string, file: File) {
    const form = new FormData()
    form.append('file', file)
    return api.post<ConversationInspectResult>(
      `/datasets/${name}/cases/import-conversations/inspect`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
  },
  importConversations(
    name: string,
    file: File,
    opts?: {
      split?: string; messagesColumn?: string; goalColumn?: string;
      category?: string; columnMap?: ConversationColumnMap
    },
  ) {
    const form = new FormData()
    form.append('file', file)
    const params: Record<string, string> = {}
    if (opts?.split) params.split = opts.split
    if (opts?.messagesColumn) params.messages_column = opts.messagesColumn
    if (opts?.goalColumn) params.goal_column = opts.goalColumn
    if (opts?.category) params.category = opts.category
    if (opts?.columnMap && Object.keys(opts.columnMap).length > 0)
      params.column_map = JSON.stringify(opts.columnMap)
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
    opts?: {
      messagesColumn?: string; goalColumn?: string;
      category?: string; columnMap?: ConversationColumnMap
    },
  ) {
    const form = new FormData()
    form.append('file', file)
    const params: Record<string, string> = {}
    if (opts?.messagesColumn) params.messages_column = opts.messagesColumn
    if (opts?.goalColumn) params.goal_column = opts.goalColumn
    if (opts?.category) params.category = opts.category
    if (opts?.columnMap && Object.keys(opts.columnMap).length > 0)
      params.column_map = JSON.stringify(opts.columnMap)
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
