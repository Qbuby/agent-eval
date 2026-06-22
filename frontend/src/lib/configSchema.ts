// Per-key metadata for the system_config UI.
//
// Drives the Config CRUD page: when adding/editing a row, we look up the
// key here to pick the right input control and offer sensible default
// values. Unknown keys still work (free-text input), they just don't get
// suggestions.

export type ConfigValueType =
  | 'text'        // single-line free-form
  | 'url'         // single-line, monospace, URL placeholder
  | 'password'    // masked input, no suggestions
  | 'number'      // numeric input
  | 'json'        // multi-line, monospace, JSON-pretty placeholder
  | 'select'      // suggestions are the only allowed values
  | 'textarea'    // multi-line free-form

export interface ConfigSchemaEntry {
  key: string
  label: string
  category: string
  type: ConfigValueType
  description?: string
  placeholder?: string
  suggestions?: Array<{ value: string; label?: string }>
}

// Order matters — used as a "known keys" dropdown in creation.
export const CONFIG_SCHEMA: ConfigSchemaEntry[] = [
  {
    key: 'langsmith.api_url',
    label: 'LangSmith API 地址',
    category: 'langsmith',
    type: 'url',
    placeholder: 'https://api.smith.langchain.com',
    suggestions: [
      { value: 'https://api.smith.langchain.com', label: '官方默认' },
    ],
  },
  {
    key: 'langsmith.api_key',
    label: 'LangSmith API Key',
    category: 'langsmith',
    type: 'password',
    description: '有 read 权限即可',
  },
  {
    key: 'langsmith.connection',
    label: 'LangSmith 连接预设',
    category: 'langsmith',
    type: 'json',
    description: '一组 {api_url, api_key} 凭据。可配多组，标记一个为默认即当前生效连接。',
    placeholder: '{"api_url": "https://api.smith.langchain.com", "api_key": ""}',
    suggestions: [
      { value: '{"api_url": "https://api.smith.langchain.com", "api_key": ""}', label: '官方默认' },
    ],
  },
  {
    key: 'langfuse.connection',
    label: 'Langfuse 连接预设',
    category: 'langfuse',
    type: 'json',
    description: '一组 {host, public_key, secret_key, remote_write} 凭据。可配多组，标记一个为默认即当前生效连接。',
    placeholder: '{"host": "https://cloud.langfuse.com", "public_key": "", "secret_key": "", "remote_write": false}',
    suggestions: [
      { value: '{"host": "https://cloud.langfuse.com", "public_key": "", "secret_key": "", "remote_write": false}', label: 'Langfuse Cloud' },
    ],
  },

  {
    key: 'llm.base_url',
    label: 'LLM 服务地址',
    category: 'llm',
    type: 'url',
    placeholder: 'https://api.openai.com/v1',
    suggestions: [
      { value: 'https://api.openai.com/v1', label: 'OpenAI' },
      { value: 'https://kiro.aidong-ai.com/v1', label: '内部代理' },
      { value: 'https://api.anthropic.com/v1', label: 'Anthropic' },
    ],
  },
  {
    key: 'llm.api_key',
    label: 'LLM API Key',
    category: 'llm',
    type: 'password',
  },
  {
    key: 'llm.judge_model',
    label: 'Judge 模型',
    category: 'llm',
    type: 'text',
    description: 'LLM-as-judge 用的模型名',
    suggestions: [
      { value: 'claude-opus-4-7' },
      { value: 'claude-sonnet-4-6' },
      { value: 'claude-haiku-4-5' },
      { value: 'gpt-4o' },
      { value: 'gpt-4o-mini' },
    ],
  },

  {
    key: 'target_agent.endpoint_url',
    label: '智能体 URL',
    category: 'target_agent',
    type: 'url',
    placeholder: 'https://your-agent.example.com/api/chat',
  },
  {
    key: 'target_agent.api_key',
    label: '智能体 API Key',
    category: 'target_agent',
    type: 'password',
    description: '如需鉴权',
  },
  {
    key: 'target_agent.timeout',
    label: '请求超时（秒）',
    category: 'target_agent',
    type: 'number',
    placeholder: '30',
    suggestions: [
      { value: '30' }, { value: '60' }, { value: '120' }, { value: '300' },
    ],
  },
  {
    key: 'target_agent.request_template',
    label: '请求体模板',
    category: 'target_agent',
    type: 'json',
    description: '用 {{question}} 作为问题占位符',
    placeholder: '{"query": "{{question}}"}',
    suggestions: [
      { value: '{"query": "{{question}}"}', label: '简单 query' },
      { value: '{"messages": [{"role": "user", "content": "{{question}}"}]}', label: 'OpenAI 兼容' },
    ],
  },
  {
    key: 'target_agent.response_path',
    label: '响应提取路径',
    category: 'target_agent',
    type: 'text',
    description: '点分隔，例如 data.answer',
    placeholder: 'data.answer',
    suggestions: [
      { value: 'data.answer' },
      { value: 'choices.0.message.content', label: 'OpenAI 兼容' },
      { value: 'output' },
      { value: 'response' },
    ],
  },
  {
    key: 'target_agent.headers',
    label: '自定义请求头',
    category: 'target_agent',
    type: 'json',
    placeholder: '{"Content-Type": "application/json"}',
    suggestions: [
      { value: '{"Content-Type": "application/json"}', label: '基础 JSON' },
    ],
  },

  {
    key: 'eval.retry.max_retries',
    label: '最大重试次数',
    category: 'eval.retry',
    type: 'number',
    description: '不含首次。0 表示不重试。',
    suggestions: [{ value: '0' }, { value: '1' }, { value: '2' }, { value: '3' }, { value: '5' }],
  },
  {
    key: 'eval.retry.initial_backoff_s',
    label: '首次退避（秒）',
    category: 'eval.retry',
    type: 'number',
    suggestions: [{ value: '1' }, { value: '2' }, { value: '5' }, { value: '10' }],
  },
  {
    key: 'eval.retry.backoff_factor',
    label: '退避乘数',
    category: 'eval.retry',
    type: 'number',
    suggestions: [{ value: '1.5' }, { value: '2' }, { value: '3' }],
  },
  {
    key: 'eval.retry.max_backoff_s',
    label: '退避上限（秒）',
    category: 'eval.retry',
    type: 'number',
    suggestions: [{ value: '15' }, { value: '30' }, { value: '60' }, { value: '120' }],
  },
]

const SCHEMA_INDEX: Record<string, ConfigSchemaEntry> = Object.fromEntries(
  CONFIG_SCHEMA.map(s => [s.key, s]),
)

// Categories shown in filters / "new entry" form. The values here cover
// every entry in CONFIG_SCHEMA; unknown keys infer category by prefix.
export const CONFIG_CATEGORIES: Array<{ value: string; label: string }> = [
  { value: 'langsmith', label: 'LangSmith' },
  { value: 'langfuse', label: 'Langfuse' },
  { value: 'llm', label: 'LLM 服务' },
  { value: 'target_agent', label: '测试目标模型' },
  { value: 'eval.retry', label: '评估重试' },
  { value: 'general', label: '其他' },
]

export function getConfigSchema(key: string): ConfigSchemaEntry | undefined {
  return SCHEMA_INDEX[key]
}

// Used when the row's key isn't in CONFIG_SCHEMA — best effort guess
// from the dotted prefix (mirrors backend ConfigService._infer_category).
export function inferConfigCategory(key: string): string {
  const parts = key.split('.')
  if (parts[0] === 'eval' && parts.length >= 2) return `eval.${parts[1]}`
  if (['langsmith', 'langfuse', 'llm', 'target_agent', 'langfuse_metrics'].includes(parts[0])) return parts[0]
  return 'general'
}

export function inferConfigType(key: string, value: unknown): ConfigValueType {
  const hit = SCHEMA_INDEX[key]
  if (hit) return hit.type
  if (key.endsWith('api_key') || key.endsWith('secret')) return 'password'
  if (key.includes('url')) return 'url'
  if (typeof value === 'number') return 'number'
  if (typeof value === 'object') return 'json'
  if (typeof value === 'string' && (value.startsWith('{') || value.startsWith('['))) return 'json'
  return 'text'
}
