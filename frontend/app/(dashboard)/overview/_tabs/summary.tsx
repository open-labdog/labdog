"use client"

import { useMemo } from "react"
import Link from "next/link"
import { Dot, Empty, Panel, Spark, StatusBar } from "@/components/ld"
import { useNow } from "@/hooks/use-now"
import { failuresFirst, withinHours, type ActivityItem } from "@/lib/activity"
import { countStatuses, plural, shortAgo, staleHosts, STALE_DAYS, untilLabel } from "@/lib/fleet"
import {
  actionLabel,
  BAD,
  buckets,
  clock,
  durationLabel,
  failStreak,
  fires,
  FINISHED,
  isOrphaned,
  lastFinished,
  reach,
  scheduleName,
  scheduleTarget,
  useFailedRuns,
  useScheduledRuns,
  useStuckOperations,
} from "@/lib/overview"
import type { usePendingQueue } from "@/lib/pending"
import type { DriftCoverage, Host, ScheduledAction } from "@/lib/types"
import { Axis, BucketStrip, FeedRow, History, muted, Next24, PendRow, Tile } from "./parts"

const DAY = 86_400_000

export function SummaryTab({
  hosts,
  hostsLoading,
  schedules,
  pending,
  activity,
  activityLoading,
  coverage,
  wide,
  go,
  setView,
}: {
  hosts: Host[]
  hostsLoading: boolean
  schedules: ScheduledAction[]
  pending: ReturnType<typeof usePendingQueue>
  activity: ActivityItem[]
  activityLoading: boolean
  coverage: DriftCoverage | undefined
  wide: boolean
  go: (href: string) => void
  setView: (v: string) => void
}) {
  const now = useNow()
  const stuck = useStuckOperations()
  const failedRuns = useFailedRuns(7)
  const scheduledRuns = useScheduledRuns(7)
  const counts = countStatuses(hosts)
  const last24 = useMemo(() => withinHours(activity, 24), [activity])
  const failed24 = last24.filter((a) => a.status === "failed")
  const enabled = schedules.filter((s) => s.enabled)
  const runById = new Map((failedRuns.data ?? []).map((r) => [r.id, r]))

  // Scheduled runs over 7 days: the share that ended ok on every host,
  // and the same per day for the spark.
  const finished = (scheduledRuns.data ?? []).filter((r) => FINISHED.has(r.status) && r.status !== "cancelled")
  const okRuns = finished.filter((r) => r.status === "succeeded" || r.status === "completed").length
  const rate = finished.length ? Math.round((okRuns / finished.length) * 100) : null
  const perDay = Array.from({ length: 7 }, (_, i) => {
    const from = now - (7 - i) * DAY
    const day = finished.filter((r) => {
      const t = new Date(r.created_at).getTime()
      return t >= from && t < from + DAY
    })
    return day.length ? (day.filter((r) => !BAD.has(r.status)).length / day.length) * 100 : 100
  })

  const next = enabled
    .filter((s) => s.next_run_at)
    .sort((a, b) => a.next_run_at!.localeCompare(b.next_run_at!))[0]
  const firstExpiry = pending.live.find((p) => Number.isFinite(p.expiresAt) && p.expiresAt > now)
  const stuckItems = stuck.data?.items ?? []

  // Failing schedules first, then any with a bad run in the last 8, then by next run.
  const health = [...enabled].sort((a, b) => {
    const d = failStreak(b.recent_runs) - failStreak(a.recent_runs)
    if (d) return d
    const bad = (s: ScheduledAction) => Number((s.recent_runs ?? []).some((r) => BAD.has(r.status)))
    if (bad(b) - bad(a)) return bad(b) - bad(a)
    return (a.next_run_at ?? "~").localeCompare(b.next_run_at ?? "~")
  })

  const drifted = hosts.filter((h) => h.sync_status === "out_of_sync")
  const stale = staleHosts(hosts)
  const driftOff = coverage !== undefined && coverage.hosts_total > 0 && coverage.any_enabled_hosts === 0
  const orphaned = schedules.filter(isOrphaned)
  const strip = buckets(last24, (a) => a.at, (a) => (a.status === "ok" ? "ok" : a.status === "failed" ? "failed" : a.status === "running" || a.status === "queued" ? "running" : null), 24, 1, now)
  const next24 = fires(enabled, hosts, 24, now)
  const names = (hs: { hostname: string }[]) => `${hs.slice(0, 3).map((h) => h.hostname).join(", ")}${hs.length > 3 ? ` +${hs.length - 3}` : ""}`

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <StatusBar counts={counts} onPick={(k) => go(`/hosts?status=${k}`)} />

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
        <Tile
          label="waiting on you"
          value={pending.laneCounts.all}
          tone={pending.laneCounts.all ? "hold" : undefined}
          sub={firstExpiry ? `first expires ${untilLabel(new Date(firstExpiry.expiresAt).toISOString())}` : pending.laneCounts.all ? "nothing expires" : "queue is empty"}
          onClick={() => setView("pending")}
        />
        <Tile
          label="failed · 24h"
          value={failed24.length}
          tone={failed24.length ? "danger" : undefined}
          sub={failed24.length ? failed24.slice(0, 2).map((a) => a.title).join(" · ") : "no failures"}
          onClick={() => go("/overview?view=activity&kind=failed")}
        />
        <Tile
          label="scheduled runs · 7d"
          value={rate === null ? "—" : `${rate}%`}
          tone={rate === null ? undefined : rate === 100 ? "ok" : rate >= 90 ? "warn" : "danger"}
          sub={finished.length ? `${okRuns} of ${finished.length} ok on every host` : "no scheduled runs this week"}
          onClick={() => setView("upcoming")}
        >
          {finished.length > 0 && (
            <div className="h-[14px] w-full">
              <Spark data={perDay} tone={rate === 100 ? "ok" : "warn"} h={14} fill={false} />
            </div>
          )}
        </Tile>
        <Tile
          label="stuck"
          value={stuckItems.length}
          tone={stuckItems.length ? "warn" : undefined}
          sub={
            stuckItems.length
              ? `${actionLabel(stuckItems[0].label)} on ${stuckItems[0].hostnames.join(", ")}, ${durationLabel(now - new Date(stuckItems[0].started_at).getTime())}${stuckItems[0].queued_behind ? ` · ${stuckItems[0].queued_behind} queued` : ""}`
              : `nothing running past ${stuck.data?.threshold_minutes ?? 30} min`
          }
          onClick={() => go("/overview?view=pending&lane=stuck")}
        />
        <Tile
          label="next run"
          value={<span className="text-[15px]">{next?.next_run_at ? untilLabel(next.next_run_at) : "—"}</span>}
          sub={next ? `${scheduleName(next)} · ${plural(reach(next, hosts).length, "host")}` : "nothing scheduled"}
          onClick={() => setView("upcoming")}
        />
      </div>

      <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1.6fr) minmax(0,1fr)" : "minmax(0,1fr)", alignItems: "start" }}>
        <div className="flex min-w-0 flex-col gap-3">
          <Panel
            title="pending"
            meta={`${pending.laneCounts.all} waiting · soonest to expire`}
            actions={
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setView("pending")}>
                open →
              </button>
            }
          >
            {pending.live.length === 0 ? (
              <Empty title="Nothing waiting on you" note="Approval gates, failed scheduled runs, stuck operations, discovered hosts, firing alerts and drift all land here." />
            ) : (
              pending.live.slice(0, 4).map((p) => <PendRow key={p.id} p={p} go={go} onDismiss={pending.dismiss} />)
            )}
            {pending.live.length > 4 && (
              <button type="button" className="btn btn-sm btn-ghost m-[9px] justify-center" onClick={() => setView("pending")}>
                {pending.live.length - 4} more waiting
              </button>
            )}
          </Panel>
          <Panel
            title="activity · last 24h"
            meta="failures first"
            actions={
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setView("activity")}>
                open →
              </button>
            }
          >
            <div className="px-[11px] pb-1 pt-2.5">
              <BucketStrip data={strip} label={(b) => clock(b.start)} />
              <Axis labels={["24h ago", "12h", "now"]} />
            </div>
            {activityLoading ? (
              <div className="p-3 text-xs text-text-3">Loading…</div>
            ) : last24.length === 0 ? (
              <Empty title="Quiet day" note="No applies or action runs in the last 24 hours." />
            ) : (
              failuresFirst(last24)
                .slice(0, 6)
                .map((a) => <FeedRow key={a.id} a={a} go={go} />)
            )}
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel
            title="schedule health"
            meta="last 8 runs · failing first"
            actions={
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setView("upcoming")}>
                upcoming →
              </button>
            }
          >
            {health.length === 0 ? (
              <div className={muted}>Nothing is scheduled. Schedules are actions with a cron, created from Operations · Actions.</div>
            ) : (
              health.slice(0, 6).map((s) => {
                const streak = failStreak(s.recent_runs)
                const last = lastFinished(s.recent_runs)
                const ff = last ? runById.get(last.id)?.first_failure : null
                const why = !last
                  ? s.next_run_at
                    ? `never run · first ${untilLabel(s.next_run_at)}`
                    : "never run"
                  : BAD.has(last.status)
                    ? `last: ${last.status}${ff ? `, ${ff.hostname} failed` : ""}${streak > 1 ? ` · ${streak} in a row` : ""}`
                    : `last: ok ${shortAgo(last.created_at)} ago`
                return (
                  <button
                    key={s.id}
                    type="button"
                    className="row-hover flex w-full items-center gap-2.5 border-0 border-b border-line-faint bg-transparent px-[11px] py-[7px] text-left"
                    style={{ boxShadow: streak ? "inset 2px 0 0 var(--danger)" : undefined }}
                    onClick={() => go(last && BAD.has(last.status) ? `/actions/runs/${last.id}` : "/overview?view=upcoming")}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="trunc text-xs font-medium text-text">{scheduleName(s)}</div>
                      <div className="trunc text-[10.5px]" style={{ color: streak ? "var(--danger-ink)" : "var(--text-3)" }}>
                        {scheduleTarget(s)} · {why}
                      </div>
                    </div>
                    <History runs={s.recent_runs} />
                  </button>
                )
              })
            )}
            {health.length > 6 && (
              <button type="button" className="btn btn-sm btn-ghost m-[9px] justify-center" onClick={() => setView("upcoming")}>
                {health.length - 6} more schedules
              </button>
            )}
          </Panel>

          <Panel title="next 24 hours" meta="height = hosts reached">
            {next24.length === 0 ? (
              <div className={muted}>{next?.next_run_at ? `Nothing runs in the next 24 hours. Next: ${scheduleName(next)} ${untilLabel(next.next_run_at)}.` : "Nothing is scheduled to run."}</div>
            ) : (
              <Next24 fires={next24} fleet={hosts.length} now={now} />
            )}
          </Panel>

          <Panel title="hygiene" meta="one line each">
            <Line
              tone={drifted.length ? "warn" : driftOff ? "warn" : "ok"}
              text={
                drifted.length
                  ? `${plural(drifted.length, "host")} drifted · ${names(drifted)}`
                  : driftOff
                    ? `Drift checking is off on all ${coverage?.hosts_total} hosts`
                    : "No host has drifted"
              }
              href={drifted.length ? "/drift" : driftOff ? "/hosts" : undefined}
              link={drifted.length ? "findings →" : "enable →"}
            />
            <Line
              tone={stale.length ? "warn" : "ok"}
              text={hostsLoading ? "Loading…" : stale.length ? `${plural(stale.length, "host")} not verified in ${STALE_DAYS} days · ${names(stale)}` : `Every host verified in the last ${STALE_DAYS} days`}
              href={stale.length ? "/overview?view=state" : undefined}
              link="fleet state →"
            />
            {orphaned.length > 0 && (
              <Line
                tone="danger"
                text={`${plural(orphaned.length, "schedule")} ${orphaned.length === 1 ? "points" : "point"} at an action no pack provides · ${orphaned.map((s) => s.action_key).join(", ")}`}
                href="/actions?tab=schedules"
                link="schedules →"
              />
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}

function Line({ tone, text, href, link }: { tone: "ok" | "warn" | "danger"; text: string; href?: string; link?: string }) {
  return (
    <div className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]">
      <Dot tone={tone} />
      <span className="trunc flex-1 text-xs text-text-2" title={text}>
        {text}
      </span>
      {href && link && (
        <Link href={href} className="shrink-0 text-[11px]">
          {link}
        </Link>
      )}
    </div>
  )
}
