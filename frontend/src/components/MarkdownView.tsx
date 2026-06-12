import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// ──────────────────────────────────────────────────────────────────────────
// Portal 样例问题/答案的 Markdown 渲染。对齐 D:\file\files\EPtestcases\
// xlsx_to_html.py 的渲染效果：GFM 表格/代码/引用/标题/列表 + 图片，外加两个
// 文本预处理：
//   1. [视频:url] / [视频：url] -> 「🎬 视频链接 [点击此处](url)」(markdown 链接)
//   2. [citation:fileXXX_NN]   -> 直接移除。它们是检索用的【内部文件 id】，对
//      外部客户既无意义也无查看入口，渲染出来纯属噪音、打断阅读，故剥除。
// 样式走全局 .markdown-body（index.css，全部用项目设计 token，支持暗色）。
//
// ⚠️ 安全：答案是外部客户上传的内容，**不启用 rehype-raw / 原始 HTML 渲染**
// （react-markdown v9 默认即不渲染原始 HTML），避免存储型 XSS 打到查看反馈的
// 内部 admin。所以参考脚本里的 <sup> 上标改用安全的 markdown 等价物近似。
// ──────────────────────────────────────────────────────────────────────────

const VIDEO_RE = /\[视频[:：]\s*(https?:\/\/[^\]\s]+)\s*\]/g
// 连续的 [citation:xxx] 一并吃掉（含其间空白），避免留下孤立空格
const CITE_RE = /(?:\[citation:[^\]]+\]\s*)+/g

function preprocess(text: string): string {
  if (!text) return ''
  let out = text.replace(VIDEO_RE, (_m, url) => `🎬 视频链接 [点击此处](${url})`)
  out = out.replace(CITE_RE, '')
  return out
}

export default function MarkdownView({
  text,
  className = '',
}: {
  text: string | null | undefined
  className?: string
}) {
  const source = useMemo(() => preprocess(text ?? ''), [text])

  if (!source.trim()) {
    return <span className="text-text-tertiary">—</span>
  }

  return (
    <div className={`markdown-body ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // 图片懒加载（样式在 .markdown-body img）
          // eslint-disable-next-line jsx-a11y/alt-text
          img: (props) => <img loading="lazy" decoding="async" {...props} />,
          // 外链新窗口打开
          a: (props) => <a target="_blank" rel="noopener noreferrer" {...props} />,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
