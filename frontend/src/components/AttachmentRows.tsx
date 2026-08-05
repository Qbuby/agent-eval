import { useRef, useState } from 'react'
import { AttachmentStrip } from './MessageContentView'
import type { MessageContent } from '@/lib/contentBlocks'

// ──────────────────────────────────────────────────────────────────────────
// 附件录入行：单轮 QuestionContentEditor 与多轮 ConversationEditor 共用。
//
// 真实录入路径有三种，这里都收敛成同一个「附件行 = 一个 URL 或 data URL 串」
// 的表示，最终由调用方的 buildContent 组装进 canonical content：
//   1. 本地选文件（主路径）：读成 data URL 塞进新行，后端 normalize_content
//      会把 data URL 拆成 base64 source，无需任何上传接口。
//   2. 粘贴外链 URL：展开「填写链接」后手输。
//   3. 直接粘 data:...;base64 串：同上，兼容既有写法与导出回填。
//
// 为什么不做上传接口：图片以 base64 存在样例的 question_content / content
// JSONB 里（与库同生命周期，备份天然一致），链路上没有对象存储，也不需要。
// 代价是单图体积会膨胀 ~33%，故这里做体积门禁，超限直接拒绝并提示。
// ──────────────────────────────────────────────────────────────────────────

/** 单文件体积上限。base64 后约 6.7MB，进 JSONB 尚可接受。 */
const MAX_FILE_BYTES = 5 * 1024 * 1024

const ACCEPT = 'image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.mp4,.mov,.webm'

/** 读成 data URL：`buildContent` 会据 media_type 拆成 base64 source。 */
function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error ?? new Error('读取文件失败'))
    reader.readAsDataURL(file)
  })
}

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`
  return `${Math.max(1, Math.round(bytes / 1024))}KB`
}

/** data URL 行只显示文件名/类型摘要，避免几 MB 的 base64 撑爆输入框。 */
function summarizeDataUrl(url: string): string {
  const m = /^data:([\w.+-]+\/[\w.+-]+);base64,(.*)$/is.exec(url)
  if (!m) return '本地文件'
  // base64 长度 → 原始字节数（忽略 padding 的两字节误差，展示够用）。
  const bytes = Math.floor((m[2].length * 3) / 4)
  return `${m[1]}，${fmtSize(bytes)}`
}

export default function AttachmentRows({
  rows,
  onChange,
  content,
  label = '附件（图片 / 文档 / 视频，可选）',
  onError,
  idPrefix,
}: {
  rows: string[]
  /** 覆写整组附件行。调用方据此重组 content。 */
  onChange: (next: string[]) => void
  /** 当前 content，用于缩略预览。 */
  content: MessageContent
  label?: string
  /** 体积超限等录入错误的提示回调（通常接 toast）。 */
  onError?: (message: string) => void
  /** 多轮场景下每条消息一个 file input，需要区分 id。 */
  idPrefix?: string
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  // 「填写链接」是次要路径，默认收起，避免又把 URL 输入框推到用户面前。
  const [showUrlInput, setShowUrlInput] = useState(false)

  async function pickFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    const added: string[] = []
    for (const file of Array.from(files)) {
      if (file.size > MAX_FILE_BYTES) {
        onError?.(`${file.name} 有 ${fmtSize(file.size)}，超过单文件 ${fmtSize(MAX_FILE_BYTES)} 上限`)
        continue
      }
      try {
        added.push(await readAsDataUrl(file))
      } catch {
        onError?.(`${file.name} 读取失败`)
      }
    }
    if (added.length) {
      // 已有的空白行（点了「填写链接」还没填的）顺手清掉，避免留空壳。
      onChange([...rows.filter(r => r.trim()), ...added])
    }
    // 清空 input，使同一个文件能被再次选中。
    if (fileRef.current) fileRef.current.value = ''
  }

  return (
    <div className="mt-1.5">
      <div className="flex items-center justify-between">
        <label className="field-label text-[11px] mb-0">{label}</label>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="text-action text-[11px]"
          >
            + 选择文件
          </button>
          <button
            type="button"
            onClick={() => {
              setShowUrlInput(true)
              onChange([...rows, ''])
            }}
            className="text-[11px] text-text-tertiary hover:text-text-primary"
            title="粘贴图片外链，或 data:image/png;base64,… 串"
          >
            填写链接
          </button>
        </div>
      </div>
      <input
        ref={fileRef}
        id={idPrefix ? `${idPrefix}-file` : undefined}
        type="file"
        multiple
        accept={ACCEPT}
        onChange={e => void pickFiles(e.target.files)}
        className="hidden"
      />
      {rows.length > 0 && (
        <div className="mt-1 space-y-1">
          {rows.map((url, ri) => {
            const isLocal = /^data:/i.test(url)
            return (
              <div key={ri} className="flex items-center gap-1.5">
                {isLocal ? (
                  // 本地文件：只显示摘要。几 MB 的 base64 放进 input 会卡输入法。
                  <span className="input text-[12px] flex-1 truncate text-text-tertiary">
                    本地文件（{summarizeDataUrl(url)}）
                  </span>
                ) : (
                  <input
                    value={url}
                    onChange={e => onChange(rows.map((u, k) => (k === ri ? e.target.value : u)))}
                    placeholder="https://…/a.png 或 data:image/png;base64,…"
                    className="input text-[12px] flex-1"
                  />
                )}
                <button
                  type="button"
                  onClick={() => onChange(rows.filter((_, k) => k !== ri))}
                  className="text-[11px] text-action-danger shrink-0"
                >
                  移除
                </button>
              </div>
            )
          })}
        </div>
      )}
      {rows.length === 0 && !showUrlInput && (
        <p className="text-[11px] text-text-tertiary mt-1">
          可直接选择本地图片，Excel 导入时嵌在单元格里的图片也会自动带上。
        </p>
      )}
      <AttachmentStrip content={content} />
    </div>
  )
}
