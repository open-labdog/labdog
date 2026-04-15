import { clsx, type ClassValue } from "clsx"
import { useState, useEffect } from "react"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function useDelayedLoading(isLoading: boolean, delayMs: number = 200): boolean {
  const [elapsed, setElapsed] = useState(false)

  useEffect(() => {
    if (!isLoading) return
    const timer = setTimeout(() => setElapsed(true), delayMs)
    return () => {
      clearTimeout(timer)
      setElapsed(false)
    }
  }, [isLoading, delayMs])

  return isLoading && elapsed
}
