import api from './client'
import { triggerExport, type ExportFormat } from '@/lib/download'

export interface Project {
  id: string
  name: string
  description: string
  created_at: string
}

export interface Category {
  id: string
  name: string
  description: string
  created_at: string
}

export interface BenchmarkCase {
  id: string
  project_id: string
  category_id: string | null
  question: string
  reference_answer: string | null
  key_points: string[]
  negative_points: string[]
  tags: string[]
  difficulty: string | null
  extra_fields: Record<string, any> | null
  source: string
  status: string
  created_at: string
  updated_at: string
}

export interface SchemaColumn {
  name: string
  type: string
  required?: boolean
  description?: string
  value?: string
  source?: string
}

export interface CategorySchema {
  id: string
  name: string
  schema_config: {
    id_prefix?: string
    id_digits?: number
    columns?: SchemaColumn[]
  } | null
}

export interface CandidateCase {
  id: string
  project_id: string | null
  category: string | null
  source: string
  question: string
  answer: string | null
  key_points: string[] | null
  negative_points: string[] | null
  tags: string[]
  langsmith_example_id: string | null
  status: string
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface ImportPreview {
  file: string
  total_rows: number
  source_headers: string[]
  field_mapping: Record<string, string>
  suggested_mapping: { question: string | null; reference_answer: string | null }
  sample_values: Record<string, string[]>
  schema_columns: SchemaColumn[]
  preview: { question: string | null; reference_answer: string | null; extra_fields: Record<string, unknown> | null; has_answer: boolean }[]
}

export interface ImportResult {
  file: string
  total: number
  imported_to_benchmark: number
  pending_in_staging: number
  skipped: number
  duplicates: number
  field_mapping: Record<string, string> | null
}

export const projectsApi = {
  list() {
    return api.get<Project[]>('/projects')
  },
  create(data: { name: string; description?: string }) {
    return api.post<{ id: string; name: string }>('/projects', data)
  },
  getCategories(projectId: string) {
    return api.get<Category[]>(`/projects/${projectId}/categories`)
  },
  createCategory(projectId: string, data: { name: string; description?: string }) {
    return api.post<{ id: string; name: string }>(`/projects/${projectId}/categories`, data)
  },
  updateCategory(categoryId: string, data: { name?: string; description?: string }) {
    return api.put<{ id: string; name: string }>(`/projects/categories/${categoryId}`, data)
  },
  deleteCategory(categoryId: string) {
    return api.delete(`/projects/categories/${categoryId}`)
  },
}

export const benchmarkApi = {
  listCases(projectId: string, params?: { category_id?: string; tag?: string; search?: string; status?: string; page?: number; page_size?: number }) {
    return api.get<PaginatedResponse<BenchmarkCase>>(`/benchmark/${projectId}/cases`, { params })
  },
  createCase(projectId: string, data: Partial<BenchmarkCase>) {
    return api.post<{ id: string }>(`/benchmark/${projectId}/cases`, data)
  },
  updateCase(caseId: string, data: Partial<BenchmarkCase>) {
    return api.put(`/benchmark/cases/${caseId}`, data)
  },
  deleteCase(caseId: string) {
    return api.delete(`/benchmark/cases/${caseId}`)
  },
  importPreview(projectId: string, file: File, opts?: { categoryId?: string; questionColumn?: string; answerColumn?: string }) {
    const formData = new FormData()
    formData.append('file', file)
    const params: Record<string, string | number> = { max_rows: 5 }
    if (opts?.categoryId) params.category_id = opts.categoryId
    if (opts?.questionColumn) params.question_column = opts.questionColumn
    if (opts?.answerColumn) params.answer_column = opts.answerColumn
    return api.post<ImportPreview>(
      `/benchmark/${projectId}/import/preview`, formData,
      { headers: { 'Content-Type': 'multipart/form-data' }, params },
    )
  },
  importFile(
    projectId: string,
    file: File,
    opts?: { categoryId?: string; questionColumn?: string; answerColumn?: string },
  ) {
    const formData = new FormData()
    formData.append('file', file)
    const params: Record<string, string> = {}
    if (opts?.categoryId) params.category_id = opts.categoryId
    if (opts?.questionColumn) params.question_column = opts.questionColumn
    if (opts?.answerColumn) params.answer_column = opts.answerColumn
    return api.post<ImportResult>(
      `/benchmark/${projectId}/import`, formData,
      { headers: { 'Content-Type': 'multipart/form-data' }, params: Object.keys(params).length ? params : undefined }
    )
  },
  export(projectId: string, categoryId?: string) {
    return api.get(`/benchmark/${projectId}/export`, { params: categoryId ? { category_id: categoryId } : undefined })
  },
  exportCases(
    projectId: string,
    params: { category_id?: string; tag?: string; search?: string; status?: string },
    format: ExportFormat,
  ) {
    return triggerExport({
      url: `/benchmark/${projectId}/cases/export`,
      params: { ...params, format },
      format,
      fallbackName: `benchmark_${projectId.slice(0, 8)}_cases`,
    })
  },
  listVersions(projectId: string) {
    return api.get(`/benchmark/${projectId}/versions`)
  },
  createVersion(projectId: string, data: { version_tag: string; description?: string }) {
    return api.post(`/benchmark/${projectId}/versions`, data)
  },
  getCategorySchema(categoryId: string) {
    return api.get<CategorySchema>(`/benchmark/categories/${categoryId}/schema`)
  },
  updateCategorySchema(categoryId: string, schema_config: Record<string, any>) {
    return api.put(`/benchmark/categories/${categoryId}/schema`, { schema_config })
  },
}

export const candidatesApi = {
  list(params?: { status?: string; project_id?: string; dataset_name?: string; source?: string; category?: string; search?: string; page?: number; page_size?: number }) {
    return api.get<PaginatedResponse<CandidateCase>>('/candidates', { params })
  },
  exportCases(
    params: { status?: string; project_id?: string; dataset_name?: string; source?: string; search?: string },
    format: ExportFormat,
  ) {
    return triggerExport({
      url: '/candidates/export',
      params: { ...params, format },
      format,
      fallbackName: 'candidates',
    })
  },
  create(data: { question: string; answer?: string; project_id?: string; dataset_name?: string; category?: string; tags?: string[]; source?: string }) {
    return api.post<{ id: string; status: string }>('/candidates', data)
  },
  // 批量创建备选样例（生成页在 candidate 数据集上「确认添加」走这里，落 candidate_cases）。
  batchCreate(data: {
    dataset_name: string
    cases: { question: string; answer?: string; category?: string; tags?: string[]; source?: string }[]
  }) {
    return api.post<{ added: number; ids: string[] }>('/candidates/batch', data)
  },
  categories(params?: { dataset_name?: string; project_id?: string }) {
    return api.get<{ categories: string[] }>('/candidates/categories', { params })
  },
  update(caseId: string, data: Partial<CandidateCase>) {
    return api.put<{ updated: string; status: string }>(`/candidates/${caseId}`, data)
  },
  delete(caseId: string) {
    return api.delete<{ deleted: string }>(`/candidates/${caseId}`)
  },
  batchReview(ids: string[], action: 'approve' | 'reject') {
    return api.post('/candidates/batch-review', { ids, action })
  },
  promote(ids: string[], projectId: string, categoryId?: string) {
    return api.post('/candidates/promote', { ids, project_id: projectId, category_id: categoryId })
  },
  importFromLangSmith(data: { dataset_name: string; project_id?: string; limit?: number }) {
    return api.post<{ imported: number; dataset: string }>('/candidates/import-langsmith', data)
  },
  importFromTraces(data: { project_name: string; run_ids: string[]; target_project_id?: string }) {
    return api.post<{ imported: number }>('/candidates/import-traces', data)
  },
  importPreview(file: File, opts?: { questionColumn?: string; answerColumn?: string }) {
    const formData = new FormData()
    formData.append('file', file)
    const params: Record<string, string | number> = { max_rows: 5 }
    if (opts?.questionColumn) params.question_column = opts.questionColumn
    if (opts?.answerColumn) params.answer_column = opts.answerColumn
    return api.post<ImportPreview>(
      '/candidates/import/preview', formData,
      { headers: { 'Content-Type': 'multipart/form-data' }, params },
    )
  },
  importFile(
    file: File,
    opts?: { datasetName?: string; projectId?: string; category?: string; questionColumn?: string; answerColumn?: string },
  ) {
    const formData = new FormData()
    formData.append('file', file)
    const params: Record<string, string> = {}
    if (opts?.datasetName) params.dataset_name = opts.datasetName
    if (opts?.projectId) params.project_id = opts.projectId
    if (opts?.category) params.category = opts.category
    if (opts?.questionColumn) params.question_column = opts.questionColumn
    if (opts?.answerColumn) params.answer_column = opts.answerColumn
    return api.post<ImportResult>(
      '/candidates/import', formData,
      { headers: { 'Content-Type': 'multipart/form-data' }, params: Object.keys(params).length ? params : undefined },
    )
  },
}
