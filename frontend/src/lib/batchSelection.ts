/**
 * 列表页批量选择的纯逻辑。
 *
 * 选中态语义是「当前筛选结果里的 id 集合」，而不是「当前页的勾选」。
 * 表头全选只对当页 id 做并集/差集，翻页后再点全选不会丢掉之前页的选择
 * （旧实现是 `setSelectedIds(new Set(当页 id))`，直接把整个集合替换掉了）。
 */

/** 列表页可选的每页条数。后端 `page_size` 上限是 100，故最大给到 100。 */
export const PAGE_SIZE_OPTIONS = [20, 50, 100] as const

export interface PageSelectionState {
  /** 当页所有行都已选中 */
  all: boolean
  /** 当页只选中了一部分（表头 checkbox 走半选态） */
  some: boolean
  /** 当页已选中的行数 */
  count: number
}

/** 当页的勾选状态，用于表头 checkbox 的 checked / indeterminate。 */
export function pageSelectionState(selected: Set<string>, idsOnPage: string[]): PageSelectionState {
  let count = 0
  for (const id of idsOnPage) if (selected.has(id)) count++
  return {
    all: idsOnPage.length > 0 && count === idsOnPage.length,
    some: count > 0 && count < idsOnPage.length,
    count,
  }
}

/**
 * 表头全选/取消全选：当页全选中则只移除当页 id，否则把当页 id 并入。
 * 两个分支都保留其它页已选的 id。
 */
export function togglePageIds(selected: Set<string>, idsOnPage: string[]): Set<string> {
  if (idsOnPage.length === 0) return selected
  const next = new Set(selected)
  const allSelected = idsOnPage.every(id => next.has(id))
  if (allSelected) for (const id of idsOnPage) next.delete(id)
  else for (const id of idsOnPage) next.add(id)
  return next
}

/** 把一批 id 并入选中集合。 */
export function addIds(selected: Set<string>, ids: string[]): Set<string> {
  const next = new Set(selected)
  for (const id of ids) next.add(id)
  return next
}

/**
 * 按当前筛选条件逐页拉取，收集全部 id（用于「选择全部 N 条」）。
 *
 * 单页固定用 100（后端上限），走完 `total` 条或遇到空页为止；
 * `maxPages` 是兜底，避免后端 total 不准时无限循环。
 *
 * 判停用的是服务端返回的条数而非收集到的 id 数：多轮对话页会在前端再按类型
 * 过滤一遍，`ids` 比该页实际条数少，此时要靠 `rawCount` 才能算准翻到哪页。
 */
export async function collectAllIds(
  fetchPage: (
    page: number,
    pageSize: number,
  ) => Promise<{ ids: string[]; total: number; rawCount?: number }>,
  opts?: { pageSize?: number; maxPages?: number },
): Promise<string[]> {
  const pageSize = opts?.pageSize ?? 100
  const maxPages = opts?.maxPages ?? 200
  const collected: string[] = []
  let seen = 0
  for (let page = 1; page <= maxPages; page++) {
    const { ids, total, rawCount } = await fetchPage(page, pageSize)
    const fetched = rawCount ?? ids.length
    collected.push(...ids)
    seen += fetched
    if (fetched === 0 || seen >= total) break
  }
  return collected
}
