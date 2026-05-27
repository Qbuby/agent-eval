import { useEffect, useState, useCallback } from 'react'

export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'agent-eval-theme'

function readStored(): ThemeChoice {
  if (typeof window === 'undefined') return 'system'
  const v = localStorage.getItem(STORAGE_KEY)
  return v === 'light' || v === 'dark' || v === 'system' ? v : 'system'
}

function systemPrefers(): ResolvedTheme {
  if (typeof window === 'undefined' || !window.matchMedia) return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function apply(resolved: ResolvedTheme) {
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')
  root.style.colorScheme = resolved
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(() => readStored())
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    readStored() === 'system' ? systemPrefers() : (readStored() as ResolvedTheme),
  )

  useEffect(() => {
    apply(resolved)
  }, [resolved])

  useEffect(() => {
    if (choice !== 'system') {
      setResolved(choice)
      return
    }
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setResolved(mq.matches ? 'dark' : 'light')
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [choice])

  const set = useCallback((next: ThemeChoice) => {
    localStorage.setItem(STORAGE_KEY, next)
    setChoice(next)
  }, [])

  return { choice, resolved, setChoice: set }
}

export function initThemeBeforeRender() {
  // Run synchronously before React mounts to avoid a flash of wrong theme.
  try {
    const stored = readStored()
    const resolved: ResolvedTheme =
      stored === 'system'
        ? window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light'
        : stored
    apply(resolved)
  } catch {
    /* no-op */
  }
}
