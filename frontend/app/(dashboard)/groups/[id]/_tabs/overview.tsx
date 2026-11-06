"use client"

import { useMemo, type ReactNode } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { countStatuses, plural, shortAgo, untilLabel, type Tone } from "@/lib/fleet"
import { MODULES, moduleByAnyName, type ModuleDef, type ModuleId } from "@/lib/modules"
import { isOrphaned, scheduleName, useSchedules } from "@/lib/overview"
import { Panel, RunStatus, Spark, Table, Tag } from "@/components/ld"
import { toneVar } from "@/components/ld/tone"
import type { ActionDefinition, ActionRun, DriftTrendSeries, GroupSummary, HostGroup, HostSummary, MemberModuleStatus, ScheduledAction, SyncRateSeries } from "@/lib/types"

/**
 * A group's Overview: member health first — headline numbers, a host ×
 * module matrix, what members actually get once the merge is done, a small
 * drift line and what is coming up. Settings, GitOps and delete are set
 * once and rarely touched, so they sit at the bottom.
 */
export function GroupOverview({
  group,
  summary,
  summaries,
  members,
  declared,
  runs,
  catalog,
  onTab,
  onModule,
  bottom,
}: {
  group: HostGroup
  summary: GroupSummary
  summaries: GroupSummary[]
  members: HostSummary[]
  declared: ModuleDef[]
  runs: ActionRun[] | undefined
  catalog: ActionDefinition[] | undefined
  onTab: (t: "members" | "activity" | "schedules" | "config") => void
  onModule: (m: ModuleId) => void
  bottom: ReactNode
}) {
  const router = useRouter()
  const id = group.id
  const { data: cells } = useQuery<MemberModuleStatus[]>({
    queryKey: ["group-module-status", id],
    queryFn: () => apiFetch<MemberModuleStatus[]>(`/api/groups/${id}/module-status`),
    refetchInterval: 60_000,
  })
  const { data: rate } = useQuery<SyncRateSeries>({
    queryKey: ["dashboard", "sync-success-rate", "group", id],
    queryFn: () => apiFetch<SyncRateSeries>(`/api/dashboard/sync-success-rate?days=7&granularity=day&group_id=${id}`),
  })
  const { data: trend } = useQuery<DriftTrendSeries>({
    queryKey: ["dashboard", "drift-trend", "group", id],
    queryFn: () => apiFetch<DriftTrendSeries>(`/api/dashboard/drift-trend?days=14&granularity=day&group_id=${id}`),
  })
  const { data: schedules } = useSchedules()

  const higher = useMemo(() => summaries.filter((g) => g.id !== id && g.priority > group.priority), [summaries, id, group.priority])
  const alsoHigher = members.filter((h) => h.group_ids.some((gid) => higher.some((g) => g.id === gid)))
  const counts = countStatuses(members)
  const total = members.length
  const byHost = useMemo(() => {
    const m = new Map<number, MemberModuleStatus[]>()
    for (const c of cells ?? []) m.set(c.host_id, [...(m.get(c.host_id) ?? []), c])
    return m
  }, [cells])
  const driftedMods = [...new Set((cells ?? []).filter((c) => c.sync_status === "out_of_sync").map((c) => moduleByAnyName(c.module_type)?.label.toLowerCase() ?? c.module_type))]
  const failedHosts = members.filter((h) => h.sync_status === "error")
  const firstError = failedHosts.length ? byHost.get(failedHosts[0].id)?.find((c) => c.error_message)?.error_message : null

  const syncs = (rate?.points ?? []).reduce((a, p) => ({ total: a.total + p.total, ok: a.ok + p.success }), { total: 0, ok: 0 })
  const ratePct = syncs.total ? Math.round((syncs.ok / syncs.total) * 100) : null

  // Schedules that reach every member: aimed at this group, or at the fleet.
  const coming = useMemo(
    () =>
      (schedules ?? [])
        .filter((s) => (s.target_kind === "group" && s.target_id === id) || s.target_kind === "fleet")
        .sort((a, b) => Number(b.enabled) - Number(a.enabled) || (a.next_run_at ?? "~").localeCompare(b.next_run_at ?? "~")),
    [schedules, id],
  )
  const next = coming.find((s) => s.enabled && s.next_run_at && !isOrphaned(s))

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }} aria-label="headline numbers">
        <Kpi
          label="members"
          value={total}
          sub={total ? (alsoHigher.length ? `${alsoHigher.length} also in a higher group` : "none in a higher group") : "no hosts yet"}
          onClick={() => onTab("members")}
        />
        <Kpi
          label="in sync"
          value={counts.in_sync}
          tone={counts.in_sync && counts.in_sync === total ? "ok" : undefined}
          sub={total ? `${Math.round((counts.in_sync / total) * 100)}%` : "—"}
          onClick={() => router.push(`/hosts?group=${id}&status=in_sync`)}
        />
        <Kpi
          label="drifted"
          value={counts.out_of_sync}
          tone={counts.out_of_sync ? "warn" : undefined}
          sub={counts.out_of_sync ? driftedMods.join(", ") || "modules not named yet" : "none"}
          onClick={() => router.push(`/hosts?group=${id}&status=out_of_sync`)}
        />
        <Kpi
          label="failed"
          value={counts.error}
          tone={counts.error ? "danger" : undefined}
          sub={failedHosts.length ? `${failedHosts[0].hostname}${firstError ? ` · ${firstError}` : ""}` : "none"}
          onClick={() => router.push(`/hosts?group=${id}&status=error`)}
        />
        <Kpi
          label="sync success · 7d"
          value={ratePct == null ? "—" : `${ratePct}%`}
          tone={ratePct != null && ratePct < 90 ? "warn" : undefined}
          sub={syncs.total ? `${syncs.ok} of ${plural(syncs.total, "sync")}` : "no syncs in 7 days"}
        >
          {(rate?.points.length ?? 0) > 1 && (
            <Spark data={rate!.points.map((p) => Math.round((p.success_rate ?? 1) * 100))} tone={ratePct != null && ratePct < 90 ? "warn" : "ok"} h={16} fill={false} />
          )}
        </Kpi>
        <Kpi
          label="next run"
          value={next ? untilLabel(next.next_run_at) : "—"}
          small
          sub={next ? `${scheduleName(next)} · ${next.target_kind === "fleet" ? "whole fleet" : plural(total, "host")}` : coming.length ? "every schedule is paused" : "nothing scheduled"}
          onClick={() => onTab("schedules")}
        />
      </div>

      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1.6fr)_minmax(300px,1fr)]">
        <HealthMatrix members={members} byHost={byHost} declared={declared} loading={!cells} onMembers={() => onTab("members")} />
        <div className="flex min-w-0 flex-col gap-3">
          <WhatMembersGet group={group} summaries={summaries} members={members} declared={declared} />
          <DriftDays trend={trend} />
        </div>
      </div>

      <div className="grid items-start gap-3 lg:grid-cols-3">
        <Panel
          title="recent activity"
          meta="this group"
          footer={
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => onTab("activity")}>
              all activity →
            </button>
          }
        >
          {(runs ?? []).slice(0, 5).map((r) => (
            <Link key={r.id} href={`/groups/${id}/actions/runs/${r.id}`} className="row-hover flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px] text-text hover:no-underline">
              <span className="mono num w-[52px] shrink-0 text-[10.5px] text-text-faint">{shortAgo(r.created_at)} ago</span>
              <RunStatus s={r.status} reason={r.pending_reason} />
              <span className="trunc flex-1 text-xs">{catalog?.find((a) => a.key === r.action_key)?.name ?? r.action_key}</span>
              <span className="tt text-[9px]">{r.scheduled_action_id ? "schedule" : r.alert_event_id ? "alert" : r.triggered_by_user_id ? "user" : "system"}</span>
            </Link>
          ))}
          {runs && runs.length === 0 && <div className="p-[11px] text-[11.5px] text-text-3">No runs against this group yet. Run action… starts one; plans show up under Operations › Runs.</div>}
        </Panel>

        <ComingUp coming={coming} members={total} onAll={() => onTab("schedules")} />

        <Panel
          title="modules"
          meta="declared state, by module"
          footer={
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => onTab("config")}>
              all modules →
            </button>
          }
        >
          <Table
            dense
            cols={[
              {
                k: "m",
                label: "module",
                w: "minmax(110px,1fr)",
                sortable: false,
                cell: (m: ModuleDef) => <span className="text-text">{m.label}</span>,
              },
              {
                k: "n",
                label: "items",
                w: "110px",
                right: true,
                sortable: false,
                cell: (m) => (
                  <span className="mono num">
                    {summary.module_counts[m.countKey]} {m.unit}
                  </span>
                ),
              },
            ]}
            rows={declared}
            keyOf={(m) => m.id}
            onRowClick={(m) => onModule(m.id)}
            empty="This group declares nothing yet — open Config and add the first item."
          />
        </Panel>
      </div>

      <div className="grid items-start gap-3 lg:grid-cols-3" data-testid="group-settings-row">
        {bottom}
      </div>
    </div>
  )
}

function Kpi({ label, value, tone, sub, small, onClick, children }: { label: string; value: ReactNode; tone?: Tone; sub?: ReactNode; small?: boolean; onClick?: () => void; children?: ReactNode }) {
  const body = (
    <>
      <span className="tt text-[8.5px]">{label}</span>
      <span className={`mono num font-semibold leading-tight ${small ? "pt-1 text-[14px]" : "text-[19px]"}`} style={{ color: tone ? toneVar(tone) : "var(--text)" }}>
        {value}
      </span>
      {children}
      {sub && (
        <span className="trunc w-full text-[10.5px] text-text-3" title={typeof sub === "string" ? sub : undefined}>
          {sub}
        </span>
      )}
    </>
  )
  const cls = "flex min-w-0 flex-col gap-[3px] rounded-r border border-line bg-surface px-[11px] py-[9px] text-left"
  return onClick ? (
    <button type="button" onClick={onClick} className={`row-hover ${cls}`}>
      {body}
    </button>
  ) : (
    <div className={cls}>{body}</div>
  )
}

const CELL_TONE: Record<string, Tone> = {
  in_sync: "ok",
  out_of_sync: "warn",
  error: "danger",
  pending: "sync",
  unknown: "idle",
}
const MATRIX_CAP = 40
const SHORT: Record<ModuleId, string> = {
  firewall: "firewall",
  services: "services",
  "hosts-file": "hosts",
  packages: "packages",
  users: "users",
  cron: "cron",
  resolver: "resolver",
  "ca-certs": "ca certs",
}

/** Members down the side, modules across the top, one cell each. An
 *  outlined cell is a module this group declares. */
function HealthMatrix({
  members,
  byHost,
  declared,
  loading,
  onMembers,
}: {
  members: HostSummary[]
  byHost: Map<number, MemberModuleStatus[]>
  declared: ModuleDef[]
  loading: boolean
  onMembers: () => void
}) {
  const router = useRouter()
  const mine = new Set(declared.map((m) => m.id))
  // Worst first, so a 40-row cap never hides the host that needs looking at.
  const rank: Record<string, number> = {
    error: 0,
    out_of_sync: 1,
    pending: 2,
    unknown: 3,
    in_sync: 4,
  }
  // A host's place is its worst module, not only its own status: one
  // never-collected module on an otherwise green host still sorts up.
  const worst = (h: HostSummary) => Math.min(rank[h.sync_status] ?? 3, ...(byHost.get(h.id) ?? []).map((c) => rank[c.sync_status] ?? 3))
  const rows = [...members].sort((a, b) => worst(a) - worst(b) || a.hostname.localeCompare(b.hostname))
  const shown = rows.slice(0, MATRIX_CAP)
  const cols = `minmax(72px,130px) repeat(${MODULES.length}, minmax(24px,1fr))`
  return (
    <Panel
      title="member health"
      meta="host × module · outlined: this group declares it"
      actions={
        <button type="button" className="btn btn-sm btn-ghost" onClick={onMembers}>
          members →
        </button>
      }
    >
      {members.length === 0 ? (
        <div className="px-[11px] py-2.5 text-[11.5px] text-text-3">No hosts in this group yet.</div>
      ) : (
        <div className="flex flex-col gap-2 p-[11px]">
          <div className="overflow-x-auto">
            <div className="grid min-w-[300px] items-center gap-[3px] text-[10.5px]" style={{ gridTemplateColumns: cols }} data-testid="health-matrix">
              <span />
              {MODULES.map((m) => (
                <span
                  key={m.id}
                  className="mono trunc text-center text-[10px]"
                  style={{
                    color: mine.has(m.id) ? "var(--text-2)" : "var(--text-faint)",
                  }}
                  title={m.label}
                >
                  {SHORT[m.id]}
                </span>
              ))}
              {shown.map((h) => (
                <MatrixRow key={h.id} host={h} cells={byHost.get(h.id) ?? []} mine={mine} loading={loading} onOpen={(m) => router.push(`/hosts/${h.id}?tab=${m.hostTab}`)} />
              ))}
            </div>
          </div>
          {rows.length > MATRIX_CAP && (
            <button type="button" className="btn btn-sm btn-ghost self-start" onClick={onMembers}>
              {rows.length - MATRIX_CAP} more members →
            </button>
          )}
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-text-3">
            {(
              [
                ["ok", "in sync"],
                ["warn", "drifted"],
                ["danger", "error"],
                ["idle", "unknown"],
              ] as [Tone, string][]
            ).map(([t, l]) => (
              <span key={l} className="inline-flex items-center gap-1.5">
                <i className="block h-[10px] w-[10px] rounded-[2px]" style={{ background: toneVar(t) }} />
                {l}
              </span>
            ))}
            <span className="inline-flex items-center gap-1.5">
              <i className="block h-[10px] w-[10px] rounded-[2px] border border-dashed border-line-strong" />
              never collected
            </span>
          </div>
        </div>
      )}
    </Panel>
  )
}

function MatrixRow({ host, cells, mine, loading, onOpen }: { host: HostSummary; cells: MemberModuleStatus[]; mine: Set<ModuleId>; loading: boolean; onOpen: (m: ModuleDef) => void }) {
  return (
    <>
      <Link href={`/hosts/${host.id}`} className="mono trunc text-[11px] text-text-2 hover:no-underline" title={host.ip_address}>
        {host.hostname}
      </Link>
      {MODULES.map((m) => {
        const c = cells.find((x) => m.statusTypes.includes(x.module_type))
        const tone = c ? (CELL_TONE[c.sync_status] ?? "idle") : null
        const what = c
          ? `${c.sync_status.replace(/_/g, " ")}${c.checked_at ? ` · checked ${shortAgo(c.checked_at)} ago` : ""}${c.error_message ? ` · ${c.error_message}` : ""}`
          : loading
            ? "loading"
            : "never collected"
        return (
          <button
            key={m.id}
            type="button"
            onClick={() => onOpen(m)}
            className="h-[14px] rounded-[2px] border-0 p-0"
            style={{
              background: tone ? toneVar(tone) : "transparent",
              border: tone ? undefined : "1px dashed var(--border)",
              boxShadow: mine.has(m.id) && tone ? "inset 0 0 0 1.5px var(--text)" : undefined,
              opacity: tone === "idle" ? 0.55 : 1,
            }}
            title={`${host.hostname} · ${m.label}: ${what}`}
            aria-label={`${host.hostname} ${m.label}: ${what}`}
          />
        )
      })}
    </>
  )
}

/**
 * For each module this group declares: how many members get it from this
 * group alone, and how many also get it from a higher-priority group or a
 * host override, which wins any conflict.
 */
function WhatMembersGet({ group, summaries, members, declared }: { group: HostGroup; summaries: GroupSummary[]; members: HostSummary[]; declared: ModuleDef[] }) {
  const byId = new Map(summaries.map((g) => [g.id, g]))
  const rows = declared.map((m) => {
    const beaten: { host: string; by: string }[] = []
    for (const h of members) {
      if (h.override_counts[m.countKey] > 0) {
        beaten.push({ host: h.hostname, by: "a host override" })
        continue
      }
      const over = h.group_ids
        .map((gid) => byId.get(gid))
        .filter((g): g is GroupSummary => !!g && g.id !== group.id && g.priority > group.priority && g.module_counts[m.countKey] > 0)
        .sort((a, b) => b.priority - a.priority)[0]
      if (over)
        beaten.push({
          host: h.hostname,
          by: `${over.name} (p ${over.priority})`,
        })
    }
    return { m, beaten, alone: members.length - beaten.length }
  })
  const notes = rows.filter((r) => r.beaten.length).slice(0, 3)
  return (
    <Panel title="what members actually get" meta="after the merge">
      {declared.length === 0 || members.length === 0 ? (
        <div className="px-[11px] py-2.5 text-[11.5px] text-text-3">{declared.length === 0 ? "This group declares nothing yet." : "No members to merge into."}</div>
      ) : (
        <div className="flex flex-col gap-[7px] p-[11px] text-[11.5px]">
          {rows.map(({ m, beaten, alone }) => (
            <div key={m.id} className="grid items-center gap-2" style={{ gridTemplateColumns: "84px minmax(0,1fr) auto" }}>
              <span className="trunc">{m.label.toLowerCase()}</span>
              <span className="flex h-[6px] overflow-hidden rounded-[3px] bg-surface-3">
                <i
                  className="block h-full"
                  style={{
                    width: `${(alone / members.length) * 100}%`,
                    background: "var(--accent)",
                  }}
                />
                <i
                  className="block h-full"
                  style={{
                    width: `${(beaten.length / members.length) * 100}%`,
                    background: "var(--hold)",
                  }}
                />
              </span>
              <span className="mono num text-[10.5px] text-text-3" title={`${alone} of ${members.length} members get ${m.label.toLowerCase()} from this group alone`}>
                {alone} / {members.length}
              </span>
            </div>
          ))}
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-text-3">
            <span className="inline-flex items-center gap-1.5">
              <i className="block h-[9px] w-[9px] rounded-[2px]" style={{ background: "var(--accent)" }} />
              only this group sets it
            </span>
            <span className="inline-flex items-center gap-1.5">
              <i className="block h-[9px] w-[9px] rounded-[2px]" style={{ background: "var(--hold)" }} />a higher group or host override also sets it
            </span>
          </div>
          {notes.map(({ m, beaten }) => {
            const by = [...new Set(beaten.map((b) => b.by))]
            const hosts = beaten.map((b) => b.host)
            return (
              <div key={m.id} className="text-[11px] text-text-3">
                {m.label.toLowerCase()}: <span className="mono text-text-2">{hosts.slice(0, 3).join(", ")}</span>
                {hosts.length > 3 ? ` +${hosts.length - 3}` : ""} {hosts.length === 1 ? "also gets" : "also get"} it from {by.slice(0, 2).join(" and ")}
                {by.length > 2 ? " and others" : ""}, which {by.length === 1 ? "wins" : "win"} conflicts.
              </div>
            )
          })}
          {notes.length === 0 && <div className="text-[11px] text-text-3">Every member gets these modules from this group alone.</div>}
        </div>
      )}
    </Panel>
  )
}

/** Drifted checks per day across the members. Kept small: drift is rare. */
function DriftDays({ trend }: { trend: DriftTrendSeries | undefined }) {
  const days = useMemo(() => {
    const byDay = new Map((trend?.points ?? []).map((p) => [p.bucket.slice(0, 10), p]))
    const since = trend ? new Date(trend.since) : null
    if (!since) return []
    const out: { day: string; drifted: number; checks: number }[] = []
    for (let i = 1; i <= 14; i++) {
      const d = new Date(since.getTime() + i * 86_400_000).toISOString().slice(0, 10)
      const p = byDay.get(d)
      out.push({
        day: d,
        drifted: p?.drifted_checks ?? 0,
        checks: p?.checks ?? 0,
      })
    }
    return out
  }, [trend])
  const checks = days.reduce((a, d) => a + d.checks, 0)
  const drifted = days.reduce((a, d) => a + d.drifted, 0)
  const max = Math.max(1, ...days.map((d) => d.drifted))
  return (
    <Panel title="drift · 14 days" meta={!checks ? "drifted checks per day" : drifted ? `${drifted} of ${plural(checks, "check")} found drift` : `${plural(checks, "check")}, none found drift`}>
      {!trend ? (
        <div className="px-[11px] py-2.5 text-[11.5px] text-text-3">Loading…</div>
      ) : checks === 0 ? (
        <div className="px-[11px] py-2.5 text-[11.5px] text-text-3">No drift checks ran on these hosts in 14 days.</div>
      ) : (
        <div className="p-[11px]">
          <div
            className="grid h-[44px] items-end gap-[4px]"
            style={{
              gridTemplateColumns: `repeat(${days.length}, minmax(0,1fr))`,
            }}
          >
            {days.map((d) => (
              <span
                key={d.day}
                className="block rounded-[1px]"
                style={{
                  height: d.drifted ? `${Math.max(12, (d.drifted / max) * 100)}%` : 2,
                  background: d.drifted ? "var(--warn)" : "var(--border-strong)",
                }}
                title={`${d.day}: ${d.drifted} of ${plural(d.checks, "check")} found drift`}
              />
            ))}
          </div>
          <div className="mono mt-1 flex justify-between text-[10px] text-text-faint">
            <span>{days[0]?.day.slice(5)}</span>
            <span>today</span>
          </div>
        </div>
      )}
    </Panel>
  )
}

/** Schedules that reach every member, the next to fire first. */
function ComingUp({ coming, members, onAll }: { coming: ScheduledAction[]; members: number; onAll: () => void }) {
  return (
    <Panel
      title="coming up"
      meta="schedules on this group"
      footer={
        <button type="button" className="btn btn-sm btn-ghost" onClick={onAll}>
          schedules →
        </button>
      }
    >
      {coming.length === 0 ? (
        <div className="p-[11px] text-[11.5px] text-text-3">Nothing is scheduled on this group. Activity › Schedules adds one.</div>
      ) : (
        coming.slice(0, 6).map((s) => {
          const orphan = isOrphaned(s)
          return (
            <div key={s.id} className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]" style={{ opacity: s.enabled ? 1 : 0.62 }}>
              <span className="mono num w-[76px] shrink-0 whitespace-nowrap text-[10.5px] text-text-3">{!s.enabled ? "paused" : orphan ? "skipped" : untilLabel(s.next_run_at)}</span>
              <span className="trunc flex-1 text-xs text-text">
                {scheduleName(s)}
                <span className="text-text-3">
                  {" "}
                  · {plural(members, "host")}
                  {s.target_kind === "fleet" ? " (whole fleet)" : ""}
                </span>
              </span>
              {s.destructive && s.snapshot_enabled && (
                <Tag tone="ok" title="Proxmox snapshot before running">
                  snapshot
                </Tag>
              )}
              {orphan && (
                <Tag tone="danger" title="No enabled pack provides this action, so the scheduler skips it">
                  action missing
                </Tag>
              )}
            </div>
          )
        })
      )}
    </Panel>
  )
}
