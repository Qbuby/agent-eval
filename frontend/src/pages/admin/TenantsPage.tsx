import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, Drawer, useToast } from '@/components/ui'
import { formatApiError, toToastMessage } from '@/lib/errors'
import { adminTenantsApi } from '@/services/adminTenants'
import type {
  AdminUser, CreateTenantRequest, CreateTenantUserRequest, Tenant, UpdateUserRequest,
} from '@/services/adminTenants'

// ──────────────────────────────────────────────────────────────────────────
// 内部 admin 租户管理页：建租户 / 列租户 / 启停 / 进入某租户开外部客户账号。
// 用户的细粒度管理（列/改角色/启停/重置密码）在 TenantUsersDrawer 里。
// ──────────────────────────────────────────────────────────────────────────

function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export default function TenantsPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const [showCreate, setShowCreate] = useState(false)
  const [usersOf, setUsersOf] = useState<Tenant | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['admin-tenants'],
    queryFn: () => adminTenantsApi.listTenants().then((r) => r.data),
  })

  const handleToggle = async (t: Tenant) => {
    setTogglingId(t.id)
    try {
      await adminTenantsApi.updateTenant(t.id, { is_active: !t.is_active })
      qc.invalidateQueries({ queryKey: ['admin-tenants'] })
      toast.success(t.is_active ? '租户已停用' : '租户已启用')
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '操作失败' })
      toast.error(toToastMessage(norm), '操作失败')
    } finally {
      setTogglingId(null)
    }
  }

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">内部管理</div>
        <h1 className="page-title">租户管理</h1>
        <p className="page-subtitle">
          创建外部客户租户、在租户下开通客户账号。数据按租户隔离，内部账号（superadmin）跨租户可见。
        </p>
      </header>

      <div className="section-row">
        <div className="page-eyebrow">租户列表</div>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)}>
          新建租户
        </Button>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>名称</th>
              <th>Slug</th>
              <th className="w-20 text-right">用户数</th>
              <th className="w-20">状态</th>
              <th className="w-44">创建时间</th>
              <th className="w-44 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={6} className="empty-state">加载中…</td></tr>
            )}
            {!listQuery.isLoading && (listQuery.data?.length ?? 0) === 0 && (
              <tr><td colSpan={6} className="empty-state">
                还没有租户。新建一个，再在它下面开通外部客户账号。
              </td></tr>
            )}
            {listQuery.data?.map((t) => (
              <tr key={t.id} className="group animate-fade-in">
                <td className="font-medium">{t.name}</td>
                <td className="font-mono text-[11px] text-text-secondary">{t.slug}</td>
                <td className="text-right font-mono tabular-nums text-text-secondary">
                  {t.user_count}
                </td>
                <td>
                  <span className={t.is_active ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {t.is_active ? '启用' : '停用'}
                  </span>
                </td>
                <td className="font-mono text-[11px] text-text-tertiary">
                  {new Date(t.created_at).toLocaleString()}
                </td>
                <td className="text-right">
                  <div className="flex gap-3 justify-end items-center">
                    <button onClick={() => setUsersOf(t)} className="text-action">
                      用户
                    </button>
                    <button
                      onClick={() => handleToggle(t)}
                      disabled={togglingId === t.id}
                      className="text-action"
                    >
                      {togglingId === t.id ? '处理中…' : t.is_active ? '停用' : '启用'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreateTenantDialog open={showCreate} onClose={() => setShowCreate(false)} />
      )}

      {usersOf && (
        <TenantUsersDrawer tenant={usersOf} onClose={() => setUsersOf(null)} />
      )}
    </div>
  )
}

// ── 新建租户 ────────────────────────────────────────────────────────────────

function CreateTenantDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const reactId = useId()
  const ids = { name: `${reactId}-name`, slug: `${reactId}-slug` }

  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)

  const effectiveSlug = slugTouched ? slug : slugify(name)

  const saveMutation = useMutation({
    mutationFn: () => {
      const body: CreateTenantRequest = { name: name.trim(), slug: effectiveSlug }
      return adminTenantsApi.createTenant(body).then((r) => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tenants'] })
      toast.success('租户已创建')
      onClose()
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '创建失败' })
      toast.error(toToastMessage(norm), '创建失败')
    },
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="新建租户"
      width={460}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            size="md"
            disabled={!name.trim() || !effectiveSlug}
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
          <label htmlFor={ids.name} className="field-label">租户名称</label>
          <input
            id={ids.name}
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：某某科技有限公司"
            className="input"
          />
        </div>
        <div>
          <label htmlFor={ids.slug} className="field-label">
            Slug<span className="text-text-tertiary"> · 唯一标识，仅小写字母/数字/连字符</span>
          </label>
          <input
            id={ids.slug}
            type="text"
            value={effectiveSlug}
            onChange={(e) => { setSlugTouched(true); setSlug(slugify(e.target.value)) }}
            placeholder="acme-tech"
            className="input font-mono"
          />
          <div className="mt-1.5 text-[10px] text-text-tertiary">默认按名称自动生成，可手动覆盖。</div>
        </div>
      </div>
    </Dialog>
  )
}

// ── 租户用户管理（列 / 开户 / 启停 / 改角色 / 重置密码）─────────────────────────

const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: 'external_customer', label: '外部客户' },
  { value: 'user', label: '内部用户' },
  { value: 'admin', label: '管理员' },
]

function roleLabel(role: string): string {
  return ROLE_OPTIONS.find((r) => r.value === role)?.label ?? role
}

function TenantUsersDrawer({ tenant, onClose }: { tenant: Tenant; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [showCreate, setShowCreate] = useState(false)
  const [editing, setEditing] = useState<AdminUser | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)

  const usersQuery = useQuery({
    queryKey: ['admin-users', tenant.id],
    queryFn: () => adminTenantsApi.listUsers({ tenant_id: tenant.id }).then((r) => r.data),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['admin-users', tenant.id] })
    qc.invalidateQueries({ queryKey: ['admin-tenants'] })
  }

  const handleToggle = async (u: AdminUser) => {
    setTogglingId(u.id)
    try {
      await adminTenantsApi.updateUser(u.id, { is_active: !u.is_active })
      invalidate()
      toast.success(u.is_active ? '账号已停用' : '账号已启用')
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '操作失败' })
      toast.error(toToastMessage(norm), '操作失败')
    } finally {
      setTogglingId(null)
    }
  }

  return (
    <Drawer
      open
      onClose={onClose}
      title={`${tenant.name} · 用户`}
      subtitle={tenant.slug}
    >
      <div className="mb-4 flex justify-end">
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)}>
          开通客户账号
        </Button>
      </div>

      <div className="space-y-2">
        {usersQuery.isLoading && (
          <div className="empty-state text-[12px]">加载中…</div>
        )}
        {!usersQuery.isLoading && (usersQuery.data?.length ?? 0) === 0 && (
          <div className="empty-state text-[12px]">该租户下还没有账号。</div>
        )}
        {usersQuery.data?.map((u) => (
          <div
            key={u.id}
            className="rounded-md border border-border p-3 animate-fade-in"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="font-medium text-[13px] truncate">{u.username}</span>
                  {u.is_superadmin && (
                    <span className="badge badge-info text-[10px]">superadmin</span>
                  )}
                  <span className="badge badge-neutral text-[10px]">{roleLabel(u.role)}</span>
                </div>
                <div className="font-mono text-[11px] text-text-tertiary truncate">{u.email}</div>
              </div>
              <span className={u.is_active ? 'badge badge-positive' : 'badge badge-neutral'}>
                {u.is_active ? '启用' : '停用'}
              </span>
            </div>
            <div className="mt-2 flex gap-3 justify-end items-center">
              <button onClick={() => setEditing(u)} className="text-action">编辑</button>
              <button
                onClick={() => handleToggle(u)}
                disabled={togglingId === u.id}
                className="text-action"
              >
                {togglingId === u.id ? '处理中…' : u.is_active ? '停用' : '启用'}
              </button>
            </div>
          </div>
        ))}
      </div>

      {showCreate && (
        <CreateUserDialog
          tenant={tenant}
          open={showCreate}
          onClose={() => setShowCreate(false)}
          onSaved={invalidate}
        />
      )}

      {editing && (
        <EditUserDialog
          user={editing}
          open={!!editing}
          onClose={() => setEditing(null)}
          onSaved={invalidate}
        />
      )}
    </Drawer>
  )
}

// ── 开通客户账号 ──────────────────────────────────────────────────────────────

function CreateUserDialog({
  tenant, open, onClose, onSaved,
}: {
  tenant: Tenant
  open: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const toast = useToast()
  const reactId = useId()
  const ids = {
    username: `${reactId}-u`,
    email: `${reactId}-e`,
    password: `${reactId}-p`,
  }

  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const saveMutation = useMutation({
    mutationFn: () => {
      const body: CreateTenantUserRequest = {
        username: username.trim(),
        email: email.trim(),
        password,
      }
      return adminTenantsApi.createTenantUser(tenant.id, body).then((r) => r.data)
    },
    onSuccess: () => {
      onSaved()
      toast.success('客户账号已开通')
      onClose()
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '开通失败' })
      toast.error(toToastMessage(norm), '开通失败')
    },
  })

  const valid = username.trim() && email.trim() && password.length >= 6

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`在「${tenant.name}」下开通客户账号`}
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
            开通
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="text-[11px] text-text-tertiary">
          账号将以「外部客户」角色创建，仅可访问本租户数据。
        </div>
        <div>
          <label htmlFor={ids.username} className="field-label">用户名</label>
          <input
            id={ids.username}
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="登录用户名"
            className="input"
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor={ids.email} className="field-label">邮箱</label>
          <input
            id={ids.email}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="customer@example.com"
            className="input font-mono"
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor={ids.password} className="field-label">
            初始密码<span className="text-text-tertiary"> · 至少 6 位</span>
          </label>
          <input
            id={ids.password}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••"
            className="input font-mono"
            autoComplete="new-password"
          />
        </div>
      </div>
    </Dialog>
  )
}

// ── 编辑用户（改角色 / 重置密码）─────────────────────────────────────────────

function EditUserDialog({
  user, open, onClose, onSaved,
}: {
  user: AdminUser
  open: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const toast = useToast()
  const reactId = useId()
  const ids = { role: `${reactId}-role`, password: `${reactId}-pw` }

  const [role, setRole] = useState(user.role)
  const [password, setPassword] = useState('') // 留空 = 不改密码

  const saveMutation = useMutation({
    mutationFn: () => {
      const body: UpdateUserRequest = {}
      if (role !== user.role) body.role = role
      if (password !== '') body.password = password
      return adminTenantsApi.updateUser(user.id, body).then((r) => r.data)
    },
    onSuccess: () => {
      onSaved()
      toast.success('用户已更新')
      onClose()
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '保存失败' })
      toast.error(toToastMessage(norm), '保存失败')
    },
  })

  const passwordValid = password === '' || password.length >= 6

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`编辑用户 · ${user.username}`}
      width={460}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            size="md"
            disabled={!passwordValid}
            loading={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            保存
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label htmlFor={ids.role} className="field-label">角色</label>
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
          <label htmlFor={ids.password} className="field-label">
            重置密码<span className="text-text-tertiary"> · 留空保持原密码，至少 6 位</span>
          </label>
          <input
            id={ids.password}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••"
            className="input font-mono"
            autoComplete="new-password"
          />
        </div>
      </div>
    </Dialog>
  )
}
