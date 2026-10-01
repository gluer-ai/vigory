import { createContext, useCallback, useState } from 'react'

/** UI layout flags (is a panel collapsed?) remembered across reloads. Storage can
 * be unavailable (private mode, quota) - then the flag simply is not remembered. */
const PREFIX = 'vigory.ui.'

export function readFlag(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    return raw === null ? fallback : raw === '1'
  } catch {
    return fallback
  }
}

export function writeFlag(key: string, value: boolean): void {
  try {
    localStorage.setItem(PREFIX + key, value ? '1' : '0')
  } catch {
    // not remembered; the panel still works
  }
}

export function usePersistedFlag(key: string, fallback = false): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState(() => readFlag(key, fallback))
  const set = useCallback(
    (next: boolean) => {
      setValue(next)
      writeFlag(key, next)
    },
    [key],
  )
  return [value, set]
}

/** The app's left navigation rail, owned by AppShell. Pages read it to offer a
 * "wide canvas" that also tucks the rail away. */
export interface RailState {
  collapsed: boolean
  setCollapsed: (collapsed: boolean) => void
}
export const RailContext = createContext<RailState>({ collapsed: false, setCollapsed: () => {} })

/** "Wide canvas" is on when every side panel is collapsed. */
export const isWide = (panels: boolean[]) => panels.every(Boolean)
