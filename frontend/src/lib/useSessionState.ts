import { useState, useCallback } from 'react'

/**
 * Like useState but persists to sessionStorage.
 * Survives page refresh (F5) but clears when the tab closes.
 */
export function useSessionState(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => {
    try {
      const stored = sessionStorage.getItem(key)
      return stored !== null ? stored === 'true' : initial
    } catch {
      return initial
    }
  })

  const set = useCallback(
    (v: boolean) => {
      try {
        sessionStorage.setItem(key, String(v))
      } catch {
        // sessionStorage full or disabled — ignore
      }
      setValue(v)
    },
    [key],
  )

  return [value, set]
}
