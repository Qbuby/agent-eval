import api, { authFetch } from './client'
import type {
  OmniAgentMessagePage,
  OmniAgentAction,
  OmniAgentArtifact,
  OmniAgentDurableEvent,
  OmniAgentJob,
  OmniAgentMemory,
  OmniAgentNotification,
  OmniAgentSchedule,
  OmniAgentSession,
  OmniAgentSessionPage,
  OmniAgentSseEvent,
  SendMessageRequest,
  UpdateSessionRequest,
} from '@/types'

// ──────────────────────────────────────────────────────────────────────────
// OmniAgent 对话数据层。对接 routers/omniagent.py。
//
// 除「发消息」外都是普通 JSON 请求，走 axios 实例（拦截器管 token + 401 刷新）。
// 发消息是 SSE 流，必须走 authFetch + ReadableStream：axios 的 XHR 适配器要等
// 整段响应结束才给 body，逐 token 打字机效果拿不到。
// ──────────────────────────────────────────────────────────────────────────

export const omniagentApi = {
  listSessions(page = 1, pageSize = 50) {
    return api.get<OmniAgentSessionPage>('/omniagent/sessions', {
      params: { page, page_size: pageSize },
    })
  },
  createSession(title?: string) {
    return api.post<OmniAgentSession>('/omniagent/sessions', { title: title ?? null })
  },
  getSession(sessionId: string) {
    return api.get<OmniAgentSession>(`/omniagent/sessions/${sessionId}`)
  },
  /** 目前只用于重命名。 */
  updateSession(sessionId: string, data: UpdateSessionRequest) {
    return api.patch<OmniAgentSession>(`/omniagent/sessions/${sessionId}`, data)
  },
  deleteSession(sessionId: string) {
    return api.delete<void>(`/omniagent/sessions/${sessionId}`)
  },
  listMessages(sessionId: string, beforeSequence?: number, limit = 100) {
    return api.get<OmniAgentMessagePage>(`/omniagent/sessions/${sessionId}/messages`, {
      params: { before_sequence: beforeSequence, limit },
    })
  },
  listEvents(after = 0, sessionId?: string, limit = 100) {
    return api.get<{ items: OmniAgentDurableEvent[]; cursor: number }>('/omniagent/events', {
      params: { after, session_id: sessionId, limit },
    })
  },
  listJobs(limit = 50) {
    return api.get<{ items: OmniAgentJob[] }>('/omniagent/jobs', { params: { limit } })
  },
  cancelJob(jobId: string) {
    return api.post<OmniAgentJob>(`/omniagent/jobs/${jobId}/cancel`)
  },
  listActions(state?: string, limit = 50) {
    return api.get<{ items: OmniAgentAction[] }>('/omniagent/actions', {
      params: { state, limit },
    })
  },
  decideAction(actionId: string, decision: 'approve' | 'deny', digest: string) {
    return api.post<OmniAgentAction>(`/omniagent/actions/${actionId}/decision`, {
      decision,
      digest,
    })
  },
  listArtifacts(limit = 50) {
    return api.get<{ items: OmniAgentArtifact[] }>('/omniagent/artifacts', { params: { limit } })
  },
  uploadArtifact(file: File, sessionId?: string) {
    const form = new FormData()
    form.append('file', file)
    return api.post<OmniAgentArtifact>('/omniagent/artifacts', form, {
      params: { session_id: sessionId },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  downloadArtifact(artifactId: string) {
    return api.get<Blob>(`/omniagent/artifacts/${artifactId}/download`, {
      responseType: 'blob',
    })
  },
  listMemories(query = '', limit = 50) {
    return api.get<{ items: OmniAgentMemory[] }>('/omniagent/memories', {
      params: { q: query, limit },
    })
  },
  deleteMemory(memoryId: string) {
    return api.delete<void>(`/omniagent/memories/${memoryId}`)
  },
  listNotifications(unreadOnly = false, limit = 50) {
    return api.get<{ items: OmniAgentNotification[] }>('/omniagent/notifications', {
      params: { unread_only: unreadOnly, limit },
    })
  },
  markNotificationRead(notificationId: string) {
    return api.post<OmniAgentNotification>(`/omniagent/notifications/${notificationId}/read`)
  },
  listSchedules() {
    return api.get<{ items: OmniAgentSchedule[] }>('/omniagent/schedules')
  },
  pauseSchedule(scheduleId: string) {
    return api.post<OmniAgentSchedule>(`/omniagent/schedules/${scheduleId}/pause`)
  },
}

/** SSE 流的消费回调。每个事件一次调用，顺序与服务端下发一致。 */
export interface StreamHandlers {
  onEvent: (event: OmniAgentSseEvent) => void
}

// 一个 SSE 事件块的形状：event: <type>\ndata: <json>\n\n
// 只认 data 行（可多行，按协议用 \n 拼接）；event 行的类型冗余于 data.type，
// 以 data.type 为准，避免两处不一致时分叉。
function parseSseBlock(block: string): OmniAgentSseEvent | null {
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (dataLines.length === 0) return null
  const payload = dataLines.join('\n')
  if (!payload || payload === '[DONE]') return null
  try {
    return JSON.parse(payload) as OmniAgentSseEvent
  } catch {
    // 半个 JSON / 心跳注释等：跳过而非炸掉整个流。
    return null
  }
}

/**
 * 发一条消息并消费 SSE 流。调用方用 AbortSignal 实现「停止生成」——
 * abort 后本函数正常返回（不抛 AbortError），已 append 的增量保留，
 * 与后端把半截回复落库的行为对齐。
 */
export async function streamMessage(
  sessionId: string,
  body: SendMessageRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authFetch(`/omniagent/sessions/${sessionId}/messages/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  await consumeSse(res, handlers, signal)
}

/** 重跑某条 assistant 消息。语义与 streamMessage 相同，只是不新增 user 消息。 */
export async function retryMessage(
  sessionId: string,
  assistantMessageId: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authFetch(
    `/omniagent/sessions/${sessionId}/messages/${assistantMessageId}/retry`,
    {
      method: 'POST',
      headers: { Accept: 'text/event-stream' },
      signal,
    },
  )
  await consumeSse(res, handlers, signal)
}

async function consumeSse(
  res: Response,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  if (!res.ok) {
    // 错误响应是普通 JSON（FastAPI 的 {detail}），不是流。抛成 axios 形状，
    // 让 formatApiError 能照常分类出 status + hint。
    let detail: unknown = null
    try {
      detail = (await res.json())?.detail
    } catch {
      detail = res.statusText
    }
    throw { response: { status: res.status, data: { detail }, statusText: res.statusText } }
  }
  if (!res.body) throw new Error('响应没有可读流，浏览器可能不支持 ReadableStream')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // 事件以空行分隔；\r\n\r\n 兼容代理改写过行尾的情况。
      let sep = findSeparator(buffer)
      while (sep) {
        const block = buffer.slice(0, sep.index)
        buffer = buffer.slice(sep.index + sep.length)
        const event = parseSseBlock(block)
        if (event) handlers.onEvent(event)
        sep = findSeparator(buffer)
      }
    }
    // 流干净结束但末块没跟空行时，补一次解析。
    const tail = parseSseBlock(buffer)
    if (tail) handlers.onEvent(tail)
  } catch (err) {
    // 用户点「停止」→ reader.read() 抛 AbortError。这不是故障，静默收尾。
    if (signal?.aborted) return
    throw err
  } finally {
    reader.releaseLock()
  }
}

function findSeparator(buf: string): { index: number; length: number } | null {
  const lf = buf.indexOf('\n\n')
  const crlf = buf.indexOf('\r\n\r\n')
  if (lf === -1 && crlf === -1) return null
  if (crlf !== -1 && (lf === -1 || crlf < lf)) return { index: crlf, length: 4 }
  return { index: lf, length: 2 }
}
