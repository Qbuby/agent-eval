import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useToast } from '@/components/ui'
import { formatApiError, toToastMessage } from '@/lib/errors'
import { entryCodesApi } from '@/services/entryCodes'
import type { CreateEntryCodeRequest, EntryCode } from '@/services/entryCodes'
import { adminTenantsApi } from '@/services/adminTenants'

// ──────────────────────────────────────────────────────────────────────────
// 内部 admin 入口码管理页：建码 / 列码 / 启停 / 改 / 删。
// 用户注册时凭码绑定到码所指的租户 + 角色（见后端 auth.register / admin_entry_codes）。
// ──────────────────────────────────────────────────────────────────────────

const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: 'external_customer', label: '外部客户' },
  { value: 'user', label: '内部用户' },
  { value: 'admin', label: '管理员' },
]

function roleLabel(role: string): string {
  return ROLE_OPTIONS.find((r) => r.value === role)?.label ?? role
}

export default function EntryCodesPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const [showCreate, setShowCreate] = useState(false)
  const [editing, setEditing] = useState<EntryCode | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['admin-entry-codes'],
    queryFn: () => entryCodesApi.list().then((r) => r.data),
  })

  // 租户名映射，用于把 tenant_id 展示成租户名。
  const tenantsQuery = useQuery({
    queryKey: ['admin-tenants'],
    queryFn: () => adminTenantsApi.listTenants().then((r) => r.data),
  })
  const tenantName = (id: string): string =>
    tenantsQuery.data?.find((t) => t.id === id)?.name ?? id

  const invalidate = () => qc.invalidateQueries({ queryKey: ['admin-entry-codes'] })

  const handleToggle = async (c: EntryCode) => {
    setTogglingId(c.id)
    try {
      await entryCodesApi.update(c.id, { is_active: !c.is_active })
      invalidate()
      toast.success(c.is_active ? '入口码已停用' : '入口码已启用')
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '操作失败' })
      toast.error(toToastMessage(norm), '操作失败')
    } finally {
      setTogglingId(null)
    }
  }

  const handleDelete = async (c: EntryCode) => {
    if (!window.confirm(`确认删除入口码「${c.code}」？已注册的用户不受影响。`)) return
    setDeletingId(c.id)
    try {
      await entryCodesApi.remove(c.id)
      invalidate()
      toast.success('入口码已删除')
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '删除失败' })
      toast.error(toToastMessage(norm), '删除失败')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">内部管理</div>
        <h1 className="page-title">入口码管理</h1>
        <p className="page-subtitle">
          用户注册时凭入口码绑定到对应租户与角色。把码分发给客户，他们注册即落入其租户。
        </p>
      </header>

      <div className="section-row">
        <div className="page-eyebrow">入口码列表</div>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)}>
          新建入口码
        </Button>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>入口码</th>
              <th>租户</th>
              <th className="w-24">角色</th>
              <th>描述</th>
              <th className="w-20">状态</th>
              <th className="w-44 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={6} className="empty-state">加载中…</td></tr>
            )}
            {!listQuery.isLoading && (listQuery.data?.length ?? 0) === 0 && (
              <tr><td colSpan={6} className="empty-state">
                还没有入口码。新建一个，把它分发给客户用于注册。
              </td></tr>
            )}
            {listQuery.data?.map((c) => (
              <tr key={c.id} className="group animate-fade-in">
                <td className="font-mono text-[12px] font-medium">{c.code}</td>
                <td className="text-text-secondary">{tenantName(c.tenant_id)}</td>
                <td>
                  <span className="badge badge-neutral text-[10px]">{roleLabel(c.role)}</span>
                </td>
                <td className="text-[12px] text-text-tertiary">{c.label ?? '—'}</td>
                <td>
                  <span className={c.is_active ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {c.is_active ? '启用' : '停用'}
                  </span>
                </td>
                <td className="text-right">
                  <div className="flex gap-3 justify-end items-center">
                    <button onClick={() => setEditing(c)} className="text-action">编辑</button>
                    <button
                      onClick={() => handleToggle(c)}
                      disabled={togglingId === c.id}
                      className="text-action"
                    >
                      {togglingId === c.id ? '处理中…' : c.is_active ? '停用' : '启用'}
                    </button>
                    <button
                      onClick={() => handleDelete(c)}
                      disabled={deletingId === c.id}
                      className="text-action text-negative"
                    >
                      {deletingId === c.id ? '删除中…' : '删除'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreateEntryCodeDialog open={showCreate} onClose={() => setShowCreate(false)} />
      )}

      {editing && (
        <EditEntryCodeDialog
          code={editing}
          open={!!editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

// ── 新建入口码 ────────────────────────────────────────────────────────────────

function CreateEntryCodeDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const reactId = useId()
  const ids = {
    code: `${reactId}-code`,
    tenant: `${reactId}-tenant`,
    role: `${reactId}-role`,
    label: `${reactId}-label`,
  }

  const [code, setCode] = useState('')
  const [tenantId, setTenantId] = useState('')
  const [role, setRole] = useState('external_customer')
  const [label, setLabel] = useState('')

  const tenantsQuery = useQuery({
    queryKey: ['admin-tenants'],
    queryFn: () => adminTenantsApi.listTenants().then((r) => r.data),
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      const body: CreateEntryCodeRequest = {
        code: code.trim(),
        tenant_id: tenantId,
        role,
        label: label.trim() || undefined,
      }
      return entryCodesApi.create(body).then((r) => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-entry-codes'] })
      toast.success('入口码已创建')
      onClose()
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '创建失败' })
      toast.error(toToastMessage(norm), '创建失败')
    },
  })

  const valid = code.trim() && tenantId && role

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="新建入口码"
      width={460}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            size="md"
            disabled={!valid}
            loading={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            创建
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label htmlFor={ids.code} className="field-label">入口码</label>
          <input
            id={ids.code}
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="例如：Ep2026!"
            className="input font-mono"
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor={ids.tenant} className="field-label">绑定租户</label>
          <select
            id={ids.tenant}
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            className="input"
          >
            <option value="">选择租户…</option>
            {tenantsQuery.data?.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={ids.role} className="field-label">注册角色</label>
          <select
            id={ids.role}
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="input"
          >
            {ROLE_OPTIONS.map((r) => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={ids.label} className="field-label">
            描述<span className="text-text-tertiary"> · 可选</span>
          </label>
          <input
            id={ids.label}
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="例如：中力客户入口"
            className="input"
          />
        </div>
      </div>
    </Dialog>
  )
}

// ── 编辑入口码（改码 / 角色 / 描述）─────────────────────────────────────────────

function EditEntryCodeDialog({
  code, open, onClose,
}: {
  code: EntryCode
  open: boolean
  onClose: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const reactId = useId()
  const ids = { code: `${reactId}-code`, role: `${reactId}-role`, label: `${reactId}-label` }

  const [codeVal, setCodeVal] = useState(code.code)
  const [role, setRole] = useState(code.role)
  const [label, setLabel] = useState(code.label ?? '')

  const saveMutation = useMutation({
    mutationFn: () => {
      const body: Partial<CreateEntryCodeRequest> & { is_active?: boolean } = {}
      if (codeVal.trim() !== code.code) body.code = codeVal.trim()
      if (role !== code.role) body.role = role
      if (label.trim() !== (code.label ?? '')) body.label = label.trim()
      return entryCodesApi.update(code.id, body).then((r) => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-entry-codes'] })
      toast.success('入口码已更新')
      onClose()
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '保存失败' })
      toast.error(toToastMessage(norm), '保存失败')
    },
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`编辑入口码 · ${code.code}`}
      width={460}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            size="md"
            disabled={!codeVal.trim()}
            loading={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            保存
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="text-[11px] text-text-tertiary">
          租户绑定不可改（避免已分发的码语义漂移）；需换租户请新建一个码。
        </div>
        <div>
          <label htmlFor={ids.code} className="field-label">入口码</label>
          <input
            id={ids.code}
            type="text"
            value={codeVal}
            onChange={(e) => setCodeVal(e.target.value)}
            className="input font-mono"
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor={ids.role} className="field-label">注册角色</label>
          <select
            id={ids.role}
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="input"
          >
            {ROLE_OPTIONS.map((r) => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={ids.label} className="field-label">
            描述<span className="text-text-tertiary"> · 可选</span>
          </label>
          <input
            id={ids.label}
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="input"
          />
        </div>
      </div>
    </Dialog>
  )
}
