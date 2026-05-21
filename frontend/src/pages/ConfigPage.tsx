import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { configApi } from '@/services'

interface ConfigField {
  key: string
  label: string
  placeholder: string
  type: 'text' | 'password' | 'textarea'
}

interface ConfigSection {
  title: string
  description: string
  fields: ConfigField[]
}

const CONFIG_SECTIONS: ConfigSection[] = [
  {
    title: 'LangSmith',
    description: '追踪与数据集服务',
    fields: [
      { key: 'langsmith.api_url', label: 'API 地址', placeholder: 'https://api.smith.langchain.com', type: 'text' },
      { key: 'langsmith.api_key', label: 'API 密钥', placeholder: '输入 API Key', type: 'password' },
    ],
  },
  {
    title: 'LLM 服务',
    description: '评估用 LLM（Judge 模型）',
    fields: [
      { key: 'llm.base_url', label: '服务地址', placeholder: 'https://api.openai.com/v1', type: 'text' },
      { key: 'llm.api_key', label: 'API 密钥', placeholder: '输入 API Key', type: 'password' },
    ],
  },
  {
    title: '测试目标模型',
    description: '被评估的 Agent POST 接口配置',
    fields: [
      { key: 'target_agent.endpoint_url', label: '接口地址', placeholder: 'https://your-agent.example.com/api/chat', type: 'text' },
      { key: 'target_agent.api_key', label: 'API 密钥（如需鉴权）', placeholder: '输入 API Key 或留空', type: 'password' },
      { key: 'target_agent.timeout', label: '超时时间（秒）', placeholder: '30', type: 'text' },
      { key: 'target_agent.request_template', label: '请求体模板', placeholder: '{"query": "{{question}}"}', type: 'textarea' },
      { key: 'target_agent.response_path', label: '响应提取路径', placeholder: 'data.answer', type: 'text' },
      { key: 'target_agent.headers', label: '自定义请求头', placeholder: '{"Content-Type": "application/json"}', type: 'textarea' },
    ],
  },
]

const ALL_CONFIG_KEYS = CONFIG_SECTIONS.flatMap((s) => s.fields.map((f) => f.key))

export default function ConfigPage() {
  const queryClient = useQueryClient()
  const [values, setValues] = useState<Record<string, string>>({})
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set())
  const initialized = useRef(false)

  const { data: configData, isLoading } = useQuery({
    queryKey: ['configs'],
    queryFn: () => configApi.list().then((r) => r.data),
    select: (data) => {
      const map: Record<string, string> = {}
      for (const item of data) {
        if (ALL_CONFIG_KEYS.includes(item.key)) {
          map[item.key] = typeof item.value === 'string' ? item.value : JSON.stringify(item.value)
        }
      }
      return map
    },
  })

  useEffect(() => {
    if (configData && !initialized.current) {
      initialized.current = true
      setValues((prev) => {
        const merged = { ...prev }
        for (const [k, v] of Object.entries(configData)) {
          if (!(k in merged)) merged[k] = v
        }
        return merged
      })
    }
  }, [configData])

  const updateMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      configApi.update(key, { value }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['configs'] })
      setSavedKeys((prev) => new Set(prev).add(variables.key))
      setTimeout(() => {
        setSavedKeys((prev) => {
          const next = new Set(prev)
          next.delete(variables.key)
          return next
        })
      }, 2000)
    },
  })

  if (isLoading) {
    return (
      <div>
        <div className="skeleton h-5 w-36 rounded mb-6" />
        {[1, 2, 3, 4].map((i) => <div key={i} className="skeleton h-14 w-full rounded mb-3" />)}
      </div>
    )
  }

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-lg font-light tracking-tight mb-1">配置</h1>
        <p className="text-[10px] text-text-tertiary tracking-widest uppercase">SYSTEM PARAMETERS · RUNTIME SETTINGS</p>
      </header>

      <div className="space-y-8 max-w-[560px]">
        {CONFIG_SECTIONS.map((section) => (
          <div key={section.title} className="bg-surface border border-border rounded-md p-6">
            <div className="mb-5">
              <h2 className="text-[13px] font-medium tracking-tight">{section.title}</h2>
              <p className="text-[10px] text-text-tertiary mt-0.5">{section.description}</p>
            </div>
            <div className="space-y-5">
              {section.fields.map((field) => (
                <div key={field.key} className="group">
                  <label
                    htmlFor={`cfg-${field.key}`}
                    className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors"
                  >
                    {field.label}
                  </label>
                  <div className="flex gap-2 items-start">
                    {field.type === 'textarea' ? (
                      <textarea
                        id={`cfg-${field.key}`}
                        placeholder={field.placeholder}
                        value={values[field.key] ?? ''}
                        onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
                        rows={3}
                        className="flex-1 py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200 font-mono resize-y"
                      />
                    ) : (
                      <input
                        id={`cfg-${field.key}`}
                        type={field.type}
                        placeholder={field.placeholder}
                        value={values[field.key] ?? ''}
                        onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
                        className="flex-1 py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface text-text-primary outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all duration-200 font-mono"
                      />
                    )}
                    <button
                      onClick={() => updateMutation.mutate({ key: field.key, value: values[field.key] ?? '' })}
                      disabled={updateMutation.isPending}
                      className="shrink-0 py-2 px-3 text-[10px] font-medium tracking-wide rounded-[6px] bg-accent text-white border border-accent cursor-pointer hover:opacity-90 active:scale-[0.97] disabled:opacity-40 transition-all duration-200"
                    >
                      {savedKeys.has(field.key) ? '已保存' : '保存'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
