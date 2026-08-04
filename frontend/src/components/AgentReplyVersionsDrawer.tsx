import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { agentRepliesApi } from '@/services'
import type { ReplyDatasetType, ReplyVersion } from '@/services/agentReplies'
import { Drawer, Button, LoadingBlock, ErrorCard, useToast, useConfirm } from './ui'
import { formatApiError } from '@/lib/errors'

// 单个样例的「agent 生成答案」版本回溯抽屉。
// 能做四件事：切换当前版本（评估默认消费的那个）、手工修订内容、删除版本、
// 对失败样例重新生成（重新生成需要 agent 配置，所以交回父级去开生成弹窗）。
interface Props {
  open: boolean
  onClose: () => void
  datasetType: ReplyDatasetType
  caseRef: string | null
  /** 抽屉副标题，一般传样例问题的前若干字 */
  caseTitle?: string | null
  /** 是否允许写操作（设为当前 / 编辑 / 删除 / 重新生成） */
  canWrite?: boolean
  /** 点「重新生成这条」时回调，父级用同一个 case 打开生成弹窗 */
  onRetryCase?: (caseRef: string) => void
  /** 版本发生任何变化后通知父级刷新行内标记 */
  onChanged?: () => void
}

function statusBadge(status: string) {
  if (status === 'succeeded') return <span className="badge badge-success">成功</span>
  if (status === 'failed') return <span className="badge badge-error">失败</span>
  if (status === 'running') return <span className="badge badge-info">生成中</span>
  if (status === 'cancelled') return <span className="badge badge-warning">已取消</span>
  return <span className="badge">{status}</span>
}

function fmtTime(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('zh-CN', { hour12: false })
}

export function AgentReplyVersionsDrawer({
  open,
  onClose,
  datasetType,
  caseRef,
  caseTitle,
  canWrite = true,
  onRetryCase,
  onChanged,
}: Props) {
  const toast = useToast()
  const confirm = useConfirm()
  const qc = useQueryClient()

  const [activeId, setActiveId] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draftContent, setDraftContent] = useState('')
  const [draftLabel, setDraftLabel] = useState('')

  const enabled = open && !!caseRef
  const queryKey = ['agent-reply-versions', datasetType, caseRef]

  const {
    data: versions,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey,
    enabled,
    queryFn: async () => {
      const res = await agentRepliesApi.listVersions(datasetType, caseRef as string)
      return res.data
    },
  })

  // 打开 / 换样例时默认选中当前版本（没有则选最新一条）。
  useEffect(() => {
    if (!versions || versions.length === 0) {
      setActiveId(null)
      return
    }
    setActiveId((prev) => {
      if (prev && versions.some((v) => v.id === prev)) return prev
      return (versions.find((v) => v.is_current) ?? versions[0]).id
    })
  }, [versions])

  useEffect(() => {
    if (!open) {
      setEditing(false)
      setActiveId(null)
    }
  }, [open])

  const active: ReplyVersion | null = useMemo(
    () => versions?.find((v) => v.id === activeId) ?? null,
    [versions, activeId],
  )

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey })
    onChanged?.()
  }

  const setCurrent = useMutation({
    mutationFn: (id: string) => agentRepliesApi.setCurrentVersion(id),
    onSuccess: () => {
      toast.success('已设为当前版本，评估选「使用已有回复」时会用它')
      invalidate()
    },
    onError: (e: any) => {
      toast.error(e?.response?.data?.detail ?? '设为当前版本失败')
    },
  })

  const save = useMutation({
    mutationFn: (payload: { id: string; content: string; version_label: string }) =>
      agentRepliesApi.updateVersion(payload.id, {
        content: payload.content,
        version_label: payload.version_label,
      }),
    onSuccess: () => {
      toast.success('已保存修订')
      setEditing(false)
      invalidate()
    },
    onError: (e: any) => {
      toast.error(e?.response?.data?.detail ?? '保存失败')
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => agentRepliesApi.deleteVersion(id),
    onSuccess: () => {
      toast.success('版本已删除')
      setActiveId(null)
      invalidate()
    },
    onError: (e: any) => {
      // 后端对「已被评估结果引用」的版本返回 409，原文直出更有信息量。
      toast.error(e?.response?.data?.detail ?? '删除失败')
    },
  })

  const startEdit = () => {
    if (!active) return
    setDraftContent(active.content ?? '')
    setDraftLabel(active.version_label ?? '')
    setEditing(true)
  }

  const askDelete = async (v: ReplyVersion) => {
    const ok = await confirm({
      title: `删除版本 v${v.version_number}？`,
      description:
        v.used_by_results > 0
          ? `这个版本已被 ${v.used_by_results} 条评估结果引用，删除会被后端拒绝。`
          : '删除后无法恢复。如果它是当前版本，指针会自动落到其他版本上。',
      confirmText: '删除',
      danger: true,
    })
    if (ok) remove.mutate(v.id)
  }

  const busy = setCurrent.isPending || save.isPending || remove.isPending

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="agent 回复版本"
      subtitle={caseTitle || caseRef || undefined}
      width="wide"
      actions={
        <Button variant="ghost" size="sm" onClick={() => void refetch()} disabled={busy}>
          刷新
        </Button>
      }
    >
      {isLoading ? (
        <LoadingBlock text="加载版本…" />
      ) : error ? (
        <ErrorCard error={formatApiError(error, { fallbackTitle: '加载版本失败' })} />
      ) : !versions || versions.length === 0 ? (
        <div className="py-10 text-center text-sm text-slate-500 dark:text-slate-400">
          <p>这个样例还没有 agent 生成的回复。</p>
          {canWrite && onRetryCase && caseRef && (
            <Button className="mt-4" size="sm" onClick={() => onRetryCase(caseRef)}>
              生成答案
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-5">
          {/* 版本列表 */}
          <div className="space-y-2">
            {versions.map((v) => {
              const selected = v.id === activeId
              return (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => {
                    setActiveId(v.id)
                    setEditing(false)
                  }}
                  className={`w-full rounded-lg border px-3 py-2 text-left transition ${
                    selected
                      ? 'border-indigo-400 bg-indigo-50/60 dark:border-indigo-500 dark:bg-indigo-500/10'
                      : 'border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-medium">v{v.version_number}</span>
                    {v.is_current && <span className="badge badge-success">当前</span>}
                    {statusBadge(v.status)}
                    {v.edited && <span className="badge">已修订</span>}
                    {v.version_label && (
                      <span className="truncate text-xs text-slate-500 dark:text-slate-400">
                        {v.version_label}
                      </span>
                    )}
                    {v.agent_config?.model ? (
                      <span
                        className="shrink-0 truncate rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                        title={String(v.agent_config.model)}
                      >
                        {String(v.agent_config.model)}
                      </span>
                    ) : null}
                    <span className="flex-1" />
                    <span className="text-xs text-slate-400">{fmtTime(v.created_at)}</span>
                  </div>
                  {v.status === 'failed' && v.error_message && (
                    <p className="mt-1 line-clamp-2 text-xs text-rose-600 dark:text-rose-400">
                      {v.error_message}
                    </p>
                  )}
                </button>
              )
            })}
          </div>

          {/* 选中版本详情 */}
          {active && (
            <div className="rounded-lg border border-slate-200 dark:border-slate-700">
              <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-700">
                <span className="text-sm font-medium">v{active.version_number} 详情</span>
                <span className="flex-1" />
                {canWrite && !editing && (
                  <>
                    {!active.is_current && active.status === 'succeeded' && (
                      <Button
                        size="sm"
                        onClick={() => setCurrent.mutate(active.id)}
                        loading={setCurrent.isPending}
                      >
                        设为当前
                      </Button>
                    )}
                    {active.status === 'succeeded' && (
                      <Button variant="secondary" size="sm" onClick={startEdit}>
                        编辑
                      </Button>
                    )}
                    {onRetryCase && caseRef && (
                      <Button variant="secondary" size="sm" onClick={() => onRetryCase(caseRef)}>
                        重新生成
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-rose-600 dark:text-rose-400"
                      onClick={() => void askDelete(active)}
                      loading={remove.isPending}
                    >
                      删除
                    </Button>
                  </>
                )}
              </div>

              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 px-4 py-3 text-xs text-slate-500 dark:text-slate-400 sm:grid-cols-3">
                <div>
                  <dt className="field-label">耗时</dt>
                  <dd>{active.latency_ms != null ? `${active.latency_ms} ms` : '—'}</dd>
                </div>
                <div>
                  <dt className="field-label">tokens</dt>
                  <dd>{active.total_tokens ?? '—'}</dd>
                </div>
                <div>
                  <dt className="field-label">被评估引用</dt>
                  <dd>{active.used_by_results} 次</dd>
                </div>
                <div>
                  <dt className="field-label">生成人</dt>
                  <dd>{active.created_by_name || '—'}</dd>
                </div>
                <div className="col-span-2 sm:col-span-2">
                  <dt className="field-label">模型 / 端点</dt>
                  <dd className="truncate">
                    {String(active.agent_config?.model ?? '—')}
                    {active.agent_config?.url ? ` @ ${String(active.agent_config.url)}` : ''}
                  </dd>
                </div>
              </dl>

              <div className="border-t border-slate-200 px-4 py-3 dark:border-slate-700">
                {editing ? (
                  <div className="space-y-3">
                    <div>
                      <label className="field-label" htmlFor="reply-version-label">
                        版本备注
                      </label>
                      <input
                        id="reply-version-label"
                        className="input"
                        value={draftLabel}
                        onChange={(e) => setDraftLabel(e.target.value)}
                        placeholder="可选，比如「人工修订后」"
                      />
                    </div>
                    <div>
                      <label className="field-label" htmlFor="reply-version-content">
                        回复内容
                      </label>
                      <textarea
                        id="reply-version-content"
                        className="input min-h-[220px] font-mono text-xs"
                        value={draftContent}
                        onChange={(e) => setDraftContent(e.target.value)}
                      />
                    </div>
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
                        取消
                      </Button>
                      <Button
                        size="sm"
                        loading={save.isPending}
                        onClick={() =>
                          save.mutate({
                            id: active.id,
                            content: draftContent,
                            version_label: draftLabel,
                          })
                        }
                      >
                        保存
                      </Button>
                    </div>
                  </div>
                ) : active.turns && active.turns.length > 0 ? (
                  // 多轮对话集：逐轮展示 agent 回复
                  <div className="space-y-3">
                    {active.turns.map((t, i) => (
                      <div key={i} className="rounded-md bg-slate-50 p-3 dark:bg-slate-800/60">
                        <p className="page-eyebrow">第 {i + 1} 轮</p>
                        {t.question != null && (
                          <p className="mt-1 whitespace-pre-wrap text-xs text-slate-500 dark:text-slate-400">
                            用户：{String(t.question)}
                          </p>
                        )}
                        <p className="mt-1 whitespace-pre-wrap text-sm">
                          {String(t.answer ?? t.content ?? '')}
                        </p>
                      </div>
                    ))}
                  </div>
                ) : active.status === 'failed' ? (
                  <p className="whitespace-pre-wrap text-sm text-rose-600 dark:text-rose-400">
                    {active.error_message || '生成失败，没有错误详情'}
                  </p>
                ) : (
                  <p className="whitespace-pre-wrap text-sm">{active.content || '（空回复）'}</p>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </Drawer>
  )
}
