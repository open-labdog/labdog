"use client"

import { useEffect, useState } from "react"

/**
 * The current time, refreshed every `intervalMs`. Relative labels ("in
 * 4h 20m", "2h ago") and time windows read it instead of calling
 * `Date.now()` while rendering, so a render is a function of state and the
 * labels still move on their own.
 */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}
