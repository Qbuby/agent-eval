import { useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, useToast } from '@/components/ui'
import { portalApi } from '@/services/portal'
import { formatApiError, toToastMessage } from '@/lib/errors'

// Portal 首页：上传 xlsx + 批次列表。点击批次进入样例打分页。
export default function PortalBatchesPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data: batches, isLoading, isFetching } = useQuery({
    queryKey: ['portal-batches'],
    queryFn: () => portalApi.listBatches().then((r) => r.data),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  })

  const uploadMutation = useMutation({
    mutationFn: (file: File) => portalApi.uploadBatch(file),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['portal-batches'] })
      toast.success(`已上传 ${res.data.row_count} 条样例`)
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '上传失败' })
      toast.error(toToastMessage(norm), '上传失败')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (batchId: string) => portalApi.deleteBatch(batchId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['portal-batches'] })
      toast.success('样例集已删除')
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '删除失败' })
      toast.error(toToastMessage(norm), '删除失败')
    },
  })

  // 删除前二次确认：连同其下全部样例与已提交反馈一并删除，不可恢复。
  function handleDelete(e: React.MouseEvent, batch: { id: string; name: string; row_count: number }) {
    e.stopPropagation() // 阻止冒泡，避免触发卡片的导航
    if (
      window.confirm(
        `确定删除样例集「${batch.name}」吗？\n其下 ${batch.row_count} 条样例及所有评审反馈将一并删除，且不可恢复。`,
      )
    ) {
      deleteMutation.mutate(batch.id)
    }
  }

  function handlePick() {
    fileInputRef.current?.click()
  }

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    // 清空 value，保证同名文件可重复选择触发 change
    e.target.value = ''
    if (!file) return
    const lower = file.name.toLowerCase()
    if (!lower.endsWith('.xlsx') && !lower.endsWith('.xls')) {
      toast.error('请选择 .xlsx 或 .xls 文件', '文件格式不支持')
      return
    }
    uploadMutation.mutate(file)
  }

  return (
    <div>
      <header className="mb-6">
        <h1 className="page-title">样例评审</h1>
        <p className="page-subtitle">上传 QA 样例表格，逐条打分并提交意见反馈</p>
      </header>

      <div className="toolbar">
        {isFetching && !isLoading && (
          <span className="text-[10px] text-text-tertiary">刷新中…</span>
        )}
        <div className="flex-1" />
        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx,.xls"
          onChange={handleFile}
          className="hidden"
        />
        <Button
          onClick={handlePick}
          variant="primary"
          size="md"
          loading={uploadMutation.isPending}
        >
          上传 xlsx
        </Button>
      </div>

      <p className="text-[11px] text-text-tertiary mb-4">
        表格首行为表头，自动识别 question / answer 列（支持中英文，如 问题 / 答案），其余列作为附加信息保留。
      </p>

      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="card p-5">
              <div className="skeleton h-4 w-32 rounded mb-3" />
              <div className="skeleton h-3 w-20 rounded mb-2" />
              <div className="skeleton h-3 w-24 rounded" />
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
          {batches?.map((b, i) => (
            <div
              key={b.id}
              role="link"
              tabIndex={0}
              onClick={() => navigate(`/portal/batches/${b.id}`)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  navigate(`/portal/batches/${b.id}`)
                }
              }}
              className="card p-5 cursor-pointer animate-fade-in transition-[transform,box-shadow,border-color] duration-200 ease-standard hover:-translate-y-0.5 hover:shadow-md hover:border-border-strong focus:outline-none focus-visible:shadow-focus"
              style={{ animationDelay: `${i * 40}ms` }}
            >
              <div className="flex justify-between items-start mb-3 gap-2">
                <span className="text-[15px] font-display font-semibold tracking-[-0.2px] text-text-primary truncate">
                  {b.name}
                </span>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="badge badge-positive">
                    {b.status === 'active' ? '可评审' : b.status}
                  </span>
                  <button
                    type="button"
                    onClick={(e) => handleDelete(e, b)}
                    disabled={deleteMutation.isPending}
                    aria-label={`删除样例集 ${b.name}`}
                    title="删除样例集"
                    className="p-1 rounded text-text-tertiary hover:text-negative hover:bg-negative/10 transition-colors disabled:opacity-50"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                      <line x1="10" y1="11" x2="10" y2="17" />
                      <line x1="14" y1="11" x2="14" y2="17" />
                    </svg>
                  </button>
                </div>
              </div>
              <div className="space-y-1.5">
                <div className="flex justify-between items-center">
                  <span className="text-[11px] text-text-tertiary">样例数</span>
                  <span className="text-[12px] tabular-nums font-medium text-text-primary">
                    {b.row_count}
                  </span>
                </div>
                <div className="flex justify-between items-center gap-2">
                  <span className="text-[11px] text-text-tertiary shrink-0">上传时间</span>
                  <span className="text-[12px] text-text-secondary truncate">
                    {b.created_at ? new Date(b.created_at).toLocaleString() : '—'}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {!isLoading && batches?.length === 0 && (
        <div className="card border-dashed empty-state mt-6">
          <h3 className="text-[14px] font-medium text-text-primary mb-1">暂无样例批次</h3>
          <p className="text-[12px] text-text-tertiary max-w-[280px] mx-auto">
            上传一份 xlsx 表格开始评审
          </p>
        </div>
      )}
    </div>
  )
}
