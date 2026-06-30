import { useMemo, useState, type ImgHTMLAttributes } from 'react'
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
// 连续的 [citation:xxx] 一并吃掉（含其间水平空白），避免留下孤立空格。
// ⚠️ 只吃空格/制表符 [ \t]，绝不吃换行：citation 常紧跟在某行行尾（如标题、
// 列表项），若把后续 \n\n 一并吞掉，会让下一行的 ``` 代码块围栏被拼到上一行
// 行尾，退化成行内 code 标记，导致整篇 fence 配对错位、标题/表格/图片全部当
// 原始文本渲染（实测「中力CPD16pro」批次多条样例如此炸裂）。
const CITE_RE = /(?:\[citation:[^\]]+\][ \t]*)+/g

function preprocess(text: string): string {
  // 防御：调用方偶尔会误传非字符串（如未序列化的 JSON 对象），String() 兜底，
  // 避免 .replace 在对象上抛 TypeError 导致整页崩溃。
  if (!text) return ''
  if (typeof text !== 'string') text = String(text)
  let out = text.replace(VIDEO_RE, (_m, url) => `🎬 视频链接 [点击此处](${url})`)
  out = out.replace(CITE_RE, '')
  return out
}

// 图片渲染：外链图（阿里云 OSS / 内网图床）浏览器 <img> 直连取不到——
//   * OSS 配了 Referer 防盗链：浏览器自带 Referer → 403；
//   * 内网图床：浏览器所在网络解析/可达不稳定。
// 故把 src 改写成走后端代理 /api/img-proxy（服务端不带 Referer 拉图、部署环境
// 可达内网）。非 http(s) 的 src（如 data: URI）原样透传，不绕代理。
// 仍保留 onError 降级占位：代理也取不到（真 404 / 不在白名单）时告诉评审者
// 「这里本应有张图但没加载出来」，附原始 URL 便于排查。
function toProxyUrl(src: string): string {
  if (!/^https?:\/\//i.test(src)) return src
  return `/api/img-proxy?url=${encodeURIComponent(src)}`
}

function MarkdownImage(props: ImgHTMLAttributes<HTMLImageElement>) {
  const [failed, setFailed] = useState(false)
  const { src, alt, ...rest } = props
  const rawSrc = typeof src === 'string' ? src : undefined
  if (failed) {
    return (
      <span className="md-img-fallback" title={rawSrc}>
        🖼️ 图片未能加载{alt ? `：${alt}` : ''}
      </span>
    )
  }
  const proxied = rawSrc ? toProxyUrl(rawSrc) : rawSrc
  // eslint-disable-next-line jsx-a11y/alt-text
  return <img loading="lazy" decoding="async" {...rest} alt={alt} src={proxied} onError={() => setFailed(true)} />
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
          // 图片懒加载 + 加载失败降级占位（样式在 .markdown-body img / .md-img-fallback）
          img: (props) => <MarkdownImage {...props} />,
          // 外链新窗口打开
          a: (props) => <a target="_blank" rel="noopener noreferrer" {...props} />,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
