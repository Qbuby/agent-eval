import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useConfirm, useToast } from '@/components/ui'
import { evaluatorProvidersApi } from '@/services'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type {
  CreateEvaluatorProviderRequest, EvaluatorProvider, TestProviderResponse,
} from '@/types'

const PROVIDER_TYPES: { value: string; label: string; hint?: string }[] = [
  { value: 'openai', label: 'OpenAI', hint: 'api.openai.com / Bearer auth' },
  { value: 'openai_compatible', label: 'OpenAI 兼容', hint: '同协议的第三方端点' },
  { value: 'anthropic', label: 'Anthropic', hint: 'api.anthropic.com / x-api-key' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'azure', label: 'Azure OpenAI' },
  { value: 'custom', label: 'Custom', hint: '其他 OpenAI 兼容端点' },
  { value: 'agent', label: 'Agent (SSE)', hint: '直接 SSE 连目标 agent 当裁判' },
]

export default function EvaluatorProvidersPage() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [editing, setEditing] = useState<EvaluatorProvider | null>(null)
  const [showEditor, setShowEditor] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<Record<string, TestProviderResponse>>({})
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['evaluator-providers'],
    queryFn: () => evaluatorProvidersApi.list().then(r => r.data),
  })

  const handleTest = async (p: EvaluatorProvider) => {
    setTestingId(p.id)
    try {
      const res = await evaluatorProvidersApi.test(p.id)
      setTestResult(prev => ({ ...prev, [p.id]: res.data }))
      if (res.data.ok) {
        toast.success(res.data.detail || '连接成功', '测试通过')
      } else {
        toast.error(res.data.detail || '连接失败', '测试失败')
      }
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '测试失败' })
      toast.error(toToastMessage(norm), '测试失败')
    } finally {
      setTestingId(null)
    }
  }

  const handleDelete = async (p: EvaluatorProvider) => {
    const ok = await confirm({
      title: '删除 Provider',
      description: `删除"${p.name}"？引用了它的评估器会变为"未配置"状态。`,
      confirmText: '删除',
      danger: true,
    })
    if (!ok) return
    setDeletingId(p.id)
    try {
      await evaluatorProvidersApi.remove(p.id)
      qc.invalidateQueries({ queryKey: ['evaluator-providers'] })
      toast.success('Provider 已删除')
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
        <div className="page-eyebrow">系统</div>
        <h1 className="page-title">LLM Judge Providers</h1>
        <p className="page-subtitle">
          配置评估器要调用的 LLM 端点（OpenAI / Anthropic / DeepSeek / 自建）。API key 在 DB 里 fernet 加密存储。
        </p>
      </header>

      <div className="section-row">
        <div className="page-eyebrow">Provider 列表</div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => { setEditing(null); setShowEditor(true) }}
        >
          新建 Provider
        </Button>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>Base URL</th>
              <th>默认模型</th>
              <th>API Key</th>
              <th className="w-20">状态</th>
              <th className="w-44 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr><td colSpan={7} className="empty-state">加载中…</td></tr>
            )}
            {!listQuery.isLoading && (listQuery.data?.length ?? 0) === 0 && (
              <tr><td colSpan={7} className="empty-state">
                还没有 Provider。新建一个，评估器就能选它作为 judge LLM 后端。
              </td></tr>
            )}
            {listQuery.data?.map(p => {
              const tested = testResult[p.id]
              return (
                <tr key={p.id} className="group">
                  <td className="font-medium">{p.name}</td>
                  <td>
                    <span className="badge badge-neutral font-mono text-[10px]">{p.provider_type}</span>
                  </td>
                  <td className="font-mono text-[11px] text-text-secondary">{p.base_url || '—'}</td>
                  <td className="font-mono text-[11px] text-text-secondary">{p.default_model || '—'}</td>
                  <td>
                    {p.has_api_key ? (
                      <span className="font-mono text-[11px] text-text-secondary">{p.api_key_masked}</span>
                    ) : (
                      <span className="badge badge-warning text-[10px]">未设置</span>
                    )}
                  </td>
                  <td>
                    <span className={p.is_active ? 'badge badge-positive' : 'badge badge-neutral'}>
                      {p.is_active ? '启用' : '停用'}
                    </span>
                  </td>
                  <td className="text-right">
                    <div className="flex gap-3 justify-end items-center">
                      {tested && (
                        <span
                          className={`text-[10px] ${tested.ok ? 'text-positive' : 'text-negative'}`}
                          title={tested.detail}
                        >
                          {tested.ok ? `✓ ${tested.latency_ms}ms` : '✗'}
                        </span>
                      )}
                      <button
                        onClick={() => handleTest(p)}
                        disabled={testingId === p.id}
                        className="text-action"
                      >
                        {testingId === p.id ? '测试中…' : '测试'}
                      </button>
                      <button
                        onClick={() => { setEditing(p); setShowEditor(true) }}
                        className="text-action"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => handleDelete(p)}
                        disabled={deletingId === p.id}
                        className="text-action-danger"
                      >
                        {deletingId === p.id ? '删除中…' : '删除'}
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {showEditor && (
        <ProviderEditor
          open={showEditor}
          editing={editing}
          onClose={() => { setShowEditor(false); setEditing(null) }}
        />
      )}
    </div>
  )
}


function ProviderEditor({
  open, editing, onClose,
}: {
  open: boolean
  editing: EvaluatorProvider | null
  onClose: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const reactId = useId()
  const ids = {
    name: `${reactId}-name`,
    type: `${reactId}-type`,
    baseUrl: `${reactId}-baseurl`,
    apiKey: `${reactId}-apikey`,
    model: `${reactId}-model`,
    active: `${reactId}-active`,
    agentMode: `${reactId}-agentmode`,
    agentLanguage: `${reactId}-agentlang`,
  }

  const [name, setName] = useState(editing?.name || '')
  const [providerType, setProviderType] = useState(editing?.provider_type || 'openai')
  const [baseUrl, setBaseUrl] = useState(editing?.base_url || '')
  const [apiKey, setApiKey] = useState('')  // never prefill - user has to re-type to change
  const [defaultModel, setDefaultModel] = useState(editing?.default_model || '')
  const [isActive, setIsActive] = useState(editing ? editing.is_active : true)
  // agent (SSE) 专属：SSE 事件模式 + 传给 agent 的 language，落进 extra_config。
  const [agentMode, setAgentMode] = useState<string>(
    (editing?.extra_config?.mode as string) || 'langgraph_v2',
  )
  const [agentLanguage, setAgentLanguage] = useState<string>(
    (editing?.extra_config?.language as string) || '请用中文回复',
  )
  const isAgent = providerType === 'agent'

  const saveMutation = useMutation({
    mutationFn: async () => {
      // agent 类型把 SSE 模式 / language 收进 extra_config；其他类型不带。
      const extraConfig = isAgent
        ? { mode: agentMode, language: agentLanguage }
        : undefined
      if (editing) {
        const body: Record<string, unknown> = {
          name, provider_type: providerType,
          base_url: baseUrl || null,
          default_model: defaultModel || null,
          is_active: isActive,
        }
        if (extraConfig) body.extra_config = extraConfig
        // Only include api_key in payload when user typed something:
        // empty string would mean "clear", undefined means "keep existing".
        if (apiKey !== '') body.api_key = apiKey
        return evaluatorProvidersApi.update(editing.id, body).then(r => r.data)
      }
      const body: CreateEvaluatorProviderRequest = {
        name, provider_type: providerType,
        base_url: baseUrl || null,
        api_key: apiKey || null,
        default_model: defaultModel || null,
        is_active: isActive,
        ...(extraConfig ? { extra_config: extraConfig } : {}),
      }
      return evaluatorProvidersApi.create(body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['evaluator-providers'] })
      toast.success(editing ? 'Provider 已更新' : 'Provider 已创建')
      onClose()
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '保存失败' })
      toast.error(toToastMessage(norm), '保存失败')
    },
  })

  const typeMeta = PROVIDER_TYPES.find(t => t.value === providerType)

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={editing ? '编辑 Provider' : '新建 Provider'}
      width={560}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            size="md"
            disabled={!name.trim()}
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
          <label htmlFor={ids.name} className="field-label">名称（唯一）</label>
          <input
            id={ids.name}
            type="text" value={name} onChange={e => setName(e.target.value)}
            placeholder="例如：OpenAI 主账号"
            className="input"
          />
        </div>

        <div>
          <label htmlFor={ids.type} className="field-label">类型</label>
          <select
            id={ids.type}
            value={providerType}
            onChange={e => setProviderType(e.target.value)}
            className="input"
          >
            {PROVIDER_TYPES.map(t => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
          {typeMeta?.hint && (
            <div className="mt-1.5 text-[10px] text-text-tertiary">{typeMeta.hint}</div>
          )}
        </div>

        <div>
          <label htmlFor={ids.baseUrl} className="field-label">
            {isAgent ? 'Agent SSE URL' : 'Base URL'}
            {providerType === 'openai' && <span className="text-text-tertiary"> · 可选（默认 api.openai.com）</span>}
            {isAgent && <span className="text-text-tertiary"> · 必填</span>}
          </label>
          <input
            id={ids.baseUrl}
            type="text" value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
            placeholder={isAgent
              ? 'http://host.docker.internal:18094/api/agent/langgraph'
              : 'https://kiro.aidong-ai.com/v1'}
            className="input font-mono"
          />
        </div>

        {isAgent && (
          <>
            <div>
              <label htmlFor={ids.agentMode} className="field-label">SSE 模式</label>
              <select
                id={ids.agentMode}
                value={agentMode}
                onChange={e => setAgentMode(e.target.value)}
                className="input"
              >
                <option value="langgraph_v2">LangGraph v2（astream_events）</option>
                <option value="generic">Generic（payload.response）</option>
              </select>
              <div className="mt-1.5 text-[10px] text-text-tertiary">
                目标 agent 的流式协议。默认 LangGraph v2，与被测 agent 一致。
              </div>
            </div>
            <div>
              <label htmlFor={ids.agentLanguage} className="field-label">Language 参数</label>
              <input
                id={ids.agentLanguage}
                type="text" value={agentLanguage} onChange={e => setAgentLanguage(e.target.value)}
                placeholder="请用中文回复"
                className="input"
              />
              <div className="mt-1.5 text-[10px] text-text-tertiary">
                随请求传给 agent 的 language（LangGraph v2 模式）。
              </div>
            </div>
          </>
        )}

        {/* agent (SSE) 端点无 API Key / 模型概念 —— 端点本身就是裁判，隐藏这两项。 */}
        {!isAgent && (
          <div>
            <label htmlFor={ids.apiKey} className="field-label">
              API Key
              {editing && (
                <span className="text-text-tertiary"> · 留空保持原样，输入空格后清空</span>
              )}
            </label>
            <input
              id={ids.apiKey}
              type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
              placeholder={editing && editing.has_api_key ? editing.api_key_masked : 'sk-...'}
              className="input font-mono"
              autoComplete="new-password"
            />
          </div>
        )}

        {!isAgent && (
          <div>
            <label htmlFor={ids.model} className="field-label">默认模型（可选）</label>
            <input
              id={ids.model}
              type="text" value={defaultModel} onChange={e => setDefaultModel(e.target.value)}
              placeholder="例如：gpt-4o-mini / claude-3-5-sonnet-20241022"
              className="input font-mono"
            />
            <div className="mt-1.5 text-[10px] text-text-tertiary">
              评估器没指定 model 时回退到这个值。可在评估器编辑器里覆盖。
            </div>
          </div>
        )}

        <label htmlFor={ids.active} className="inline-flex items-center gap-2 text-[12px] cursor-pointer">
          <input
            id={ids.active}
            type="checkbox" checked={isActive}
            onChange={e => setIsActive(e.target.checked)}
            className="accent-accent"
          />
          启用（停用后评估器无法选它）
        </label>
      </div>
    </Dialog>
  )
}
