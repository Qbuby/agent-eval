import MarkdownView from './MarkdownView'
import { attachmentSrc, contentAttachments, contentText } from '@/lib/contentBlocks'
import type { AttachmentBlock, MessageContent } from '@/lib/contentBlocks'

// ──────────────────────────────────────────────────────────────────────────
// 一条消息 content 的只读渲染，兼容两种形态：
//   - 字符串（存量样例的唯一形态）→ 行为与改造前完全一致，直接走 MarkdownView。
//   - canonical blocks 数组 → 文本块走 MarkdownView，附件块在下方排成缩略图。
// 图片用 attachmentSrc 走 /api/img-proxy（OSS 防盗链 + 内网可达，与
// MarkdownView 同策略）；document / video 不试图内联预览，给图标 + 文件名的
// 外链，评审者要看细节自己点开。
// ──────────────────────────────────────────────────────────────────────────

const TYPE_ICON: Record<string, string> = {
  image: '🖼️',
  document: '📄',
  video: '🎬',
}

function fileNameOf(a: AttachmentBlock): string {
  if (a.name) return a.name
  if (a.source.type === 'base64') return '内联文件'
  try {
    const path = new URL(a.source.url).pathname
    const base = path.split('/').filter(Boolean).pop()
    if (base) return decodeURIComponent(base)
  } catch {
    // 非法 URL（相对路径等）：退回原串尾段
    const base = a.source.url.split('?')[0].split('/').filter(Boolean).pop()
    if (base) return base
  }
  return '附件'
}

function AttachmentThumb({ item }: { item: AttachmentBlock }) {
  const src = attachmentSrc(item.source)
  const label = fileNameOf(item)
  // 原图/原文件的可点开地址：base64 用 data URL，外链用代理地址（直连大概率 403）
  const href = src

  if (item.type === 'image') {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        title={label}
        className="block h-20 w-20 overflow-hidden rounded border border-border bg-fill/10 hover:border-accent/50"
      >
        <img
          src={src}
          alt={label}
          loading="lazy"
          decoding="async"
          className="h-full w-full object-cover"
        />
      </a>
    )
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      title={label}
      className="flex h-20 w-20 flex-col items-center justify-center gap-1 rounded border border-border bg-fill/10 px-1 text-center hover:border-accent/50"
    >
      <span className="text-lg leading-none">{TYPE_ICON[item.type] ?? '📎'}</span>
      <span className="line-clamp-2 break-all text-[10px] text-text-tertiary">{label}</span>
    </a>
  )
}

/** 附件块列表的缩略图网格。无附件时不渲染任何东西。 */
export function AttachmentStrip({ content }: { content: MessageContent | undefined | null }) {
  const items = contentAttachments(content)
  if (items.length === 0) return null
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {items.map((item, i) => (
        <AttachmentThumb key={i} item={item} />
      ))}
    </div>
  )
}

export default function MessageContentView({
  content,
  className = '',
}: {
  content: MessageContent | undefined | null
  className?: string
}) {
  // 字符串形态：一个字都不改，走原来的 Markdown 路径。
  if (typeof content === 'string' || content == null) {
    return <MarkdownView text={content} className={className} />
  }
  const text = contentText(content)
  return (
    <div className={className}>
      {text && <MarkdownView text={text} />}
      <AttachmentStrip content={content} />
    </div>
  )
}
