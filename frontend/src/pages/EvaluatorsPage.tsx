import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, useConfirm, useToast } from '@/components/ui'
import EvaluatorEditorDrawer from '@/components/EvaluatorEditorDrawer'
import { evaluationApi } from '@/services'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { EvaluatorInstance } from '@/types'

export default function EvaluatorsPage() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [importing, setImporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  async function handleExport() {
    setExporting(true)
    try {
      await evaluationApi.exportEvaluators()
      toast.success('评估器已导出')
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '导出失败' })
      toast.error(toToastMessage(norm), '导出失败')
    } finally {
      setExporting(false)
    }
  }

  async function handleImportFile(file: File) {
    setImporting(true)
    try {
      const { data } = await evaluationApi.importEvaluators(file)
      qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
      // 汇总一条可读结果：新建 / 更新 / 跳过（跳过附原因）。
      const parts: string[] = []
      if (data.created.length) parts.push(`新建 ${data.created.length}`)
      if (data.updated.length) parts.push(`更新 ${data.updated.length}`)
      if (data.skipped.length) parts.push(`跳过 ${data.skipped.length}`)
      const summary = parts.length ? parts.join('，') : '没有可导入的评估器'
      if (data.skipped.length) {
        const detail = data.skipped.map(s => `${s.name}：${s.reason}`).join('；')
        toast.error(`${summary}。跳过原因 — ${detail}`, '导入完成（有跳过）')
      } else {
        toast.success(`导入完成：${summary}`)
      }
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '导入失败' })
      toast.error(toToastMessage(norm), '导入失败')
    } finally {
      setImporting(false)
    }
  }

  const listQuery = useQuery({
    queryKey: ['evaluator-instances'],
    queryFn: () => evaluationApi.listEvaluators().then(r => r.data),
  })

  const [showEditor, setShowEditor] = useState(false)
  const [editing, setEditing] = useState<EvaluatorInstance | null>(null)

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">评估</div>
        <h1 className="page-title">评估器</h1>
        <p className="page-subtitle">
          标签模板用于触发 Langfuse 端打分；可配置 LLM Judge 直接由本平台调 provider 打分
        </p>
      </header>

      <div className="section-row">
        <div className="page-eyebrow">评估器列表</div>
        <div className="flex gap-2">
          <Link to="/evaluators/compare">
            <Button variant="ghost" size="sm">对比评估器</Button>
          </Link>
          <Button variant="ghost" size="sm" onClick={handleExport} disabled={exporting}>
            {exporting ? '导出中…' : '导出'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
          >
            {importing ? '导入中…' : '导入'}
          </Button>
          {/* 隐藏的文件选择器：导入仅接受导出的 JSON。选完即清空 value，
              以便连续选同一个文件也能再次触发 onChange。 */}
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0]
              e.target.value = ''
              if (f) void handleImportFile(f)
            }}
          />
          <Button variant="primary" size="sm" onClick={() => { setEditing(null); setShowEditor(true) }}>
            新建评估器
          </Button>
        </div>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>Tag</th>
              <th className="w-24">状态</th>
              <th className="w-44">创建时间</th>
              <th className="w-28 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={6} className="empty-state">加载中…</td></tr>
            )}
            {listQuery.data?.length === 0 && !listQuery.isLoading && (
              <tr><td colSpan={6} className="empty-state">
                还没有评估器。新建一个，运行评估时勾选它即可。
              </td></tr>
            )}
            {listQuery.data?.map(e => (
              <tr key={e.id} className="group">
                <td className="font-medium">{e.name}</td>
                <td>
                  <span className="badge badge-neutral text-[10px]">
                    {e.evaluator_type === 'configurable_judge' ? 'LLM Judge' : '标签模板'}
                  </span>
                </td>
                <td>
                  <span className="font-mono text-[11px] text-text-secondary">{e.tag || e.name}</span>
                </td>
                <td>
                  <span className={e.is_active ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {e.is_active ? '启用' : '停用'}
                  </span>
                </td>
                <td className="text-text-tertiary text-[11px]">
                  {e.created_at ? new Date(e.created_at).toLocaleString() : '—'}
                </td>
                <td className="text-right">
                  <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => { setEditing(e); setShowEditor(true) }}
                      className="text-action"
                    >
                      编辑
                    </button>
                    <button
                      onClick={async () => {
                        const ok = await confirm({
                          title: '删除评估器',
                          description: `删除评估器"${e.name}"？`,
                          confirmText: '删除',
                          danger: true,
                        })
                        if (!ok) return
                        setDeletingId(e.id)
                        try {
                          await evaluationApi.deleteEvaluator(e.id)
                          qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
                          toast.success('评估器已删除')
                        } catch (err) {
                          const norm = formatApiError(err, { fallbackTitle: '删除失败' })
                          toast.error(toToastMessage(norm), '删除失败')
                        } finally {
                          setDeletingId(null)
                        }
                      }}
                      disabled={deletingId === e.id}
                      className="text-action-danger"
                    >
                      {deletingId === e.id ? '删除中…' : '删除'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showEditor && (
        <EvaluatorEditorDrawer
          open={showEditor}
          editing={editing}
          onClose={() => { setShowEditor(false); setEditing(null) }}
        />
      )}
    </div>
  )
}
