/**
 * Absolute local time, to the minute — "23 Aug 2026, 19:03".
 *
 * The counterpart to `shortAgo` in lib/fleet.ts, not a replacement for
 * it: "10m ago" is the better answer to "is this recent?", and the wrong
 * answer to "which of these three runs is which?". Three sessions run the
 * same evening all read "23h ago", which is how a list of identically
 * titled alert investigations became impossible to tell apart.
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
