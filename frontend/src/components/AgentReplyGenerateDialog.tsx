/**
 * 「agent生成答案」表单弹窗 + 生成进度。
 *
 * 三类数据集（备选 / 基准 / 多轮对话集）的列表页共用这一个组件：调用方只传
 * datasetType + 勾选的样例 id（以及多轮对话集的 datasetName），agent 配置字段
 * 与评估页同构（EvalAgentConfig 协议），生成后立刻转入进度视图，轮询到终态。
 *
 * 边缘路径：
 * - 409（同配置同样例已在生成中）→ 展示冲突样例，不关闭弹窗，用户可改配置重试。
 * - 生成中刷新页面 → 调用方用 listJobs(active_only) 恢复进度（本组件只管本次提交）。
 * - 部分失败 → 进度视图列出失败样例与原因，可「重试失败项」开新任务。
 */
import { useEffect, useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, OptionPicker, useToast } from '@/components/ui'
import { agentRepliesApi, type ReplyDatasetType } from '@/services/agentReplies'
import { configOptionToString, useConfigOptions } from '@/hooks/useConfigOptions'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { EvalAgentConfig } from '@/types'

type AgentType = 'sse' | 'openai' | 'sse_generic'

export interface GenerateDialogProps {
  open: boolean
  onClose: () => void
  datasetType: ReplyDatasetType
  /** 多轮对话集必填：Langfuse 数据集名（后端据此 load 样例真身） */
  datasetName?: string
  /** 基准数据集可选：归属项目，便于按项目查历史任务 */
  projectId?: string
  /** 勾选的样例 id（candidate/benchmark = 本地主键；conversation = dataset item id） */
  caseIds: string[]
  /** 任务进入终态后回调，调用方据此刷新列表的回复状态标记 */
  onFinished?: () => void
}

export default function AgentReplyGenerateDialog({
  open,
  onClose,
  datasetType,
  datasetName,
  projectId,
  caseIds,
  onFinished,
}: GenerateDialogProps) {
  const toast = useToast()
  const qc = useQueryClient()
  const rid = useId()

  const [type, setType] = useState<AgentType>('sse')
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [language, setLanguage] = useState('请用中文回复')
  const [headersText, setHeadersText] = useState('')
  const [payloadText, setPayloadText] = useState('')
  const [timeout, setTimeoutSec] = useState(300)
  const [versionLabel, setVersionLabel] = useState('')
  const [concurrency, setConcurrency] = useState(3)
  // 提交成功后切换到进度视图；null = 还在填表单。
  const [jobId, setJobId] = useState<string | null>(null)
  const [conflict, setConflict] = useState<{ message: string; caseRefs: string[] } | null>(null)

  const endpointOpts = useConfigOptions('target_agent.endpoint_url')
  const apiKeyOpts = useConfigOptions('target_agent.api_key')

  // 默认 URL 取配置中心第一项，省得每次手填。
  useEffect(() => {
    if (!open || url) return
    const first = endpointOpts.options[0]
    if (first) setUrl(configOptionToString(first.value))
  }, [open, url, endpointOpts.options])

  // 关闭时重置进度态，下次打开回到表单。
  useEffect(() => {
    if (!open) {
      setJobId(null)
      setConflict(null)
    }
  }, [open])

  const buildAgent = (): EvalAgentConfig | null => {
    let headers: Record<string, string> | undefined
    let payloadTpl: Record<string, unknown> | undefined
    try {
      if (headersText.trim()) headers = JSON.parse(headersText)
    } catch {
      toast.error('请求头必须是合法 JSON')
      return null
    }
    try {
      if (payloadText.trim()) payloadTpl = JSON.parse(payloadText)
    } catch {
      toast.error('请求体模板必须是合法 JSON')
      return null
    }
    if (!url.trim()) {
      toast.error('请填写智能体 URL')
      return null
    }
    return {
      type,
      url: url.trim(),
      api_key: apiKey || undefined,
      model: model || undefined,
      headers,
      payload_template: payloadTpl,
      timeout,
      language,
    }
  }

  const generateMutation = useMutation({
    mutationFn: async () => {
      const agent = buildAgent()
      if (!agent) throw new Error('__invalid__')
      const res = await agentRepliesApi.generate({
        dataset_type: datasetType,
        dataset_name: datasetName,
        project_id: projectId,
        case_ids: caseIds,
        agent,
        version_label: versionLabel.trim() || undefined,
        concurrency,
      })
      return res.data
    },
    onSuccess: (data) => {
      setConflict(null)
      setJobId(data.job_id)
      toast.success(`已开始生成 ${data.case_count} 条样例的回复`)
    },
    onError: (e: unknown) => {
      if (e instanceof Error && e.message === '__invalid__') return
      // 409：同配置同样例在途。后端 detail 是结构化对象，取出冲突样例展示。
      const resp = (e as { response?: { status?: number; data?: { detail?: unknown } } }).response
      if (resp?.status === 409 && resp.data?.detail && typeof resp.data.detail === 'object') {
        const d = resp.data.detail as { message?: string; case_refs?: string[] }
        setConflict({
          message: d.message || '选中样例中有正在生成的任务',
          caseRefs: d.case_refs || [],
        })
        return
      }
      toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '发起生成失败' })))
    },
  })

  // 进度轮询：终态停止。刷新页面后由调用方的 active_only 列表恢复。
  const jobQuery = useQuery({
    queryKey: ['agent-reply-job', jobId],
    queryFn: () => agentRepliesApi.getJob(jobId!).then(r => r.data),
    enabled: !!jobId && open,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'running' || s === 'cancelling' ? 1500 : false
    },
  })
  const job = jobQuery.data
  const isTerminal = !!job && !['running', 'cancelling'].includes(job.status)

  // 终态时刷新列表标记（只触发一次，靠 job.status 变化收敛）。
  useEffect(() => {
    if (!isTerminal) return
    qc.invalidateQueries({ queryKey: ['agent-reply-states'] })
    onFinished?.()
  }, [isTerminal, job?.status, qc, onFinished])

  const cancelMutation = useMutation({
    mutationFn: () => agentRepliesApi.cancelJob(jobId!),
    onSuccess: () => toast.success('已请求取消，正在跑的那条会跑完'),
    onError: (e) => toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '取消失败' }))),
  })

  const retryFailedMutation = useMutation({
    mutationFn: () => agentRepliesApi.retryFailed(jobId!).then(r => r.data),
    onSuccess: (data) => {
      setJobId(data.job_id)
      toast.success(`已重试 ${data.case_count} 条失败样例`)
    },
    onError: (e) => toast.error(toToastMessage(formatApiError(e, { fallbackMessage: '重试失败' }))),
  })

  const failedItems = (job?.items || []).filter(i => i.status === 'failed' || i.status === 'cancelled')

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={jobId ? 'agent 生成答案 · 进度' : 'agent 生成答案'}
      description={
        jobId
          ? undefined
          : `将对勾选的 ${caseIds.length} 条样例调用 agent 生成答案，结果存为可回溯的版本。`
      }
      width={640}
      footer={
        jobId ? (
          <div className="flex gap-2 justify-end">
            {job?.status === 'running' && (
              <Button
                variant="danger"
                size="md"
                onClick={() => cancelMutation.mutate()}
                loading={cancelMutation.isPending}
              >
                取消剩余
              </Button>
            )}
            {isTerminal && failedItems.length > 0 && (
              <Button
                variant="secondary"
                size="md"
                onClick={() => retryFailedMutation.mutate()}
                loading={retryFailedMutation.isPending}
              >
                重试失败项 ({failedItems.length})
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
              onClick={() => generateMutation.mutate()}
              loading={generateMutation.isPending}
              disabled={caseIds.length === 0}
            >
              开始生成
            </Button>
          </div>
        )
      }
    >
      {jobId ? (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3 text-caption">
            <span className={
              job?.status === 'succeeded' ? 'badge badge-positive'
              : job?.status === 'failed' ? 'badge badge-negative'
              : job?.status === 'partial' ? 'badge badge-warning'
              : job?.status === 'cancelled' ? 'badge badge-neutral'
              : 'badge badge-info'
            }>
              {job?.status === 'running' ? '生成中'
                : job?.status === 'succeeded' ? '全部成功'
                : job?.status === 'partial' ? '部分失败'
                : job?.status === 'failed' ? '全部失败'
                : job?.status === 'cancelled' ? '已取消'
                : job?.status === 'cancelling' ? '取消中'
                : job?.status === 'interrupted' ? '已中断'
                : job?.status || '—'}
            </span>
            <span className="text-text-secondary">
              成功 {job?.succeeded_count ?? 0} / 失败 {job?.failed_count ?? 0} / 共 {job?.total_count ?? 0}
            </span>
          </div>

          <div className="h-1.5 rounded-full bg-surface-secondary overflow-hidden">
            <div
              className="h-full bg-accent transition-all"
              style={{
                width: `${job && job.total_count > 0
                  ? Math.round(((job.succeeded_count + job.failed_count) / job.total_count) * 100)
                  : 0}%`,
              }}
            />
          </div>

          {failedItems.length > 0 && (
            <div className="rounded-md border border-border p-2 max-h-[220px] overflow-auto">
              <div className="page-eyebrow mb-1">失败样例</div>
              {failedItems.map(i => (
                <div key={i.id} className="text-[11px] py-1 border-b border-border last:border-0">
                  <div className="text-text-primary truncate">{i.question || i.case_ref}</div>
                  <div className="text-text-tertiary">{i.error_message || '未知错误'}</div>
                </div>
              ))}
            </div>
          )}

          <p className="text-[11px] text-text-tertiary">
            生成失败的样例不会被设为当前版本，评估选「使用已有回复」时会明确报错而不是静默跳过。
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {conflict && (
            <div className="rounded-md border border-negative/40 bg-negative/5 p-2 text-[11px]">
              <div className="text-negative font-medium">{conflict.message}</div>
              {conflict.caseRefs.length > 0 && (
                <div className="text-text-tertiary mt-1 break-all">
                  冲突样例：{conflict.caseRefs.slice(0, 5).join('、')}
                  {conflict.caseRefs.length > 5 ? ` 等 ${conflict.caseRefs.length} 个` : ''}
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-0">
              <span className="field-label">类型</span>
              <select
                id={`${rid}-type`}
                value={type}
                onChange={e => setType(e.target.value as AgentType)}
                className="input"
              >
                <option value="sse">SSE (LangGraph v2)</option>
                <option value="openai">OpenAI 兼容</option>
                <option value="sse_generic">SSE 通用模板</option>
              </select>
            </label>
            <label className="flex flex-col gap-0">
              <span className="field-label">模型（可选，展示用）</span>
              <input
                id={`${rid}-model`}
                type="text"
                value={model}
                onChange={e => setModel(e.target.value)}
                className="input"
              />
            </label>
            <label className="flex flex-col gap-0 col-span-2">
              <span className="field-label">智能体 URL</span>
              <div className="relative">
                <input
                  id={`${rid}-url`}
                  type="text"
                  value={url}
                  onChange={e => setUrl(e.target.value)}
                  placeholder="http://localhost:18094/api/agent/langgraph"
                  className="input pr-9"
                />
                <OptionPicker
                  options={endpointOpts.options}
                  currentValue={url}
                  onPick={v => setUrl(configOptionToString(v))}
                />
              </div>
            </label>
            <label className="flex flex-col gap-0">
              <span className="field-label">API Key（可选）</span>
              <div className="relative">
                <input
                  id={`${rid}-key`}
                  type="password"
                  value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                  className="input pr-9"
                />
                <OptionPicker
                  options={apiKeyOpts.options}
                  currentValue={apiKey}
                  onPick={v => setApiKey(configOptionToString(v))}
                  maskValues
                />
              </div>
            </label>
            <label className="flex flex-col gap-0">
              <span className="field-label">超时（秒）</span>
              <input
                id={`${rid}-timeout`}
                type="number"
                value={timeout}
                min={10}
                onChange={e => setTimeoutSec(Number(e.target.value) || 300)}
                className="input"
              />
            </label>
            <label className="flex flex-col gap-0">
              <span className="field-label">版本号（可选，便于回溯区分）</span>
              <input
                id={`${rid}-label`}
                type="text"
                value={versionLabel}
                onChange={e => setVersionLabel(e.target.value)}
                placeholder="如 v1-基线 / 换了系统提示"
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
                onChange={e => setConcurrency(Math.min(20, Math.max(1, Number(e.target.value) || 3)))}
                className="input"
              />
            </label>
            <label className="flex flex-col gap-0 col-span-2">
              <span className="field-label">语言指令</span>
              <input
                id={`${rid}-lang`}
                type="text"
                value={language}
                onChange={e => setLanguage(e.target.value)}
                className="input"
              />
            </label>
            <label className="flex flex-col gap-0 col-span-2">
              <span className="field-label">请求头 JSON（可选）</span>
              <textarea
                id={`${rid}-headers`}
                value={headersText}
                onChange={e => setHeadersText(e.target.value)}
                rows={2}
                className="input font-mono text-[11px]"
              />
            </label>
            <label className="flex flex-col gap-0 col-span-2">
              <span className="field-label">请求体模板 JSON（可选）</span>
              <textarea
                id={`${rid}-payload`}
                value={payloadText}
                onChange={e => setPayloadText(e.target.value)}
                rows={2}
                className="input font-mono text-[11px]"
              />
            </label>
          </div>

          {datasetType === 'conversation' && (
            <p className="text-[11px] text-text-tertiary">
              多轮对话样例会逐轮调用 agent，每轮带上此前完整上下文，补全所有 assistant 回复。
            </p>
          )}
        </div>
      )}
    </Dialog>
  )
}
