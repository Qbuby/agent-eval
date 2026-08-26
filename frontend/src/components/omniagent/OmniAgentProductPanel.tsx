import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, LoadingBlock, useConfirm, useToast } from '@/components/ui'
import { omniagentApi } from '@/services/omniagent'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type {
  OmniAgentAction,
  OmniAgentArtifact,
  OmniAgentDurableEvent,
  OmniAgentJob,
  OmniAgentMemory,
  OmniAgentNotification,
  OmniAgentSchedule,
} from '@/types'

type PanelTab = 'activity' | 'approvals' | 'files' | 'memory' | 'notifications' | 'schedules'

const TABS: Array<{ id: PanelTab; label: string }> = [
  { id: 'activity', label: '活动' },
  { id: 'approvals', label: '审批' },
  { id: 'files', label: '制品' },
  { id: 'memory', label: '记忆' },
  { id: 'notifications', label: '通知' },
  { id: 'schedules', label: '计划' },
]

export default function OmniAgentProductPanel({ sessionId }: { sessionId: string | null }) {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const [tab, setTab] = useState<PanelTab>('activity')
  const [events, setEvents] = useState<OmniAgentDurableEvent[]>([])
  const cursorRef = useRef(0)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [uploading, setUploading] = useState(false)

  useEffect(() => {
    cursorRef.current = 0
    setEvents([])
  }, [sessionId])

  const eventQuery = useQuery({
    queryKey: ['omniagent-events', sessionId],
    queryFn: () => omniagentApi.listEvents(cursorRef.current, sessionId ?? undefined).then(r => r.data),
    refetchInterval: () => (document.hidden ? 15_000 : 3_000),
  })

  useEffect(() => {
    const page = eventQuery.data
    if (!page || page.items.length === 0) return
    cursorRef.current = Math.max(cursorRef.current, page.cursor)
    setEvents(prev => {
      const byCursor = new Map(prev.map(item => [item.cursor, item]))
      for (const item of page.items) byCursor.set(item.cursor, item)
      return [...byCursor.values()].sort((a, b) => b.cursor - a.cursor).slice(0, 100)
    })
    const types = new Set(page.items.map(item => item.entity_type))
    if (types.has('job')) qc.invalidateQueries({ queryKey: ['omniagent-jobs'] })
    if (types.has('action')) qc.invalidateQueries({ queryKey: ['omniagent-actions'] })
    if (types.has('artifact')) qc.invalidateQueries({ queryKey: ['omniagent-artifacts'] })
    if (types.has('schedule')) qc.invalidateQueries({ queryKey: ['omniagent-schedules'] })
    qc.invalidateQueries({ queryKey: ['omniagent-notifications'] })
  }, [eventQuery.data, qc])

  const jobsQuery = useQuery({
    queryKey: ['omniagent-jobs'],
    queryFn: () => omniagentApi.listJobs().then(r => r.data.items),
    refetchInterval: 5_000,
  })
  const actionsQuery = useQuery({
    queryKey: ['omniagent-actions'],
    queryFn: () => omniagentApi.listActions().then(r => r.data.items),
    refetchInterval: 5_000,
  })
  const artifactsQuery = useQuery({
    queryKey: ['omniagent-artifacts'],
    queryFn: () => omniagentApi.listArtifacts().then(r => r.data.items),
  })
  const memoriesQuery = useQuery({
    queryKey: ['omniagent-memories'],
    queryFn: () => omniagentApi.listMemories().then(r => r.data.items),
  })
  const notificationsQuery = useQuery({
    queryKey: ['omniagent-notifications'],
    queryFn: () => omniagentApi.listNotifications().then(r => r.data.items),
    refetchInterval: 10_000,
  })
  const schedulesQuery = useQuery({
    queryKey: ['omniagent-schedules'],
    queryFn: () => omniagentApi.listSchedules().then(r => r.data.items),
  })

  const actions = actionsQuery.data ?? []
  const pendingActions = useMemo(
    () => actions.filter(item => item.state === 'prepared' && (!sessionId || item.session_id === sessionId)),
    [actions, sessionId],
  )
  const unread = (notificationsQuery.data ?? []).filter(item => !item.read_at).length

  async function decide(action: OmniAgentAction, decision: 'approve' | 'deny') {
    try {
      await omniagentApi.decideAction(action.id, decision, action.argument_digest)
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['omniagent-actions'] }),
        qc.invalidateQueries({ queryKey: ['omniagent-jobs'] }),
      ])
      toast.success(decision === 'approve' ? '已批准，任务已入队' : '已拒绝')
    } catch (error) {
      const normalized = formatApiError(error, { fallbackTitle: '审批失败' })
      toast.error(toToastMessage(normalized), '审批失败')
    }
  }

  async function upload(file: File) {
    setUploading(true)
    try {
      await omniagentApi.uploadArtifact(file, sessionId ?? undefined)
      qc.invalidateQueries({ queryKey: ['omniagent-artifacts'] })
      toast.success('附件已上传并进入扫描流程')
    } catch (error) {
      const normalized = formatApiError(error, { fallbackTitle: '上传失败' })
      toast.error(toToastMessage(normalized), '上传失败')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function download(artifact: OmniAgentArtifact) {
    try {
      const { data } = await omniagentApi.downloadArtifact(artifact.id)
      const url = URL.createObjectURL(data)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = artifact.filename
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      const normalized = formatApiError(error, { fallbackTitle: '下载失败' })
      toast.error(toToastMessage(normalized), '下载失败')
    }
  }

  async function removeMemory(memory: OmniAgentMemory) {
    const ok = await confirm({
      title: '删除个人记忆',
      description: `删除「${memory.title}」后，OmniAgent 将无法再召回这条内容。`,
      confirmText: '删除',
      danger: true,
    })
    if (!ok) return
    await omniagentApi.deleteMemory(memory.id)
    qc.invalidateQueries({ queryKey: ['omniagent-memories'] })
  }

  async function markRead(notification: OmniAgentNotification) {
    if (notification.read_at) return
    await omniagentApi.markNotificationRead(notification.id)
    qc.invalidateQueries({ queryKey: ['omniagent-notifications'] })
  }

  async function pause(schedule: OmniAgentSchedule) {
    await omniagentApi.pauseSchedule(schedule.id)
    qc.invalidateQueries({ queryKey: ['omniagent-schedules'] })
    toast.success('计划已暂停')
  }

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-separator bg-surface">
      <div className="border-b border-separator px-3 py-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="page-eyebrow">运行状态</div>
            <div className="mt-1 text-[12px] font-medium text-text-primary">
              {pendingActions.length > 0 ? `${pendingActions.length} 项待审批` : '执行面已连接'}
            </div>
          </div>
          {unread > 0 && <span className="badge badge-info">{unread} 未读</span>}
        </div>
        <div className="mt-3 grid grid-cols-3 gap-1" role="tablist" aria-label="运行视图">
          {TABS.map(item => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              onClick={() => setTab(item.id)}
              className={`h-7 border text-[10px] transition-colors ${
                tab === item.id
                  ? 'border-accent/40 bg-accent/10 text-accent'
                  : 'border-border bg-surface text-text-secondary hover:bg-fill/5'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === 'activity' && <ActivityView events={events} jobs={jobsQuery.data ?? []} loading={eventQuery.isLoading} />}
        {tab === 'approvals' && <ApprovalView actions={pendingActions} loading={actionsQuery.isLoading} onDecision={decide} />}
        {tab === 'files' && (
          <FilesView
            artifacts={artifactsQuery.data ?? []}
            loading={artifactsQuery.isLoading}
            uploading={uploading}
            fileRef={fileRef}
            onUpload={upload}
            onDownload={download}
          />
        )}
        {tab === 'memory' && <MemoryView items={memoriesQuery.data ?? []} loading={memoriesQuery.isLoading} onDelete={removeMemory} />}
        {tab === 'notifications' && <NotificationView items={notificationsQuery.data ?? []} loading={notificationsQuery.isLoading} onRead={markRead} />}
        {tab === 'schedules' && <ScheduleView items={schedulesQuery.data ?? []} loading={schedulesQuery.isLoading} onPause={pause} />}
      </div>
    </aside>
  )
}

function ActivityView({ events, jobs, loading }: { events: OmniAgentDurableEvent[]; jobs: OmniAgentJob[]; loading: boolean }) {
  if (loading && events.length === 0) return <LoadingBlock text="加载活动…" />
  return (
    <div className="space-y-4">
      <PanelSection title="进行中的任务">
        {jobs.filter(job => ['queued', 'provisioning', 'running'].includes(job.status)).length === 0 ? (
          <Empty text="没有运行中的任务" />
        ) : jobs.filter(job => ['queued', 'provisioning', 'running'].includes(job.status)).map(job => (
          <div key={job.id} className="border-b border-separator py-2 last:border-0">
            <div className="flex items-center justify-between gap-2 text-[11px]">
              <span className="truncate font-mono text-text-primary">{job.kind}</span>
              <StatusBadge status={job.status} />
            </div>
            <div className="mt-1 text-[10px] text-text-tertiary">尝试 {job.attempt_count}/{job.max_attempts}</div>
          </div>
        ))}
      </PanelSection>
      <PanelSection title="事件流">
        {events.length === 0 ? <Empty text="等待持久事件" /> : events.map(event => (
          <div key={event.cursor} className="border-b border-separator py-2 last:border-0">
            <div className="text-[11px] font-medium text-text-primary">{eventLabel(event.type)}</div>
            <div className="mt-0.5 flex justify-between gap-2 text-[10px] text-text-tertiary">
              <span className="truncate">{event.entity_type || 'system'}</span>
              <span className="shrink-0">{formatTime(event.created_at)}</span>
            </div>
          </div>
        ))}
      </PanelSection>
    </div>
  )
}

function ApprovalView({ actions, loading, onDecision }: { actions: OmniAgentAction[]; loading: boolean; onDecision: (item: OmniAgentAction, decision: 'approve' | 'deny') => void }) {
  if (loading) return <LoadingBlock text="加载审批…" />
  if (actions.length === 0) return <Empty text="当前没有待审批动作" />
  return <div className="space-y-3">{actions.map(action => (
    <div key={action.id} className="border border-warning/30 bg-warning/5 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[11px] font-medium text-text-primary">{action.capability}</span>
        <span className="badge badge-warning">{action.risk}</span>
      </div>
      <dl className="mt-2 space-y-1 text-[10px] text-text-secondary">
        {Object.entries(action.impact_preview ?? {}).slice(0, 6).map(([key, value]) => (
          <div key={key} className="grid grid-cols-[80px_1fr] gap-2"><dt className="text-text-tertiary">{key}</dt><dd className="break-all">{compact(value)}</dd></div>
        ))}
      </dl>
      <div className="mt-2 truncate font-mono text-[9px] text-text-tertiary" title={action.argument_digest}>摘要 {action.argument_digest.slice(0, 16)}</div>
      <div className="mt-3 flex gap-2">
        <Button size="sm" variant="primary" onClick={() => onDecision(action, 'approve')}>批准</Button>
        <Button size="sm" variant="secondary" onClick={() => onDecision(action, 'deny')}>拒绝</Button>
      </div>
    </div>
  ))}</div>
}

function FilesView({ artifacts, loading, uploading, fileRef, onUpload, onDownload }: {
  artifacts: OmniAgentArtifact[]; loading: boolean; uploading: boolean
  fileRef: RefObject<HTMLInputElement>; onUpload: (file: File) => void
  onDownload: (item: OmniAgentArtifact) => void
}) {
  return <div>
    <input ref={fileRef} className="hidden" type="file" onChange={event => { const file = event.target.files?.[0]; if (file) onUpload(file) }} />
    <Button size="sm" variant="secondary" loading={uploading} onClick={() => fileRef.current?.click()}>上传附件</Button>
    <div className="mt-3">
      {loading ? <LoadingBlock text="加载制品…" /> : artifacts.length === 0 ? <Empty text="还没有制品" /> : artifacts.map(item => (
        <div key={item.id} className="border-b border-separator py-2.5 last:border-0">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0"><div className="truncate text-[11px] font-medium text-text-primary" title={item.filename}>{item.filename}</div><div className="mt-0.5 text-[10px] text-text-tertiary">{formatBytes(item.size_bytes)} · {item.retention === 'pinned' ? '已固定' : '临时'}</div></div>
            <StatusBadge status={item.state} />
          </div>
          {item.state === 'available' && <button type="button" className="text-action mt-1" onClick={() => onDownload(item)}>下载</button>}
        </div>
      ))}
    </div>
  </div>
}

function MemoryView({ items, loading, onDelete }: { items: OmniAgentMemory[]; loading: boolean; onDelete: (item: OmniAgentMemory) => void }) {
  if (loading) return <LoadingBlock text="加载记忆…" />
  if (items.length === 0) return <Empty text="没有显式保存的个人记忆" />
  return <div>{items.map(item => (
    <div key={item.id} className="border-b border-separator py-2.5 last:border-0">
      <div className="text-[11px] font-medium text-text-primary">{item.title}</div>
      <div className="mt-1 line-clamp-4 whitespace-pre-wrap text-[10px] leading-4 text-text-secondary">{item.content}</div>
      <button type="button" className="text-action-danger mt-1" onClick={() => onDelete(item)}>删除</button>
    </div>
  ))}</div>
}

function NotificationView({ items, loading, onRead }: { items: OmniAgentNotification[]; loading: boolean; onRead: (item: OmniAgentNotification) => void }) {
  if (loading) return <LoadingBlock text="加载通知…" />
  if (items.length === 0) return <Empty text="没有通知" />
  return <div>{items.map(item => (
    <button key={item.id} type="button" onClick={() => onRead(item)} className={`block w-full border-b border-separator py-2.5 text-left last:border-0 ${item.read_at ? 'opacity-60' : ''}`}>
      <div className="flex items-center gap-2"><span className="text-[11px] font-medium text-text-primary">{item.title}</span>{!item.read_at && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />}</div>
      <div className="mt-1 text-[10px] leading-4 text-text-secondary">{item.body}</div>
      <div className="mt-1 text-[9px] text-text-tertiary">{formatTime(item.created_at)}</div>
    </button>
  ))}</div>
}

function ScheduleView({ items, loading, onPause }: { items: OmniAgentSchedule[]; loading: boolean; onPause: (item: OmniAgentSchedule) => void }) {
  if (loading) return <LoadingBlock text="加载计划…" />
  if (items.length === 0) return <Empty text="没有自动化计划" />
  return <div>{items.map(item => (
    <div key={item.id} className="border-b border-separator py-2.5 last:border-0">
      <div className="flex items-center justify-between gap-2"><span className="truncate text-[11px] font-medium text-text-primary">{item.name}</span><StatusBadge status={item.enabled ? 'enabled' : 'paused'} /></div>
      <div className="mt-1 font-mono text-[10px] text-text-secondary">{item.capability}</div>
      <div className="mt-1 text-[9px] text-text-tertiary">下次 {item.next_run_at ? formatTime(item.next_run_at) : '未安排'} · v{item.version}</div>
      {item.enabled && <button type="button" className="text-action-warning mt-1" onClick={() => onPause(item)}>暂停</button>}
    </div>
  ))}</div>
}

function PanelSection({ title, children }: { title: string; children: ReactNode }) {
  return <section><h3 className="mb-1 text-[10px] font-medium uppercase text-text-tertiary">{title}</h3>{children}</section>
}

function Empty({ text }: { text: string }) {
  return <div className="border border-dashed border-border px-3 py-5 text-center text-[10px] text-text-tertiary">{text}</div>
}

function StatusBadge({ status }: { status: string }) {
  const positive = ['succeeded', 'available', 'delivered', 'enabled'].includes(status)
  const negative = ['failed', 'quarantined', 'expired', 'deleted'].includes(status)
  const warning = ['queued', 'provisioning', 'scanning', 'paused', 'cancelled'].includes(status)
  return <span className={`badge ${positive ? 'badge-positive' : negative ? 'badge-negative' : warning ? 'badge-warning' : 'badge-info'}`}>{status}</span>
}

function eventLabel(type: string): string {
  const labels: Record<string, string> = {
    'action.prepared': '动作等待审批', 'action.succeeded': '动作执行成功',
    'action.failed': '动作执行失败', 'job.queued': '任务已入队',
    'job.running': '任务开始执行', 'job.succeeded': '任务执行成功',
    'job.failed': '任务执行失败', 'artifact.available': '制品可用',
    'artifact.quarantined': '制品已隔离', 'schedule.triggered': '自动化计划触发',
  }
  return labels[type] ?? type
}

function compact(value: unknown): string {
  if (typeof value === 'string') return value
  try { return JSON.stringify(value) } catch { return String(value) }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / 1024 / 1024).toFixed(1)} MiB`
}
