import { useCallback, useSyncExternalStore } from 'react'
import {
  getPricingConfig,
  resolvePrice,
  subscribePricing,
  type ModelPrice,
  type PricingConfig,
} from '@/lib/pricing'

/**
 * 订阅本地价格配置。任一处改价后，所有用到本 hook 的展示点即时重算成本
 * （store 是模块级的，跨页面/跨标签页共享）。
 */
export function usePricing(): {
  config: PricingConfig
  currency: string
  /** 取某模型单价；未配价返回 null，调用方据此显示「未配价」而非 0。 */
  priceOf: (model: string | null | undefined) => ModelPrice | null
  /** 单价是否精确命中该模型名（false 表示走了前缀匹配）。 */
  matchOf: (model: string | null | undefined) => { matchedKey: string; exact: boolean } | null
} {
  const config = useSyncExternalStore(subscribePricing, getPricingConfig, getPricingConfig)

  const priceOf = useCallback(
    (model: string | null | undefined) => resolvePrice(model, config)?.price ?? null,
    [config],
  )
  const matchOf = useCallback(
    (model: string | null | undefined) => {
      const hit = resolvePrice(model, config)
      return hit ? { matchedKey: hit.matchedKey, exact: hit.exact } : null
    },
    [config],
  )

  return { config, currency: config.currency, priceOf, matchOf }
}
