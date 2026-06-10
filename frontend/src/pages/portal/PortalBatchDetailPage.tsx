import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, useToast } from '@/components/ui'
import {
  portalApi,
  SCORE_DIMENSIONS,
  type PortalSample,
  type FeedbackPayload,
} from '@/services/portal'
import { formatApiError, toToastMessage } from '@/lib/errors'

const PAGE_SIZE = 10

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
            className={`text-[16px] leading-none transition-colors ${
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

// 单条样例卡片：问题/答案展示 + 打分 + 意见反馈表单。
// 表单 state 局部维护，初值来自已提交 feedback；提交走 upsert。
function SampleCard({ sample, index }: { sample: PortalSample; index: number }) {
  const queryClient = useQueryClient()
  const toast = useToast()

  const initial = sample.feedback
  const [overall, setOverall] = useState<number | null>(initial?.overall ?? null)
  const [scores, setScores] = useState<Record<string, number>>(initial?.scores ?? {})
  const [comment, setComment] = useState(initial?.comment ?? '')

  // 翻页后组件复用同 key 时同步最新已提交值
  useEffect(() => {
    setOverall(sample.feedback?.overall ?? null)
    setScores(sample.feedback?.scores ?? {})
    setComment(sample.feedback?.comment ?? '')
  }, [sample.id, sample.feedback])

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

  const hasFeedback = initial != null && (initial.overall != null || initial.comment)

  return (
    <div className="card p-5 animate-fade-in" style={{ animationDelay: `${index * 30}ms` }}>
      <div className="flex items-start justify-between gap-2 mb-3">
        <span className="text-[11px] tabular-nums text-text-tertiary">#{sample.row_index + 1}</span>
        {hasFeedback && <span className="badge badge-positive shrink-0">已评</span>}
      </div>

      {/* 问题 / 答案：可读性优先，整段展示并保留换行 */}
      <div className="space-y-3 mb-4">
        <div>
          <div className="field-label">问题</div>
          <p className="text-[13px] leading-relaxed text-text-primary whitespace-pre-wrap break-words">
            {sample.question || '—'}
          </p>
        </div>
        <div>
          <div className="field-label">答案</div>
          <p className="text-[13px] leading-relaxed text-text-secondary whitespace-pre-wrap break-words">
            {sample.answer || '—'}
          </p>
        </div>
        {extraEntries.length > 0 && (
          <details className="text-[12px]">
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

      {/* 打分 + 意见 */}
      <div className="border-t border-border pt-4 space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="field-label !mb-0 min-w-[80px]">总体评分</span>
          <StarRating value={overall} onChange={setOverall} ariaLabel="总体评分" />
        </div>
        {SCORE_DIMENSIONS.map((dim) => (
          <div key={dim.key} className="flex items-center gap-3 flex-wrap">
            <span className="field-label !mb-0 min-w-[80px]">{dim.label}</span>
            <StarRating
              value={scores[dim.key] ?? null}
              onChange={(v) => setScores((prev) => ({ ...prev, [dim.key]: v }))}
              ariaLabel={dim.label}
            />
          </div>
        ))}
        <div>
          <label className="field-label">意见反馈</label>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="对该样例的质量、问题或改进建议（可选）"
            rows={2}
            className="input w-full resize-y"
          />
        </div>
        <div className="flex justify-end">
          <Button
            variant="primary"
            size="sm"
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

// 批次样例评审页：分页渲染（每页 PAGE_SIZE 条，低性能负担），逐条打分。
export default function PortalBatchDetailPage() {
  const { batchId } = useParams<{ batchId: string }>()
  const [page, setPage] = useState(1)

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['portal-samples', batchId, page],
    queryFn: () =>
      portalApi
        .listSamples(batchId!, { page, page_size: PAGE_SIZE })
        .then((r) => r.data),
    enabled: !!batchId,
    placeholderData: (prev) => prev,
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <header className="mb-6">
        <Link to="/portal" className="text-[12px] text-accent hover:underline">
          ← 返回批次列表
        </Link>
        <h1 className="page-title mt-2">样例评审</h1>
        <p className="page-subtitle">
          共 {total} 条样例 · 逐条打分（1-5）并填写意见反馈
          {isFetching && !isLoading && <span className="ml-2 text-text-tertiary">刷新中…</span>}
        </p>
      </header>

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="card p-5">
              <div className="skeleton h-3 w-16 rounded mb-3" />
              <div className="skeleton h-4 w-full rounded mb-2" />
              <div className="skeleton h-4 w-3/4 rounded" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="card border-dashed empty-state mt-6">
          <h3 className="text-[14px] font-medium text-text-primary mb-1">该批次暂无样例</h3>
        </div>
      ) : (
        <div className="space-y-4">
          {items.map((s, i) => (
            <SampleCard key={s.id} sample={s} index={i} />
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-[11px] text-text-tertiary">
            共 {total} 条 · 第 {page} / {totalPages} 页
          </span>
          <div className="flex gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="pager-btn"
            >
              上一页
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="pager-btn"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
