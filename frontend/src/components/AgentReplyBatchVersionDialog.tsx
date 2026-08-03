/**
 * 「批量切换当前版本」弹窗。
 *
 * 四个数据集页和评估页共用：调用方传 datasetType + 勾选的样例 case_ref 列表。
 *
 * 为什么不是「选一个 version_id 应用到所有样例」：version_id 是
 * per-(dataset_type, case_ref) 的，A 样例的 v2 和 B 样例的 v2 是两条不同的版本
 * 行，跨样例不存在同一个 id。所以批量只能按跨样例可识别的标识来指定：
 *   最新 / 精确版本号 vN / 版本备注完全相同的那条。
 *
 * 先干跑预览（batchResolve）再执行（batchSetCurrent），两次走后端同一套解析逻辑，
 * 用户看到的和实际切的一致。不是原子语义：能切的先切，切不了的原样不动并列出原因。
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useToast } from '@/components/ui'
import {
  agentRepliesApi,
  type BatchVersionMode,
  type ReplyDatasetType,
} from '@/services/agentReplies'
import { formatApiError, toToastMessage } from '@/lib/errors'

/**
 * 弹窗把「批量意图」解析成每个样例各自的一条版本后，有两种落地方式：
 *   set_current  —— 写库，挪动每个样例的当前版本指针（四个数据集页）
 *   select_only  —— 不写库，只把解析结果交回调用方（评估页的本次评估版本覆盖）
 * 解析逻辑两者完全一致，区别只在最后一步。
 */
export type BatchVersionApplyMode = 'set_current' | 'select_only'

export interface BatchVersionDialogProps {
  open: boolean
  onClose: () => void
  datasetType: ReplyDatasetType
  /** 勾选的样例 id（candidate/benchmark = 本地主键；conversation = dataset item id） */
  caseRefs: string[]
  /** 切换成功后回调，调用方据此刷新列表的版本标记 */
  onDone?: () => void
  /** 默认 set_current（写库）。评估页传 select_only，一行都不写库。 */
  applyMode?: BatchVersionApplyMode
  /** select_only 下回传 case_ref -> version_id；set_current 下不会被调用 */
  onResolved?: (versionIds: Record<string, string>) => void
}

export default function AgentReplyBatchVersionDialog({
  open,
  onClose,
  datasetType,
  caseRefs,
  onDone,
  applyMode = 'set_current',
  onResolved,
}: BatchVersionDialogProps) {
  const toast = useToast()
  const qc = useQueryClient()

  const [mode, setMode] = useState<BatchVersionMode>('latest')
  const [versionNumber, setVersionNumber] = useState<number | null>(null)
  const [label, setLabel] = useState('')
  const [showDetail, setShowDetail] = useState(false)
  // 切换模式后「等选项到了再默认选中命中最多的那个」的待办标记，见下方 effect。
  const [autoPick, setAutoPick] = useState(false)

  // 关闭时回到默认选择，下次打开不带上次的残留。
  useEffect(() => {
    if (!open) {
      setMode('latest')
      setVersionNumber(null)
      setLabel('')
      setShowDetail(false)
      setAutoPick(false)
    }
  }, [open])

  // 选了「按版本号 / 按版本备注」但还没选具体值时不请求（后端会 400）。
  const selectorReady =
    mode === 'latest' ||
    (mode === 'version_number' && versionNumber !== null) ||
    (mode === 'label' && label.trim() !== '')

  const refsKey = caseRefs.join(',')

  const resolve = (selector: {
    mode: BatchVersionMode
    version_number: number | null
    label: string | null
  }) =>
    agentRepliesApi
      .batchResolve({ dataset_type: datasetType, case_refs: caseRefs, selector })
      .then(r => r.data)

  // 下拉选项单独取，不能搭在预览那个 query 上：预览在「选了按版本号但还没选具体版本号」
  // 时是禁用的（后端会 400），选项若跟着一起禁用就永远补不回来 —— 下拉退化成手填框，
  // 用户没有候选可填，请求也就一直不发，卡死。所以选项固定按 latest 拉，与 mode 无关。
  // queryKey 与 mode==='latest' 时的预览完全一致，两者会被合并成同一次请求。
  const optionsQuery = useQuery({
    queryKey: ['agent-reply-batch-resolve', datasetType, refsKey, 'latest', null, ''],
    queryFn: () => resolve({ mode: 'latest', version_number: null, label: null }),
    enabled: open && caseRefs.length > 0,
  })

  const previewQuery = useQuery({
    queryKey: [
      'agent-reply-batch-resolve',
      datasetType,
      refsKey,
      mode,
      versionNumber,
      label.trim(),
    ],
    queryFn: () =>
      resolve({
        mode,
        version_number: mode === 'version_number' ? versionNumber : null,
        label: mode === 'label' ? label.trim() : null,
      }),
    enabled: open && caseRefs.length > 0 && selectorReady,
  })

  const preview = previewQuery.data

  const labelOptions = optionsQuery.data?.label_options ?? []
  const numberOptions = optionsQuery.data?.version_number_options ?? []

  // 选项可能在切换模式之后才到（首次打开就立刻切「按版本号」）。到了再补上默认值，
  // 否则 versionNumber 一直是 null，预览请求不会发出去。
  useEffect(() => {
    if (!autoPick) return
    if (mode === 'version_number' && versionNumber === null) {
      const first = numberOptions[0]
      if (first) {
        setVersionNumber(Number(first.value.replace(/^v/, '')))
        setAutoPick(false)
      }
      return
    }
    if (mode === 'label' && !label.trim()) {
      const first = labelOptions[0]
      if (first) {
        setLabel(first.value)
        setAutoPick(false)
      }
      return
    }
    setAutoPick(false)
  }, [autoPick, mode, versionNumber, label, numberOptions, labelOptions])

  const blockedItems = useMemo(
    () => (preview?.items || []).filter(i => !i.matched),
    [preview],
  )

  // select_only 的产出。已经是当前版本的样例也一并回填：用户选的是一个明确意图，
  // 把 version_id 钉死能避免评估排队期间别人挪动当前指针，导致跑的不是刚才看到的。
  const resolvedMap = useMemo(() => {
    const out: Record<string, string> = {}
    for (const i of preview?.items || []) {
      if (i.matched && i.version_id) out[i.case_ref] = i.version_id
    }
    return out
  }, [preview])

  // set_current 只有「真正要变的」才值得点确认；select_only 下 already_current
  // 同样是一次有效的显式指定，所以门槛是 matched 而不是 changed。
  const actionableCount = applyMode === 'select_only'
    ? (preview?.matched_count ?? 0)
    : (preview?.changed_count ?? 0)

  const handleSelectOnly = () => {
    onResolved?.(resolvedMap)
    const parts = [`已为 ${Object.keys(resolvedMap).length} 条样例指定版本`]
    if (preview?.missing_count) parts.push(`${preview.missing_count} 条无匹配版本，仍用当前版本`)
    toast.success(parts.join('，'))
    onDone?.()
    onClose()
  }

  const applyMutation = useMutation({
    mutationFn: () =>
      agentRepliesApi
        .batchSetCurrent({
          dataset_type: datasetType,
          case_refs: caseRefs,
          selector: {
            mode,
            version_number: mode === 'version_number' ? versionNumber : null,
            label: mode === 'label' ? label.trim() : null,
          },
        })
        .then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['agent-reply-states'] })
      qc.invalidateQueries({ queryKey: ['agent-reply-versions'] })
      const parts = [`已切换 ${data.changed_count} 条`]
      if (data.unchanged_count) parts.push(`${data.unchanged_count} 条本来就是当前版本`)
      if (data.missing_count) parts.push(`${data.missing_count} 条无匹配版本`)
      if (data.failed_count) parts.push(`${data.failed_count} 条切换失败`)
      if (data.changed_count === 0 && data.missing_count === data.total) {
        toast.error(parts.join('，'))
      } else {
        toast.success(parts.join('，'))
      }
      onDone?.()
      onClose()
    },
    onError: (e) =>
      toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '批量切换失败' }))),
  })

  const modeHint =
    mode === 'latest'
      ? '每个样例各自切到自己版本链里最新的成功版本。'
      : mode === 'version_number'
        ? '每个样例各自切到自己的第 N 个版本（不同样例的 vN 是不同的回复）。'
        : '每个样例各自切到版本备注完全相同的那条；同一备注有多条时取最新。'

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={applyMode === 'select_only' ? '批量指定评估版本' : '批量切换当前版本'}
      description={
        applyMode === 'select_only'
          ? `对本次评估的 ${caseRefs.length} 条样例批量指定用哪个回复版本，不改数据库里的当前版本。`
          : `对勾选的 ${caseRefs.length} 条样例批量指定评估时使用哪个回复版本。`
      }
      width={640}
      footer={
        <div className="flex gap-2 justify-end">
          <Button variant="secondary" size="md" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="md"
            onClick={() => {
              if (applyMode === 'select_only') handleSelectOnly()
              else applyMutation.mutate()
            }}
            loading={applyMutation.isPending}
            disabled={!selectorReady || !preview || actionableCount === 0}
          >
            {applyMode === 'select_only'
              ? (actionableCount > 0 ? `确认指定 ${actionableCount} 条` : '确认指定')
              : (actionableCount > 0 ? `确认切换 ${actionableCount} 条` : '确认切换')}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-border bg-surface-secondary/40 p-2 text-[11px] text-text-tertiary">
          版本 id 是每个样例各自独立的，所以批量切换按「跨样例可识别的标识」指定，
          再由每个样例在自己的版本链里各自解析出一条。
          {applyMode === 'select_only'
            ? '结果只作用于本次评估，不会改动数据集里各样例的当前版本。'
            : null}
        </div>

        <label className="flex flex-col gap-0">
          <span className="field-label">按什么挑</span>
          <select
            value={mode}
            onChange={(e) => {
              const next = e.target.value as BatchVersionMode
              setMode(next)
              setShowDetail(false)
              // 默认选命中样例最多的那个，省一步操作。选项可能还没到（弹窗刚开就切模式），
              // 所以只置待办，由上面的 effect 在选项到位后补。
              if (next !== 'latest') setAutoPick(true)
            }}
            className="input"
          >
            <option value="latest">最新版本</option>
            <option value="version_number">指定版本号</option>
            <option value="label">指定版本备注</option>
          </select>
        </label>

        {mode === 'version_number' && (
          <label className="flex flex-col gap-0">
            <span className="field-label">版本号</span>
            {numberOptions.length > 0 ? (
              <select
                value={versionNumber ?? ''}
                onChange={e => setVersionNumber(Number(e.target.value) || null)}
                className="input"
              >
                <option value="">请选择</option>
                {numberOptions.map(o => (
                  <option key={o.value} value={o.value.replace(/^v/, '')}>
                    {o.value}（{o.case_count} 条样例有）
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="number"
                min={1}
                value={versionNumber ?? ''}
                onChange={e => setVersionNumber(Number(e.target.value) || null)}
                className="input"
                placeholder="如 2"
              />
            )}
          </label>
        )}

        {mode === 'label' && (
          <label className="flex flex-col gap-0">
            <span className="field-label">版本备注</span>
            {labelOptions.length > 0 ? (
              <select
                value={label}
                onChange={e => setLabel(e.target.value)}
                className="input"
              >
                <option value="">请选择</option>
                {labelOptions.map(o => (
                  <option key={o.value} value={o.value}>
                    {o.value}（{o.case_count} 条样例有）
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                value={label}
                onChange={e => setLabel(e.target.value)}
                className="input"
                placeholder="生成时填的版本号，如 v1-基线"
              />
            )}
          </label>
        )}

        <p className="text-[11px] text-text-tertiary">{modeHint}</p>

        {!selectorReady ? (
          <div className="rounded-md border border-border p-3 text-caption text-text-tertiary">
            选择具体的{mode === 'version_number' ? '版本号' : '版本备注'}后显示预览。
          </div>
        ) : previewQuery.isLoading ? (
          <div className="rounded-md border border-border p-3 text-caption text-text-tertiary">
            正在解析每个样例会切到哪个版本…
          </div>
        ) : previewQuery.isError ? (
          <div className="rounded-md border border-negative/40 bg-negative/5 p-2 text-[11px] text-negative">
            {toToastMessage(
              formatApiError(previewQuery.error, { fallbackMessage: '预览失败' }),
            )}
          </div>
        ) : preview ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3 text-caption">
              {applyMode === 'select_only' ? (
                <span className="badge badge-info">将指定 {preview.matched_count}</span>
              ) : (
                <>
                  <span className="badge badge-info">将切换 {preview.changed_count}</span>
                  {preview.unchanged_count > 0 && (
                    <span className="badge badge-neutral">
                      已是当前 {preview.unchanged_count}
                    </span>
                  )}
                </>
              )}
              {preview.missing_count > 0 && (
                <span className="badge badge-warning">
                  {applyMode === 'select_only' ? '无匹配' : '无法切换'} {preview.missing_count}
                </span>
              )}
              <span className="text-text-tertiary">共 {preview.total} 条</span>
            </div>

            {blockedItems.length > 0 && (
              <div className="rounded-md border border-border">
                <button
                  type="button"
                  onClick={() => setShowDetail(v => !v)}
                  className="w-full text-left px-2 py-1.5 text-[11px] text-text-secondary hover:bg-surface-secondary/60"
                  aria-expanded={showDetail}
                >
                  {showDetail ? '收起' : '展开'}
                  {applyMode === 'select_only' ? '无匹配版本的' : '无法切换的'}{' '}
                  {blockedItems.length} 条及原因
                </button>
                {showDetail && (
                  <div className="max-h-[200px] overflow-auto border-t border-border">
                    {blockedItems.map(i => (
                      <div
                        key={i.case_ref}
                        className="text-[11px] px-2 py-1 border-b border-border last:border-0"
                      >
                        <div className="text-text-primary truncate">{i.case_ref}</div>
                        <div className="text-text-tertiary">{i.reason || '未知原因'}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {actionableCount === 0 && (
              <p className="text-[11px] text-text-tertiary">
                {applyMode === 'select_only'
                  ? '这批样例按当前标识都解析不到可用版本，评估时会各自沿用当前版本。'
                  : '没有需要变更的样例：要么已经是当前版本，要么按这个标识解析不到可用版本。'}
              </p>
            )}
          </div>
        ) : null}
      </div>
    </Dialog>
  )
}
