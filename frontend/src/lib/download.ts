// File-download helpers for list exports.
//
// The axios instance injects a Bearer token via interceptor, so a plain
// `<a href>` to an export endpoint would hit it unauthenticated. Instead we
// request the file as a blob through axios (token attached), then create an
// object URL and click a synthetic anchor to save it.
//
// `triggerExport` is the single entry point pages use: it issues the request,
// derives a filename from the Content-Disposition header (falling back to a
// caller-supplied name), and saves. Errors propagate so callers can surface
// them through the shared formatApiError / ErrorCard path.

import type { AxiosResponse } from 'axios'
import api from '@/services/client'

export type ExportFormat = 'csv' | 'xlsx' | 'json'

const EXT: Record<ExportFormat, string> = { csv: 'csv', xlsx: 'xlsx', json: 'json' }

/** Save a Blob to disk under `filename` via a synthetic anchor click. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke on the next tick so the download has a chance to start.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Pull `filename="..."` out of a Content-Disposition header, if present. */
function filenameFromDisposition(disposition: unknown): string | null {
  if (typeof disposition !== 'string') return null
  // Handles both `filename="x.csv"` and RFC5987 `filename*=UTF-8''x.csv`.
  const star = disposition.match(/filename\*=(?:UTF-8'')?([^;]+)/i)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].replace(/"/g, '').trim())
    } catch {
      /* fall through */
    }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/i)
  return plain?.[1]?.trim() ?? null
}

interface TriggerExportOptions {
  /** HTTP method; export endpoints are GET (query filters) or POST (body). */
  method?: 'get' | 'post'
  url: string
  params?: Record<string, unknown>
  data?: unknown
  format: ExportFormat
  /** Used when the server doesn't send a Content-Disposition filename. */
  fallbackName: string
}

/**
 * Request an export endpoint as a blob and save the resulting file.
 * Resolves once the download has been triggered; rejects on request error.
 */
export async function triggerExport(opts: TriggerExportOptions): Promise<void> {
  const { method = 'get', url, params, data, format, fallbackName } = opts
  const res: AxiosResponse<Blob> = await api.request({
    method,
    url,
    params,
    data,
    responseType: 'blob',
  })
  const headerName = filenameFromDisposition(
    res.headers?.['content-disposition'] ?? res.headers?.['Content-Disposition'],
  )
  const filename = headerName ?? `${fallbackName}.${EXT[format]}`
  downloadBlob(res.data, filename)
}
