/**
 * 「提炼关键点」批量弹窗 + 进度。
 *
 * 三类数据集（备选 / 基准 / 多轮对话集）共用：调用方传 target + 可选的勾选样例 id
 * （多轮对话集另需 datasetName）。不传 caseIds 表示全量扫「有答案且关键点为空」的样例。
 *
 * 提炼是幂等的：后端只挑关键点为空的行，重跑不会覆盖已有关键点。答案过短的样例
 * 会被跳过（提炼没意义），进度里单独计数。
 *
 * 边缘路径：
 * - 待提炼 0 条 → 直接提示，不让用户对着空任务干等。
 * - Langfuse 不可达（多轮对话集）→ 待提炼计数返回 502，展示原因。
 * - 生成中关弹窗 → 任务是内存态后台 job，继续跑；重开弹窗看不到旧进度（按需重查计数）。
 */
import { useEffect, useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useToast } from '@/components/ui'
import {
  keyPointsApi,
  isTerminalPhase,
  type KeyPointsTarget,
  type KeyPointsPhase,
} from '@/services/keyPoints'
import { formatApiError, toToastMessage } from '@/lib/errors'

const PHASE_LABEL: Record<KeyPointsPhase, string> = {
  pending: '排队中',
  collecting: '收集样例',
  extracting: '提炼中',
  writing: '写回数据集',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const PHASE_BADGE: Record<KeyPointsPhase, string> = {
  pending: 'badge badge-info',
  collecting: 'badge badge-info',
  extracting: 'badge badge-info',
  writing: 'badge badge-info',
  done: 'badge badge-positive',
  failed: 'badge badge-negative',
  cancelled: 'badge badge-neutral',
}

export interface KeyPointsExtractDialogProps {
  open: boolean
  onClose: () => void
  target: KeyPointsTarget
  /** 多轮对话集必填：Langfuse 数据集名 */
  datasetName?: string
  /** 勾选的样例 id；为空表示全量扫待提炼样例 */
  caseIds?: string[]
  /** 任务进入终态后回调，调用方据此刷新列表 */
  onFinished?: () => void
}

export default function KeyPointsExtractDialog({
  open,
  onClose,
  target,
  datasetName,
  caseIds,
  onFinished,
}: KeyPointsExtractDialogProps) {
  const toast = useToast()
  const qc = useQueryClient()
  const rid = useId()

  const [model, setModel] = useState('')
  const [concurrency, setConcurrency] = useState(8)
  const [limit, setLimit] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)

  const selectedCount = caseIds?.length ?? 0
  const isSelection = selectedCount > 0

  // 关闭时回到表单态，下次打开重新查计数。
  useEffect(() => {
    if (!open) setJobId(null)
  }, [open])

  // 全量模式才需要问后端「会提炼多少条」；勾选模式条数已知。
  const pendingQuery = useQuery({
    queryKey: ['key-points-pending', target, datasetName],
    queryFn: () =>
      keyPointsApi
        .pendingCount({ target, dataset_name: datasetName })
        .then(r => r.data),
    enabled: open && !isSelection && !jobId,
  })

  const extractMutation = useMutation({
    mutationFn: async () => {
      const res = await keyPointsApi.extract({
        target,
        dataset_name: datasetName,
        case_ids: isSelection ? caseIds : undefined,
        limit: limit ? Number(limit) : undefined,
        model: model.trim() || undefined,
        concurrency,
      })
      return res.data
    },
    onSuccess: (data) => {
      setJobId(data.job_id)
      toast.success('已开始提炼关键点')
    },
    onError: (e) => {
      toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '发起提炼失败' })))
    },
  })

  const jobQuery = useQuery({
    queryKey: ['key-points-job', jobId],
    queryFn: () => keyPointsApi.getJob(jobId!).then(r => r.data),
    enabled: !!jobId && open,
    refetchInterval: (q) => {
      const phase = q.state.data?.phase
      return phase && !isTerminalPhase(phase) ? 1500 : false
    },
  })
  const job = jobQuery.data
  const isTerminal = !!job && isTerminalPhase(job.phase)

  // 终态时刷新列表（写回后关键点才会出现在样例上）。
  useEffect(() => {
    if (!isTerminal) return
    qc.invalidateQueries({ queryKey: ['key-points-pending'] })
    onFinished?.()
  }, [isTerminal, job?.phase, qc, onFinished])

  const cancelMutation = useMutation({
    mutationFn: () => keyPointsApi.cancelJob(jobId!),
    onSuccess: () => toast.success('已请求取消，正在提炼的那条会跑完'),
    onError: (e) => toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '取消失败' })))
  })

  const pendingCount = pendingQuery.data?.pending ?? 0
  const canSubmit = isSelection || (pendingQuery.isSuccess && pendingCount > 0)
  const progressPct = job && job.total > 0 ? Math.round((job.done / job.total) * 100) : 0

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={jobId ? '提炼关键点 · 进度' : '提炼关键点'}
      description={
        jobId
          ? undefined
          : isSelection
            ? `将对勾选的 ${selectedCount} 条样例，从参考答案提炼关键点。`
            : '将扫描本数据集里「有参考答案但关键点为空」的样例，逐条提炼关键点。'
      }
      width={560}
      footer={
        jobId ? (
          <div className="flex gap-2 justify-end">
            {!isTerminal && (
              <Button
                variant="danger"
                size="md"
                onClick={() => cancelMutation.mutate()}
                loading={cancelMutation.isPending}
              >
                取消剩余
              </Button>
            )}
            <Button variant="primary" size="md" onClick={onClose}>
              {isTerminal ? '完成' : '后台运行'}
            </Button>
          </div>
        ) : (
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="md" onClick={onClose}>
              取消
            </Button>
            <Button
              variant="primary"
              size="md"
              onClick={() => extractMutation.mutate()}
              loading={extractMutation.isPending}
              disabled={!canSubmit}
            >
              开始提炼
            </Button>
          </div>
        )
      }
    >
      {jobId ? (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3 text-caption">
            <span className={job ? PHASE_BADGE[job.phase] : 'badge badge-info'}>
              {job ? PHASE_LABEL[job.phase] : '—'}
            </span>
            <span className="text-text-secondary">
              已提炼 {job?.extracted ?? 0} / 写回 {job?.written ?? 0} / 失败 {job?.failed ?? 0} / 共 {job?.total ?? 0}
            </span>
          </div>

          <div className="h-1.5 rounded-full bg-surface-secondary overflow-hidden">
            <div className="h-full bg-accent transition-all" style={{ width: `${progressPct}%` }} />
          </div>

          {!!job?.skipped_short && (
            <p className="text-[11px] text-text-tertiary">
              有 {job.skipped_short} 条答案过短已跳过，提炼这类答案没有意义。
            </p>
          )}

          {job?.error && (
            <div className="rounded-md border border-negative/40 bg-negative/5 p-2 text-[11px] text-negative break-all">
              {job.error}
            </div>
          )}

          <p className="text-[11px] text-text-tertiary">
            提炼只写关键点为空的样例，已有关键点不会被覆盖。关闭弹窗任务仍在后台继续。
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {!isSelection && (
            <div className="rounded-md border border-border p-2 text-[11px]">
              {pendingQuery.isLoading && <span className="text-text-tertiary">正在统计待提炼样例…</span>}
              {pendingQuery.isError && (
                <span className="text-negative break-all">
                  {toToastMessage(formatApiError(pendingQuery.error, { fallbackMessage: '统计待提炼样例失败' }))}
                </span>
              )}
              {pendingQuery.isSuccess && (
                <div className="text-text-secondary">
                  待提炼 <span className="text-text-primary">{pendingCount}</span> 条
                  {!!pendingQuery.data.skipped_short && (
                    <span className="text-text-tertiary">
                      （另有 {pendingQuery.data.skipped_short} 条答案过短，会跳过）
                    </span>
                  )}
                  {pendingCount === 0 && (
                    <span className="text-text-tertiary">，没有需要提炼的样例</span>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-0">
              <span className="field-label">模型（可选，默认用评估器配置）</span>
              <input
                id={`${rid}-model`}
                type="text"
                value={model}
                onChange={e => setModel(e.target.value)}
                className="input"
              />
            </label>
            <label className="flex flex-col gap-0">
              <span className="field-label">并发</span>
              <input
                id={`${rid}-conc`}
                type="number"
                value={concurrency}
                min={1}
                max={20}
                onChange={e => setConcurrency(Math.min(20, Math.max(1, Number(e.target.value) || 8)))}
                className="input"
              />
            </label>
            {!isSelection && (
              <label className="flex flex-col gap-0 col-span-2">
                <span className="field-label">本次最多提炼条数（可选，留空为全部）</span>
                <input
                  id={`${rid}-limit`}
                  type="number"
                  value={limit}
                  min={1}
                  onChange={e => setLimit(e.target.value)}
                  placeholder="如 50，先小批量试一下"
                  className="input"
                />
              </label>
            )}
          </div>

          {target === 'multichat' && (
            <p className="text-[11px] text-text-tertiary">
              多轮对话样例逐轮提炼，关键点写回该轮的核对标准（turn_expectations[].criteria）。
            </p>
          )}
        </div>
      )}
    </Dialog>
  )
}
