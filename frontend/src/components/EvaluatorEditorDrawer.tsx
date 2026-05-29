/**
 * 评估器编辑抽屉。两种模式合在一个组件里：
 *  - "tag" 模式：只配置 name / tag / is_active，运行时把 tag 打到 Langfuse trace
 *  - "configurable_judge" 模式：本平台直连 LLM 打分。1:1 复刻 Langfuse 自带
 *    evaluator 表单：3 段 prompt + Mustache 变量映射 + 单分数（numeric / boolean
 *    / categorical）
 *
 * params schema 对齐 `agent_eval.evaluation.configurable_judge`：
 *
 *   {
 *     provider_id, model, temperature, max_tokens, timeout,
 *     evaluation_prompt,    // 主任务说明 + 待评样本（Mustache: {{Query}} 等）
 *     reasoning_prompt,     // system 段：引导写理由
 *     output_prompt,        // system 段：约束输出格式
 *     variable_mapping,     // { "Query": "input", "Foo": "metadata.foo", ... }
 *     score_type,           // "numeric" | "boolean" | "categorical"
 *     score_range,          // numeric 时 [min, max]
 *     categories,           // categorical 时 [{label, value(0..1)}, ...]
 *   }
 *
 * Variable mapping 设计与 Langfuse 一致：
 *  - 变量名大小写敏感（Langfuse 也是）
 *  - 数据源：input / output / expected_output / metadata / metadata.&lt;key&gt;
 *  - 模板里出现的 {{name}} 没在 mapping 里 → 阻止保存（避免 silent 渲染空串）
 *
 * 试跑：调 POST /eval/evaluators/{id}/dry-run。新建场景下 evaluator 还没
 * id，按钮置灰显示"先保存再试跑"；编辑场景下用表单当前值为 params 试跑。
 */
import { useEffect, useId, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Drawer, ErrorCard, useConfirm, useToast } from './ui'
import { evaluationApi, evaluatorProvidersApi } from '@/services'
import { formatApiError, formatDryRunError, toToastMessage, type NormalizedError } from '@/lib/errors'
import type {
  CreateEvaluatorRequest,
  DryRunResponse,
  EvaluatorInstance,
  EvaluatorVersion,
  ProviderModelsResponse,
} from '@/types'

type EditorMode = 'tag' | 'configurable_judge'
type ScoreType = 'numeric' | 'boolean' | 'categorical'

interface Category {
  label: string
  value: number  // 归一到 0..1
}

interface JudgeParams {
  provider_id?: string
  model?: string
  temperature?: number
  max_tokens?: number
  timeout?: number
  evaluation_prompt?: string
  reasoning_prompt?: string
  output_prompt?: string
  variable_mapping?: Record<string, string>
  score_type?: ScoreType
  score_range?: [number, number]
  categories?: Category[]
}

// 与后端 configurable_judge.DEFAULT_* 保持一致
const DEFAULT_EVALUATION_PROMPT = `请评估下面 AI 助手的回答质量。

## 用户输入
{{Query}}

## AI 回答
{{Generation}}

## 期望答案（如有）
{{GroundTruth}}

请给出一个 0 到 1 之间的总分（0=完全错误，1=完美）。`

const DEFAULT_REASONING_PROMPT = `你是一个严谨、客观的评估专家。
请先简短地写出评分理由（2-3 句话），再给出分数。
理由要可复核，避免空泛的"很好/不错"。`

const DEFAULT_OUTPUT_PROMPT = `严格只输出以下 JSON，不要附加任何其他文字、Markdown 或代码围栏：

{"score": <数值或布尔或类别字符串>, "reasoning": "<简短理由>"}`

const DEFAULT_VARIABLE_MAPPING: Record<string, string> = {
  Query: 'input',
  Generation: 'output',
  GroundTruth: 'expected_output',
}

const DEFAULT_CATEGORIES: Category[] = [
  { label: 'good', value: 1.0 },
  { label: 'partial', value: 0.5 },
  { label: 'bad', value: 0.0 },
]

// 数据源选项 —— 与后端 _resolve_source 识别的表达式 1:1 对齐。
// metadata.<key> 由用户手填（自由形式），不在下拉里枚举。
const VARIABLE_SOURCE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'input', label: 'input（用户输入）' },
  { value: 'output', label: 'output（AI 回答）' },
  { value: 'expected_output', label: 'expected_output（期望答案）' },
  { value: 'metadata', label: 'metadata（整体 JSON）' },
]

// 从 prompt 里抽出所有 {{Name}} 占位符（去重保序，大小写敏感）
const VAR_REGEX = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g
function extractVariables(template: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  if (!template) return out
  let m: RegExpExecArray | null
  VAR_REGEX.lastIndex = 0
  while ((m = VAR_REGEX.exec(template)) !== null) {
    const name = m[1]
    if (!seen.has(name)) {
      seen.add(name)
      out.push(name)
    }
  }
  return out
}

interface Props {
  open: boolean
  editing: EvaluatorInstance | null
  onClose: () => void
}

export default function EvaluatorEditorDrawer({ open, editing, onClose }: Props) {
  const qc = useQueryClient()
  const toast = useToast()
  const reactId = useId()

  // ── 基础字段 ──
  // 新评估器走 evaluator_type 字段；早于该字段引入的 legacy 行 evaluator_type=null，
  // 但 params 里塞着完整 judge 配置（provider_id + 三段 prompt 之一）。两者都视为 judge，
  // 否则打开抽屉只能看到 tag 模式，judge 段 + 试跑面板都不渲染。
  const looksLikeJudge = (() => {
    if (editing?.evaluator_type === 'configurable_judge') return true
    const p = (editing?.params || {}) as Record<string, unknown>
    return Boolean(
      p.provider_id ||
      p.evaluation_prompt ||
      p.reasoning_prompt ||
      p.output_prompt ||
      p.user_template ||
      p.system_prompt,
    )
  })()
  const initialMode: EditorMode = looksLikeJudge ? 'configurable_judge' : 'tag'
  const [mode, setMode] = useState<EditorMode>(initialMode)
  const [name, setName] = useState(editing?.name || '')
  const [tag, setTag] = useState(editing?.tag || '')
  const [description, setDescription] = useState(editing?.description || '')
  const [isActive, setIsActive] = useState(editing ? editing.is_active : true)

  // ── judge 参数 ──
  const initialJudge: JudgeParams = useMemo(() => {
    const p = (editing?.params || {}) as Record<string, unknown>
    // 兼容旧 schema（system_prompt/user_template）：迁移为新字段
    let evalP = (p.evaluation_prompt as string) || (p.user_template as string) || ''
    const sysP = p.system_prompt as string | undefined
    const reasoningP = (p.reasoning_prompt as string) || sysP || ''
    const outputP = (p.output_prompt as string) || ''
    const scoreType = ((p.score_type as ScoreType) || 'numeric') as ScoreType
    const scoreRange = Array.isArray(p.score_range) && p.score_range.length === 2
      ? [Number(p.score_range[0]), Number(p.score_range[1])] as [number, number]
      : [0, 1] as [number, number]
    const categories = Array.isArray(p.categories) && p.categories.length > 0
      ? (p.categories as Array<{ label: string; value: number }>).map(c => ({
          label: String(c.label || ''),
          value: typeof c.value === 'number' ? c.value : 0,
        }))
      : DEFAULT_CATEGORIES

    // 变量映射：优先读 params.variable_mapping；否则若模板里仍是旧
    // 单大括号占位符，做一次"加载时迁移"——把 {input}→{{Query}} 等
    // 替换并塞默认 mapping，让用户保存后即对齐 Mustache 风格
    let mapping: Record<string, string> = {}
    const rawMap = p.variable_mapping
    if (rawMap && typeof rawMap === 'object' && !Array.isArray(rawMap)) {
      mapping = Object.fromEntries(
        Object.entries(rawMap as Record<string, unknown>)
          .filter(([k, v]) => typeof k === 'string' && typeof v === 'string')
          .map(([k, v]) => [k, String(v)]),
      )
    }
    if (Object.keys(mapping).length === 0 && evalP && !VAR_REGEX.test(evalP)) {
      // 模板里没有 {{...}}：可能是旧 single-brace 模板，做迁移
      VAR_REGEX.lastIndex = 0
      const legacy = /\{(input|output|expected_output|metadata)\}/g
      if (legacy.test(evalP)) {
        evalP = evalP
          .replace(/\{input\}/g, '{{Query}}')
          .replace(/\{output\}/g, '{{Generation}}')
          .replace(/\{expected_output\}/g, '{{GroundTruth}}')
          .replace(/\{metadata\}/g, '{{Metadata}}')
        mapping = { ...DEFAULT_VARIABLE_MAPPING }
        if (evalP.includes('{{Metadata}}')) mapping['Metadata'] = 'metadata'
      } else if (!evalP.trim()) {
        mapping = { ...DEFAULT_VARIABLE_MAPPING }
      }
    }
    VAR_REGEX.lastIndex = 0
    if (!evalP) evalP = ''

    return {
      provider_id: p.provider_id as string | undefined,
      model: p.model as string | undefined,
      temperature: typeof p.temperature === 'number' ? p.temperature : 0,
      max_tokens: typeof p.max_tokens === 'number' ? p.max_tokens : 1024,
      timeout: typeof p.timeout === 'number' ? p.timeout : 60,
      evaluation_prompt: evalP,
      reasoning_prompt: reasoningP,
      output_prompt: outputP,
      variable_mapping: mapping,
      score_type: scoreType,
      score_range: scoreRange,
      categories,
    }
  }, [editing])
  const [judge, setJudge] = useState<JudgeParams>(initialJudge)

  // ── 试跑状态（必须在 useEffect 之前声明，因为 useEffect 里要 set 它们）──
  const [dryInput, setDryInput] = useState('')
  const [dryOutput, setDryOutput] = useState('')
  const [dryExpected, setDryExpected] = useState('')
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null)
  const [dryRunError, setDryRunError] = useState<NormalizedError | null>(null)
  const [showRawContent, setShowRawContent] = useState(false)

  // editing 切换时重置（同一抽屉打开多次复用）
  useEffect(() => {
    setMode(initialMode)
    setName(editing?.name || '')
    setTag(editing?.tag || '')
    setDescription(editing?.description || '')
    setIsActive(editing ? editing.is_active : true)
    setJudge(initialJudge)
    setDryRunResult(null)
    setDryRunError(null)
  }, [editing, initialMode, initialJudge])

  // ── providers / models ──
  const providersQuery = useQuery({
    queryKey: ['evaluator-providers', 'active'],
    queryFn: () => evaluatorProvidersApi.list(true).then(r => r.data),
    enabled: open && mode === 'configurable_judge',
  })
  const modelsQuery = useQuery({
    queryKey: ['provider-models', judge.provider_id],
    queryFn: () => evaluatorProvidersApi
      .listModels(judge.provider_id!)
      .then(r => r.data as ProviderModelsResponse),
    enabled: open && mode === 'configurable_judge' && !!judge.provider_id,
    staleTime: 30_000,
  })

  const selectedProvider = providersQuery.data?.find(p => p.id === judge.provider_id)
  const fallbackModel = selectedProvider?.default_model || ''

  const dryRunMutation = useMutation({
    mutationFn: async () => {
      if (!editing) {
        throw new Error('请先保存评估器再试跑')
      }
      const params = buildParams(judge)
      return evaluationApi.dryRunEvaluator(editing.id, {
        provider_id: judge.provider_id,
        params,
        input: dryInput,
        output: dryOutput,
        expected_output: dryExpected || null,
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      setDryRunResult(data)
      setDryRunError(data.error ? formatDryRunError(data.error) : null)
    },
    onError: (err) => {
      setDryRunError(formatApiError(err, { fallbackTitle: '试跑失败' }))
      setDryRunResult(null)
    },
  })

  // ── 保存 ──
  const saveMutation = useMutation({
    mutationFn: async () => {
      const effectiveTag = tag.trim() || name.trim()
      if (mode === 'tag') {
        if (editing) {
          return evaluationApi.updateEvaluator(editing.id, {
            name, tag: effectiveTag, description: description || null,
            params: {}, is_active: isActive,
          }).then(r => r.data)
        }
        const body: CreateEvaluatorRequest = {
          name, tag: effectiveTag, description: description || null,
          evaluator_type: null, params: {}, is_active: isActive,
        }
        return evaluationApi.createEvaluator(body).then(r => r.data)
      }
      // configurable_judge
      if (!judge.provider_id) {
        throw new Error('请选择 Provider')
      }
      const unmapped = findUnmappedVariables(judge)
      if (unmapped.length > 0) {
        throw new Error(
          `Prompt 里的变量 ${unmapped.map(v => `{{${v}}}`).join('、')} 还没在"变量映射"里选数据源`,
        )
      }
      const params = buildParams(judge)
      if (editing) {
        return evaluationApi.updateEvaluator(editing.id, {
          name, tag: effectiveTag, description: description || null,
          params, is_active: isActive,
        }).then(r => r.data)
      }
      const body: CreateEvaluatorRequest = {
        name, tag: effectiveTag, description: description || null,
        evaluator_type: 'configurable_judge', params, is_active: isActive,
      }
      return evaluationApi.createEvaluator(body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
      toast.success(editing ? '评估器已更新' : '评估器已创建')
      onClose()
    },
    onError: (err) => {
      const norm = formatApiError(err, { fallbackTitle: '保存失败' })
      toast.error(toToastMessage(norm), '保存失败')
    },
  })

  // ── 渲染 ──
  const [tab, setTab] = useState<'config' | 'versions'>('config')
  // 切换 editing 时回到 config tab
  useEffect(() => { setTab('config') }, [editing?.id])

  const showVersionsTab = !!editing && mode === 'configurable_judge'
  const ids = {
    name: `${reactId}-name`,
    tag: `${reactId}-tag`,
    desc: `${reactId}-desc`,
    active: `${reactId}-active`,
    mode: `${reactId}-mode`,
    provider: `${reactId}-provider`,
    model: `${reactId}-model`,
    temp: `${reactId}-temp`,
    maxTok: `${reactId}-maxtok`,
    timeout: `${reactId}-timeout`,
    evalPrompt: `${reactId}-evalprompt`,
    reasoningPrompt: `${reactId}-reasoningprompt`,
    outputPrompt: `${reactId}-outputprompt`,
    scoreType: `${reactId}-scoretype`,
    rangeMin: `${reactId}-rangemin`,
    rangeMax: `${reactId}-rangemax`,
    dryIn: `${reactId}-dryin`,
    dryOut: `${reactId}-dryout`,
    dryExp: `${reactId}-dryexp`,
  }

  const scoreType: ScoreType = judge.score_type || 'numeric'
  const primaryScore = dryRunResult?.scores?.[0] || null
  const unmappedVars = mode === 'configurable_judge' ? findUnmappedVariables(judge) : []
  const hasMappingError = unmappedVars.length > 0

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={editing ? '编辑评估器' : '新建评估器'}
      subtitle={editing ? editing.id : undefined}
      width="wide"
    >
      {showVersionsTab && (
        <div className="-mt-3 mb-5 flex gap-1 border-b border-separator">
          <TabBtn active={tab === 'config'} onClick={() => setTab('config')}>配置</TabBtn>
          <TabBtn active={tab === 'versions'} onClick={() => setTab('versions')}>历史版本</TabBtn>
        </div>
      )}

      {tab === 'versions' && editing ? (
        <VersionsPanel
          evaluatorId={editing.id}
          currentVersionId={editing.current_version_id || null}
          onActivated={() => {
            qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
          }}
        />
      ) : (
      <div className="space-y-6 pb-24">
        {/* 基础信息 */}
        <section className="space-y-4">
          <div className="page-eyebrow">基础</div>
          <div>
            <label htmlFor={ids.name} className="field-label">名称（唯一）</label>
            <input
              id={ids.name} className="input"
              value={name} onChange={e => setName(e.target.value)}
              placeholder="例如：正确性 Judge / Goal Accuracy"
            />
          </div>
          <div>
            <label htmlFor={ids.tag} className="field-label">
              Tag（写到 Langfuse trace，留空则用名称）
            </label>
            <input
              id={ids.tag} className="input font-mono"
              value={tag} onChange={e => setTag(e.target.value)}
              placeholder="例如：agent-eval-correctness"
            />
          </div>
          <div>
            <label htmlFor={ids.desc} className="field-label">描述（可选）</label>
            <input
              id={ids.desc} className="input"
              value={description} onChange={e => setDescription(e.target.value)}
              placeholder="一句话说明这个评估器的用途"
            />
          </div>
          <label htmlFor={ids.active} className="inline-flex items-center gap-2 text-[12px] cursor-pointer">
            <input
              id={ids.active} type="checkbox" className="accent-accent"
              checked={isActive} onChange={e => setIsActive(e.target.checked)}
            />
            启用（运行时可选）
          </label>
        </section>

        {/* 类型 */}
        <section className="space-y-3">
          <div className="page-eyebrow">类型</div>
          <div className="flex gap-2">
            <ModeChip
              label="标签模板"
              hint="把 tag 打到 trace，由 Langfuse 端 evaluator 处理"
              active={mode === 'tag'}
              onClick={() => setMode('tag')}
            />
            <ModeChip
              label="可配置 LLM Judge"
              hint="本平台直连 provider 打分（单分数）"
              active={mode === 'configurable_judge'}
              onClick={() => setMode('configurable_judge')}
            />
          </div>
        </section>

        {/* judge 配置 */}
        {mode === 'configurable_judge' && (
          <>
            <section className="space-y-4">
              <div className="page-eyebrow">Judge 后端</div>
              <div>
                <label htmlFor={ids.provider} className="field-label">Provider</label>
                <select
                  id={ids.provider} className="input"
                  value={judge.provider_id || ''}
                  onChange={e => setJudge({ ...judge, provider_id: e.target.value || undefined })}
                >
                  <option value="">— 请选择 —</option>
                  {providersQuery.data?.map(p => (
                    <option key={p.id} value={p.id}>
                      {p.name}（{p.provider_type}）
                    </option>
                  ))}
                </select>
                {providersQuery.data?.length === 0 && (
                  <div className="mt-1.5 text-[11px] text-warning">
                    还没有启用的 Provider — 先到"系统 → LLM Judge Providers"建一个。
                  </div>
                )}
              </div>

              <div>
                <label htmlFor={ids.model} className="field-label">
                  模型
                  {fallbackModel && !judge.model && (
                    <span className="text-text-tertiary"> · 留空使用默认 {fallbackModel}</span>
                  )}
                </label>
                {modelsQuery.data?.ok && modelsQuery.data.models.length > 0 ? (
                  <select
                    id={ids.model} className="input font-mono"
                    value={judge.model || ''}
                    onChange={e => setJudge({ ...judge, model: e.target.value || undefined })}
                  >
                    <option value="">{fallbackModel ? `（默认 ${fallbackModel}）` : '— 请选择 —'}</option>
                    {modelsQuery.data.models.map(m => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={ids.model} className="input font-mono"
                    value={judge.model || ''}
                    onChange={e => setJudge({ ...judge, model: e.target.value || undefined })}
                    placeholder={fallbackModel || 'gpt-4o-mini'}
                  />
                )}
                {judge.provider_id && modelsQuery.isError && (
                  <div className="mt-1.5 text-[10px] text-text-tertiary">
                    无法拉取模型列表 — 手填即可。
                  </div>
                )}
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label htmlFor={ids.temp} className="field-label">温度</label>
                  <input
                    id={ids.temp} type="number" step="0.1" min="0" max="2"
                    className="input"
                    value={judge.temperature ?? 0}
                    onChange={e => setJudge({ ...judge, temperature: Number(e.target.value) })}
                  />
                </div>
                <div>
                  <label htmlFor={ids.maxTok} className="field-label">Max tokens</label>
                  <input
                    id={ids.maxTok} type="number" min="1"
                    className="input"
                    value={judge.max_tokens ?? 1024}
                    onChange={e => setJudge({ ...judge, max_tokens: Number(e.target.value) })}
                  />
                </div>
                <div>
                  <label htmlFor={ids.timeout} className="field-label">超时（秒）</label>
                  <input
                    id={ids.timeout} type="number" min="1"
                    className="input"
                    value={judge.timeout ?? 60}
                    onChange={e => setJudge({ ...judge, timeout: Number(e.target.value) })}
                  />
                </div>
              </div>
            </section>

            {/* Prompt 三段式 —— 1:1 复刻 Langfuse */}
            <section className="space-y-4">
              <div className="page-eyebrow">Prompt</div>

              <div>
                <label htmlFor={ids.evalPrompt} className="field-label">
                  评估 Prompt（Evaluation Prompt）
                </label>
                <textarea
                  id={ids.evalPrompt} className="input font-mono text-[12px]" rows={9}
                  value={judge.evaluation_prompt ?? ''}
                  onChange={e => setJudge({ ...judge, evaluation_prompt: e.target.value })}
                  placeholder={DEFAULT_EVALUATION_PROMPT}
                />
                <div className="mt-1.5 text-[10px] text-text-tertiary">
                  使用 Mustache 占位符 <code className="font-mono">{`{{Variable}}`}</code>{' '}
                  （大小写敏感）。在下方"变量映射"段为每个变量选数据源；
                  默认模板内置 <code className="font-mono">{`{{Query}}`}</code>{' '}
                  <code className="font-mono">{`{{Generation}}`}</code>{' '}
                  <code className="font-mono">{`{{GroundTruth}}`}</code>。
                </div>
              </div>

              <div>
                <label htmlFor={ids.reasoningPrompt} className="field-label">
                  Reasoning Prompt（system 段，引导写理由 / chain-of-thought）
                </label>
                <textarea
                  id={ids.reasoningPrompt} className="input font-mono text-[12px]" rows={4}
                  value={judge.reasoning_prompt ?? ''}
                  onChange={e => setJudge({ ...judge, reasoning_prompt: e.target.value })}
                  placeholder={DEFAULT_REASONING_PROMPT}
                />
              </div>

              <div>
                <label htmlFor={ids.outputPrompt} className="field-label">
                  Output Prompt（system 段，约束输出格式）
                </label>
                <textarea
                  id={ids.outputPrompt} className="input font-mono text-[12px]" rows={5}
                  value={judge.output_prompt ?? ''}
                  onChange={e => setJudge({ ...judge, output_prompt: e.target.value })}
                  placeholder={DEFAULT_OUTPUT_PROMPT}
                />
                <div className="mt-1.5 text-[10px] text-text-tertiary">
                  必须让模型返回形如{' '}
                  <code className="font-mono">{`{"score": ..., "reasoning": "..."}`}</code>{' '}
                  的 JSON 对象。
                </div>
              </div>
            </section>

            {/* 变量映射 —— 跟 Langfuse 一致：模板里写 {{Name}}，这里给每个 Name 选数据源 */}
            <VariableMappingPanel judge={judge} setJudge={setJudge} idPrefix={reactId} />

            {/* 分数类型 */}
            <section className="space-y-3">
              <div className="page-eyebrow">分数</div>
              <div>
                <label htmlFor={ids.scoreType} className="field-label">类型</label>
                <select
                  id={ids.scoreType} className="input"
                  value={scoreType}
                  onChange={e => setJudge({ ...judge, score_type: e.target.value as ScoreType })}
                >
                  <option value="numeric">Numeric（数值，按 [min, max] 归一）</option>
                  <option value="boolean">Boolean（true/false 映射到 1/0）</option>
                  <option value="categorical">Categorical（类别名 → 预设分值）</option>
                </select>
              </div>

              {scoreType === 'numeric' && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor={ids.rangeMin} className="field-label">分数下界</label>
                    <input
                      id={ids.rangeMin} type="number" step="0.1"
                      className="input"
                      value={judge.score_range?.[0] ?? 0}
                      onChange={e => setJudge({
                        ...judge,
                        score_range: [Number(e.target.value), judge.score_range?.[1] ?? 1],
                      })}
                    />
                  </div>
                  <div>
                    <label htmlFor={ids.rangeMax} className="field-label">分数上界</label>
                    <input
                      id={ids.rangeMax} type="number" step="0.1"
                      className="input"
                      value={judge.score_range?.[1] ?? 1}
                      onChange={e => setJudge({
                        ...judge,
                        score_range: [judge.score_range?.[0] ?? 0, Number(e.target.value)],
                      })}
                    />
                  </div>
                </div>
              )}

              {scoreType === 'categorical' && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] text-text-tertiary">
                      label 是模型应返回的字符串，value 是该类别归一后的分数（0..1）
                    </span>
                    <button
                      type="button"
                      className="text-action text-[11px]"
                      onClick={() => setJudge({
                        ...judge,
                        categories: [...(judge.categories || []), { label: '', value: 0 }],
                      })}
                    >
                      + 添加类别
                    </button>
                  </div>
                  {(judge.categories || []).map((c, i) => (
                    <div key={i} className="grid grid-cols-[3fr_1fr_auto] gap-2 items-center">
                      <input
                        className="input font-mono text-[12px]"
                        value={c.label}
                        onChange={e => updateCategory(setJudge, judge, i, { label: e.target.value })}
                        placeholder="label，例如 good / partial / bad"
                        aria-label={`category-${i}-label`}
                      />
                      <input
                        type="number" step="0.1" min="0" max="1"
                        className="input"
                        value={c.value}
                        onChange={e => updateCategory(setJudge, judge, i, { value: Number(e.target.value) })}
                        aria-label={`category-${i}-value`}
                      />
                      <button
                        type="button"
                        className="text-action-danger text-[11px] px-2"
                        onClick={() => setJudge({
                          ...judge,
                          categories: (judge.categories || []).filter((_, j) => j !== i),
                        })}
                        aria-label={`删除类别 ${i + 1}`}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* 试跑 */}
            <section className="space-y-3 border-t border-separator pt-5">
              <div className="flex items-center justify-between">
                <div className="page-eyebrow">试跑</div>
                {!editing && (
                  <span className="text-[10px] text-text-tertiary">保存后才能试跑</span>
                )}
              </div>
              <div className="grid grid-cols-1 gap-3">
                <div>
                  <label htmlFor={ids.dryIn} className="field-label">输入</label>
                  <textarea
                    id={ids.dryIn} className="input font-mono text-[12px]" rows={2}
                    value={dryInput} onChange={e => setDryInput(e.target.value)}
                    placeholder="用户问题 / agent 输入"
                  />
                </div>
                <div>
                  <label htmlFor={ids.dryOut} className="field-label">AI 输出</label>
                  <textarea
                    id={ids.dryOut} className="input font-mono text-[12px]" rows={3}
                    value={dryOutput} onChange={e => setDryOutput(e.target.value)}
                    placeholder="agent 给出的回答"
                  />
                </div>
                <div>
                  <label htmlFor={ids.dryExp} className="field-label">期望答案（可选）</label>
                  <textarea
                    id={ids.dryExp} className="input font-mono text-[12px]" rows={2}
                    value={dryExpected} onChange={e => setDryExpected(e.target.value)}
                    placeholder="若有 ground truth 填这里"
                  />
                </div>
              </div>
              <div className="flex items-center gap-3">
                <Button
                  variant="secondary" size="sm"
                  disabled={!editing || !judge.provider_id || hasMappingError || dryRunMutation.isPending}
                  loading={dryRunMutation.isPending}
                  onClick={() => dryRunMutation.mutate()}
                >
                  试跑一次
                </Button>
                {primaryScore && (
                  <span className="text-[12px] text-text-secondary">
                    得分：<strong className="text-positive font-mono">
                      {(primaryScore.value * 100).toFixed(1)}%
                    </strong>
                    {primaryScore.raw_value != null && (
                      <span className="text-text-tertiary ml-2 font-mono text-[10px]">
                        · raw {String(primaryScore.raw_value)}
                      </span>
                    )}
                    {dryRunResult?.model && (
                      <span className="text-text-tertiary ml-2 font-mono text-[10px]">
                        · {dryRunResult.model}
                      </span>
                    )}
                  </span>
                )}
              </div>

              {dryRunError && (
                <ErrorCard
                  error={dryRunError}
                  rawDetails={
                    dryRunResult?.raw_content
                      ? { label: '原始响应', content: dryRunResult.raw_content }
                      : undefined
                  }
                />
              )}

              {primaryScore && (
                <div className="rounded-md bg-fill/5 border border-separator p-3">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-mono text-[11px] text-text-secondary">
                      {primaryScore.name}
                    </span>
                    <span className="font-mono text-[11px] text-positive">
                      {(primaryScore.value * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="h-1 bg-fill/10 rounded overflow-hidden">
                    <div
                      className="h-full bg-positive/70"
                      style={{ width: `${primaryScore.value * 100}%` }}
                    />
                  </div>
                  {primaryScore.reason && (
                    <div className="mt-2 text-[11px] text-text-tertiary leading-relaxed whitespace-pre-wrap">
                      {primaryScore.reason}
                    </div>
                  )}
                </div>
              )}

              {dryRunResult && (dryRunResult.raw_content || dryRunResult.usage) && (
                <details className="text-[11px]" open={showRawContent}>
                  <summary
                    className="cursor-pointer text-text-tertiary hover:text-text-secondary"
                    onClick={() => setShowRawContent(s => !s)}
                  >
                    原始响应
                    {dryRunResult.usage && (
                      <span className="ml-2 font-mono text-text-tertiary">
                        · in {dryRunResult.usage.input_tokens || 0} / out {dryRunResult.usage.output_tokens || 0}
                      </span>
                    )}
                  </summary>
                  {dryRunResult.raw_content && (
                    <pre className="mt-2 p-2 bg-fill/5 border border-separator rounded-md font-mono text-[10px] overflow-auto max-h-48 whitespace-pre-wrap">
                      {dryRunResult.raw_content}
                    </pre>
                  )}
                </details>
              )}
            </section>
          </>
        )}
      </div>
      )}

      {/* footer pin */}
      {tab === 'config' && (
        <div className="absolute bottom-0 left-0 right-0 px-6 py-3 border-t border-separator bg-bg-elevated flex justify-end gap-2">
          <Button variant="secondary" size="md" onClick={onClose}>取消</Button>
          <Button
            variant="primary" size="md"
            disabled={
              !name.trim()
              || (mode === 'configurable_judge' && (!judge.provider_id || hasMappingError))
            }
            loading={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            保存
          </Button>
        </div>
      )}
    </Drawer>
  )
}


function ModeChip({
  label, hint, active, onClick,
}: { label: string; hint: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'flex-1 text-left rounded-lg border px-3 py-2.5 transition-colors',
        active
          ? 'border-accent bg-accent/5 text-text-primary'
          : 'border-separator hover:border-border bg-bg text-text-secondary hover:text-text-primary',
      ].join(' ')}
    >
      <div className="text-[12px] font-medium">{label}</div>
      <div className="text-[10px] text-text-tertiary mt-0.5">{hint}</div>
    </button>
  )
}


function TabBtn({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'px-3 py-2 text-[12px] border-b-2 -mb-[1px] transition-colors',
        active
          ? 'border-accent text-text-primary font-medium'
          : 'border-transparent text-text-secondary hover:text-text-primary',
      ].join(' ')}
    >
      {children}
    </button>
  )
}


function VersionsPanel({
  evaluatorId, currentVersionId, onActivated,
}: {
  evaluatorId: string
  currentVersionId: string | null
  onActivated: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const [busyId, setBusyId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const versionsQuery = useQuery({
    queryKey: ['evaluator-versions', evaluatorId],
    queryFn: () => evaluationApi.listEvaluatorVersions(evaluatorId).then(r => r.data),
  })

  const handleActivate = async (v: EvaluatorVersion) => {
    if (v.id === currentVersionId) return
    const ok = await confirm({
      title: '激活此版本',
      description: `把 v${v.version_number} 设为当前版本？后续运行会使用这份配置。`,
      confirmText: '激活',
    })
    if (!ok) return
    setBusyId(v.id)
    try {
      await evaluationApi.activateEvaluatorVersion(evaluatorId, v.id)
      toast.success(`已激活 v${v.version_number}`)
      qc.invalidateQueries({ queryKey: ['evaluator-versions', evaluatorId] })
      qc.invalidateQueries({ queryKey: ['evaluator-instances'] })
      onActivated()
    } catch (err) {
      const norm = formatApiError(err, { fallbackTitle: '激活失败' })
      toast.error(toToastMessage(norm), '激活失败')
    } finally {
      setBusyId(null)
    }
  }

  if (versionsQuery.isLoading) {
    return <div className="text-[12px] text-text-tertiary">加载历史版本…</div>
  }
  const versions = versionsQuery.data || []
  if (versions.length === 0) {
    return (
      <div className="text-[12px] text-text-tertiary">
        还没有版本快照。在"配置"标签里修改并保存，会自动写入 v1。
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-text-tertiary leading-relaxed">
        每次保存配置时自动追加一个版本。运行评估时会用当前激活版本，便于日后复现历史结果。
      </p>
      {versions.map(v => {
        const isCurrent = v.id === currentVersionId
        const isOpen = expanded === v.id
        return (
          <div
            key={v.id}
            className={[
              'rounded-lg border p-3 transition-colors',
              isCurrent
                ? 'border-accent/60 bg-accent/5'
                : 'border-separator hover:border-border',
            ].join(' ')}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono text-[11px] font-medium">v{v.version_number}</span>
                {isCurrent && (
                  <span className="badge badge-positive text-[10px]">当前</span>
                )}
                <span className="text-text-tertiary text-[11px] truncate">
                  {v.description || '—'}
                </span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className="text-[10px] text-text-tertiary">
                  {v.created_at ? new Date(v.created_at).toLocaleString() : '—'}
                </span>
                <button
                  type="button"
                  className="text-action text-[11px]"
                  onClick={() => setExpanded(isOpen ? null : v.id)}
                >
                  {isOpen ? '收起' : '查看'}
                </button>
                {!isCurrent && (
                  <button
                    type="button"
                    className="text-action text-[11px]"
                    disabled={busyId === v.id}
                    onClick={() => handleActivate(v)}
                  >
                    {busyId === v.id ? '激活中…' : '激活'}
                  </button>
                )}
              </div>
            </div>
            {isOpen && (
              <pre className="mt-2 p-2 bg-fill/5 border border-separator rounded font-mono text-[10px] overflow-auto max-h-72 whitespace-pre-wrap">
                {JSON.stringify(v.params, null, 2)}
              </pre>
            )}
          </div>
        )
      })}
    </div>
  )
}


function buildParams(j: JudgeParams): Record<string, unknown> {
  // 只把非空的字段写进 params。后端对缺省字段有合理 fallback
  // （DEFAULT_EVALUATION_PROMPT 等）。
  const params: Record<string, unknown> = {}
  if (j.provider_id) params.provider_id = j.provider_id
  if (j.model) params.model = j.model
  if (typeof j.temperature === 'number') params.temperature = j.temperature
  if (typeof j.max_tokens === 'number') params.max_tokens = j.max_tokens
  if (typeof j.timeout === 'number') params.timeout = j.timeout
  if (j.evaluation_prompt?.trim()) params.evaluation_prompt = j.evaluation_prompt
  if (j.reasoning_prompt?.trim()) params.reasoning_prompt = j.reasoning_prompt
  if (j.output_prompt?.trim()) params.output_prompt = j.output_prompt
  // 只持久化在模板里实际出现的变量（避免历史脏数据）
  const usedVars = extractVariables(j.evaluation_prompt || '')
  if (usedVars.length > 0 && j.variable_mapping) {
    const m: Record<string, string> = {}
    for (const name of usedVars) {
      const src = j.variable_mapping[name]
      if (src && src.trim()) m[name] = src.trim()
    }
    if (Object.keys(m).length > 0) params.variable_mapping = m
  }
  const stype: ScoreType = j.score_type || 'numeric'
  params.score_type = stype
  if (stype === 'numeric' && Array.isArray(j.score_range) && j.score_range.length === 2) {
    params.score_range = [Number(j.score_range[0]), Number(j.score_range[1])]
  }
  if (stype === 'categorical') {
    const cats = (j.categories || []).filter(c => c.label.trim())
    if (cats.length > 0) params.categories = cats
  }
  return params
}

// 校验 mapping 完整性：模板里出现的每个变量都必须有非空 source。
// 返回未配置或留空的变量名列表（空数组表示通过）。
function findUnmappedVariables(j: JudgeParams): string[] {
  const used = extractVariables(j.evaluation_prompt || '')
  const mapping = j.variable_mapping || {}
  return used.filter(v => {
    const src = mapping[v]
    return !src || !src.trim()
  })
}

function updateCategory(
  setJudge: React.Dispatch<React.SetStateAction<JudgeParams>>,
  judge: JudgeParams,
  i: number,
  patch: Partial<Category>,
) {
  const next = [...(judge.categories || [])]
  next[i] = { ...next[i], ...patch }
  setJudge({ ...judge, categories: next })
}


/**
 * 变量映射面板：自动从 evaluation_prompt 抽出 {{Name}}，给每个 Name 选数据源。
 * 数据源下拉枚举 input/output/expected_output/metadata；选 metadata 时旁边
 * 出现一个文本框允许填子字段（最终值 ``metadata.<key>``，对齐后端 _resolve_source）。
 */
function VariableMappingPanel({
  judge, setJudge, idPrefix,
}: {
  judge: JudgeParams
  setJudge: React.Dispatch<React.SetStateAction<JudgeParams>>
  idPrefix: string
}) {
  const usedVars = useMemo(
    () => extractVariables(judge.evaluation_prompt || ''),
    [judge.evaluation_prompt],
  )

  if (usedVars.length === 0) {
    return (
      <section className="space-y-2">
        <div className="page-eyebrow">变量映射</div>
        <div className="text-[11px] text-text-tertiary leading-relaxed">
          评估 Prompt 里目前没有 <code className="font-mono">{`{{Variable}}`}</code> 占位符。
          常见做法是把样本字段（input / output / expected_output 或 metadata 子字段）
          作为变量插入 prompt，再在这里选择数据源。
        </div>
      </section>
    )
  }

  const setMapping = (name: string, source: string) => {
    const next = { ...(judge.variable_mapping || {}) }
    if (source) next[name] = source
    else delete next[name]
    setJudge({ ...judge, variable_mapping: next })
  }

  return (
    <section className="space-y-3">
      <div className="page-eyebrow">变量映射</div>
      <div className="text-[11px] text-text-tertiary leading-relaxed">
        Prompt 里检测到 {usedVars.length} 个变量。变量名大小写敏感；
        每个变量必须选一个数据源，否则无法保存。
      </div>
      <div className="space-y-2">
        {usedVars.map(name => {
          const cur = (judge.variable_mapping || {})[name] || ''
          const isMetaPath = cur.startsWith('metadata.')
          const isMeta = cur === 'metadata' || isMetaPath
          const dropdownVal = isMetaPath ? 'metadata.' : cur
          const subKey = isMetaPath ? cur.slice('metadata.'.length) : ''
          const selectId = `${idPrefix}-varmap-${name}`
          return (
            <div key={name} className="grid grid-cols-[1fr_2fr] gap-2 items-start">
              <code className="font-mono text-[11px] px-2 py-1.5 bg-fill/5 border border-separator rounded text-text-secondary truncate">
                {`{{${name}}}`}
              </code>
              <div className="flex gap-2">
                <select
                  id={selectId}
                  className="input flex-1"
                  value={dropdownVal}
                  onChange={e => {
                    const v = e.target.value
                    if (v === 'metadata.') {
                      // 切到 metadata 子字段模式：保留之前填的子 key（若有）
                      setMapping(name, subKey ? `metadata.${subKey}` : 'metadata.')
                    } else {
                      setMapping(name, v)
                    }
                  }}
                  aria-label={`数据源 for ${name}`}
                >
                  <option value="">— 请选择 —</option>
                  {VARIABLE_SOURCE_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                  <option value="metadata.">metadata.&lt;key&gt;（子字段）</option>
                </select>
                {isMetaPath && (
                  <input
                    className="input flex-1 font-mono text-[12px]"
                    value={subKey}
                    onChange={e => setMapping(name, `metadata.${e.target.value}`)}
                    placeholder="子字段名（可用点路径）"
                    aria-label={`metadata 子字段 for ${name}`}
                  />
                )}
                {isMeta && !isMetaPath && cur === 'metadata' && (
                  <span className="text-[10px] text-text-tertiary self-center">
                    取整个 metadata 的 JSON
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

