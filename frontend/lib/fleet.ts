import type { Host, SyncStatus } from "@/lib/types"

/**
 * Status vocabulary — one place that says what each backend `sync_status`
 * is called and what tone it takes, so a host reads the same in the
 * status bar, the hosts table, the palette and the host header.
 */
export type Tone = "ok" | "warn" | "danger" | "sync" | "idle" | "hold" | "accent" | "add" | "del"

export interface StatusDef {
  label: string
  tone: Tone
}

export const STATUS: Record<SyncStatus, StatusDef> = {
  in_sync: { label: "in sync", tone: "ok" },
  out_of_sync: { label: "drifted", tone: "warn" },
  pending: { label: "syncing", tone: "sync" },
  error: { label: "failed", tone: "danger" },
  unknown: { label: "unknown", tone: "idle" },
}

/** Order of the fleet status bar — healthiest first, unknown last. */
export const STATUS_ORDER: SyncStatus[] = ["in_sync", "out_of_sync", "pending", "error", "unknown"]

export function statusDef(s: string | null | undefined): StatusDef {
  return STATUS[(s ?? "unknown") as SyncStatus] ?? { label: s ?? "unknown", tone: "idle" }
}

export type StatusCounts = Record<SyncStatus, number>

export function countStatuses(hosts: Pick<Host, "sync_status">[]): StatusCounts {
  const c: StatusCounts = { in_sync: 0, out_of_sync: 0, pending: 0, error: 0, unknown: 0 }
  for (const h of hosts) c[h.sync_status] = (c[h.sync_status] ?? 0) + 1
  return c
}

/** Whole days since an ISO timestamp; null when it never happened. */
export function daysSince(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms)) return null
  return Math.max(0, Math.floor(ms / 86_400_000))
}

/** "today", "3d", or "never" — the compact age the dense tables use. */
export function ageLabel(iso: string | null | undefined): string {
  const d = daysSince(iso)
  if (d === null) return "never"
  if (d === 0) return "today"
  return `${d}d`
}

export const STALE_DAYS = 30

/**
 * Hosts not synced in 30+ days (or never), oldest first. The failure mode
 * nobody notices: a host that is neither drifted nor failed, just
 * forgotten.
 */
export function staleHosts<T extends Pick<Host, "last_sync_at">>(hosts: T[]): T[] {
  return hosts
    .filter((h) => {
      const d = daysSince(h.last_sync_at)
      return d === null || d >= STALE_DAYS
    })
    .sort((a, b) => {
      const da = daysSince(a.last_sync_at)
      const db = daysSince(b.last_sync_at)
      // never-synced ranks as infinitely stale
      if (da === null && db === null) return 0
      if (da === null) return -1
      if (db === null) return 1
      return db - da
    })
}

/** "3 hosts", "1 rule" — the count and its noun, pluralised by adding an s. */
export function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`
}

/** Short relative age — "12m", "3h", "2d" — for activity streams. */
export function shortAgo(iso: string | null | undefined): string {
  if (!iso) return "—"
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h`
  const d = Math.floor(h / 24)
  if (d < 60) return `${d}d`
  return `${Math.floor(d / 30)}mo`
}

/** Compact future distance — "in 6h 12m", "in 3d" — for schedules. */
export function untilLabel(iso: string | null | undefined): string {
  if (!iso) return "—"
  const s = Math.floor((new Date(iso).getTime() - Date.now()) / 1000)
  if (Number.isNaN(s)) return "—"
  if (s <= 0) return "due"
  const m = Math.floor(s / 60)
  if (m < 60) return `in ${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `in ${h}h ${m % 60}m`
  const d = Math.floor(h / 24)
  return `in ${d}d`
}
