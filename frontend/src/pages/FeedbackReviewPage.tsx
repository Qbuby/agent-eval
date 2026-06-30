import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Drawer, SkeletonRow, ErrorCard } from '@/components/ui'
import { formatApiError } from '@/lib/errors'
import { feedbackReviewApi } from '@/services/feedbackReview'
import MarkdownView from '@/components/MarkdownView'
import type {
  FeedbackBatchSummary,
  FeedbackSampleRow,
  SampleFeedbackDetail,
} from '@/services/feedbackReview'

const SAMPLE_PAGE_SIZE = 20

// 维度 key → 中文标签。与 portal 评审页 SCORE_DIMENSIONS 对齐；
// 未知 key 回退原文，保证新增维度也能显示。
const DIMENSION_LABELS: Record<string, string> = {
  relevance: '相关性',
  difficulty: '难度',
  answer_accuracy: '答案准确性',
}

function dimLabel(key: string): string {
  return DIMENSION_LABELS[key] ?? key
}

function fmtScore(v: number | null | undefined): string {
  return v == null ? '—' : v.toFixed(2)
}

function fmtPct(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

function scoreClass(v: number | null | undefined): string {
  if (v == null) return 'text-text-tertiary'
  if (v >= 4) return 'text-positive'
  if (v >= 3) return 'text-text-primary'
  if (v >= 2) return 'text-warning'
  return 'text-negative'
}

export default function FeedbackReviewPage() {
  const [tenantFilter, setTenantFilter] = useState('')
  // 钻取状态：选中批次 → 看样例列表；选中样例 → 看反馈明细 drawer
  const [activeBatch, setActiveBatch] = useState<FeedbackBatchSummary | null>(null)
  const [samplePage, setSamplePage] = useState(1)
  const [activeSampleId, setActiveSampleId] = useState<string | null>(null)

  const statsQuery = useQuery({
    queryKey: ['feedback-stats'],
    queryFn: () => feedbackReviewApi.stats().then((r) => r.data),
  })

  const batchesQuery = useQuery({
    queryKey: ['feedback-batches', tenantFilter],
    queryFn: () =>
      feedbackReviewApi
        .batches({ tenant_id: tenantFilter || undefined })
        .then((r) => r.data),
  })

  const stats = statsQuery.data
  const batches = batchesQuery.data?.batches ?? []

  // 用 stats.rows 推导租户下拉（去重）
  const tenantOptions = Array.from(
    new Map((stats?.rows ?? []).map((r) => [r.tenant_id, r.tenant_name])).entries(),
  ).map(([id, name]) => ({ id, name }))

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">客户反馈</div>
        <h1 className="page-title">客户反馈展示</h1>
        <p className="page-subtitle">
          按租户 / 批次查看外部客户对样例的手动打分与意见反馈 · 跨租户汇总
        </p>
      </header>

      {/* 总览指标 */}
      <div className="grid grid-cols-4 gap-3 mb-8">
        <div className="metric-card">
          <div className="metric-eyebrow">批次数</div>
          <div className="metric-value">{stats?.total_batches ?? '—'}</div>
          <div className="text-[11px] text-text-tertiary mt-1">有反馈的批次</div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">样例覆盖</div>
          <div className="metric-value">
            {stats ? `${stats.total_rated}/${stats.total_samples}` : '—'}
          </div>
          <div className="text-[11px] text-text-tertiary mt-1">已评 / 总样例</div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">反馈条数</div>
          <div className="metric-value">{stats?.total_feedbacks ?? '—'}</div>
          <div className="text-[11px] text-text-tertiary mt-1">含多人重复评价</div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">平均总体分</div>
          <div className={`metric-value ${scoreClass(stats?.overall_avg)}`}>
            {fmtScore(stats?.overall_avg)}
          </div>
          <div className="text-[11px] text-text-tertiary mt-1">全局 1-5</div>
        </div>
      </div>

      {/* 工具栏 */}
      <div className="toolbar">
        <select
          value={tenantFilter}
          onChange={(e) => setTenantFilter(e.target.value)}
          className="select-sm"
          aria-label="租户筛选"
        >
          <option value="">全部租户</option>
          {tenantOptions.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <Button
          variant="secondary"
          size="sm"
          loading={batchesQuery.isFetching}
          onClick={() => {
            batchesQuery.refetch()
            statsQuery.refetch()
          }}
        >
          {batchesQuery.isFetching ? '刷新中' : '刷新'}
        </Button>
        <span className="text-[11px] text-text-tertiary ml-auto tabular-nums">
          {batches.length} 个批次
        </span>
      </div>

      {batchesQuery.isError && (
        <div className="mb-3">
          <ErrorCard
            error={formatApiError(batchesQuery.error, { fallbackTitle: '加载批次失败' })}
          />
        </div>
      )}

      {/* 批次列表 */}
      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>批次</th>
              <th className="w-32">租户</th>
              <th className="w-20 text-right">样例数</th>
              <th className="w-24 text-right">已评 / 覆盖</th>
              <th className="w-24 text-right">反馈条数</th>
              <th className="w-24 text-right">平均总体分</th>
              <th className="w-36">上传时间</th>
            </tr>
          </thead>
          <tbody>
            {batchesQuery.isLoading
              ? Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} cols={7} />)
              : batches.map((b) => {
                  const coverage = b.row_count > 0 ? b.rated_count / b.row_count : null
                  return (
                    <tr
                      key={b.batch_id}
                      onClick={() => {
                        setActiveBatch(b)
                        setSamplePage(1)
                      }}
                      className="cursor-pointer animate-fade-in"
                    >
                      <td
                        className="text-text-primary font-medium truncate max-w-[280px]"
                        title={b.batch_name}
                      >
                        {b.batch_name}
                      </td>
                      <td className="text-text-secondary truncate" title={b.tenant_name}>
                        {b.tenant_name}
                      </td>
                      <td className="text-right font-mono tabular-nums text-text-secondary">
                        {b.row_count}
                      </td>
                      <td className="text-right font-mono tabular-nums text-text-secondary">
                        {b.rated_count}
                        <span className="text-text-tertiary"> · {fmtPct(coverage)}</span>
                      </td>
                      <td className="text-right font-mono tabular-nums text-text-secondary">
                        {b.feedback_count}
                      </td>
                      <td
                        className={`text-right font-mono font-medium tabular-nums ${scoreClass(b.avg_overall)}`}
                      >
                        {fmtScore(b.avg_overall)}
                      </td>
                      <td className="font-mono text-text-tertiary text-[11px]">
                        {new Date(b.created_at).toLocaleString()}
                      </td>
                    </tr>
                  )
                })}
            {!batchesQuery.isLoading && batches.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">
                  暂无含反馈的批次
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 批次内样例钻取 */}
      <BatchSamplesDrawer
        batch={activeBatch}
        page={samplePage}
        onPageChange={setSamplePage}
        onClose={() => setActiveBatch(null)}
        onOpenSample={(id) => setActiveSampleId(id)}
      />

      {/* 单样例反馈明细 */}
      <SampleFeedbackDrawer
        sampleId={activeSampleId}
        onClose={() => setActiveSampleId(null)}
      />
    </div>
  )
}

function BatchSamplesDrawer({
  batch,
  page,
  onPageChange,
  onClose,
  onOpenSample,
}: {
  batch: FeedbackBatchSummary | null
  page: number
  onPageChange: (p: number) => void
  onClose: () => void
  onOpenSample: (id: string) => void
}) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['feedback-batch-samples', batch?.batch_id, page],
    queryFn: () =>
      feedbackReviewApi
        .batchSamples(batch!.batch_id, { page, page_size: SAMPLE_PAGE_SIZE })
        .then((r) => r.data),
    enabled: !!batch,
  })

  const samples: FeedbackSampleRow[] = data?.samples ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / SAMPLE_PAGE_SIZE))

  return (
    <Drawer
      open={!!batch}
      onClose={onClose}
      title={batch?.batch_name}
      subtitle={batch ? `${batch.tenant_name} · ${total || batch.row_count} 条样例` : undefined}
      width="wide"
    >
      {isError && (
        <div className="text-[12px] text-negative mb-3">
          加载失败：{(error as Error).message}
        </div>
      )}
      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th className="w-12 text-right">#</th>
              <th>问题</th>
              <th className="w-16 text-right">反馈</th>
              <th className="w-20 text-right">平均分</th>
            </tr>
          </thead>
          <tbody>
            {isLoading
              ? Array.from({ length: 6 }).map((_, i) => <SkeletonRow key={i} cols={4} />)
              : samples.map((s) => (
                  <tr
                    key={s.id}
                    onClick={() => onOpenSample(s.id)}
                    className="cursor-pointer animate-fade-in"
                  >
                    <td className="text-right font-mono text-text-tertiary tabular-nums">
                      {s.row_index}
                    </td>
                    <td
                      className="text-text-primary truncate max-w-[360px]"
                      title={s.question}
                    >
                      {s.question}
                    </td>
                    <td className="text-right font-mono tabular-nums text-text-secondary">
                      {s.feedback_count}
                    </td>
                    <td
                      className={`text-right font-mono font-medium tabular-nums ${scoreClass(s.avg_overall)}`}
                    >
                      {fmtScore(s.avg_overall)}
                    </td>
                  </tr>
                ))}
            {!isLoading && samples.length === 0 && (
              <tr>
                <td colSpan={4} className="empty-state">
                  该批次暂无样例
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-3 text-[12px] text-text-secondary">
          <span className="tabular-nums">
            第 {page} / {totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
            >
              上一页
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => onPageChange(page + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      )}
    </Drawer>
  )
}

function SampleFeedbackDrawer({
  sampleId,
  onClose,
}: {
  sampleId: string | null
  onClose: () => void
}) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['feedback-sample', sampleId],
    queryFn: () => feedbackReviewApi.sample(sampleId!).then((r) => r.data),
    enabled: !!sampleId,
  })

  return (
    <Drawer
      open={!!sampleId}
      onClose={onClose}
      title="样例反馈明细"
      subtitle={data ? `${data.tenant_name} · ${data.batch_name} · #${data.row_index}` : undefined}
      width="wide"
    >
      {isError && (
        <div className="text-[12px] text-negative mb-3">
          加载失败：{(error as Error).message}
        </div>
      )}
      {isLoading && <div className="text-[12px] text-text-tertiary">加载中…</div>}
      {data && (
        <div className="space-y-5">
          {/* 样例本体 */}
          <div>
            <div className="field-label">问题</div>
            <div className="bg-fill/5 rounded-md p-3">
              <MarkdownView text={data.question} />
            </div>
          </div>
          {data.answer != null && (
            <div>
              <div className="field-label">答案</div>
              <div className="bg-fill/5 rounded-md p-3">
                <MarkdownView text={data.answer} />
              </div>
            </div>
          )}
          {data.extra && Object.keys(data.extra).length > 0 && (
            <div>
              <div className="field-label">其他列</div>
              <dl className="space-y-1.5 text-[12px]">
                {Object.entries(data.extra).map(([k, v]) => (
                  <div key={k} className="grid grid-cols-[120px_1fr] gap-2 items-start">
                    <dt className="text-text-tertiary truncate" title={k}>
                      {k}
                    </dt>
                    <dd className="text-text-secondary break-words">{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {/* 反馈明细 */}
          <div>
            <div className="field-label">
              客户反馈（{data.feedbacks.length}）
            </div>
            {data.feedbacks.length === 0 ? (
              <div className="empty-state !py-6">该样例暂无客户反馈</div>
            ) : (
              <div className="space-y-3">
                {data.feedbacks.map((f) => (
                  <FeedbackCard key={f.id} feedback={f} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Drawer>
  )
}

function FeedbackCard({ feedback }: { feedback: SampleFeedbackDetail }) {
  const scoreEntries = Object.entries(feedback.scores ?? {})
  return (
    <div className="border border-border rounded-lg p-3.5 bg-surface animate-fade-in">
      {/* 头部：评价人 + 总体分 */}
      <div className="flex items-center justify-between gap-2 mb-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-accent/10 text-accent text-[11px] font-medium shrink-0">
            {(feedback.rated_by_name ?? '匿')[0]}
          </span>
          <span className="text-[13px] font-medium text-text-primary truncate">
            {feedback.rated_by_name ?? '匿名'}
          </span>
        </div>
        <span className="flex items-baseline gap-1 shrink-0">
          <span className="text-[11px] text-text-tertiary">总体</span>
          <span className={`font-mono font-semibold tabular-nums text-[15px] ${scoreClass(feedback.overall)}`}>
            {feedback.overall == null ? '—' : feedback.overall}
          </span>
          <span className="text-[11px] text-text-tertiary">/ 5</span>
        </span>
      </div>

      {/* 维度分：中文标签 + 星档，整齐排列 */}
      {scoreEntries.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5 mb-2.5">
          {scoreEntries.map(([dim, val]) => (
            <span key={dim} className="inline-flex items-center gap-1.5 text-[12px]">
              <span className="text-text-tertiary">{dimLabel(dim)}</span>
              <span className="text-warning tracking-tight" aria-hidden>
                {'★'.repeat(Math.max(0, Math.min(5, Number(val) || 0)))}
                <span className="text-text-tertiary/40">
                  {'★'.repeat(Math.max(0, 5 - (Number(val) || 0)))}
                </span>
              </span>
              <span className="font-mono tabular-nums text-text-secondary">{val}</span>
            </span>
          ))}
        </div>
      )}

      {/* 期望答案：评审人补写的参考标准答案 */}
      {feedback.expected_answer && (
        <div className="mb-2.5">
          <div className="text-[11px] font-medium text-text-tertiary mb-1">期望答案</div>
          <div className="rounded-md bg-accent/[0.04] border border-accent/15 p-2.5 text-[12px]">
            <MarkdownView text={feedback.expected_answer} />
          </div>
        </div>
      )}

      {feedback.comment && (
        <div className="text-[12px] text-text-secondary whitespace-pre-wrap break-words border-t border-separator pt-2">
          {feedback.comment}
        </div>
      )}
      <div className="text-[11px] text-text-tertiary mt-2 font-mono">
        {new Date(feedback.updated_at).toLocaleString()}
      </div>
    </div>
  )
}
