import { useState, useCallback } from 'react'

export function useExpandCollapseAll(getKeys: () => string[]) {
  const [remountKey, setRemountKey] = useState(0)

  const expandAll = useCallback(() => {
    try {
      for (const key of getKeys()) sessionStorage.setItem(key, 'true')
    } catch { /* sessionStorage may be unavailable */ }
    setRemountKey((k) => k + 1)
  }, [getKeys])

  const collapseAll = useCallback(() => {
    try {
      for (const key of getKeys()) sessionStorage.setItem(key, 'false')
    } catch { /* sessionStorage may be unavailable */ }
    setRemountKey((k) => k + 1)
  }, [getKeys])

  return { remountKey, expandAll, collapseAll }
}
