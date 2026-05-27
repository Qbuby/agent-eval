import { useQuery } from '@tanstack/react-query'
import { configApi } from '@/services'
import type { ConfigItem, ConfigOption } from '@/types'

export interface ConfigOptionsResult {
  options: ConfigOption[]
  defaultValue: unknown
  defaultIndex: number
  isLoading: boolean
  isError: boolean
}

/**
 * Fetch configured option values for a single config key.
 *
 * Used by forms that want to offer a dropdown of pre-configured values
 * (e.g. New Evaluation page picking from `target_agent.endpoint_url`).
 * Falls back to an empty options list when the key is unknown — callers
 * should still allow free-text input.
 */
export function useConfigOptions(key: string | null | undefined): ConfigOptionsResult {
  const enabled = Boolean(key)
  const query = useQuery<ConfigItem | null>({
    queryKey: ['config-options', key],
    enabled,
    queryFn: async () => {
      try {
        const r = await configApi.get(key as string)
        return r.data
      } catch (err: unknown) {
        const e = err as { response?: { status?: number } }
        if (e?.response?.status === 404) return null
        throw err
      }
    },
    staleTime: 30_000,
  })

  const item = query.data
  const options = item?.options ?? []
  const defaultIndex = item?.default_index ?? 0
  const defaultValue = options[defaultIndex]?.value

  return {
    options,
    defaultValue,
    defaultIndex,
    isLoading: query.isLoading,
    isError: query.isError,
  }
}

/**
 * Coerce a config option's stored value (which may be number/object) to
 * a string suitable for an <input> or <select>.
 */
export function configOptionToString(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try { return JSON.stringify(value) } catch { return String(value) }
}
