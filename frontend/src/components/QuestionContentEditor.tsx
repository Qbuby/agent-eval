import { useState } from 'react'
import {
  attachmentInputs,
  buildContent,
  contentText,
} from '@/lib/contentBlocks'
import type { MessageContent } from '@/lib/contentBlocks'
import AttachmentRows from './AttachmentRows'
import { useToast } from './ui'

// ──────────────────────────────────────────────────────────────────────────
// 单轮样例「问题」的录入控件：文本域 + 附件行（本地选文件 / 链接）+ 缩略预览。
//
// 对外只暴露 canonical content（字符串 或 blocks 数组），与多轮的
// ConversationEditor 用同一套 contentBlocks helper：文本读 contentText、
// 附件读 attachmentInputs，任一侧改动都用 buildContent 重组整个 content。
// 无附件时 buildContent 回落成字符串，故纯文本样例提交的 payload 与改造前
// 逐字节相同。
//
// 空附件行（用户点了「+ 附件」还没填）在 buildContent 里会被滤掉，从 value
// 派生不回来，故内部另存一份可见行 draft。切换样例时请由调用方给本组件传
// key（如 key={case.id}）以重置 draft。
// ──────────────────────────────────────────────────────────────────────────

export default function QuestionContentEditor({
  value,
  onChange,
  textareaId,
  rows = 3,
  placeholder = '输入测试问题…',
}: {
  value: MessageContent
  onChange: (next: MessageContent) => void
  textareaId?: string
  rows?: number
  placeholder?: string
}) {
  const [attachDraft, setAttachDraft] = useState<string[] | null>(null)
  const attachRows = attachDraft ?? attachmentInputs(value)
  const toast = useToast()

  function setAttachRows(next: string[]) {
    setAttachDraft(next)
    onChange(buildContent(contentText(value), next))
  }

  function updateText(text: string) {
    onChange(buildContent(text, attachRows))
  }

  return (
    <div>
      <textarea
        id={textareaId}
        value={contentText(value)}
        onChange={e => updateText(e.target.value)}
        rows={rows}
        placeholder={placeholder}
        className="input resize-y"
      />
      <AttachmentRows
        rows={attachRows}
        onChange={setAttachRows}
        content={value}
        onError={msg => toast.error(msg)}
        idPrefix={textareaId ? `${textareaId}-attach` : undefined}
      />
    </div>
  )
}

/** 问题是否算填了：文本非空 或 带附件（纯图样例文本可为空）。 */
export function questionFilled(value: MessageContent): boolean {
  if (contentText(value).trim()) return true
  return Array.isArray(value) && attachmentInputs(value).some(u => u.trim())
}
