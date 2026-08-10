import { useEffect, useMemo, useState } from 'react'
import { Button, Drawer, useToast } from '@/components/ui'
import { usePricing } from '@/hooks/usePricing'
import {
  EMPTY_PRICE,
  formatUnitPrice,
  normalizeModelKey,
  setPricingConfig,
  type ModelPrice,
  type PricingConfig,
} from '@/lib/pricing'

// 价格编辑抽屉。价格只存浏览器本地（localStorage），不上后端，因此这里没有
// 保存请求也没有 loading 态：点「保存」即写入并广播给所有展示点。

type Draft = { model: string; inputHit: string; inputMiss: string; output: string }

function toDraft(model: string, price: ModelPrice): Draft {
  return {
    model,
    inputHit: String(price.inputHit),
    inputMiss: String(price.inputMiss),
    output: String(price.output),
  }
}

function draftsFromConfig(config: PricingConfig): Draft[] {
  return Object.entries(config.models)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([model, price]) => toDraft(model, price))
}

function parseAmount(text: string): number | null {
  const t = text.trim()
  if (!t) return 0
  const v = Number(t)
  if (!Number.isFinite(v) || v < 0) return null
  return v
}

export function ModelPricingDrawer({
  open,
  onClose,
  /** 建议模型名：当前页面出现过的模型，点一下即加一行，省得手抄。 */
  suggestModels = [],
}: {
  open: boolean
  onClose: () => void
  suggestModels?: string[]
}) {
  const toast = useToast()
  const { config } = usePricing()
  const [rows, setRows] = useState<Draft[]>([])
  const [currency, setCurrency] = useState(config.currency)

  // 每次打开都从当前配置重置草稿，避免上次未保存的编辑残留。
  useEffect(() => {
    if (!open) return
    setRows(draftsFromConfig(config))
    setCurrency(config.currency)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const configuredKeys = useMemo(
    () => new Set(rows.map(r => normalizeModelKey(r.model)).filter(Boolean)),
    [rows],
  )
  const missing = useMemo(
    () => suggestModels.filter(m => m && !configuredKeys.has(normalizeModelKey(m))),
    [suggestModels, configuredKeys],
  )

  const addRow = (model = '') => setRows(prev => [...prev, toDraft(model, EMPTY_PRICE)])
  const removeRow = (index: number) => setRows(prev => prev.filter((_, i) => i !== index))
  const patchRow = (index: number, patch: Partial<Draft>) =>
    setRows(prev => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  const handleSave = () => {
    const models: Record<string, ModelPrice> = {}
    for (const [i, row] of rows.entries()) {
      const key = normalizeModelKey(row.model)
      if (!key) {
        toast.error(`第 ${i + 1} 行未填模型名`, '保存失败')
        return
      }
      if (key in models) {
        toast.error(`模型名重复：${key}`, '保存失败')
        return
      }
      const inputHit = parseAmount(row.inputHit)
      const inputMiss = parseAmount(row.inputMiss)
      const output = parseAmount(row.output)
      if (inputHit == null || inputMiss == null || output == null) {
        toast.error(`${key} 的单价必须是非负数字`, '保存失败')
        return
      }
      models[key] = { inputHit, inputMiss, output }
    }
    setPricingConfig({ version: 1, currency: currency.trim() || '$', models })
    toast.success(`已保存 ${Object.keys(models).length} 个模型的单价`)
    onClose()
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="模型价格"
      subtitle="仅存本浏览器 · 改价后所有成本展示即时重算"
      width="wide"
      actions={
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button variant="primary" size="sm" onClick={handleSave}>保存</Button>
        </div>
      }
    >
      <div className="mb-4 rounded-md border border-border bg-fill/5 p-3 text-[11px] leading-relaxed text-text-secondary">
        <div className="mb-1 font-medium text-text-primary">计价口径</div>
        成本 = 缓存命中输入 × 命中价 + 未命中输入 × 未命中价 + 输出 × 输出价。
        其中未命中输入 = 输入 token − 缓存命中；
        <span className="text-text-primary">缓存写入 token 按未命中档计费</span>（已含在未命中输入内），明细里单列其数量供核对。
        单价按<span className="text-text-primary">每 100 万 token</span> 录入。
      </div>

      <div className="mb-4 flex items-center gap-2">
        <label className="field-label mb-0" htmlFor="pricing-currency">货币符号</label>
        <input
          id="pricing-currency"
          value={currency}
          onChange={e => setCurrency(e.target.value)}
          placeholder="$"
          className="input-sm w-[80px]"
        />
        <span className="text-[10px] text-text-tertiary">仅用于展示，不做汇率换算</span>
      </div>

      {rows.length === 0 ? (
        <p className="mb-4 text-[12px] text-text-tertiary">还没有配置任何模型单价。</p>
      ) : (
        <div className="table-card mb-4 overflow-x-auto">
          <table className="table-base min-w-[560px]">
            <thead>
              <tr>
                <th>模型名</th>
                <th className="text-right">命中输入价</th>
                <th className="text-right">未命中输入价</th>
                <th className="text-right">输出价</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  <td>
                    <input
                      value={row.model}
                      onChange={e => patchRow(i, { model: e.target.value })}
                      placeholder="claude-sonnet-4-5"
                      aria-label={`第 ${i + 1} 行模型名`}
                      className="input-sm w-full font-mono"
                    />
                  </td>
                  {(['inputHit', 'inputMiss', 'output'] as const).map(field => (
                    <td key={field}>
                      <input
                        value={row[field]}
                        onChange={e => patchRow(i, { [field]: e.target.value })}
                        inputMode="decimal"
                        placeholder="0"
                        aria-label={`${row.model || `第 ${i + 1} 行`} ${
                          field === 'inputHit' ? '命中输入价' : field === 'inputMiss' ? '未命中输入价' : '输出价'
                        }`}
                        className="input-sm w-[92px] text-right tabular-nums"
                      />
                    </td>
                  ))}
                  <td className="text-right">
                    <Button variant="ghost" size="sm" onClick={() => removeRow(i)}>删除</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" onClick={() => addRow()}>添加模型</Button>
        {missing.length > 0 && (
          <>
            <span className="text-[11px] text-text-tertiary">本页未配价：</span>
            {missing.map(m => (
              <button
                key={m}
                type="button"
                onClick={() => addRow(m)}
                className="rounded border border-border px-1.5 py-0.5 font-mono text-[10px] text-text-secondary transition-colors hover:border-accent hover:text-accent"
              >
                + {m}
              </button>
            ))}
          </>
        )}
      </div>

      <p className="mt-4 text-[10px] text-text-tertiary">
        模型名支持前缀匹配：配 <span className="font-mono">claude-sonnet-4-5</span> 即可覆盖
        <span className="font-mono"> claude-sonnet-4-5-20250929</span>。当前示例单价格式：
        {formatUnitPrice(3, currency.trim() || '$')}。
      </p>
    </Drawer>
  )
}

/** 各页面统一的「模型价格」入口按钮，点开同一个抽屉。 */
export function PricingButton({
  suggestModels = [],
  size = 'sm',
}: {
  suggestModels?: string[]
  size?: 'sm' | 'md'
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="secondary" size={size} onClick={() => setOpen(true)}>模型价格</Button>
      <ModelPricingDrawer open={open} onClose={() => setOpen(false)} suggestModels={suggestModels} />
    </>
  )
}
