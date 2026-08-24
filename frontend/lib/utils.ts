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

export function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return "Never"
  const diff = Date.now() - new Date(dateStr).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months}mo ago`
  const years = Math.floor(months / 12)
  return `${years}y ago`
}

/**
 * Absolute local time, to the minute — "23 Aug 2026, 19:03".
 *
 * The counterpart to `formatRelativeTime`, not a replacement for it: "10m
 * ago" is the better answer to "is this recent?", and the wrong answer to
 * "which of these three runs is which?". Three sessions run the same
 * evening all read "23h ago", which is how a list of identically titled
 * alert investigations became impossible to tell apart.
 *
 * Rendered in the viewer's own locale and timezone. Callers correlating
 * against a log line should keep the ISO string in a `title`.
 */
export function formatTimestamp(dateStr: string | null): string {
  if (!dateStr) return "Never"
  const date = new Date(dateStr)
  if (Number.isNaN(date.getTime())) return "Unknown"
  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}
