import { clsx, type ClassValue } from "clsx"
import { useState, useEffect } from "react"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function useDelayedLoading(isLoading: boolean, delayMs: number = 200): boolean {
  const [showLoading, setShowLoading] = useState(false)

  useEffect(() => {
    if (!isLoading) {
      setShowLoading(false)
      return
    }
    const timer = setTimeout(() => setShowLoading(true), delayMs)
    return () => clearTimeout(timer)
  }, [isLoading, delayMs])

  return showLoading
}
