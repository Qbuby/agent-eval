import { useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, useToast } from '@/components/ui'
import MarkdownView from '@/components/MarkdownView'
import {
  portalApi,
  SCORE_DIMENSIONS,
  type PortalSample,
  type FeedbackPayload,
} from '@/services/portal'
import { formatApiError, toToastMessage } from '@/lib/errors'

// ──────────────────────────────────────────────────────────────────────────
// 客户评审页：左侧样例导航（编号 + 问题摘要 + 已评状态 + 搜索）+ 右侧选中样例
// 详情（问题 / 答案 Markdown 渲染）+ 常驻打分区。
//
// 形态参考 D:\file\files\EPtestcases\xlsx_to_html.py（导航 + 单卡），但保留逐条
// 打分与意见反馈。性能：详情区只渲染「当前选中」那一条的长答案，导航只用列表
// 轻量字段，避免一次性渲染整页 N 个长答案。
// ──────────────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50 // 导航一页装更多条（只渲染摘要，开销低），减少翻页

// 1-5 星形打分控件（也用于维度分）。0/null = 未评。
function StarRating({
  value,
  onChange,
  ariaLabel,
}: {
  value: number | null
  onChange: (v: number) => void
  ariaLabel: string
}) {
  return (
    <div className="inline-flex items-center gap-0.5" role="radiogroup" aria-label={ariaLabel}>
      {[1, 2, 3, 4, 5].map((n) => {
        const active = value != null && n <= value
        return (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            aria-label={`${n} 分`}
            onClick={() => onChange(n)}
            className={`text-[18px] leading-none transition-colors ${
              active ? 'text-warning' : 'text-text-tertiary hover:text-text-secondary'
            }`}
          >
            {active ? '★' : '☆'}
          </button>
        )
      })}
      {value != null && (
        <span className="ml-1.5 text-[11px] tabular-nums text-text-tertiary">{value}/5</span>
      )}
    </div>
  )
}

function hasFeedback(s: PortalSample): boolean {
  const f = s.feedback
  return f != null && (f.overall != null || (f.comment != null && f.comment.trim() !== ''))
}

// 右侧详情 + 打分。state 局部维护，初值来自已提交 feedback；提交走 upsert。
// key 绑定 sample.id，切换样例时组件重建，天然重置表单。
function SampleDetail({ sample }: { sample: PortalSample }) {
  const queryClient = useQueryClient()
  const toast = useToast()

  const initial = sample.feedback
  const [overall, setOverall] = useState<number | null>(initial?.overall ?? null)
  const [scores, setScores] = useState<Record<string, number>>(initial?.scores ?? {})
  const [comment, setComment] = useState(initial?.comment ?? '')

  const submitMutation = useMutation({
    mutationFn: (data: FeedbackPayload) => portalApi.submitFeedback(sample.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['portal-samples'] })
      toast.success('反馈已保存')
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '保存失败' })
      toast.error(toToastMessage(norm), '保存失败')
    },
  })

  const extraEntries = sample.extra
    ? Object.entries(sample.extra).filter(([, v]) => v != null && String(v).trim() !== '')
    : []

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* 滚动区：问题 + 答案 */}
      <div className="flex-1 min-h-0 overflow-y-auto pr-1">
        <div className="flex items-center gap-2 mb-3">
          <span className="badge badge-accent shrink-0">#{sample.row_index + 1}</span>
          {hasFeedback(sample) && <span className="badge badge-positive shrink-0">已评</span>}
        </div>

        <div className="mb-5">
          <div className="field-label">问题</div>
          <div className="text-[14px] leading-relaxed text-text-primary font-medium">
            <MarkdownView text={sample.question} />
          </div>
        </div>

        <div className="mb-5">
          <div className="field-label">答案</div>
          <MarkdownView text={sample.answer} />
        </div>

        {extraEntries.length > 0 && (
          <details className="text-[12px] mb-2">
            <summary className="cursor-pointer text-text-tertiary hover:text-text-secondary">
              附加字段（{extraEntries.length}）
            </summary>
            <dl className="mt-2 space-y-1">
              {extraEntries.map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <dt className="text-text-tertiary shrink-0 min-w-[80px]">{k}</dt>
                  <dd className="text-text-secondary break-words whitespace-pre-wrap">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>

      {/* 常驻打分区：固定在详情区底部，答案再长也不用滚到底 */}
      <div className="shrink-0 border-t border-border bg-surface pt-4 mt-2">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
          <div className="flex items-center gap-3">
            <span className="field-label !mb-0 min-w-[64px]">总体评分</span>
            <StarRating value={overall} onChange={setOverall} ariaLabel="总体评分" />
          </div>
          {SCORE_DIMENSIONS.map((dim) => (
            <div key={dim.key} className="flex items-center gap-3">
              <span className="field-label !mb-0 min-w-[64px]">{dim.label}</span>
              <StarRating
                value={scores[dim.key] ?? null}
                onChange={(v) => setScores((prev) => ({ ...prev, [dim.key]: v }))}
                ariaLabel={dim.label}
              />
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-end gap-3">
          <div className="flex-1">
            <label className="field-label">意见反馈</label>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="对该样例的质量、问题或改进建议（可选）"
              rows={2}
              className="input w-full resize-y"
            />
          </div>
          <Button
            variant="primary"
            size="md"
            loading={submitMutation.isPending}
            onClick={() =>
              submitMutation.mutate({
                overall,
                scores,
                comment: comment.trim() || null,
              })
            }
          >
            保存反馈
          </Button>
        </div>
      </div>
    </div>
  )
}

export default function PortalBatchDetailPage() {
  const { batchId } = useParams<{ batchId: string }>()
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['portal-samples', batchId, page],
    queryFn: () =>
      portalApi.listSamples(batchId!, { page, page_size: PAGE_SIZE }).then((r) => r.data),
    enabled: !!batchId,
    placeholderData: (prev) => prev,
  })

  const items = useMemo(() => data?.items ?? [], [data])
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const reviewedCount = items.filter(hasFeedback).length

  // 默认选中当前页第一条；翻页或首次加载后纠正选中态
  useEffect(() => {
    if (items.length === 0) {
      setSelectedId(null)
    } else if (!items.some((s) => s.id === selectedId)) {
      setSelectedId(items[0].id)
    }
  }, [items, selectedId])

  const selected = items.find((s) => s.id === selectedId) ?? null

  const filteredItems = useMemo(() => {
    const kw = search.trim().toLowerCase()
    if (!kw) return items
    return items.filter(
      (s) =>
        String(s.row_index + 1).includes(kw) ||
        (s.question ?? '').toLowerCase().includes(kw),
    )
  }, [items, search])

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)]">
      <header className="mb-4 shrink-0">
        <Link to="/portal" className="back-link">
          ← 返回批次列表
        </Link>
        <h1 className="page-title mt-2">样例评审</h1>
        <p className="page-subtitle">
          共 {total} 条 · 本页已评 {reviewedCount}/{items.length} · 逐条打分（1-5）并填写意见
          {isFetching && !isLoading && <span className="ml-2 text-text-tertiary">刷新中…</span>}
        </p>
      </header>

      {isLoading ? (
        <div className="flex-1 card p-5">
          <div className="skeleton h-4 w-full rounded mb-2" />
          <div className="skeleton h-4 w-3/4 rounded" />
        </div>
      ) : items.length === 0 ? (
        <div className="card border-dashed empty-state">
          <h3 className="text-[14px] font-medium text-text-primary mb-1">该批次暂无样例</h3>
        </div>
      ) : (
        <div className="flex-1 min-h-0 grid grid-cols-[300px_1fr] gap-4">
          {/* 左侧导航 */}
          <aside className="flex flex-col min-h-0 card p-0 overflow-hidden">
            <div className="p-3 border-b border-border shrink-0">
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索编号或问题…"
                className="input w-full"
              />
            </div>
            <ul className="flex-1 min-h-0 overflow-y-auto">
              {filteredItems.map((s) => {
                const active = s.id === selectedId
                const reviewed = hasFeedback(s)
                return (
                  <li key={s.id}>
                    <button
                      onClick={() => setSelectedId(s.id)}
                      className={`w-full text-left px-3 py-2.5 border-b border-separator flex gap-2 items-start transition-colors ${
                        active ? 'bg-accent/10' : 'hover:bg-fill/5'
                      }`}
                    >
                      <span
                        className={`text-[11px] tabular-nums font-medium shrink-0 mt-0.5 ${
                          active ? 'text-accent' : 'text-text-tertiary'
                        }`}
                      >
                        #{s.row_index + 1}
                      </span>
                      <span className="flex-1 min-w-0 text-[12px] leading-snug text-text-secondary line-clamp-2">
                        {s.question || '（无问题文本）'}
                      </span>
                      {reviewed && (
                        <span
                          className="shrink-0 mt-1 w-1.5 h-1.5 rounded-full bg-positive"
                          title="已评"
                        />
                      )}
                    </button>
                  </li>
                )
              })}
              {filteredItems.length === 0 && (
                <li className="empty-state text-[12px]">无匹配样例</li>
              )}
            </ul>
            {totalPages > 1 && (
              <div className="flex items-center justify-between gap-2 p-2 border-t border-border shrink-0">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="pager-btn"
                >
                  上一页
                </button>
                <span className="text-[11px] text-text-tertiary tabular-nums">
                  {page}/{totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  className="pager-btn"
                >
                  下一页
                </button>
              </div>
            )}
          </aside>

          {/* 右侧详情 + 打分 */}
          <section className="min-h-0 card p-5">
            {selected ? (
              <SampleDetail key={selected.id} sample={selected} />
            ) : (
              <div className="empty-state">← 从左侧选择一个样例</div>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
