import { useEffect, useRef } from 'react'

const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

/**
 * 给模态/抽屉用的焦点管理：
 * - open 变 true 时：记下当前焦点，把焦点移进 panel 内的第一个可聚焦元素（找不到就聚焦 panel 本身）
 * - open 变 false / 卸载时：把焦点还给打开前的元素
 */
export function useDialogFocus<T extends HTMLElement = HTMLDivElement>(open: boolean) {
  const panelRef = useRef<T | null>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    previousFocusRef.current = (document.activeElement as HTMLElement) ?? null
    const node = panelRef.current
    if (node) {
      const focusable = node.querySelector<HTMLElement>(FOCUSABLE_SELECTOR)
      ;(focusable ?? node).focus()
    }
    return () => {
      previousFocusRef.current?.focus?.()
    }
  }, [open])

  return panelRef
}
