import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { configApi } from '@/services'
import { Button, Drawer, useConfirm, useToast } from '@/components/ui'
import {
  CONFIG_CATEGORIES,
  CONFIG_SCHEMA,
  ConfigSchemaEntry,
  ConfigValueType,
  getConfigSchema,
  inferConfigCategory,
  inferConfigType,
} from '@/lib/configSchema'
import type { ConfigItem, ConfigOption } from '@/types'

type FormMode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; row: ConfigItem }

const SECRET_PLACEHOLDER = '••••••••'

export default function ConfigPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [form, setForm] = useState<FormMode>({ kind: 'closed' })

  const { data: configs = [], isLoading } = useQuery({
    queryKey: ['configs'],
    queryFn: () => configApi.list().then(r => r.data),
  })

  const filtered = useMemo(() => {
    return configs.filter(item => {
      if (categoryFilter && item.category !== categoryFilter) return false
      if (search) {
        const s = search.toLowerCase()
        const hay = `${item.key} ${item.description ?? ''}`.toLowerCase()
        if (!hay.includes(s)) return false
      }
      return true
    })
  }, [configs, search, categoryFilter])

  const deleteMutation = useMutation({
    mutationFn: (key: string) => configApi.delete(key),
    onSuccess: (_data, key) => {
      qc.invalidateQueries({ queryKey: ['configs'] })
      toast.success(`已删除 ${key}`)
    },
    onError: (err) => toast.error(extractError(err), '删除失败'),
  })

  const handleDelete = async (item: ConfigItem) => {
    const ok = await confirm({
      title: '删除配置',
      description: `确定删除 ${item.key}？删除后服务会回退到环境变量或代码默认值。`,
      confirmText: '删除',
      danger: true,
    })
    if (!ok) return
    deleteMutation.mutate(item.key)
  }

  const editingRow = form.kind === 'edit'
    ? configs.find(c => c.key === form.row.key) ?? form.row
    : null

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">系统</div>
        <h1 className="page-title">配置</h1>
        <p className="page-subtitle">一个 key 可配置多个候选值，标记一个为默认（敏感项 auth./db. 不在此暴露）</p>
      </header>

      <div className="toolbar">
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="按 key / 描述搜索"
          className="input-sm w-[260px]"
        />
        <select
          value={categoryFilter}
          onChange={e => setCategoryFilter(e.target.value)}
          className="select-sm"
        >
          <option value="">全部分类</option>
          {CONFIG_CATEGORIES.map(c => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
        <span className="text-[11px] text-text-tertiary">
          共 {filtered.length} / {configs.length} 条
        </span>
        <div className="ml-auto">
          <Button variant="primary" size="sm" onClick={() => setForm({ kind: 'create' })}>
            新建配置
          </Button>
        </div>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>Key</th>
              <th className="w-28">分类</th>
              <th>默认值</th>
              <th className="w-20 text-right">选项数</th>
              <th>描述</th>
              <th className="w-36">更新时间</th>
              <th className="w-28 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={7} className="empty-state">加载中…</td></tr>
            )}
            {!isLoading && filtered.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">
                  {configs.length === 0
                    ? '还没有配置项。点右上角"新建配置"添加。'
                    : '没有匹配的配置项。'}
                </td>
              </tr>
            )}
            {filtered.map(item => (
              <ConfigRow
                key={item.key}
                item={item}
                onEdit={() => setForm({ kind: 'edit', row: item })}
                onDelete={() => handleDelete(item)}
                deleting={deleteMutation.isPending && deleteMutation.variables === item.key}
              />
            ))}
          </tbody>
        </table>
      </div>

      <Drawer
        open={form.kind !== 'closed'}
        onClose={() => setForm({ kind: 'closed' })}
        title={form.kind === 'edit' ? `编辑：${form.row.key}` : '新建配置'}
        subtitle={
          form.kind === 'edit'
            ? form.row.description || form.row.category
            : '从已知 key 列表选择，或自定义新 key'
        }
      >
        {form.kind === 'create' && (
          <CreateForm
            existingKeys={configs.map(c => c.key)}
            onClose={() => setForm({ kind: 'closed' })}
          />
        )}
        {form.kind === 'edit' && editingRow && (
          <EditForm
            row={editingRow}
            onClose={() => setForm({ kind: 'closed' })}
          />
        )}
      </Drawer>
    </div>
  )
}

function ConfigRow({ item, onEdit, onDelete, deleting }: {
  item: ConfigItem
  onEdit: () => void
  onDelete: () => void
  deleting: boolean
}) {
  const schema = getConfigSchema(item.key)
  const type = schema?.type ?? inferConfigType(item.key, item.value)
  const display = formatValueForDisplay(item.value, type)
  const isSecret = type === 'password'
  const optionCount = item.options.length

  return (
    <tr className="group">
      <td className="font-mono text-[11px]">
        <div className="flex flex-col">
          <span className="text-text-primary">{item.key}</span>
          {schema && <span className="text-[10px] text-text-tertiary">{schema.label}</span>}
        </div>
      </td>
      <td>
        <span className="badge badge-neutral">{item.category}</span>
      </td>
      <td className="font-mono text-[11px]">
        <div className="max-w-[420px] truncate" title={isSecret ? '' : display}>
          {isSecret && display ? SECRET_PLACEHOLDER : (display || '—')}
        </div>
      </td>
      <td className="text-right tabular-nums">
        <span className={optionCount > 1 ? 'badge badge-accent' : 'badge badge-neutral'}>
          {optionCount}
        </span>
      </td>
      <td>
        <div className="max-w-[260px] truncate text-text-secondary" title={item.description ?? ''}>
          {item.description || '—'}
        </div>
      </td>
      <td className="text-text-tertiary text-[11px]">
        {fmtTime(item.updated_at)}
      </td>
      <td className="text-right">
        <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={onEdit}
            className="text-action"
          >
            编辑
          </button>
          <button
            onClick={onDelete}
            disabled={deleting}
            className="text-action-danger"
          >
            {deleting ? '删除中…' : '删除'}
          </button>
        </div>
      </td>
    </tr>
  )
}


// ─── Create form ───────────────────────────────────────────────────────────

function CreateForm({ existingKeys, onClose }: {
  existingKeys: string[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const [keyMode, setKeyMode] = useState<'known' | 'custom'>('known')
  const [knownKey, setKnownKey] = useState('')
  const [customKey, setCustomKey] = useState('')

  const effectiveKey = keyMode === 'known' ? knownKey : customKey.trim()
  const schema = getConfigSchema(effectiveKey)
  const inferredType: ConfigValueType = schema?.type ?? 'text'

  const [valueText, setValueText] = useState(schema?.suggestions?.[0]?.value ?? '')
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState(schema?.description ?? '')

  useEffect(() => {
    if (keyMode !== 'known') return
    if (!schema) return
    setValueText(schema.suggestions?.[0]?.value ?? '')
    setDescription(schema.description ?? '')
  }, [keyMode, schema])

  const createMutation = useMutation({
    mutationFn: ({ key, value, desc }: { key: string; value: unknown; desc?: string }) =>
      configApi.update(key, { value, description: desc }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['configs'] })
      toast.success('已创建')
      onClose()
    },
    onError: (err) => toast.error(extractError(err), '创建失败'),
  })

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!effectiveKey) {
      toast.error('请选择或填写 key')
      return
    }
    if (existingKeys.includes(effectiveKey)) {
      toast.error(`配置 ${effectiveKey} 已存在，请编辑现有项`)
      return
    }
    const parsed = parseValueByType(valueText, inferredType)
    if (parsed.error) {
      toast.error(parsed.error)
      return
    }
    void label
    createMutation.mutate({
      key: effectiveKey,
      value: parsed.value,
      desc: description.trim() || undefined,
    })
  }

  const knownKeyOptions = useMemo(
    () => CONFIG_SCHEMA.filter(s => !existingKeys.includes(s.key)),
    [existingKeys],
  )

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <div>
        <label className="field-label">Key</label>
        <div className="flex flex-col gap-2">
          <div className="flex gap-3 text-[12px]">
            {(['known', 'custom'] as const).map(m => (
              <label key={m} className="inline-flex items-center gap-1.5 cursor-pointer">
                <input
                  type="radio"
                  checked={keyMode === m}
                  onChange={() => setKeyMode(m)}
                  className="accent-accent"
                />
                {m === 'known' ? '已知 key' : '自定义'}
              </label>
            ))}
          </div>
          {keyMode === 'known' ? (
            <select
              value={knownKey}
              onChange={e => setKnownKey(e.target.value)}
              className="input"
            >
              <option value="">— 选择已知 key —</option>
              {knownKeyOptions.map(s => (
                <option key={s.key} value={s.key}>
                  {s.key} · {s.label}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={customKey}
              onChange={e => setCustomKey(e.target.value)}
              placeholder="例如 my_module.feature_flag"
              className="input font-mono"
            />
          )}
          {effectiveKey && (
            <div className="text-[10px] text-text-tertiary">
              分类：<span className="font-mono">{schema?.category ?? inferConfigCategory(effectiveKey)}</span>
              {' · 类型：'}<span className="font-mono">{inferredType}</span>
            </div>
          )}
        </div>
      </div>

      <div>
        <label className="field-label">初始值</label>
        <ValueInput
          type={inferredType}
          value={valueText}
          onChange={setValueText}
          schema={schema}
          listId={`config-list-${effectiveKey}`}
        />
        <SuggestionChips
          schema={schema}
          inferredType={inferredType}
          onPick={setValueText}
        />
      </div>

      <div>
        <label className="field-label">标签（可选）</label>
        <input
          type="text"
          value={label}
          onChange={e => setLabel(e.target.value)}
          placeholder="如 生产 / 测试"
          className="input"
        />
      </div>

      <div>
        <label className="field-label">描述（可选）</label>
        <textarea
          value={description}
          onChange={e => setDescription(e.target.value)}
          rows={2}
          className="input"
          placeholder={schema?.description ?? '简单说明这个配置项的作用'}
        />
      </div>

      <p className="text-[10px] text-text-tertiary">
        创建后，可在编辑页继续添加更多候选值。
      </p>

      <div className="flex items-center gap-2 pt-3 border-t border-separator">
        <Button type="submit" variant="primary" size="md" loading={createMutation.isPending} disabled={!effectiveKey}>
          创建
        </Button>
        <Button type="button" variant="secondary" size="md" onClick={onClose}>
          取消
        </Button>
      </div>
    </form>
  )
}


// ─── Edit form ─────────────────────────────────────────────────────────────

function EditForm({ row, onClose }: {
  row: ConfigItem
  onClose: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()

  const schema = getConfigSchema(row.key)
  const type: ConfigValueType = schema?.type ?? inferConfigType(row.key, row.value)

  const [description, setDescription] = useState(row.description ?? '')
  const [descSaving, setDescSaving] = useState(false)

  const [newValue, setNewValue] = useState(schema?.suggestions?.[0]?.value ?? '')
  const [newLabel, setNewLabel] = useState('')
  const [makeDefault, setMakeDefault] = useState(false)

  const [editIdx, setEditIdx] = useState<number | null>(null)
  const [editValueText, setEditValueText] = useState('')
  const [editLabelText, setEditLabelText] = useState('')

  const invalidate = () => qc.invalidateQueries({ queryKey: ['configs'] })

  const addOptionMutation = useMutation({
    mutationFn: () => {
      const parsed = parseValueByType(newValue, type)
      if (parsed.error) throw new Error(parsed.error)
      return configApi.addOption(row.key, {
        value: parsed.value,
        label: newLabel.trim() || null,
        make_default: makeDefault,
      })
    },
    onSuccess: () => {
      invalidate()
      toast.success('已添加候选值')
      setNewValue(schema?.suggestions?.[0]?.value ?? '')
      setNewLabel('')
      setMakeDefault(false)
    },
    onError: (err) => toast.error(extractError(err), '添加失败'),
  })

  const updateOptionMutation = useMutation({
    mutationFn: ({ index, value, label }: { index: number; value: string; label: string }) => {
      const parsed = parseValueByType(value, type)
      if (parsed.error) throw new Error(parsed.error)
      return configApi.updateOption(row.key, index, {
        value: parsed.value,
        label: label.trim() || null,
      })
    },
    onSuccess: () => {
      invalidate()
      toast.success('已保存')
      setEditIdx(null)
    },
    onError: (err) => toast.error(extractError(err), '保存失败'),
  })

  const removeOptionMutation = useMutation({
    mutationFn: (index: number) => configApi.removeOption(row.key, index),
    onSuccess: () => {
      invalidate()
      toast.success('已删除候选值')
    },
    onError: (err) => toast.error(extractError(err), '删除失败'),
  })

  const setDefaultMutation = useMutation({
    mutationFn: (index: number) => configApi.setDefault(row.key, index),
    onSuccess: () => {
      invalidate()
      toast.success('已切换默认值')
    },
    onError: (err) => toast.error(extractError(err), '设置失败'),
  })

  const onSaveDescription = async () => {
    setDescSaving(true)
    try {
      const def = row.options[row.default_index]?.value ?? ''
      await configApi.update(row.key, { value: def, description: description.trim() || undefined })
      invalidate()
      toast.success('描述已更新')
    } catch (err) {
      toast.error(extractError(err), '保存失败')
    } finally {
      setDescSaving(false)
    }
  }

  const startEdit = (idx: number) => {
    setEditIdx(idx)
    setEditValueText(formatValueForInput(row.options[idx].value, type))
    setEditLabelText(row.options[idx].label ?? '')
  }

  const isSecret = type === 'password'

  return (
    <div className="flex flex-col gap-5">
      <div>
        <label className="field-label">Key · 类型 · 分类</label>
        <div className="font-mono text-[12px] text-text-primary py-2 px-3 border border-border rounded-md bg-fill/5">
          {row.key}
          <span className="ml-2 text-[10px] text-text-tertiary">类型 {type} · 分类 {row.category}</span>
        </div>
      </div>

      <div>
        <label className="field-label">描述</label>
        <div className="flex gap-2 items-start">
          <textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            rows={2}
            className="input flex-1"
            placeholder={schema?.description ?? '简单说明这个配置项的作用'}
          />
          <Button type="button" variant="secondary" size="sm" loading={descSaving} onClick={onSaveDescription}>
            保存描述
          </Button>
        </div>
      </div>

      <div>
        <label className="field-label">已配置的候选值（{row.options.length}）</label>
        <div className="table-card">
          <table className="table-base">
            <thead>
              <tr>
                <th className="w-12">默认</th>
                <th>值</th>
                <th className="w-36">标签</th>
                <th className="w-32 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {row.options.map((opt, idx) => (
                <OptionRow
                  key={idx}
                  opt={opt}
                  idx={idx}
                  isDefault={idx === row.default_index}
                  isOnly={row.options.length === 1}
                  isSecret={isSecret}
                  type={type}
                  editing={editIdx === idx}
                  editValue={editValueText}
                  editLabel={editLabelText}
                  onEditValue={setEditValueText}
                  onEditLabel={setEditLabelText}
                  onStart={() => startEdit(idx)}
                  onCancel={() => setEditIdx(null)}
                  onSave={() => updateOptionMutation.mutate({ index: idx, value: editValueText, label: editLabelText })}
                  onSetDefault={() => setDefaultMutation.mutate(idx)}
                  onRemove={() => removeOptionMutation.mutate(idx)}
                  saving={updateOptionMutation.isPending}
                  removing={removeOptionMutation.isPending && removeOptionMutation.variables === idx}
                  settingDefault={setDefaultMutation.isPending && setDefaultMutation.variables === idx}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="border border-dashed border-border rounded-lg p-3 bg-fill/5">
        <div className="page-eyebrow mb-2">添加候选值</div>
        <div className="flex flex-col gap-2">
          <ValueInput
            type={type}
            value={newValue}
            onChange={setNewValue}
            schema={schema}
            listId={`config-add-${row.key}`}
          />
          <SuggestionChips
            schema={schema}
            inferredType={type}
            onPick={setNewValue}
          />
          <input
            type="text"
            value={newLabel}
            onChange={e => setNewLabel(e.target.value)}
            placeholder="标签（可选，比如 生产 / 测试）"
            className="input"
          />
          <label className="inline-flex items-center gap-1.5 text-[11px] cursor-pointer">
            <input
              type="checkbox"
              checked={makeDefault}
              onChange={e => setMakeDefault(e.target.checked)}
              className="accent-accent"
            />
            添加后设为默认
          </label>
          <div>
            <Button
              type="button"
              variant="primary"
              size="sm"
              loading={addOptionMutation.isPending}
              disabled={newValue === ''}
              onClick={() => addOptionMutation.mutate()}
            >
              添加候选值
            </Button>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 pt-3 border-t border-separator">
        <Button type="button" variant="secondary" size="md" onClick={onClose}>关闭</Button>
      </div>
    </div>
  )
}

function OptionRow({
  opt, idx, isDefault, isOnly, isSecret, type,
  editing, editValue, editLabel, onEditValue, onEditLabel,
  onStart, onCancel, onSave, onSetDefault, onRemove,
  saving, removing, settingDefault,
}: {
  opt: ConfigOption
  idx: number
  isDefault: boolean
  isOnly: boolean
  isSecret: boolean
  type: ConfigValueType
  editing: boolean
  editValue: string
  editLabel: string
  onEditValue: (v: string) => void
  onEditLabel: (v: string) => void
  onStart: () => void
  onCancel: () => void
  onSave: () => void
  onSetDefault: () => void
  onRemove: () => void
  saving: boolean
  removing: boolean
  settingDefault: boolean
}) {
  const display = formatValueForDisplay(opt.value, type)
  void idx

  if (editing) {
    return (
      <tr>
        <td className="align-top">
          {isDefault ? <span className="text-[12px] text-accent">★</span> : null}
        </td>
        <td className="align-top" colSpan={2}>
          <div className="flex flex-col gap-1.5">
            {(type === 'json' || type === 'textarea') ? (
              <textarea
                value={editValue}
                onChange={e => onEditValue(e.target.value)}
                rows={3}
                className="input font-mono text-[11px]"
              />
            ) : (
              <input
                type={type === 'password' ? 'password' : (type === 'number' ? 'number' : 'text')}
                value={editValue}
                onChange={e => onEditValue(e.target.value)}
                className="input font-mono"
              />
            )}
            <input
              type="text"
              value={editLabel}
              onChange={e => onEditLabel(e.target.value)}
              placeholder="标签（可选）"
              className="input"
            />
          </div>
        </td>
        <td className="text-right align-top">
          <div className="flex justify-end gap-3">
            <button onClick={onSave} disabled={saving} className="text-[11px] text-accent hover:text-accent-hover transition-colors disabled:opacity-40">
              {saving ? '保存中…' : '保存'}
            </button>
            <button onClick={onCancel} className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors">取消</button>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <tr className={`group ${isDefault ? 'bg-accent/5' : ''}`}>
      <td className="align-top">
        {isDefault ? (
          <span className="text-[12px] text-accent" title="默认值">★</span>
        ) : (
          <button
            onClick={onSetDefault}
            disabled={settingDefault}
            className="text-[12px] text-text-tertiary hover:text-accent transition-colors disabled:opacity-40"
            title="设为默认"
          >
            ☆
          </button>
        )}
      </td>
      <td className="font-mono text-[11px] align-top">
        <div className="max-w-[320px] truncate" title={isSecret ? '' : display}>
          {isSecret && display ? SECRET_PLACEHOLDER : (display || '—')}
        </div>
      </td>
      <td className="align-top text-text-secondary">{opt.label || '—'}</td>
      <td className="text-right align-top">
        <div className="flex justify-end gap-3 opacity-0 group-hover:opacity-100 transition-opacity">
          <button onClick={onStart} className="text-action">编辑</button>
          <button
            onClick={onRemove}
            disabled={isOnly || removing}
            className="text-action-danger disabled:opacity-30"
            title={isOnly ? '至少保留一个候选值' : '删除该候选值'}
          >
            {removing ? '删除中…' : '删除'}
          </button>
        </div>
      </td>
    </tr>
  )
}


// ─── Inputs / helpers ──────────────────────────────────────────────────────

function ValueInput({ type, value, onChange, schema, listId }: {
  type: ConfigValueType
  value: string
  onChange: (v: string) => void
  schema?: ConfigSchemaEntry
  listId: string
}) {
  if (type === 'select' && schema?.suggestions) {
    return (
      <select value={value} onChange={e => onChange(e.target.value)} className="input">
        {schema.suggestions.map(s => (
          <option key={s.value} value={s.value}>{s.label || s.value}</option>
        ))}
      </select>
    )
  }

  if (type === 'json' || type === 'textarea') {
    return (
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        rows={4}
        placeholder={schema?.placeholder}
        className="input font-mono text-[11px] resize-y"
      />
    )
  }

  if (type === 'password') {
    return (
      <input
        type="password"
        value={value}
        onChange={e => onChange(e.target.value)}
        className="input font-mono"
        autoComplete="new-password"
      />
    )
  }

  const inputType = type === 'number' ? 'number' : 'text'
  const hasList = (schema?.suggestions?.length ?? 0) > 0
  return (
    <>
      <input
        type={inputType}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={schema?.placeholder}
        list={hasList ? listId : undefined}
        className="input font-mono"
      />
      {hasList && (
        <datalist id={listId}>
          {schema!.suggestions!.map(s => (
            <option key={s.value} value={s.value}>{s.label || ''}</option>
          ))}
        </datalist>
      )}
    </>
  )
}

function SuggestionChips({ schema, inferredType, onPick }: {
  schema: ConfigSchemaEntry | undefined
  inferredType: ConfigValueType
  onPick: (v: string) => void
}) {
  if (!schema?.suggestions || schema.suggestions.length === 0) return null
  if (inferredType === 'select') return null
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      <span className="text-[10px] text-text-tertiary">候选：</span>
      {schema.suggestions.map(s => (
        <button
          key={s.value}
          type="button"
          onClick={() => onPick(s.value)}
          className="text-[10px] font-mono px-2 py-0.5 rounded-full border border-border bg-fill/5 text-text-secondary hover:border-accent hover:text-accent transition-colors"
          title={s.label || ''}
        >
          {s.label ? s.label : truncate(s.value, 30)}
        </button>
      ))}
    </div>
  )
}


// ─── Value parsing / formatting ───────────────────────────────────────────

function parseValueByType(text: string, type: ConfigValueType): { value: unknown; error?: string } {
  if (type === 'number') {
    const n = Number(text)
    if (Number.isNaN(n)) return { value: text, error: '请输入合法数字' }
    return { value: n }
  }
  if (type === 'json') {
    const trimmed = text.trim()
    if (!trimmed) return { value: '' }
    try { return { value: JSON.parse(trimmed) } } catch {
      return { value: text }
    }
  }
  return { value: text }
}

function formatValueForDisplay(value: unknown, type: ConfigValueType): string {
  if (value == null) return ''
  if (type === 'json' && typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  return String(value)
}

function formatValueForInput(value: unknown, type: ConfigValueType): string {
  if (value == null) return ''
  if (type === 'json' && typeof value === 'object') {
    try { return JSON.stringify(value, null, 2) } catch { return String(value) }
  }
  if (typeof value === 'object') {
    try { return JSON.stringify(value, null, 2) } catch { return String(value) }
  }
  return String(value)
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function extractError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: string } }; message?: string }
  return e?.response?.data?.detail || e?.message || '未知错误'
}
