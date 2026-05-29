// Unified frontend error normalization.
//
// Backend / network errors land in components shaped wildly differently
// (axios's err.response.data.detail, plain Error, raw fetch failures), and
// for dry-run / evaluator features we *also* get a 200-OK body containing
// a string field `error` describing an internal failure (truncated judge
// response, unmapped template variable, provider 401, etc).
//
// formatApiError() takes any of those, classifies them, and returns a
// NormalizedError with a short title, a direct message, and an optional
// hint telling the user what to do next ("raise max_tokens", "re-set the
// provider api key"). Components render this through <ErrorCard /> for a
// consistent red box, or just pull `.message` for inline text.
//
// Why centralized: previously every page hand-wrote
//   (err as {response?:{data?:{detail?:string}}})?.response?.data?.detail
//   || (err as Error)?.message || '请求失败'
// which loses status code, never adds hints, and silently swallows
// pydantic validation arrays. This module is the one place where we map
// raw error shapes → user-facing copy.

export type ErrorSeverity = 'error' | 'warning' | 'info'

export type ErrorCode =
  | 'unauthorized'        // HTTP 401
  | 'forbidden'           // HTTP 403
  | 'not_found'           // HTTP 404
  | 'rate_limited'        // HTTP 429
  | 'server_error'        // HTTP 5xx
  | 'bad_request'         // HTTP 4xx (400/422)
  | 'network'             // connection failure, DNS, ERR_NETWORK
  | 'timeout'             // ECONNABORTED / timeout
  | 'truncated'           // judge response cut at max_tokens
  | 'template_unmapped'   // {{Var}} appears but not in variable_mapping
  | 'json_parse'          // judge response not parseable JSON
  | 'provider_unauthorized' // judge provider HTTP 401
  | 'provider_rate_limited' // judge provider HTTP 429
  | 'unknown'

export interface NormalizedError {
  title: string
  message: string
  hint?: string
  severity: ErrorSeverity
  code: ErrorCode
  status?: number
  // raw is kept around so the UI can offer "show original" details for
  // debugging — never rendered by default.
  raw?: unknown
}

interface AxiosLikeError {
  isAxiosError?: boolean
  code?: string                       // 'ECONNABORTED', 'ERR_NETWORK', ...
  message?: string
  response?: {
    status?: number
    statusText?: string
    data?: unknown
  }
  request?: unknown                   // present when no response received
}

function asAxios(err: unknown): AxiosLikeError | null {
  if (!err || typeof err !== 'object') return null
  return err as AxiosLikeError
}

// FastAPI 的 detail 可能是 string、Pydantic 的 ValidationError 数组，
// 或者偶尔是一个对象。把它压成单行字符串。
function stringifyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts: string[] = []
    for (const it of detail) {
      if (typeof it === 'string') {
        parts.push(it)
        continue
      }
      if (it && typeof it === 'object') {
        const obj = it as { loc?: unknown[]; msg?: string; type?: string }
        const loc = Array.isArray(obj.loc)
          ? obj.loc.filter(x => typeof x === 'string' || typeof x === 'number').join('.')
          : ''
        if (loc && obj.msg) parts.push(`${loc}: ${obj.msg}`)
        else if (obj.msg) parts.push(obj.msg)
        else parts.push(JSON.stringify(it))
      }
    }
    return parts.join('; ') || '<empty>'
  }
  if (detail && typeof detail === 'object') {
    try {
      return JSON.stringify(detail)
    } catch {
      return String(detail)
    }
  }
  return ''
}

function pickHttpHint(status: number, message: string): { code: ErrorCode; hint?: string } {
  if (status === 401) {
    return {
      code: 'unauthorized',
      hint: '登录已过期或凭据无效，请重新登录。如果是评估器调用第三方模型，请检查 Provider 的 API Key。',
    }
  }
  if (status === 403) {
    return { code: 'forbidden', hint: '当前账号没有权限做这个操作。' }
  }
  if (status === 404) {
    return { code: 'not_found' }
  }
  if (status === 429) {
    return {
      code: 'rate_limited',
      hint: '触发限流，稍等几秒后再试。如果在跑大批样本，降低 concurrency。',
    }
  }
  if (status === 408 || /timeout/i.test(message)) {
    return { code: 'timeout', hint: '请求超时。检查网络或后端 timeout 设置。' }
  }
  if (status === 422 || status === 400) {
    return { code: 'bad_request' }
  }
  if (status >= 500) {
    return {
      code: 'server_error',
      hint: '后端报了 5xx，看 docker logs agent-eval-backend 找堆栈。',
    }
  }
  return { code: 'unknown' }
}

// 字符串中常见的 judge / provider 错误模式 → code + hint
function classifyMessage(message: string): { code: ErrorCode; hint?: string } | null {
  const m = message.toLowerCase()

  if (m.includes('truncated at max_tokens') || (m.includes('max_tokens') && m.includes('output_tokens='))) {
    return {
      code: 'truncated',
      hint: '调高评估器配置里的 max_tokens（推理模型 mimo / DeepSeek-R1 / QwQ 通常需要 ≥ 4096）。',
    }
  }
  if (m.includes('not in variable_mapping') || m.includes('appears in prompt but is not in variable_mapping')) {
    return {
      code: 'template_unmapped',
      hint: '在"变量映射"里给这个 {{变量}} 选一个数据源（input / output / expected_output / metadata.xxx）。',
    }
  }
  if (m.includes('not parseable json') || m.includes('no { ... } block found') || m.includes('returned empty content')) {
    return {
      code: 'json_parse',
      hint: '模型没返回 JSON。检查 output_prompt 是否要求严格 JSON 输出，或调高 max_tokens。',
    }
  }
  // judge_clients 抛的字符串形如 "openai_compatible: HTTP 401: ..."
  if (/http 401/i.test(message) || m.includes('unauthorized')) {
    return {
      code: 'provider_unauthorized',
      hint: 'Provider 的 API Key 无效或过期。去"评估器 Provider"页面重新填一遍。',
    }
  }
  if (/http 429/i.test(message)) {
    return {
      code: 'provider_rate_limited',
      hint: 'Provider 限流。等几秒重试，或降低并发。',
    }
  }
  // 后端把网络层重试包成 "<msg> (after N attempts)" 或
  // "openai_compatible: connection error after 3 attempts: ConnectError: ..."
  // 都映射到 network，并把"重试到底也没成功"这层信息塞进 hint 里。
  if (
    m.includes('connection error') ||
    m.includes('err_network') ||
    /\bafter\s+\d+\s+attempts?\b/.test(m) ||
    m.includes('all connection attempts failed') ||
    m.includes('connection refused') ||
    m.includes('connection reset')
  ) {
    return {
      code: 'network',
      hint: '后端已经自动重试过仍连不上目标服务。检查 agent / Provider 的 URL、DNS、网络可达性，或者 base_url 是否写错。',
    }
  }
  return null
}

export interface FormatErrorOptions {
  // 让 title 反映动作（"保存失败"/"试跑失败"），而不是固定 "请求失败"
  fallbackTitle?: string
  // 默认 message 的兜底文案
  fallbackMessage?: string
}

export function formatApiError(err: unknown, opts?: FormatErrorOptions): NormalizedError {
  const fallbackTitle = opts?.fallbackTitle ?? '请求失败'
  const fallbackMessage = opts?.fallbackMessage ?? '未知错误'

  // 直接传字符串（少数旧调用方）
  if (typeof err === 'string') {
    const cls = classifyMessage(err)
    return {
      title: fallbackTitle,
      message: err,
      hint: cls?.hint,
      severity: 'error',
      code: cls?.code ?? 'unknown',
      raw: err,
    }
  }

  const ax = asAxios(err)
  if (!ax) {
    return {
      title: fallbackTitle,
      message: fallbackMessage,
      severity: 'error',
      code: 'unknown',
      raw: err,
    }
  }

  // ── 网络层：根本没拿到 response ──
  if (ax.code === 'ECONNABORTED' || /timeout/i.test(ax.message ?? '')) {
    return {
      title: fallbackTitle,
      message: '请求超时，服务端没在规定时间内返回。',
      hint: '检查网络。如果是 LLM judge，调高 evaluator 的 timeout（或让 max_tokens 别太大）。',
      severity: 'error',
      code: 'timeout',
      raw: err,
    }
  }
  if (ax.code === 'ERR_NETWORK' || (ax.request && !ax.response)) {
    return {
      title: fallbackTitle,
      message: '连接失败，请求未到达服务器。',
      hint: '检查后端是否启动 / 反向代理是否健康 / hosts 是否能解析。',
      severity: 'error',
      code: 'network',
      raw: err,
    }
  }

  // ── HTTP 响应 ──
  if (ax.response) {
    const status = ax.response.status ?? 0
    const detailStr = stringifyDetail((ax.response.data as { detail?: unknown } | undefined)?.detail)
    const baseMsg = detailStr || ax.message || ax.response.statusText || fallbackMessage

    const fromMessage = classifyMessage(baseMsg)
    const fromStatus = pickHttpHint(status, baseMsg)
    const code = fromMessage?.code ?? fromStatus.code
    const hint = fromMessage?.hint ?? fromStatus.hint

    return {
      title: fallbackTitle,
      message: baseMsg,
      hint,
      severity: 'error',
      code,
      status,
      raw: err,
    }
  }

  // ── 其它（比如 throw new Error 的本地校验错） ──
  const msg = ax.message ?? fallbackMessage
  const cls = classifyMessage(msg)
  return {
    title: fallbackTitle,
    message: msg,
    hint: cls?.hint,
    severity: 'error',
    code: cls?.code ?? 'unknown',
    raw: err,
  }
}

// DryRunResponse.error 是 200 状态码下的内部错误字符串。同样跑一遍分类
// 给出 hint —— 单独函数是因为这里没有 axios 错误，只有一段 message。
export function formatDryRunError(error: string, opts?: FormatErrorOptions): NormalizedError {
  const cls = classifyMessage(error)
  return {
    title: opts?.fallbackTitle ?? '试跑失败',
    message: error,
    hint: cls?.hint,
    severity: 'error',
    code: cls?.code ?? 'unknown',
    raw: error,
  }
}

// 简短消息（toast 用），自动拼 hint。
export function toToastMessage(err: NormalizedError): string {
  if (err.hint) return `${err.message} — ${err.hint}`
  return err.message
}
