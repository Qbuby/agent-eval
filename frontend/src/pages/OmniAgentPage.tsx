import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Drawer, ErrorCard, LoadingBlock, useConfirm, useToast } from '@/components/ui'
import MarkdownView from '@/components/MarkdownView'
import OmniAgentProductPanel from '@/components/omniagent/OmniAgentProductPanel'
import { omniagentApi, retryMessage, streamMessage } from '@/services/omniagent'
import { formatApiError, toToastMessage, type NormalizedError } from '@/lib/errors'
import type {
  OmniAgentMessage,
  OmniAgentMessageStatus,
  OmniAgentSession,
  OmniAgentSseEvent,
  OmniAgentToolCall,
} from '@/types'

// ──────────────────────────────────────────────────────────────────────────
// OmniAgent 对话页：内部人员直接跟 OmniAgent 聊，边聊边观察工具调用与结构化
// 产物，为「哪些问题值得做成样例」积累一手感觉。
//
// 与平台其他页面的差异：唯一一处流式 UI。消息在流期间只存在于本地 state
// （messages），流结束后按服务端落库结果做一次校正（done.final_content +
// 一次历史 refetch 的合并）。react-query 只管两件慢数据：会话列表（分页，
// 「加载更多」）、切会话时的最新一页历史；流本身不进 query cache——每来一个
// token 都 setQueryData 会让整棵树重渲染。
//
// 布局：宽屏（lg+）左会话列表常驻 + 右对话区；窄屏折成单栏，会话列表收进
// 平台通用 Drawer。
// ──────────────────────────────────────────────────────────────────────────

const SESSIONS_KEY = ['omniagent-sessions']

/** 一页会话条数，与「加载更多」的步长一致。 */
const SESSION_PAGE_SIZE = 30

/** 一页消息条数；更早的历史按 before_sequence 游标往前翻。 */
const MESSAGE_PAGE_SIZE = 50

/** 空会话的占位标题，与后端 title 自动摘要前的默认值对齐。 */
const UNTITLED = '新对话'

export default function OmniAgentPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()

  const [activeId, setActiveId] = useState<string | null>(null)
  // 新建/切换会话后，React state 要到下一次 render 才对事件处理器可见。发送按钮可能
  // 在这段窗口内立刻被触发，故用 ref 同步保存最新会话，避免消息误发到旧会话。
  const activeIdRef = useRef<string | null>(null)
  const setActiveSession = useCallback((id: string | null) => {
    activeIdRef.current = id
    setActiveId(id)
  }, [])
  // 当前会话的消息。流期间由 SSE 事件增量改写，故不放 query cache。
  const [messages, setMessages] = useState<OmniAgentMessage[]>([])
  // 更早历史的游标：null 表示已翻到头。
  const [earlierCursor, setEarlierCursor] = useState<number | null>(null)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [creatingSession, setCreatingSession] = useState(false)
  const creatingSessionRef = useRef<Promise<string> | null>(null)
  const [sendError, setSendError] = useState<NormalizedError | null>(null)
  // 窄屏下会话抽屉是否展开（宽屏列表常驻，此 state 不参与）
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [productOpen, setProductOpen] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  // streaming 的镜像，供 effect 读当前值而不进依赖数组（避免因 streaming 变化
  // 重跑合并逻辑）。四处 setStreaming 都在相邻一行同步它，故不在渲染期赋值：
  // 渲染期写 ref 是副作用，被丢弃的渲染（StrictMode / 并发）会留下与已提交状态
  // 不符的值。
  const streamingRef = useRef(false)
  // handleSend 里「先建会话再发」时，乐观消息插入的目标会话 id。activeId 切到它
  // 时这批消息属于新会话，不能跟着旧会话的消息一起被清掉。
  const optimisticSessionRef = useRef<string | null>(null)
  // 当前会话的历史是否已灌入过一页。切会话时置回 false。
  const historySeededRef = useRef(false)
  // 本轮流是否收到过 message_start。它是「服务端有没有落库」的判据：后端在发出
  // 第一帧之前就已提交 user + assistant 两行（omniagent_chat.py 的 prepare），
  // 故收到过 message_start ⇒ 库里有对应行，refetch 能拿回来；没收到 ⇒ 这轮没落库。
  const startedRef = useRef(false)
  // 「加载更早」后用来把视口锚回原处的上一次 scrollHeight。
  const prependAnchorRef = useRef<number | null>(null)

  // ── 会话列表（分页 + 加载更多） ──
  const sessionsQuery = useInfiniteQuery({
    queryKey: SESSIONS_KEY,
    queryFn: ({ pageParam }) =>
      omniagentApi.listSessions(pageParam, SESSION_PAGE_SIZE).then(r => r.data),
    initialPageParam: 1,
    getNextPageParam: last => {
      // page/page_size/total 都是后端口径，按「已取回条数 < 总数」判断还有没有下一页。
      const loaded = last.page * last.page_size
      return loaded < last.total ? last.page + 1 : undefined
    },
  })

  const sessions = useMemo(
    () => sessionsQuery.data?.pages.flatMap(p => p.items) ?? [],
    [sessionsQuery.data],
  )
  const sessionTotal = sessionsQuery.data?.pages[0]?.total ?? sessions.length

  // 首次加载后自动选中最近一个会话，省一次点击。
  useEffect(() => {
    if (activeId) return
    const first = sessions[0]
    if (first) setActiveSession(first.id)
  }, [sessions, activeId])

  // ── 最新一页历史 ──
  const historyQuery = useQuery({
    queryKey: ['omniagent-messages', activeId],
    queryFn: () => omniagentApi.listMessages(activeId!, undefined, MESSAGE_PAGE_SIZE).then(r => r.data),
    enabled: !!activeId,
  })

  // 切会话：清空本地消息与游标，等 historyQuery 灌入。
  //
  // 「新建会话后立刻发送」这条路径要保住已插入的乐观消息：它属于将要切入的新会话，
  // 但此刻旧会话的消息还在数组里，两者得分开处理 —— 按 session_id 过滤而非整体
  // 跳过清空，否则旧会话的消息会赖在新会话的窗口里。
  useEffect(() => {
    const keepFor = optimisticSessionRef.current
    optimisticSessionRef.current = null
    // 新建会话的历史页必然是空的，没有更早消息可翻；标成已灌入，省得它回头改游标。
    // 否则（普通切会话）ref 必须同步置回：切到「已缓存」的会话时 historyQuery.data
    // 在同一次渲染就已就绪，下面的合并 effect 紧随本 effect 执行，若还留着上一会话
    // 的「已灌入」标记，就会漏掉本会话的 earlierCursor。
    historySeededRef.current = keepFor === activeId
    setMessages(prev =>
      keepFor === activeId ? prev.filter(m => m.session_id === activeId) : [],
    )
    setEarlierCursor(null)
    setSendError(null)
  }, [activeId])

  // 历史拉回后合并进本地：按 id 覆盖已有、追加新增，保留用户已翻出的更早消息。
  // 流进行中不合并：正在追加的增量比服务端快照新。
  useEffect(() => {
    const page = historyQuery.data
    if (!page || streamingRef.current) return
    const seeded = historySeededRef.current
    historySeededRef.current = true
    setMessages(prev => mergeServerPage(prev, page.items))
    // 只有首次灌入才认这一页的游标；已翻过更早历史时不能把游标退回去。
    if (!seeded) setEarlierCursor(page.next_before_sequence)
  }, [historyQuery.data])

  // 新消息 / 新增量到达时贴底。用户手动向上翻阅时不强拉回来。
  const atBottomRef = useRef(true)

  // 「加载更早」把内容插在顶部会把视口顶走，按 scrollHeight 增量补回去。
  // useLayoutEffect 先于下面的贴底 effect 跑，两者不打架（此时 atBottom 为 false）。
  useLayoutEffect(() => {
    const el = scrollRef.current
    const prevHeight = prependAnchorRef.current
    if (!el || prevHeight == null) return
    prependAnchorRef.current = null
    el.scrollTop += el.scrollHeight - prevHeight
  }, [messages])

  useEffect(() => {
    const el = scrollRef.current
    if (!el || !atBottomRef.current) return
    el.scrollTop = el.scrollHeight
  }, [messages])

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    // 24px 容差：贴底判定不必像素级严格。
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
  }

  async function handleLoadEarlier() {
    if (!activeId || earlierCursor == null || loadingEarlier) return
    const el = scrollRef.current
    setLoadingEarlier(true)
    atBottomRef.current = false
    try {
      const { data } = await omniagentApi.listMessages(activeId, earlierCursor, MESSAGE_PAGE_SIZE)
      if (el) prependAnchorRef.current = el.scrollHeight
      setMessages(prev => prependEarlier(prev, data.items))
      setEarlierCursor(data.next_before_sequence)
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '加载更早消息失败' })
      toast.error(toToastMessage(norm), '加载更早消息失败')
    } finally {
      setLoadingEarlier(false)
    }
  }

  // ── SSE 事件 → 本地消息树 ──
  // 所有分支都按 message_id 定位 assistant 消息；找不到就忽略该帧，
  // 而不是凭空造一条（后端 message_start 一定先到）。
  const applyEvent = useCallback((ev: OmniAgentSseEvent) => {
    setMessages(prev => {
      const patch = (id: string, fn: (m: OmniAgentMessage) => OmniAgentMessage) =>
        prev.map(m => (m.id === id ? fn(m) : m))

      switch (ev.type) {
        case 'message_start': {
          // 乐观插入的 user 占位换成真 id；本次新建的 assistant 占位换成服务端 id。
          // 重试时旧的失败消息原样留在列表里，被替换的只有新占位。
          startedRef.current = true
          return prev.map(m => {
            if (m.id === OPTIMISTIC_USER_ID && ev.user_message_id) {
              return { ...m, id: ev.user_message_id }
            }
            if (m.id === OPTIMISTIC_ASSISTANT_ID) {
              return { ...m, id: ev.message_id, status: 'streaming' as const }
            }
            return m
          })
        }
        case 'content_delta':
          return patch(ev.message_id, m => ({ ...m, content: m.content + ev.delta }))
        case 'tool_start':
          return patch(ev.message_id, m => ({
            ...m,
            tool_calls: [
              ...(m.tool_calls ?? []),
              { id: ev.tool_call_id, name: ev.name, input: ev.input },
            ],
          }))
        case 'tool_end':
          return patch(ev.message_id, m => ({
            ...m,
            tool_calls: (m.tool_calls ?? []).map(tc =>
              tc.id === ev.tool_call_id
                ? { ...tc, output: ev.output, error: ev.error, duration_ms: ev.duration_ms }
                : tc,
            ),
          }))
        case 'structured_output':
          return patch(ev.message_id, m => ({ ...m, structured_output: ev.data }))
        case 'error': {
          // 没带 message_id 时（连 message_start 都没成功）落到最后一条 assistant 上。
          const targetId = ev.message_id ?? lastAssistantId(prev)
          if (!targetId) return prev
          return patch(targetId, m => ({ ...m, status: 'failed' as const, error: ev.message }))
        }
        case 'done':
          return patch(ev.message_id, m => ({
            ...m,
            // final_content 是服务端落库的完整文本，用它校正增量拼接
            // （丢帧 / 半个 UTF-8 字符导致的偏差在这里被抹平）。
            content: ev.final_content ?? m.content,
            status: ev.status ?? ('completed' as const),
          }))
        default:
          return prev
      }
    })
  }, [])

  /** 流结束后的共用收尾：清 streaming 状态、刷会话列表（标题/计数变了）。 */
  const finishStream = useCallback(() => {
    setStreaming(false)
    streamingRef.current = false
    abortRef.current = null
    qc.invalidateQueries({ queryKey: SESSIONS_KEY })
  }, [qc])

  /**
   * 建会话并选中它，返回新会话 id。
   *
   * 单飞：创建期间的第二次调用复用同一个在途 Promise。「点新建」与「空会话下直接
   * 发送」是同一件事的两个入口，若各自 POST 一次，就会多出一个空会话，且发送很可能
   * 用的是先返回的那个 id —— 消息落进用户看不见的会话里。
   *
   * 新 id 一律记进 optimisticSessionRef：调用方可能紧接着往这个会话插乐观消息
   * （handleSend 的建会话路径），activeId 变更触发的清空要放它一马。不做成入参是
   * 因为单飞复用时第二个调用方的入参根本传不进来 —— 它拿到的是别人发起的 Promise。
   * 「点新建」这条路径没有乐观消息，按 session_id 过滤的结果与全清等价，无需分流。
   */
  const createAndSelectSession = useCallback((): Promise<string> => {
    const inflight = creatingSessionRef.current
    if (inflight) return inflight

    const promise = (async () => {
      try {
        const { data } = await omniagentApi.createSession()
        optimisticSessionRef.current = data.id
        setActiveSession(data.id)
        setEarlierCursor(null)
        qc.invalidateQueries({ queryKey: SESSIONS_KEY })
        return data.id
      } finally {
        creatingSessionRef.current = null
        setCreatingSession(false)
      }
    })()

    creatingSessionRef.current = promise
    setCreatingSession(true)
    return promise
  }, [qc, setActiveSession])

  async function handleSend() {
    const text = input.trim()
    if (!text || streaming) return

    // 没有会话时先建一个，避免用户为了发第一条消息还得先点「新建」。
    // 「新建」按钮的创建可能还在途中（用户点完立刻敲回车），createAndSelectSession
    // 会复用那次在途请求，绝不另建一个会话。
    let sessionId = activeIdRef.current
    if (!sessionId || creatingSessionRef.current) {
      try {
        sessionId = await createAndSelectSession()
      } catch (err) {
        const norm = formatApiError(err, { fallbackTitle: '新建会话失败' })
        setSendError(norm)
        toast.error(toToastMessage(norm), '新建会话失败')
        return
      }
    }

    setInput('')
    setSendError(null)
    setStreaming(true)
    streamingRef.current = true
    startedRef.current = false
    atBottomRef.current = true
    // 乐观插入 user + assistant 占位：等 message_start 回来才有真 id，
    // 但用户敲完回车就该立刻看到自己的话。
    setMessages(prev => {
      // 上一次「连 message_start 都没回来」的占位还留着时先清掉：哨兵 id 只有
      // 一份，留着会和本次的新占位撞 React key（且它本就没落库）。
      const base = dropOptimistic(prev)
      const seq = nextSequence(base)
      return [
        ...base,
        optimisticMessage(OPTIMISTIC_USER_ID, sessionId!, seq, 'user', text, 'completed'),
        optimisticMessage(OPTIMISTIC_ASSISTANT_ID, sessionId!, seq + 1, 'assistant', '', 'streaming'),
      ]
    })

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamMessage(sessionId, { content: text }, { onEvent: applyEvent }, controller.signal)
    } catch (err) {
      // 用户点「停止」且流还没建起来时，abort 是从 authFetch 本身抛出的
      // （consumeSse 里的静默收尾只覆盖读流阶段）。这不是故障：handleStop 已经
      // 把气泡落成 cancelled，不该再盖一张「发送失败」的错误卡。
      if (controller.signal.aborted) return
      const norm = formatApiError(err, { fallbackTitle: '发送失败' })
      setSendError(norm)
      if (startedRef.current) {
        // 流已经建起来又断了：两行都在库里，占位也已换成真 id。把停在
        // streaming 的那条落成 failed 收口（避免一直转圈），它带着真 id，
        // 因而能走「重新生成」。
        setMessages(prev =>
          prev.map(m =>
            m.status === 'streaming' ? { ...m, status: 'failed' as const, error: norm.message } : m,
          ),
        )
      } else {
        // 连 message_start 都没回来：服务端没落库，这一对占位在库里没有对应物，
        // 留着也重试不了（canRetryMessage 不认哨兵 id）。丢掉它们，并把刚清空的
        // 文本还回输入框——否则用户敲的那句话就这么没了，只能重打一遍。
        setMessages(dropOptimistic)
        setInput(cur => (cur.trim() ? cur : text))
      }
    } finally {
      finishStream()
      // 流结束后拉一次权威历史：真 id、落库内容、tool_calls 全部对齐。
      historyQuery.refetch()
    }
  }

  function handleStop() {
    abortRef.current?.abort()
    // 后端把已生成的半截回复落库为 cancelled；本地同步成同一状态。
    setMessages(prev =>
      prev.map(m => (m.status === 'streaming' ? { ...m, status: 'cancelled' as const } : m)),
    )
    setStreaming(false)
    streamingRef.current = false
  }

  async function handleRetry(assistantId: string) {
    const sessionId = activeIdRef.current
    if (streaming || !sessionId) return
    setSendError(null)
    setStreaming(true)
    streamingRef.current = true
    startedRef.current = false
    atBottomRef.current = true
    // 失败/已取消的那条原样留着（便于对比两次回答），紧随其后插一条新的
    // assistant 占位承接本次重试；message_start 只会替换这条新占位。
    setMessages(prev => {
      // 同上：先清掉上一次没落库的占位，哨兵 id 只能有一份。
      const base = dropOptimistic(prev)
      const idx = base.findIndex(m => m.id === assistantId)
      if (idx === -1) return base
      const placeholder = optimisticMessage(
        OPTIMISTIC_ASSISTANT_ID,
        sessionId,
        nextSequence(base),
        'assistant',
        '',
        'streaming',
      )
      placeholder.retry_of_message_id = assistantId
      return [...base.slice(0, idx + 1), placeholder, ...base.slice(idx + 1)]
    })

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await retryMessage(sessionId, assistantId, { onEvent: applyEvent }, controller.signal)
    } catch (err) {
      // 同 handleSend：流未建起时的 abort 由 authFetch 抛出，不是故障。
      if (controller.signal.aborted) return
      const norm = formatApiError(err, { fallbackTitle: '重试失败' })
      setSendError(norm)
      if (startedRef.current) {
        setMessages(prev =>
          prev.map(m =>
            m.status === 'streaming' ? { ...m, status: 'failed' as const, error: norm.message } : m,
          ),
        )
      } else {
        // 没落库：丢掉这条新占位。原来那条失败消息仍在列表里，重试入口也还在它
        // 身上，用户可以直接再点一次。这里没有输入框文本需要还原。
        setMessages(dropOptimistic)
      }
    } finally {
      finishStream()
      historyQuery.refetch()
    }
  }

  async function handleNewSession() {
    if (streaming) return
    try {
      await createAndSelectSession()
      setSidebarOpen(false)
      textareaRef.current?.focus()
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '新建会话失败' })
      toast.error(toToastMessage(norm), '新建会话失败')
    }
  }

  async function handleRename(session: OmniAgentSession) {
    const next = window.prompt('会话名称', session.title || UNTITLED)
    if (next == null) return
    const title = next.trim()
    if (!title || title === session.title) return
    try {
      await omniagentApi.updateSession(session.id, { title })
      qc.invalidateQueries({ queryKey: SESSIONS_KEY })
      toast.success('已重命名')
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '重命名失败' })
      toast.error(toToastMessage(norm), '重命名失败')
    }
  }

  async function handleDelete(session: OmniAgentSession) {
    // 后端是软删除：只给会话打 deleted_at，从列表里隐藏。消息记录仍在库里，
    // 上游 OmniAgent 的 checkpoint 也不动，别在文案里承诺「消息一并删除」。
    const ok = await confirm({
      title: '删除会话',
      description: `会话「${session.title || UNTITLED}」将从列表中隐藏（软删除），消息记录仍保留在服务端，不会被物理删除。`,
      confirmText: '删除',
      danger: true,
    })
    if (!ok) return
    try {
      await omniagentApi.deleteSession(session.id)
      qc.invalidateQueries({ queryKey: SESSIONS_KEY })
      if (activeId === session.id) setActiveSession(null)
      toast.success('会话已从列表移除')
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '删除失败' })
      toast.error(toToastMessage(norm), '删除失败')
    }
  }

  function handleSelect(id: string) {
    if (streaming) return
    setActiveSession(id)
    setSidebarOpen(false)
  }

  // 离页时掐断在跑的流，避免 setState on unmounted + 请求悬着。
  useEffect(() => () => abortRef.current?.abort(), [])

  // 视口涨到对应断点时侧栏已常驻，抽屉留着会盖住整页。
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)')
    const sync = () => {
      if (mq.matches) setSidebarOpen(false)
    }
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])

  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1280px)')
    const sync = () => {
      if (mq.matches) setProductOpen(false)
    }
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])

  const activeSession = useMemo(
    () => sessions.find(s => s.id === activeId) ?? null,
    [sessions, activeId],
  )

  const sessionList = (
    <SessionList
      sessions={sessions}
      total={sessionTotal}
      activeId={activeId}
      loading={sessionsQuery.isLoading}
      // 建会话期间不许切走：创建成功后会 setActiveSession 到新会话，
      // 用户中途选的那个会被它顶掉，白点一次。
      disabled={streaming || creatingSession}
      hasMore={!!sessionsQuery.hasNextPage}
      loadingMore={sessionsQuery.isFetchingNextPage}
      onLoadMore={() => sessionsQuery.fetchNextPage()}
      onSelect={handleSelect}
      onRename={handleRename}
      onDelete={handleDelete}
    />
  )

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">运行</div>
        <h1 className="page-title">OmniAgent 对话</h1>
        <p className="page-subtitle">
          直接与 OmniAgent 对话，观察工具调用与结构化输出；对话可留存复看，便于沉淀成评估样例
        </p>
      </header>

      {/* 窄屏工具条：宽屏（lg+）会话列表常驻，这条隐藏 */}
      <div className="mb-3 flex items-center gap-2 lg:hidden">
        <Button variant="secondary" size="sm" onClick={() => setSidebarOpen(true)}>
          会话（{sessionTotal}）
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={handleNewSession}
          loading={creatingSession}
          disabled={streaming}
        >
          新建对话
        </Button>
        <span className="ml-auto truncate text-[12px] text-text-tertiary">
          {activeSession?.title || UNTITLED}
        </span>
      </div>

      <div className="mb-3 hidden items-center justify-end lg:flex xl:hidden">
        <Button variant="secondary" size="sm" onClick={() => setProductOpen(true)}>
          运行状态
        </Button>
      </div>

      {/* 窄屏：会话列表收进平台通用抽屉 */}
      <Drawer open={sidebarOpen} onClose={() => setSidebarOpen(false)} title="会话">
        {sessionList}
      </Drawer>

      <Drawer open={productOpen} onClose={() => setProductOpen(false)} title="运行状态">
        <div className="-m-4 h-[calc(100vh-80px)]">
          <OmniAgentProductPanel sessionId={activeId} />
        </div>
      </Drawer>

      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[260px_minmax(0,1fr)_300px]">
        {/* 宽屏：会话列表常驻左栏 */}
        <aside className="hidden lg:block">
          <div className="section-row">
            <div className="page-eyebrow">会话</div>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleNewSession}
              loading={creatingSession}
              disabled={streaming}
            >
              新建
            </Button>
          </div>
          {sessionList}
        </aside>

        {/* 对话区 */}
        <section
          className="card flex min-w-0 flex-col"
          style={{ height: 'calc(100vh - 260px)', minHeight: 420 }}
        >
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="flex-1 space-y-4 overflow-y-auto px-4 py-4"
            aria-live="polite"
            aria-label="对话内容"
          >
            {/* 更早历史按 before_sequence 游标往前翻 */}
            {earlierCursor != null && (
              <div className="flex justify-center">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void handleLoadEarlier()}
                  loading={loadingEarlier}
                  disabled={loadingEarlier}
                >
                  加载更早消息
                </Button>
              </div>
            )}

            {historyQuery.isLoading && activeId ? (
              <LoadingBlock text="加载消息…" />
            ) : messages.length === 0 ? (
              <div className="empty-state">
                还没有消息。问点什么，比如让它查一台设备的报警码含义。
              </div>
            ) : (
              messages.map(m => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  onRetry={handleRetry}
                  retryDisabled={streaming}
                />
              ))
            )}
          </div>

          {sendError && (
            <div className="px-4 pb-2">
              <ErrorCard error={sendError} variant="compact" />
            </div>
          )}

          {/* 输入区 */}
          <div className="border-t border-separator p-3">
            <div className="flex items-end gap-2">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  // Enter 发送，Shift+Enter 换行——聊天窗的通行约定。
                  // 输入法组字期间的 Enter 是候选词确认，不能当发送（e.nativeEvent
                  // 的 isComposing 为 true）。
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault()
                    void handleSend()
                  }
                }}
                rows={2}
                disabled={streaming}
                placeholder={streaming ? '正在生成回复…' : '输入消息，Enter 发送，Shift+Enter 换行'}
                className="input min-h-[52px] flex-1 resize-y"
                aria-label="消息输入框"
              />
              {streaming ? (
                <Button variant="secondary" size="lg" onClick={handleStop}>
                  停止
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="lg"
                  onClick={() => void handleSend()}
                  disabled={!input.trim()}
                >
                  发送
                </Button>
              )}
            </div>
          </div>
        </section>

        <div className="hidden min-h-0 overflow-hidden border border-border xl:block" style={{ height: 'calc(100vh - 260px)', minHeight: 420 }}>
          <OmniAgentProductPanel sessionId={activeId} />
        </div>
      </div>
    </div>
  )
}

// ── 会话列表（宽屏左栏与窄屏抽屉共用一份） ──

function SessionList({
  sessions,
  total,
  activeId,
  loading,
  disabled,
  hasMore,
  loadingMore,
  onLoadMore,
  onSelect,
  onRename,
  onDelete,
}: {
  sessions: OmniAgentSession[]
  total: number
  activeId: string | null
  loading: boolean
  disabled: boolean
  hasMore: boolean
  loadingMore: boolean
  onLoadMore: () => void
  onSelect: (id: string) => void
  onRename: (session: OmniAgentSession) => void
  onDelete: (session: OmniAgentSession) => void
}) {
  if (loading) return <LoadingBlock text="加载会话…" />
  if (sessions.length === 0) {
    return <div className="empty-state">还没有会话。直接在输入框提问即可开始。</div>
  }

  return (
    <div>
      <ul className="flex flex-col gap-1">
        {sessions.map(s => (
          <li key={s.id}>
            <div
              className={`group flex items-center gap-1 rounded-md border px-2.5 py-2 transition-colors duration-150 ease-standard ${
                s.id === activeId
                  ? 'border-accent/40 bg-accent/10'
                  : 'border-transparent hover:bg-fill/5'
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(s.id)}
                disabled={disabled}
                className="min-w-0 flex-1 text-left disabled:cursor-not-allowed"
                aria-current={s.id === activeId ? 'true' : undefined}
              >
                <div className="truncate text-[12px] font-medium text-text-primary">
                  {s.title || UNTITLED}
                </div>
                <div className="mt-0.5 text-[10px] text-text-tertiary">
                  {s.message_count} 条 · {new Date(s.updated_at).toLocaleString()}
                </div>
              </button>
              <div className="flex shrink-0 gap-2 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                <button
                  type="button"
                  onClick={() => onRename(s)}
                  className="text-action"
                  aria-label={`重命名会话 ${s.title || UNTITLED}`}
                >
                  改名
                </button>
                <button
                  type="button"
                  onClick={() => onDelete(s)}
                  className="text-action-danger"
                  aria-label={`删除会话 ${s.title || UNTITLED}`}
                >
                  删除
                </button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-[10px] text-text-tertiary">
          {sessions.length} / {total}
        </span>
        {hasMore && (
          <Button variant="ghost" size="sm" onClick={onLoadMore} loading={loadingMore} disabled={loadingMore}>
            加载更多
          </Button>
        )}
      </div>
    </div>
  )
}

// ── 本地消息树的纯函数工具 ──

// 乐观插入占位消息用的哨兵 id。message_start 一到就换成服务端真 id；
// 用固定串而非随机值，是因为同一时刻最多只有一对占位在飞（同会话单飞）。
const OPTIMISTIC_USER_ID = '__optimistic_user__'
const OPTIMISTIC_ASSISTANT_ID = '__optimistic_assistant__'

function isOptimistic(id: string): boolean {
  return id === OPTIMISTIC_USER_ID || id === OPTIMISTIC_ASSISTANT_ID
}

/**
 * 丢掉仍带哨兵 id 的占位。走到这一步说明 message_start 没回来，服务端没落库，
 * 这两条在库里没有对应物：留着既撞下一次的 React key，也给不出重试入口
 * （canRetryMessage 不认哨兵 id）。失败原因由输入框上方的 ErrorCard 交代。
 */
function dropOptimistic(messages: OmniAgentMessage[]): OmniAgentMessage[] {
  return messages.filter(m => !isOptimistic(m.id))
}

function optimisticMessage(
  id: string,
  sessionId: string,
  sequence: number,
  role: 'user' | 'assistant',
  content: string,
  status: OmniAgentMessageStatus,
): OmniAgentMessage {
  return {
    id,
    session_id: sessionId,
    sequence,
    role,
    content,
    status,
    created_at: new Date().toISOString(),
  }
}

/** 占位消息的临时 sequence：排在现有最大之后，仅用于本地排序稳定性。 */
function nextSequence(messages: OmniAgentMessage[]): number {
  let max = 0
  for (const m of messages) {
    if (Number.isFinite(m.sequence) && m.sequence > max) max = m.sequence
  }
  return max + 1
}

/**
 * 把服务端一页（最新一页）合并进本地列表：
 * 同 id 用服务端版本覆盖，新增的追加到末尾，已翻出的更早消息保持不动。
 *
 * 占位一律丢掉：成功建流的占位在 message_start 时已换成真 id，走不到这里；
 * 还留着哨兵 id 说明这轮连 message_start 都没回来（服务端未落库），留着只会
 * 和权威数据重影。这种情况下用户的输入已由 handleSend 回填进输入框。
 */
function mergeServerPage(
  prev: OmniAgentMessage[],
  items: OmniAgentMessage[],
): OmniAgentMessage[] {
  const byId = new Map(items.map(m => [m.id, m]))
  const merged = dropOptimistic(prev).map(m => byId.get(m.id) ?? m)
  const seen = new Set(merged.map(m => m.id))
  for (const m of items) {
    if (!seen.has(m.id)) merged.push(m)
  }
  return merged
}

/** 更早的一页插到顶部，跳过已在列表里的（游标边界可能重叠一条）。 */
function prependEarlier(
  prev: OmniAgentMessage[],
  items: OmniAgentMessage[],
): OmniAgentMessage[] {
  const existing = new Set(prev.map(m => m.id))
  const older = items.filter(m => !existing.has(m.id))
  return [...older, ...prev]
}

function lastAssistantId(messages: OmniAgentMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'assistant') return messages[i].id
  }
  return null
}

// ── 渲染 ──

// completed / streaming 不挂徽标：正常态由内容本身表达，气泡里再堆一个
// 「完成」只是噪音。
const STATUS_BADGE: Partial<Record<OmniAgentMessageStatus, { cls: string; label: string }>> = {
  failed: { cls: 'badge badge-negative', label: '失败' },
  cancelled: { cls: 'badge badge-warning', label: '已取消' },
}

/** 只有失败与被取消的 assistant 消息可以重试；完成的不给重试入口。 */
function canRetryMessage(message: OmniAgentMessage): boolean {
  if (message.role !== 'assistant') return false
  if (isOptimistic(message.id)) return false
  return message.status === 'failed' || message.status === 'cancelled'
}

function MessageBubble({
  message,
  onRetry,
  retryDisabled,
}: {
  message: OmniAgentMessage
  onRetry: (assistantId: string) => void
  retryDisabled: boolean
}) {
  const isUser = message.role === 'user'
  const badge = STATUS_BADGE[message.status]
  // 流刚开始、还没有任何 token 时给一个「思考中」提示，避免空气泡。
  const pending = message.status === 'streaming' && !message.content
  const canRetry = canRetryMessage(message)

  return (
    <div className={isUser ? 'flex flex-col items-end' : 'flex flex-col items-start'}>
      <div
        className={`max-w-[85%] min-w-0 rounded-lg px-3 py-2 ${
          isUser ? 'border border-accent/20 bg-accent/10' : 'border border-border bg-fill/5'
        }`}
      >
        <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-text-tertiary">
          <span>{isUser ? '我' : 'OmniAgent'}</span>
          {badge && <span className={badge.cls}>{badge.label}</span>}
          {message.retry_of_message_id && <span className="badge badge-neutral">重试</span>}
        </div>

        {message.tool_calls && message.tool_calls.length > 0 && (
          <div className="mb-2 space-y-1">
            {message.tool_calls.map(tc => (
              <ToolCallRow key={tc.id} call={tc} />
            ))}
          </div>
        )}

        <div className="text-[12px] text-text-primary">
          {pending ? (
            <span className="text-text-tertiary">思考中…</span>
          ) : (
            <MarkdownView text={message.content} />
          )}
          {/* 流期间在文末挂一个光标，明确「还在写」 */}
          {message.status === 'streaming' && message.content && (
            <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-accent align-middle" />
          )}
        </div>

        {message.error && <div className="mt-1.5 text-[11px] text-negative">{message.error}</div>}

        {message.structured_output != null && (
          <details className="mt-2">
            <summary className="cursor-pointer text-[10px] text-text-tertiary hover:text-text-secondary">
              结构化输出
            </summary>
            <pre className="mt-1.5 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-2 p-2 font-mono text-[10px] text-text-secondary">
              {safeJson(message.structured_output)}
            </pre>
          </details>
        )}
      </div>

      {canRetry && (
        <button
          type="button"
          onClick={() => onRetry(message.id)}
          disabled={retryDisabled}
          className="text-action mt-1"
        >
          重新生成
        </button>
      )}
    </div>
  )
}

function ToolCallRow({ call }: { call: OmniAgentToolCall }) {
  // output 与 error 都没有 → tool_end 还没到，仍在执行。
  const running = call.output === undefined && !call.error
  return (
    <details className="rounded border border-border bg-surface px-2 py-1">
      <summary className="flex cursor-pointer items-center gap-1.5 text-[10px] text-text-secondary">
        <span className="font-mono">{call.name}</span>
        {running && <span className="badge badge-info">执行中</span>}
        {call.error && <span className="badge badge-negative">失败</span>}
        {!running && !call.error && <span className="badge badge-positive">完成</span>}
        {call.duration_ms != null && (
          <span className="text-text-tertiary">{call.duration_ms} ms</span>
        )}
      </summary>
      <div className="mt-1.5 space-y-1.5">
        <ToolPayload label="入参" value={call.input} />
        {call.error ? (
          <div className="text-[10px] text-negative">{call.error}</div>
        ) : (
          <ToolPayload label="结果" value={call.output} />
        )}
      </div>
    </details>
  )
}

function ToolPayload({ label, value }: { label: string; value: unknown }) {
  if (value === undefined) return null
  return (
    <div>
      <div className="text-[10px] text-text-tertiary">{label}</div>
      <pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-2 p-1.5 font-mono text-[10px] text-text-secondary">
        {safeJson(value)}
      </pre>
    </div>
  )
}

// 工具入参/结果是任意 JSON，可能含循环引用（少见但代价是整页崩）。
// 字符串原样展示，不套一层引号。
function safeJson(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
