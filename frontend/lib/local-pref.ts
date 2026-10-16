"use client"

import { useCallback, useEffect, useState } from "react"

/**
 * A viewer convenience remembered in this browser: a window's height, its
 * text size. Not a setting — nothing on the server reads it, and losing it
 * (a private window, cleared site data, storage that throws) only means
 * starting from the default again, so every read and write is guarded.
 *
 * The stored object is merged over the default, so a key added later reads
 * its default rather than `undefined` from an older saved value.
 */
export function useLocalPref<T extends object>(key: string, fallback: T): [T, (patch: Partial<T>) => void] {
  // The first render must match the server's (the static export has no
  // localStorage), so the saved value is applied in an effect.
  const [value, setValue] = useState<T>(fallback)

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key)
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one read from storage after mount
      if (raw) setValue((v) => ({ ...v, ...(JSON.parse(raw) as Partial<T>) }))
    } catch {
      /* unreadable: keep the default */
    }
  }, [key])

  const update = useCallback(
    (patch: Partial<T>) =>
      setValue((v) => {
        const next = { ...v, ...patch }
        try {
          window.localStorage.setItem(key, JSON.stringify(next))
        } catch {
          /* unwritable: the change still applies to this page */
        }
        return next
      }),
    [key],
  )

  return [value, update]
}
