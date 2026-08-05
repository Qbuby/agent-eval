// ──────────────────────────────────────────────────────────────────────────
// 消息 content 的多模态形态（前端侧）。
//
// 后端 `agent_eval/data/content_blocks.py` 把样例的 `input_messages[*].content`
// 从「恒为字符串」放宽成「字符串 或 canonical blocks 数组」：
//   [{"type": "text", "text": "这张图里的报警码是什么？"},
//    {"type": "image", "source": {"type": "url", "url": "https://.../a.png"}}]
// 无附件时后端会把数组降级回字符串，故纯文本样例在前端与改造前逐字节一致。
//
// 本模块是那份 Python 的前端对偶，只做三件事：
//   - `contentToText`：纯文本投影（附件渲染成 [图片]/[文档]/[视频] 占位），
//     与后端 `content_to_text` 逐字对齐，供列表预览、搜索、导出、长度截断等
//     只认字符串的既有消费点使用。
//   - `contentAttachments`：取出附件块，供 UI 渲染缩略图。
//   - `buildContent` / `attachmentInputs`：录入侧在「文本 + 图片 URL 列表」与
//     canonical content 之间来回转换。
//
// 这里**不做校验**：合法性由后端 `normalize_content` 统一裁决（前端重复一套
// 规则只会两边漂移）。解析不了的块在展示路径上跳过而非抛错。
// ──────────────────────────────────────────────────────────────────────────

export type AttachmentType = 'image' | 'document' | 'video'

// canonical source：外链 URL，或 base64 内联（含 data URL 拆出来的那种）。
export type ContentSource =
  | { type: 'url'; url: string }
  | { type: 'base64'; media_type: string; data: string }

export interface TextBlock {
  type: 'text'
  text: string
}

export interface AttachmentBlock {
  type: AttachmentType
  source: ContentSource
  // 可选展示用文件名，不参与 agent payload 语义。
  name?: string
}

export type ContentBlock = TextBlock | AttachmentBlock

// 消息 content 的两种合法形态。
export type MessageContent = string | ContentBlock[]

const ATTACHMENT_TYPES: readonly string[] = ['image', 'document', 'video']

const TEXT_LABELS: Record<AttachmentType, string> = {
  image: '[图片]',
  document: '[文档]',
  video: '[视频]',
}

const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']
const DOC_EXTS = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.csv', '.txt']
const VIDEO_EXTS = ['.mp4', '.mov', '.avi', '.mkv', '.webm']

const DATA_URL_RE = /^data:([\w.+-]+\/[\w.+-]+);base64,(.+)$/i

/**
 * 按 URL 扩展名猜附件类型；猜不出按 image 处理（与后端 `guess_attachment_type`
 * 一致：带图是主场景，猜错只影响图标，落沙箱时后端按 magic bytes 重判）。
 */
export function guessAttachmentType(url: string): AttachmentType {
  const path = url.split('?')[0].split('#')[0].toLowerCase()
  if (VIDEO_EXTS.some(e => path.endsWith(e))) return 'video'
  if (DOC_EXTS.some(e => path.endsWith(e))) return 'document'
  if (IMAGE_EXTS.some(e => path.endsWith(e))) return 'image'
  return 'image'
}

function normalizeSource(block: Record<string, unknown>): ContentSource | null {
  // canonical：{source: {type: 'url'|'base64', ...}}
  const source = block.source
  if (source && typeof source === 'object' && !Array.isArray(source)) {
    const s = source as Record<string, unknown>
    if (s.type === 'url') {
      const url = String(s.url ?? '').trim()
      if (!url) return null
      // data URL 写在 type='url' 里也要拆成 base64（下游按 HTTP 地址去拉拉不动）
      const m = DATA_URL_RE.exec(url)
      if (m) return { type: 'base64', media_type: m[1], data: m[2] }
      return { type: 'url', url }
    }
    if (s.type === 'base64') {
      const data = String(s.data ?? '')
      if (!data) return null
      return { type: 'base64', media_type: String(s.media_type ?? 'image/png'), data }
    }
  }
  // 扁平写法 {url: ...} 与 OpenAI 写法 {image_url: {url: ...}}
  let rawUrl = block.url
  if (typeof rawUrl !== 'string') {
    const imageUrl = block.image_url
    if (imageUrl && typeof imageUrl === 'object' && !Array.isArray(imageUrl)) {
      rawUrl = (imageUrl as Record<string, unknown>).url
    } else if (typeof imageUrl === 'string') {
      rawUrl = imageUrl
    }
  }
  if (typeof rawUrl === 'string' && rawUrl.trim()) {
    const url = rawUrl.trim()
    const m = DATA_URL_RE.exec(url)
    if (m) return { type: 'base64', media_type: m[1], data: m[2] }
    return { type: 'url', url }
  }
  return null
}

/**
 * content 的纯文本投影。附件渲染成 `[图片]` / `[文档]` / `[视频]` 占位（带
 * name 时后接 `(name)`），与后端 `content_to_text` 逐字对齐——两侧措辞漂移会
 * 让「后端落库的 question 快照」与「前端列表预览」显示不同的文本。
 */
export function contentToText(content: unknown): string {
  if (content === null || content === undefined) return ''
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return String(content)

  const parts: string[] = []
  for (const block of content) {
    if (typeof block === 'string') {
      parts.push(block)
      continue
    }
    if (!block || typeof block !== 'object') continue
    const b = block as Record<string, unknown>
    const btype = String(b.type ?? '')
    if (btype === 'text') {
      const text = b.text
      if (text) parts.push(String(text))
    } else if (btype === 'image' || btype === 'document' || btype === 'video') {
      const name = b.name ? String(b.name) : ''
      parts.push(`${TEXT_LABELS[btype]}${name ? `(${name})` : ''}`)
    } else if (btype === 'image_url') {
      parts.push(TEXT_LABELS.image)
    }
  }
  return parts.filter(Boolean).join('\n')
}

/**
 * 取出 content 里的附件块（已收敛成 canonical 形状）。非数组恒返回空数组，
 * 解析不了的块跳过——展示路径不做校验。
 */
export function contentAttachments(content: unknown): AttachmentBlock[] {
  if (!Array.isArray(content)) return []
  const out: AttachmentBlock[] = []
  for (const block of content) {
    if (!block || typeof block !== 'object' || Array.isArray(block)) continue
    const b = block as Record<string, unknown>
    let btype = String(b.type ?? '')
    if (btype === 'image_url') btype = 'image'
    if (!ATTACHMENT_TYPES.includes(btype)) continue
    const source = normalizeSource(b)
    if (!source) continue
    const item: AttachmentBlock = { type: btype as AttachmentType, source }
    if (b.name) item.name = String(b.name)
    out.push(item)
  }
  return out
}

/** content 是否带附件。 */
export function hasAttachments(content: unknown): boolean {
  return contentAttachments(content).length > 0
}

/** 整段消息列表里是否有任一条带附件。 */
export function messagesHaveAttachments(
  messages: Array<{ content?: unknown }> | null | undefined,
): boolean {
  return (messages ?? []).some(m => hasAttachments(m?.content))
}

/**
 * 把附件 source 还原成浏览器可直接放进 `<img src>` 的地址。
 *
 * 外链走后端代理 `/api/img-proxy`：OSS 配了 Referer 防盗链（浏览器自带
 * Referer → 403），内网图床浏览器侧也未必可达。base64 拼回 data URL 直接用。
 * 与 MarkdownView 的 `toProxyUrl` 同一策略。
 */
export function attachmentSrc(source: ContentSource): string {
  if (source.type === 'base64') {
    return `data:${source.media_type};base64,${source.data}`
  }
  if (!/^https?:\/\//i.test(source.url)) return source.url
  return `/api/img-proxy?url=${encodeURIComponent(source.url)}`
}

// ── 录入侧：canonical content ⇄ {文本, 附件 URL 列表} ────────────────────

/** 从 content 取出纯文本部分（不含附件占位），供编辑器文本域回填。 */
export function contentText(content: unknown): string {
  if (content === null || content === undefined) return ''
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return String(content)
  const parts: string[] = []
  for (const block of content) {
    if (typeof block === 'string') {
      parts.push(block)
      continue
    }
    if (!block || typeof block !== 'object') continue
    const b = block as Record<string, unknown>
    if (String(b.type ?? '') === 'text' && b.text) parts.push(String(b.text))
  }
  return parts.join('\n')
}

/**
 * 从 content 取出附件的可编辑表示：URL 字符串列表。
 * base64 内联附件还原成 data URL，保证「编辑不丢图」。
 */
export function attachmentInputs(content: unknown): string[] {
  return contentAttachments(content).map(a =>
    a.source.type === 'url'
      ? a.source.url
      : `data:${a.source.media_type};base64,${a.source.data}`,
  )
}

/**
 * 把「文本 + 附件 URL 列表」组装回 content。
 *
 * 无附件时返回**字符串**而非单元素数组：与后端 `normalize_content` 的降级
 * 行为一致，纯文本样例的 payload 与改造前逐字节相同，存量数据零影响。
 */
export function buildContent(text: string, urls: string[]): MessageContent {
  const cleaned = urls.map(u => u.trim()).filter(Boolean)
  if (cleaned.length === 0) return text
  const blocks: ContentBlock[] = []
  if (text) blocks.push({ type: 'text', text })
  for (const url of cleaned) {
    const m = DATA_URL_RE.exec(url)
    if (m) {
      // data URL 的类型按 media_type 前缀判，避免 image/* 之外的内联被当图片。
      const mediaType = m[1].toLowerCase()
      const btype: AttachmentType = mediaType.startsWith('video/')
        ? 'video'
        : mediaType.startsWith('image/')
          ? 'image'
          : 'document'
      blocks.push({ type: btype, source: { type: 'base64', media_type: m[1], data: m[2] } })
    } else {
      blocks.push({ type: guessAttachmentType(url), source: { type: 'url', url } })
    }
  }
  return blocks
}
