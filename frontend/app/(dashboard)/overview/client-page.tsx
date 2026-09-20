"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { queueHostStateCollection } from "@/lib/collect-state"
import { failuresFirst, useActivityStream, withinHours, type ActivityItem } from "@/lib/activity"
import { ageLabel, countStatuses, daysSince, shortAgo, staleHosts, STALE_DAYS } from "@/lib/fleet"
import { usePendingQueue, type PendingItem, type PendingLane } from "@/lib/pending"
import { showError, showSuccess } from "@/lib/toast"
import { useViewportWidth } from "@/hooks/use-viewport"
import type {
  AIProvider,
  AIUsageSummary,
  DriftCoverage,
  DriftTrendSeries,
  GitRepository,
  GrafanaInstance,
  Host,
  HostGroup,
  MetricsStatus,
  ProxmoxNode,
  ScheduledAction,
} from "@/lib/types"
import { Dot, Empty, PageHead, Panel, Seg, Spark, Status, StatusBar, Tag, type Tone } from "@/components/ld"

type View = "summary" | "pending" | "state" | "activity" | "upcoming"

const HEADS: Record<View, { title: string; sub: string }> = {
  summary: { title: "Overview", sub: "Fleet state and what is waiting on you, weighted evenly." },
  pending: { title: "Pending", sub: "Everything waiting on a decision, soonest to expire first. Hiding an item keeps it in history." },
  state: { title: "Fleet state", sub: "Standing condition of every host — not a queue. Every status segment filters the hosts list." },
  activity: { title: "Activity", sub: "Last 24 hours of applies, action runs and scheduled runs. Failures first." },
  upcoming: { title: "Upcoming", sub: "What will touch the fleet next, with its blast radius — plus the integrations those runs depend on." },
}

const ACT_TONE: Record<ActivityItem["status"], Tone | undefined> = {
  failed: "danger",
  running: "sync",
  queued: "idle",
  cancelled: "idle",
  ok: undefined,
}

function PendRow({ p, go, onDismiss }: { p: PendingItem; go: (href: string) => void; onDismiss: (id: string) => void }) {
  return (
    <div className="row-hover flex items-start gap-[11px] border-b border-line-faint px-[11px] py-[9px]">
      <div className="mt-1">
        <Dot tone={p.severity === "block" ? "hold" : p.severity === "warn" ? "warn" : "idle"} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="tt text-text-3">{p.kind}</span>
          <span className="mono num text-[10px]" style={{ color: p.expires === "firing" ? "var(--danger)" : "var(--text-faint)" }}>
            {p.expires}
          </span>
        </div>
        <div className="trunc text-[12.5px] font-medium text-text" title={p.title}>
          {p.title}
        </div>
        <div className="mono trunc text-[10.5px] text-text-3">{p.detail}</div>
      </div>
      <div className="flex shrink-0 gap-1.5">
        <button type="button" className="btn btn-sm" onClick={() => go(p.href)}>
          Review
        </button>
        {p.dismissible && (
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => onDismiss(p.id)} title="Hide from this browser's queue — it stays in history">
            Hide
          </button>
        )}
      </div>
    </div>
  )
}

function ActRow({ a, go }: { a: ActivityItem; go: (href: string) => void }) {
  return (
    <div className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]" style={{ background: a.status === "failed" ? "var(--danger-soft)" : "transparent" }}>
      <span className="mono num w-7 shrink-0 text-[10.5px] text-text-3">{shortAgo(a.at)}</span>
      <Tag tone={ACT_TONE[a.status]}>{a.kind}</Tag>
      <div className="min-w-0 flex-1">
        <div className="trunc text-xs text-text">{a.title}</div>
        {a.detail && (
          <div className="mono trunc text-[10.5px]" style={{ color: a.status === "failed" ? "var(--danger-ink)" : "var(--text-3)" }}>
            {a.detail}
          </div>
        )}
      </div>
      <span className="tt text-[9px]">{a.who}</span>
      {a.href ? (
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => go(a.href!)}>
          open →
        </button>
      ) : a.hostId != null ? (
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => go(`/hosts/${a.hostId}`)}>
          host →
        </button>
      ) : null}
    </div>
  )
}

function SchedRow({ s, radius, fleet }: { s: ScheduledAction; radius: number; fleet: number }) {
  return (
    <div className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-2">
      <div className="min-w-0 flex-1">
        <div className="trunc text-xs text-text">{s.action_name ?? s.action_key}</div>
        <div className="mono trunc text-[10.5px] text-text-3">
          {s.schedule_cron ?? "manual"} · {s.target_kind === "fleet" ? "all hosts" : `${s.target_kind}: ${s.target_name ?? s.target_id}`}
        </div>
      </div>
      {!s.enabled && <Tag>paused</Tag>}
      <Tag tone={radius > fleet / 2 && fleet > 1 ? "warn" : undefined} title={`${radius} of ${fleet} hosts — unattended`}>
        {radius} host{radius === 1 ? "" : "s"}
      </Tag>
      {s.snapshot_enabled && (
        <Tag tone="ok" title="Proxmox snapshot before running">
          snap
        </Tag>
      )}
      <span className="mono num w-[68px] shrink-0 text-right text-[10.5px] text-text-2" title="last run">
        {s.last_run ? `${shortAgo(s.last_run.created_at)} ago` : "never"}
      </span>
    </div>
  )
}

function StaleRow({ h, go }: { h: Host; go: (href: string) => void }) {
  const d = daysSince(h.last_sync_at)
  return (
    <button type="button" className="row-hover flex w-full items-center gap-2.5 border-0 border-b border-line-faint bg-transparent px-[11px] py-[7px] text-left" onClick={() => go(`/hosts/${h.id}`)}>
      <span className="mono trunc flex-1 text-xs text-text">{h.hostname}</span>
      <Status s={h.sync_status} />
      <span className="mono num w-11 shrink-0 text-right text-[11px]" style={{ color: d === null || d > 60 ? "var(--danger)" : "var(--warn)" }}>
        {ageLabel(h.last_sync_at)}
      </span>
    </button>
  )
}

function DriftTrendBody({ h, drifted, trendData, driftOff, hostsTotal }: { h: number; drifted: number; trendData: number[]; driftOff: boolean; hostsTotal: number }) {
  return (
    <div className="flex flex-col gap-[9px] p-[11px]">
      <div className="flex items-baseline gap-2">
        <span className="mono num text-2xl font-semibold leading-none" style={{ color: drifted ? "var(--warn)" : "var(--text-3)" }}>
          {drifted}
        </span>
        <span className="text-[11.5px] text-text-3">
          host{drifted === 1 ? "" : "s"} drifted now · {trendData.length ? `${trendData.reduce((a, b) => a + b, 0)} drifted checks in 14d` : "no checks recorded"}
        </span>
      </div>
      {trendData.length > 1 ? <Spark data={trendData} tone="warn" h={h} /> : <div className="text-[11.5px] text-text-faint">Not enough history for a trend yet.</div>}
      {driftOff && (
        <div className="flex items-center gap-2 text-[11.5px] text-warn">
          <Dot tone="warn" />
          Drift checking is off on all {hostsTotal} hosts — nothing is being checked.{" "}
          <Link href="/hosts" className="text-warn underline">
            Enable it on Hosts
          </Link>
        </div>
      )}
    </div>
  )
}

export default function OverviewPage() {
  const router = useRouter()
  const search = useSearchParams()
  const view = (search.get("view") as View | null) ?? "summary"
  const laneParam = search.get("lane") as PendingLane | "all" | null
  const [lane, setLane] = useState<PendingLane | "all">(laneParam ?? "all")
  const [checking, setChecking] = useState(false)
  const wide = useViewportWidth() >= 940

  const { data: hosts, isLoading: hostsLoading } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    refetchInterval: 30_000,
  })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const { data: trend } = useQuery<DriftTrendSeries>({
    queryKey: ["dashboard", "drift-trend", 14],
    queryFn: () => apiFetch<DriftTrendSeries>("/api/dashboard/drift-trend?days=14&granularity=day"),
    refetchInterval: 60_000,
  })
  const { data: coverage } = useQuery<DriftCoverage>({
    queryKey: ["dashboard", "drift-coverage"],
    queryFn: () => apiFetch<DriftCoverage>("/api/dashboard/drift-coverage"),
    refetchInterval: 60_000,
  })
  const { data: schedules } = useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions"],
    queryFn: () => apiFetch<ScheduledAction[]>("/api/scheduled-actions"),
    refetchInterval: 60_000,
  })
  const pending = usePendingQueue()
  const activity = useActivityStream()

  const all = hosts ?? []
  const counts = countStatuses(all)
  const stale = staleHosts(all)
  const drifted = counts.out_of_sync
  const last24 = failuresFirst(withinHours(activity.items, 24))
  const trendData = (trend?.points ?? []).map((p) => p.drifted_checks)
  const driftOff = coverage !== undefined && coverage.hosts_total > 0 && coverage.any_enabled_hosts === 0
  const lastCheck = all.reduce<string | null>((m, h) => (h.last_drift_check_at && (!m || h.last_drift_check_at > m) ? h.last_drift_check_at : m), null)

  const pend = pending.live.filter((p) => lane === "all" || p.lane === lane)
  const laneCounts = pending.laneCounts

  const go = (href: string) => router.push(href)
  const setView = (v: View) => router.push(v === "summary" ? "/overview" : `/overview?view=${v}`)

  const checkFleet = async () => {
    if (all.length === 0) return
    setChecking(true)
    const results = await Promise.allSettled(all.map((h) => queueHostStateCollection(h.id)))
    setChecking(false)
    const failed = results.filter((r) => r.status === "rejected").length
    if (failed) showError(`State collection queued for ${all.length - failed} of ${all.length} hosts`)
    else showSuccess(`State collection queued for ${all.length} hosts`)
  }

  const radius = (s: ScheduledAction) =>
    s.target_kind === "fleet" ? all.length : s.target_kind === "host" ? 1 : all.filter((h) => s.target_id != null && h.group_ids.includes(s.target_id)).length

  const head = HEADS[view] ?? HEADS.summary
  const LaneSeg = (
    <Seg
      sm
      value={lane}
      onChange={(k) => setLane(k as PendingLane | "all")}
      options={[
        { k: "all", label: `all ${laneCounts.all}` },
        { k: "approvals", label: `approvals ${laneCounts.approvals}` },
        { k: "alerts", label: `alerts ${laneCounts.alerts}` },
        { k: "drift", label: `drift ${laneCounts.drift}` },
      ]}
    />
  )

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
        sub={view === "summary" ? `${lastCheck ? `Last drift check ${shortAgo(lastCheck)} ago` : "No drift check recorded yet"} · ${head.sub}` : head.sub}
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
            {LaneSeg}
            <span className="tt ml-auto">badge counts blocking + expiring only</span>
          </div>
        )}
      </PageHead>

      {view === "summary" && (
        <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
          <StatusBar counts={counts} onPick={(k) => go(`/hosts?status=${k}`)} />
          <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1.7fr) minmax(0,1fr)" : "minmax(0,1fr)", alignItems: "stretch" }}>
            <div className="flex min-w-0 flex-col gap-3">
              <Panel
                title="pending"
                meta={`${laneCounts.all} waiting · soonest to expire`}
                actions={
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => setView("pending")}>
                    open →
                  </button>
                }
              >
                {pending.live.length === 0 ? (
                  <Empty title="Nothing waiting on you" note="Approval gates, discovered hosts, firing alerts and drift all land here." />
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
                {activity.isLoading ? (
                  <div className="p-3 text-xs text-text-3">Loading…</div>
                ) : last24.length === 0 ? (
                  <Empty title="Quiet day" note="No applies or action runs in the last 24 hours." />
                ) : (
                  last24.slice(0, 6).map((a) => <ActRow key={a.id} a={a} go={go} />)
                )}
              </Panel>
            </div>
            <div className="flex min-w-0 flex-col gap-3">
              <Panel
                title="drift trend"
                meta="14 days"
                actions={
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/drift")}>
                    findings →
                  </button>
                }
              >
                <DriftTrendBody h={42} drifted={drifted} trendData={trendData} driftOff={driftOff} hostsTotal={coverage?.hosts_total ?? 0} />
              </Panel>
              <Panel
                title="stale hosts"
                meta={`${STALE_DAYS}+ days`}
                actions={
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => setView("state")}>
                    all →
                  </button>
                }
              >
                {hostsLoading ? (
                  <div className="p-3 text-xs text-text-3">Loading…</div>
                ) : stale.length === 0 ? (
                  <div className="px-[11px] py-2 text-[11.5px] text-text-3">Every host has synced in the last {STALE_DAYS} days.</div>
                ) : (
                  <>
                    {stale.slice(0, 5).map((h) => <StaleRow key={h.id} h={h} go={go} />)}
                    <div className="px-[11px] py-2 text-[11.5px] text-text-3">
                      {stale.length} host{stale.length === 1 ? "" : "s"} over {STALE_DAYS} days — neither drifted nor failed, just never applied.
                    </div>
                  </>
                )}
              </Panel>
              <Panel
                title="upcoming"
                meta={schedules ? `${schedules.filter((s) => s.enabled).length} scheduled` : undefined}
                actions={
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => setView("upcoming")}>
                    open →
                  </button>
                }
              >
                {(schedules ?? []).filter((s) => s.enabled).length === 0 ? (
                  <div className="px-[11px] py-2 text-[11.5px] text-text-3">Nothing is scheduled to touch the fleet.</div>
                ) : (
                  (schedules ?? []).filter((s) => s.enabled).slice(0, 3).map((s) => <SchedRow key={s.id} s={s} radius={radius(s)} fleet={all.length} />)
                )}
              </Panel>
            </div>
          </div>
        </div>
      )}

      {view === "pending" && (
        <div className="scroll flex-1 p-3.5">
          <Panel title={lane === "all" ? "all lanes" : lane} meta={`${pend.length} of ${laneCounts.all}`}>
            {pend.length === 0 ? (
              <Empty
                title="Nothing waiting on you"
                note="Approval gates, discovered hosts, firing alerts and drift all land here. Hidden items stay in history."
                action={
                  <button type="button" className="btn btn-sm" onClick={() => setView("summary")}>
                    Back to summary
                  </button>
                }
              />
            ) : (
              pend.map((p) => <PendRow key={p.id} p={p} go={go} onDismiss={pending.dismiss} />)
            )}
          </Panel>
        </div>
      )}

      {view === "state" && (
        <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
          <StatusBar counts={counts} onPick={(k) => go(`/hosts?status=${k}`)} />
          <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1fr) minmax(0,1fr)" : "minmax(0,1fr)", alignItems: "start" }}>
            <Panel
              title="stale hosts"
              meta={`${stale.length} over ${STALE_DAYS} days`}
              actions={
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/hosts")}>
                  hosts list →
                </button>
              }
            >
              {stale.length === 0 ? <div className="px-[11px] py-2 text-[11.5px] text-text-3">Every host has synced in the last {STALE_DAYS} days.</div> : stale.map((h) => <StaleRow key={h.id} h={h} go={go} />)}
            </Panel>
            <div className="flex min-w-0 flex-col gap-3">
              <Panel title="by group" meta="hosts · drifted">
                {[...(groups ?? [])]
                  .sort((a, b) => b.priority - a.priority)
                  .map((g) => {
                    const hs = all.filter((h) => h.group_ids.includes(g.id))
                    const dr = hs.filter((h) => h.sync_status === "out_of_sync").length
                    return (
                      <button
                        key={g.id}
                        type="button"
                        className="row-hover flex w-full items-center gap-2.5 border-0 border-b border-line-faint bg-transparent px-[11px] py-1.5 text-left"
                        onClick={() => go(`/hosts?group=${g.id}`)}
                      >
                        <span className="mono trunc flex-1 text-xs text-text">{g.name}</span>
                        <span className="mono num text-[10.5px] text-text-3">p{g.priority}</span>
                        <div className="h-[5px] w-24 shrink-0 overflow-hidden rounded-[3px] bg-surface-3">
                          <div style={{ width: `${all.length ? (hs.length / all.length) * 100 : 0}%`, height: "100%", background: dr ? "var(--warn)" : "var(--ok)" }} />
                        </div>
                        <span className="mono num w-[30px] text-right text-[11.5px] text-text-2">{hs.length}</span>
                        <span className="mono num w-6 text-right text-[11.5px]" style={{ color: dr ? "var(--warn)" : "var(--text-faint)" }}>
                          {dr || "—"}
                        </span>
                      </button>
                    )
                  })}
                {(groups ?? []).length === 0 && <div className="px-[11px] py-2 text-[11.5px] text-text-3">No groups yet.</div>}
              </Panel>
              <Panel
                title="drift trend"
                meta="14 days"
                actions={
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/drift")}>
                    findings →
                  </button>
                }
              >
                <DriftTrendBody h={56} drifted={drifted} trendData={trendData} driftOff={driftOff} hostsTotal={coverage?.hosts_total ?? 0} />
              </Panel>
            </div>
          </div>
        </div>
      )}

      {view === "activity" && (
        <div className="scroll flex-1 p-3.5">
          <Panel
            title="last 24 hours"
            meta={`${last24.length} events · failures first`}
            actions={
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/runs")}>
                full run history →
              </button>
            }
          >
            {last24.length === 0 ? <Empty title="Quiet day" note="No applies or action runs in the last 24 hours." /> : last24.map((a) => <ActRow key={a.id} a={a} go={go} />)}
          </Panel>
        </div>
      )}

      {view === "upcoming" && (
        <div className="scroll grid flex-1 gap-3 p-3.5" style={{ gridTemplateColumns: wide ? "minmax(0,1.4fr) minmax(0,1fr)" : "minmax(0,1fr)", alignItems: "start" }}>
          <Panel
            title="scheduled"
            meta="scope + blast radius"
            actions={
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/actions?tab=schedules")}>
                schedules →
              </button>
            }
          >
            {(schedules ?? []).length === 0 ? <Empty title="Nothing scheduled" note="Schedules are actions with a cron. Create one from Operations · Actions." /> : (schedules ?? []).map((s) => <SchedRow key={s.id} s={s} radius={radius(s)} fleet={all.length} />)}
            <div className="px-[11px] py-[9px] text-[11.5px] text-text-3">Scheduled runs apply unattended. Snapshot-backed ones are reversible.</div>
          </Panel>
          <IntegrationHealth />
        </div>
      )}
    </>
  )
}

function IntegrationHealth() {
  const q = { retry: false, refetchInterval: 60_000 }
  const { data: nodes } = useQuery<ProxmoxNode[]>({ queryKey: ["proxmox-nodes"], queryFn: () => apiFetch<ProxmoxNode[]>("/api/proxmox/nodes"), ...q })
  const { data: grafana } = useQuery<GrafanaInstance[]>({ queryKey: ["grafana-instances"], queryFn: () => apiFetch<GrafanaInstance[]>("/api/grafana/instances"), ...q })
  const { data: repos } = useQuery<GitRepository[]>({ queryKey: ["git-repos"], queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"), ...q })
  const { data: providers } = useQuery<AIProvider[]>({ queryKey: ["ai-providers"], queryFn: () => apiFetch<AIProvider[]>("/api/ai/providers"), ...q })
  const { data: usage } = useQuery<AIUsageSummary>({ queryKey: ["ai-usage"], queryFn: () => apiFetch<AIUsageSummary>("/api/ai/usage"), ...q })
  const { data: metrics } = useQuery<MetricsStatus>({ queryKey: ["metrics-status"], queryFn: () => apiFetch<MetricsStatus>("/api/metrics/status"), ...q })

  const enabledProviders = (providers ?? []).filter((p) => p.enabled)
  const rows: { name: string; detail: string; state: Tone; meta: string; href: string }[] = [
    {
      name: "Proxmox",
      detail: nodes?.length ? nodes.map((n) => n.name).join(", ") : "not configured",
      state: nodes?.length ? "ok" : "idle",
      meta: nodes?.length ? `${nodes.length} node${nodes.length === 1 ? "" : "s"}` : "snapshots unavailable",
      href: "/hypervisors",
    },
    {
      name: "Grafana / Mimir / Loki",
      detail: grafana?.length ? grafana.map((g) => `${g.kind}: ${g.name}`).join(" · ") : "not configured",
      state: grafana?.length ? "ok" : "idle",
      meta: grafana?.length ? "metrics + logs" : "no host metrics",
      href: "/grafana",
    },
    {
      name: "Git remotes",
      detail: repos?.length ? repos.map((r) => r.name).join(", ") : "none",
      state: repos?.length ? (repos.every((r) => r.last_sync_at) ? "ok" : "warn") : "idle",
      meta: repos?.length ? `last sync ${shortAgo(repos.map((r) => r.last_sync_at).filter(Boolean).sort().at(-1) ?? null)} ago` : "no GitOps",
      href: "/git-repos",
    },
    {
      name: "AI provider",
      detail: enabledProviders.length ? enabledProviders.map((p) => `${p.name} · ${p.model}`).join(", ") : "none enabled",
      state: enabledProviders.length ? (usage?.exceeded ? "warn" : "ok") : "idle",
      meta: usage ? `${usage.currency} ${usage.day_spend.toFixed(2)}${usage.day_limit ? ` of ${usage.day_limit.toFixed(2)}` : ""} today` : "assistant off",
      href: "/ai-providers",
    },
    {
      name: "Prometheus export",
      detail: metrics?.enabled ? metrics.path : "disabled",
      state: metrics?.enabled ? "ok" : "idle",
      meta: metrics?.enabled ? `cache ${metrics.cache_ttl_seconds}s` : "opt-in",
      href: "/settings?section=system",
    },
  ]
  return (
    <Panel title="integration health" meta="runs depend on these">
      {rows.map((i) => (
        <Link key={i.name} href={i.href} className="row-hover flex items-center gap-2.5 border-b border-line-faint px-[11px] py-2 hover:no-underline">
          <Dot tone={i.state} />
          <div className="min-w-0 flex-1">
            <div className="trunc text-xs text-text">{i.name}</div>
            <div className="mono trunc text-[10.5px] text-text-3">{i.detail}</div>
          </div>
          <span className="mono trunc max-w-[150px] text-right text-[10.5px]" style={{ color: i.state === "warn" ? "var(--warn)" : "var(--text-3)" }}>
            {i.meta}
          </span>
        </Link>
      ))}
    </Panel>
  )
}
