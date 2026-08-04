/**
 * 「批量修改样例类别」弹窗。
 *
 * 四个数据集页共用：调用方传 datasetType + datasetName + 勾选的样例 case_ref 列表。
 *
 * 三类数据集的类别落在三处不同存储上（备选集自由文本 category、基准集 category_id
 * 外键、多轮对话集 Langfuse metadata.category），差异在后端 /api/case-categories 里
 * 已经吃掉了，这里只负责按 dataset_type 决定：
 *   benchmark      提交 category_id（只能从该 project 已有类别里选）
 *   conversation   提交 category_name（只能从该数据集受管类别里选）
 *   candidate      提交 category_name（自由文本，允许一个全新的名字）
 *
 * 先干跑预览（batchResolve）再执行（batchSet），两次走后端同一套解析逻辑，用户看到
 * 的和实际改的一致。不是原子语义：能改的先改，改不了的原样不动并列出原因。
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Button, Dialog, useToast } from '@/components/ui'
import {
  caseCategoriesApi,
  type CategoryTarget,
} from '@/services/caseCategories'
import type { ReplyDatasetType } from '@/services/agentReplies'
import { formatApiError, toToastMessage } from '@/lib/errors'

/** 下拉里两个非「已有类别」的特殊档位。用不会与类别名/uuid 撞的前缀。 */
const CLEAR_KEY = '__clear__'
const NEW_KEY = '__new__'

export interface CaseCategoryBatchDialogProps {
  open: boolean
  onClose: () => void
  datasetType: ReplyDatasetType
  /** 勾选的样例 id（candidate/benchmark = 本地主键；conversation = dataset item id） */
  caseRefs: string[]
  /** 多轮对话集必填；备选集给了则把可选类别收窄到该数据集。基准集不需要。 */
  datasetName?: string | null
  /** 改完后回调，调用方据此刷新自己的列表与类别计数 */
  onDone?: () => void
}

export default function CaseCategoryBatchDialog({
  open,
  onClose,
  datasetType,
  caseRefs,
  datasetName,
  onDone,
}: CaseCategoryBatchDialogProps) {
  const toast = useToast()

  // '' = 还没选目标；CLEAR_KEY = 清空；NEW_KEY = 输入新名字（仅备选集）；
  // 其余是已有类别的标识：基准集为 category_id，另两类为类别名。
  const [targetKey, setTargetKey] = useState('')
  const [newName, setNewName] = useState('')
  const [showDetail, setShowDetail] = useState(false)

  // 关闭时回到初始状态，下次打开不带上次的残留。
  useEffect(() => {
    if (!open) {
      setTargetKey('')
      setNewName('')
      setShowDetail(false)
    }
  }, [open])

  const isClear = targetKey === CLEAR_KEY
  const isNew = targetKey === NEW_KEY
  // 备选集的类别是自由文本，允许打一个新名字；另两类只能在既有类别间搬动，
  // 新建类别走各自页面的类别管理入口（后端也按这个口径校验）。
  const allowNewName = datasetType === 'candidate'

  // 多轮对话集没有 dataset_name 后端直接 400，这种情况连预览都不发。
  const refsReady = caseRefs.length > 0 && (datasetType !== 'conversation' || !!datasetName)

  const buildTarget = (): CategoryTarget => {
    if (isClear) return { mode: 'clear' }
    if (datasetType === 'benchmark') return { mode: 'set', category_id: targetKey }
    return { mode: 'set', category_name: isNew ? newName.trim() : targetKey }
  }

  const targetReady = targetKey !== '' && (!isNew || newName.trim() !== '')

  const refsKey = caseRefs.join(',')

  const resolve = (target: CategoryTarget) =>
    caseCategoriesApi
      .batchResolve({
        dataset_type: datasetType,
        case_refs: caseRefs,
        dataset_name: datasetName ?? null,
        target,
      })
      .then(r => r.data)

  // 可选类别下拉必须先有一次 resolve 才能拿到，而 set 模式的 resolve 又要求先有目标
  // —— 鸡生蛋。所以固定用 mode='clear' 当探针拉选项：clear 不需要目标值，同样返回
  // category_options 与 current_distribution，而且是干跑，不写任何库。
  // queryKey 与用户真选了「清空类别」时的预览完全一致，两者会被合并成同一次请求。
  const optionsQuery = useQuery({
    queryKey: ['case-category-batch-resolve', datasetType, datasetName ?? '', refsKey, CLEAR_KEY, ''],
    queryFn: () => resolve({ mode: 'clear' }),
    enabled: open && refsReady,
  })

  const previewQuery = useQuery({
    queryKey: [
      'case-category-batch-resolve',
      datasetType,
      datasetName ?? '',
      refsKey,
      targetKey,
      isNew ? newName.trim() : '',
    ],
    queryFn: () => resolve(buildTarget()),
    enabled: open && refsReady && targetReady,
  })

  const preview = previewQuery.data
  const categoryOptions = optionsQuery.data?.category_options ?? []
  // 分布来自 clear 探针：它与目标无关，且在用户还没选目标时就能显示「我选中的是些什么」。
  const distribution = optionsQuery.data?.current_distribution ?? []

  const blockedItems = useMemo(
    () => (preview?.items || []).filter(i => !i.matched),
    [preview],
  )

  // 目标类别的显示名。基准集下拉的 value 是 category_id，要换回名字才能讲人话。
  const targetName = useMemo(() => {
    if (isClear) return null
    if (isNew) return newName.trim()
    if (datasetType === 'benchmark') {
      return categoryOptions.find(o => o.value_id === targetKey)?.value ?? null
    }
    return targetKey || null
  }, [isClear, isNew, newName, datasetType, categoryOptions, targetKey])

  const changedCount = preview?.changed_count ?? 0

  const applyMutation = useMutation({
    mutationFn: () =>
      caseCategoriesApi
        .batchSet({
          dataset_type: datasetType,
          case_refs: caseRefs,
          dataset_name: datasetName ?? null,
          target: buildTarget(),
        })
        .then(r => r.data),
    onSuccess: (data) => {
      const parts = [
        isClear ? `已清空 ${data.changed_count} 条的类别` : `已修改 ${data.changed_count} 条`,
      ]
      if (data.unchanged_count) {
        parts.push(
          isClear
            ? `${data.unchanged_count} 条本来就没类别`
            : `${data.unchanged_count} 条本来就是该类别`,
        )
      }
      if (data.missing_count) parts.push(`${data.missing_count} 条改不了`)
      if (data.failed_count) parts.push(`${data.failed_count} 条写入失败`)
      const message = parts.join('，')
      // 一条没改成、而且全都改不了，那就是彻底失败，别报成绿的。
      if (data.changed_count === 0 && data.missing_count + data.failed_count === data.total) {
        toast.error(message)
      } else {
        toast.success(message)
      }
      onDone?.()
      onClose()
    },
    onError: (e) =>
      toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '批量修改类别失败' }))),
  })

  const targetHint = isClear
    ? datasetType === 'conversation'
      ? '把这些样例的 metadata.category 抹掉，类别本身不会被删除。'
      : '把这些样例的类别清空，类别本身不会被删除。'
    : isNew
      ? '备选集的类别是自由文本，填一个新名字即可，不需要先创建。'
      : datasetType === 'benchmark'
        ? '基准集的类别按项目划分：不属于样例所在项目的类别会被逐条挡下并给出原因。'
        : '只能选该数据集下已有的类别；新建类别请走上方的类别管理。'

  const optionsEmptyHint =
    datasetType === 'conversation'
      ? '该数据集还没有受管类别，先在类别管理里创建，再回来批量修改。'
      : datasetType === 'benchmark'
        ? '这些样例所在项目下还没有类别，先在项目里创建类别。'
        : null

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="批量修改类别"
      description={`对勾选的 ${caseRefs.length} 条样例批量设置或清空类别。`}
      width={640}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="md"
            onClick={() => applyMutation.mutate()}
            loading={applyMutation.isPending}
            disabled={!targetReady || !preview || changedCount === 0}
          >
            {changedCount > 0
              ? (isClear ? `确认清空 ${changedCount} 条` : `确认修改 ${changedCount} 条`)
              : (isClear ? '确认清空' : '确认修改')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {!refsReady ? (
          <div className="rounded-md border border-negative/40 bg-negative/5 p-2 text-[11px] text-negative">
            {caseRefs.length === 0 ? '没有勾选任何样例。' : '缺少数据集名称，无法批量修改类别。'}
          </div>
        ) : (
          <>
            {distribution.length > 0 && (
              <div className="rounded-md border border-border bg-surface-secondary/40 p-2 text-[11px] text-text-tertiary">
                <span className="text-text-secondary">这批样例当前的类别：</span>
                <span className="ml-1">
                  {distribution.map(d => `${d.value} ${d.case_count}`).join(' · ')}
                </span>
              </div>
            )}

            <label className="flex flex-col gap-0">
              <span className="field-label">改成什么类别</span>
              <select
                value={targetKey}
                onChange={(e) => {
                  setTargetKey(e.target.value)
                  setShowDetail(false)
                }}
                className="input"
                disabled={optionsQuery.isLoading}
              >
                <option value="">请选择</option>
                {categoryOptions.map(o => (
                  <option
                    key={o.value_id ?? o.value}
                    value={datasetType === 'benchmark' ? (o.value_id ?? '') : o.value}
                  >
                    {o.value}（本批 {o.case_count} 条）
                  </option>
                ))}
                {allowNewName && <option value={NEW_KEY}>输入新类别名…</option>}
                <option value={CLEAR_KEY}>清空类别</option>
              </select>
            </label>

            {isNew && (
              <label className="flex flex-col gap-0">
                <span className="field-label">新类别名</span>
                <input
                  type="text"
                  value={newName}
                  maxLength={128}
                  onChange={e => setNewName(e.target.value)}
                  className="input"
                  placeholder="如 售后咨询"
                />
              </label>
            )}

            {categoryOptions.length === 0 && !optionsQuery.isLoading && optionsEmptyHint && (
              <p className="text-[11px] text-warning">{optionsEmptyHint}</p>
            )}

            <p className="text-[11px] text-text-tertiary">{targetHint}</p>

            {optionsQuery.isError ? (
              <div className="rounded-md border border-negative/40 bg-negative/5 p-2 text-[11px] text-negative">
                {toToastMessage(
                  formatApiError(optionsQuery.error, { fallbackMessage: '读取可选类别失败' }),
                )}
              </div>
            ) : !targetReady ? (
              <div className="rounded-md border border-border p-3 text-caption text-text-tertiary">
                选择目标类别后显示预览。
              </div>
            ) : previewQuery.isLoading ? (
              <div className="rounded-md border border-border p-3 text-caption text-text-tertiary">
                正在解析每个样例会改成什么…
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
                  <span className="badge badge-info">
                    {isClear ? '将清空' : '将修改'} {preview.changed_count}
                  </span>
                  {preview.unchanged_count > 0 && (
                    <span className="badge badge-neutral">
                      {isClear ? '本来就没类别' : '已是该类别'} {preview.unchanged_count}
                    </span>
                  )}
                  {preview.missing_count > 0 && (
                    <span className="badge badge-warning">改不了 {preview.missing_count}</span>
                  )}
                  <span className="text-text-tertiary">共 {preview.total} 条</span>
                </div>

                {!isClear && targetName && (
                  <p className="text-[11px] text-text-tertiary">
                    目标类别：<span className="text-text-primary">{targetName}</span>
                  </p>
                )}

                {blockedItems.length > 0 && (
                  <div className="rounded-md border border-border">
                    <button
                      type="button"
                      onClick={() => setShowDetail(v => !v)}
                      className="w-full text-left px-2 py-1.5 text-[11px] text-text-secondary hover:bg-surface-secondary/60"
                      aria-expanded={showDetail}
                    >
                      {showDetail ? '收起' : '展开'}改不了的 {blockedItems.length} 条及原因
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

                {changedCount === 0 && (
                  <p className="text-[11px] text-text-tertiary">
                    没有需要变更的样例：要么已经是这个类别，要么这批样例都改不了。
                  </p>
                )}
              </div>
            ) : null}
          </>
        )}
      </div>
    </Dialog>
  )
}
