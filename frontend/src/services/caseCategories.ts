import api from './client'
import type { ReplyDatasetType } from './agentReplies'

// 样例类别的批量修改。三类数据集的类别落在三处不同存储上（备选集自由文本
// category、基准集 category_id 外键、多轮对话集 Langfuse metadata.category），
// 后端 /api/case-categories 用同一套「干跑预览 + 执行」把差异吃掉，前端这里
// 只按 dataset_type 决定往 target 里塞 category_id 还是 category_name。
// 样例引用与 agentReplies 一致：candidate / benchmark 是本地表主键，
// conversation 是 Langfuse dataset item id。

export type CategoryTargetMode = 'set' | 'clear'

export interface CategoryTarget {
  mode: CategoryTargetMode
  /** 仅基准集用（类别是 project 下的外键行）。 */
  category_id?: string | null
  /** 备选集（自由文本，可为新名）与多轮对话集（须是已有受管类别名）用。 */
  category_name?: string | null
}

export interface BatchCategoryRequest {
  dataset_type: ReplyDatasetType
  case_refs: string[]
  /** 多轮对话集必填；备选集给了则把可选类别收窄到该数据集。 */
  dataset_name?: string | null
  target: CategoryTarget
}

export interface BatchCategoryItem {
  case_ref: string
  matched: boolean
  already_current: boolean
  current_category: string | null
  target_category: string | null
  reason: string | null
}

/** 一个类别及其在本批勾选样例里的当前条数。value_id 只有基准集有值。 */
export interface BatchCategoryOption {
  value: string
  case_count: number
  value_id: string | null
}

export interface BatchCategoryResolveResult {
  total: number
  matched_count: number
  changed_count: number
  unchanged_count: number
  missing_count: number
  items: BatchCategoryItem[]
  current_distribution: BatchCategoryOption[]
  category_options: BatchCategoryOption[]
}

export interface BatchCategorySetResult {
  total: number
  changed_count: number
  unchanged_count: number
  missing_count: number
  failed_count: number
  items: BatchCategoryItem[]
}

export const caseCategoriesApi = {
  /** 干跑预览：每条会从什么改成什么、谁改不了为什么，并回可选类别与当前分布。 */
  batchResolve: (data: BatchCategoryRequest) =>
    api.post<BatchCategoryResolveResult>('/case-categories/batch-resolve', data),

  /** 执行：部分成功即提交，改不了的原样留下并带 reason。 */
  batchSet: (data: BatchCategoryRequest) =>
    api.post<BatchCategorySetResult>('/case-categories/batch-set', data),
}
