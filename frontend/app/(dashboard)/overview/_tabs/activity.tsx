"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Empty, Panel, Seg } from "@/components/ld"
import { failuresFirst, useFeedExtras, withinHours, type ActivityItem, type FeedItem, type FeedKind } from "@/lib/activity"
import { apiFetch } from "@/lib/api"
import { can, useAuth } from "@/lib/auth"
import { shortAgo } from "@/lib/fleet"
import { buckets, clock } from "@/lib/overview"
import { useNow } from "@/hooks/use-now"
import type { AdminUser } from "@/lib/types"
import { Axis, BucketStrip, FeedRow, muted } from "./parts"

export type Range = "24h" | "7d"
type KindFilter = FeedKind | "all" | "failed"

const KINDS: { k: KindFilter; label: string }[] = [
  { k: "all", label: "all" },
  { k: "failed", label: "failed" },
  { k: "schedule", label: "schedule" },
  { k: "action", label: "action" },
  { k: "apply", label: "apply" },
  { k: "collect", label: "collect" },
  { k: "drift", label: "drift" },
  { k: "alert", label: "alert" },
  { k: "ai", label: "AI" },
]

const userName = (email: string) => email.split("@")[0]

export function ActivityTab({
  runs,
  isLoading,
  range,
  setRange,
  initialKind,
  wide,
  go,
}: {
  runs: ActivityItem[]
  isLoading: boolean
  range: Range
  setRange: (r: Range) => void
  initialKind: string | null
  wide: boolean
  go: (href: string) => void
}) {
  const hours = range === "7d" ? 168 : 24
  const size = range === "7d" ? 4 : 1
  const [kind, setKind] = useState<KindFilter>(KINDS.some((k) => k.k === initialKind) ? (initialKind as KindFilter) : "all")
  const [bucket, setBucket] = useState<number | null>(null)
  const extras = useFeedExtras(hours)
  const now = useNow()
  const { user } = useAuth()
  const { data: users } = useQuery<AdminUser[]>({
    queryKey: ["admin-users"],
    queryFn: () => apiFetch<AdminUser[]>("/api/admin/users"),
    enabled: can(user, "users.manage"),
    retry: false,
  })

  const all = useMemo<FeedItem[]>(
    () => [...withinHours(runs, hours), ...extras.items].sort((a, b) => b.at.localeCompare(a.at)),
    [runs, extras.items, hours],
  )
  const strip = buckets(
    all.filter((a) => a.kind !== "alert" && a.kind !== "ai"),
    (a) => a.at,
    (a) => (a.status === "ok" ? "ok" : a.status === "failed" ? "failed" : a.status === "running" || a.status === "queued" ? "running" : null),
    hours,
    size,
    now,
  )
  const inBucket = (a: FeedItem) => {
    if (bucket == null) return true
    const t = new Date(a.at).getTime()
    return t >= bucket && t < bucket + size * 3_600_000
  }
  const match = (a: FeedItem, k: KindFilter) => k === "all" || (k === "failed" ? a.status === "failed" : a.kind === k)
  const shown = failuresFirst(all.filter((a) => match(a, kind) && inBucket(a)))

  const nameOf = useMemo(() => {
    const m = new Map((users ?? []).map((u) => [u.id, userName(u.email)]))
    if (user) m.set(user.id, userName(user.email))
    return (id: number | null | undefined) => (id != null ? (m.get(id) ?? `user #${id}`) : "user")
  }, [users, user])
  const whoLabel = (a: FeedItem) => (a.who === "user" && a.userId != null ? nameOf(a.userId) : undefined)

  // The same failure more than once, folded to one line per action and host.
  const repeats = useMemo(() => {
    const m = new Map<string, { what: string; where: string; n: number; last: FeedItem }>()
    for (const a of all) {
      if (a.status !== "failed") continue
      const ff = a.run?.first_failure
      const what = a.run ? a.run.action_key.replace(/^_builtin\./, "") : a.kind
      const where = ff?.hostname ?? a.run?.target_label ?? a.title.split(" → ").at(-1) ?? ""
      const key = `${what}|${where}`
      const cur = m.get(key)
      if (cur) cur.n++
      else m.set(key, { what, where, n: 1, last: a })
    }
    return [...m.values()].filter((r) => r.n > 1).sort((a, b) => b.n - a.n)
  }, [all])

  // Who started what: the share of the window that ran unattended.
  const starters = useMemo(() => {
    const m = new Map<string, number>()
    for (const a of all) {
      const k = a.kind === "alert" || a.kind === "ai" ? "alerts / AI" : a.who === "user" ? nameOf(a.userId) : a.who === "schedule" ? "schedules" : a.who === "alert" ? "alerts / AI" : "system"
      m.set(k, (m.get(k) ?? 0) + 1)
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1])
  }, [all, nameOf])
  const startersMax = Math.max(1, ...starters.map(([, n]) => n))

  const axis = range === "7d" ? ["7d ago", "4d", "now"] : ["24h ago", "12h", "now"]

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Seg
          sm
          value={range}
          onChange={(r) => {
            setBucket(null)
            setRange(r as Range)
          }}
          options={[
            { k: "24h", label: "24h" },
            { k: "7d", label: "7d" },
          ]}
        />
        <Seg
          sm
          value={kind}
          onChange={(k) => setKind(k as KindFilter)}
          options={KINDS.map((o) => ({ k: o.k, label: `${o.label} ${all.filter((a) => match(a, o.k)).length}` }))}
        />
      </div>

      <Panel title="by hour" meta={bucket == null ? "click a bar to filter" : `${clock(bucket, range === "7d")} · click again to clear`}>
        <div className="px-[11px] pb-2 pt-2.5">
          <BucketStrip data={strip} h={54} active={bucket} onPick={setBucket} label={(b) => clock(b.start, range === "7d")} />
          <Axis labels={axis} />
        </div>
      </Panel>

      <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1.7fr) minmax(0,1fr)" : "minmax(0,1fr)", alignItems: "start" }}>
        <Panel
          title={range === "7d" ? "last 7 days" : "last 24 hours"}
          meta={`${shown.length} events · failures first`}
          actions={
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/runs")}>
              full run history →
            </button>
          }
        >
          {isLoading ? (
            <div className="p-3 text-xs text-text-3">Loading…</div>
          ) : shown.length === 0 ? (
            <Empty title={all.length ? "Nothing matches" : "Quiet"} note={all.length ? "No events of this kind in the window." : `No applies, runs, alerts or assistant sessions in the last ${range === "7d" ? "7 days" : "24 hours"}.`} />
          ) : (
            shown.slice(0, 200).map((a) => <FeedRow key={a.id} a={a} go={go} who={whoLabel(a)} />)
          )}
          {shown.length > 200 && <div className={muted}>Showing the newest 200 of {shown.length}. The full history is on Runs.</div>}
        </Panel>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="failing most" meta="same failure, more than once">
            {repeats.length === 0 ? (
              <div className={muted}>No failure happened twice in this window.</div>
            ) : (
              repeats.slice(0, 6).map((r) => (
                <button
                  key={`${r.what}|${r.where}`}
                  type="button"
                  className="row-hover flex w-full items-center gap-2.5 border-0 border-b border-line-faint bg-transparent px-[11px] py-[7px] text-left"
                  onClick={() => r.last.href && go(r.last.href)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="mono trunc text-xs text-text">
                      {r.what} · {r.where}
                    </div>
                    <div className="mono trunc text-[10.5px] text-danger-ink" title={r.last.detail}>
                      {r.last.detail || "failed"} · last {shortAgo(r.last.at)} ago
                    </div>
                  </div>
                  <span className="mono num shrink-0 text-[12px] font-semibold text-danger">{r.n}×</span>
                </button>
              ))
            )}
          </Panel>

          <Panel title="who started what">
            {starters.length === 0 ? (
              <div className={muted}>Nothing ran.</div>
            ) : (
              <div className="flex flex-col gap-1.5 p-[11px]">
                {starters.map(([k, n]) => (
                  <div key={k} className="flex items-center gap-2.5">
                    <span className="trunc w-[90px] shrink-0 text-[11.5px] text-text-2">{k}</span>
                    <span className="h-[6px] flex-1 overflow-hidden rounded-[3px] bg-surface-3">
                      <i className="block h-full" style={{ width: `${(n / startersMax) * 100}%`, background: k === "system" || k === "schedules" ? "var(--text-3)" : "var(--accent)" }} />
                    </span>
                    <span className="mono num w-8 shrink-0 text-right text-[11.5px] text-text-2">{n}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}
