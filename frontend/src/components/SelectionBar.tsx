import { Button } from '@/components/ui'
import { PAGE_SIZE_OPTIONS } from '@/lib/batchSelection'

interface SelectionBarProps {
  /** 已选中的样例总数（跨页累积） */
  selectedCount: number
  /** 当前筛选条件下的样例总数 */
  total: number
  /** 当页行数，用于提示「本页 x / y」 */
  pageCount: number
  /** 当页已选中的行数 */
  pageSelectedCount: number
  /** 「选择全部 N 条」：按当前筛选拉全量 id 并并入选中集合 */
  onSelectAll: () => void
  /** 「清空选择」 */
  onClear: () => void
  /** 跨页全选进行中（正在逐页拉 id） */
  selectingAll?: boolean
  /** 每页条数 */
  pageSize: number
  onPageSizeChange: (size: number) => void
}

/**
 * 列表页批量选择的状态条。
 *
 * 表头 checkbox 只管当页，跨页累积的总数和「选择全部」放这里，
 * 让用户能一眼看出「我现在到底选了多少条」而不是只看到当页 20 个。
 */
export function SelectionBar({
  selectedCount,
  total,
  pageCount,
  pageSelectedCount,
  onSelectAll,
  onClear,
  selectingAll = false,
  pageSize,
  onPageSizeChange,
}: SelectionBarProps) {
  const allSelected = total > 0 && selectedCount >= total

  return (
    <div className="flex items-center justify-between gap-3 mb-2 text-[12px] text-text-secondary">
      <div className="flex items-center gap-2 min-w-0">
        {selectedCount > 0 ? (
          <>
            <span className="text-text-primary font-medium">已选 {selectedCount} 条</span>
            <span className="text-text-tertiary">
              （本页 {pageSelectedCount} / {pageCount}，选择可跨页累积）
            </span>
            {!allSelected && total > pageCount && (
              <Button variant="plain" size="sm" onClick={onSelectAll} loading={selectingAll}>
                选择全部 {total} 条
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={onClear}>
              清空选择
            </Button>
          </>
        ) : (
          <>
            <span className="text-text-tertiary">未选择样例</span>
            {total > 0 && (
              <Button variant="plain" size="sm" onClick={onSelectAll} loading={selectingAll}>
                选择全部 {total} 条
              </Button>
            )}
          </>
        )}
      </div>
      <label className="flex items-center gap-1.5 shrink-0 text-text-tertiary">
        每页
        <select
          value={pageSize}
          onChange={e => onPageSizeChange(Number(e.target.value))}
          className="input h-7 py-0 text-[12px] w-[64px]"
          aria-label="每页条数"
        >
          {PAGE_SIZE_OPTIONS.map(n => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
        条
      </label>
    </div>
  )
}
