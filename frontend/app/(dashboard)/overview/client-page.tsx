"use client"

import { useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { queueHostStateCollection } from "@/lib/collect-state"
import { useActivityStream } from "@/lib/activity"
import { LANES, usePendingQueue, type PendingLane } from "@/lib/pending"
import { useSchedules } from "@/lib/overview"
import { showError, showSuccess } from "@/lib/toast"
import { useViewportWidth } from "@/hooks/use-viewport"
import type { DriftCoverage, Host, HostGroup } from "@/lib/types"
import { PageHead, Tag } from "@/components/ld"
import { ActivityTab, type Range } from "./_tabs/activity"
import { PendingTab } from "./_tabs/pending"
import { StateTab } from "./_tabs/state"
import { SummaryTab } from "./_tabs/summary"
import { UpcomingTab } from "./_tabs/upcoming"

type View = "summary" | "pending" | "state" | "activity" | "upcoming"

const HEADS: Record<View, { title: string; sub: string }> = {
  summary: { title: "Overview", sub: "How scheduled runs are doing, what is waiting on you, and the state of the fleet." },
  pending: { title: "Pending", sub: "Everything waiting on a decision, soonest to expire first. Hiding an item keeps it in history." },
  state: { title: "Fleet state", sub: "Standing condition of every host — not a queue. Every status segment filters the hosts list." },
  activity: { title: "Activity", sub: "Applies, action runs, alerts and assistant sessions. Failures first." },
  upcoming: { title: "Upcoming", sub: "What will touch the fleet next, with its blast radius — plus the integrations those runs depend on." },
}

export default function OverviewPage() {
  const router = useRouter()
  const search = useSearchParams()
  const view = (search.get("view") as View | null) ?? "summary"
  // Lane and range live in the URL, so a tile elsewhere on the page can
  // link straight to them and the back button undoes a filter.
  const laneParam = search.get("lane") as PendingLane | null
  const lane: PendingLane | "all" = laneParam && LANES.includes(laneParam) ? laneParam : "all"
  const setLane = (l: PendingLane | "all") => router.replace(l === "all" ? "/overview?view=pending" : `/overview?view=pending&lane=${l}`)
  const range: Range = search.get("range") === "7d" ? "7d" : "24h"
  const setRange = (r: Range) => router.replace(r === "7d" ? "/overview?view=activity&range=7d" : "/overview?view=activity")
  const [checking, setChecking] = useState(false)
  const wide = useViewportWidth() >= 940

  const { data: hosts, isLoading: hostsLoading } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    refetchInterval: 30_000,
  })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const { data: coverage } = useQuery<DriftCoverage>({
    queryKey: ["dashboard", "drift-coverage"],
    queryFn: () => apiFetch<DriftCoverage>("/api/dashboard/drift-coverage"),
    refetchInterval: 60_000,
  })
  const { data: schedules } = useSchedules()
  // One queue for the whole page: hiding an item in a tab must update the head's count.
  const pending = usePendingQueue()
  const sevenDays = view === "activity" && range === "7d"
  const activity = useActivityStream(sevenDays ? 500 : 200, 30_000, sevenDays ? 168 : 24)

  const all = hosts ?? []
  const go = (href: string) => router.push(href)
  const setView = (v: string) => router.push(v === "summary" ? "/overview" : `/overview?view=${v}`)

  const checkFleet = async () => {
    if (all.length === 0) return
    setChecking(true)
    const results = await Promise.allSettled(all.map((h) => queueHostStateCollection(h.id)))
    setChecking(false)
    const failed = results.filter((r) => r.status === "rejected").length
    if (failed) showError(`State collection queued for ${all.length - failed} of ${all.length} hosts`)
    else showSuccess(`State collection queued for ${all.length} hosts`)
  }

  const head = HEADS[view] ?? HEADS.summary
  const laneCounts = pending.laneCounts

  return (
    <>
      <PageHead
        title={
          <>
            {head.title}
            {view === "summary" && (
              <span className="mono num text-xs font-normal text-text-3">
                {all.length} host{all.length === 1 ? "" : "s"} · {groups?.length ?? "…"} groups
              </span>
            )}
            {view === "pending" && laneCounts.all > 0 && <Tag tone="hold">{laneCounts.all} waiting</Tag>}
          </>
        }
        sub={head.sub}
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => void checkFleet()} disabled={checking || all.length === 0} title="Collect current state from every host and compare it to desired state">
              {checking ? "Queuing…" : "Drift-check fleet"}
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={() => go("/plans")}>
              Plan a sync
            </button>
          </>
        }
      >
        {view === "pending" && (
          <div className="flex flex-wrap items-center gap-[9px]">
            <span className="tt text-text-3">click a lane to filter it</span>
            <span className="tt ml-auto">badge counts blocking + expiring only</span>
          </div>
        )}
      </PageHead>

      {view === "summary" && (
        <SummaryTab
          hosts={all}
          hostsLoading={hostsLoading}
          schedules={schedules ?? []}
          pending={pending}
          activity={activity.items}
          activityLoading={activity.isLoading}
          coverage={coverage}
          wide={wide}
          go={go}
          setView={setView}
        />
      )}
      {view === "pending" && <PendingTab pending={pending} lane={lane} setLane={setLane} wide={wide} go={go} setView={setView} />}
      {view === "state" && <StateTab hosts={all} groups={groups ?? []} coverage={coverage} wide={wide} go={go} />}
      {view === "activity" && (
        <ActivityTab runs={activity.items} isLoading={activity.isLoading} range={range} setRange={setRange} initialKind={search.get("kind")} wide={wide} go={go} />
      )}
      {view === "upcoming" && <UpcomingTab schedules={schedules ?? []} hosts={all} groups={groups ?? []} wide={wide} go={go} />}
    </>
  )
}
