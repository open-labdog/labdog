"use client"

import { useMemo, useState, type ReactNode } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { countStatuses, plural, shortAgo, untilLabel, type Tone } from "@/lib/fleet"
import { MODULES, moduleByAnyName, type ModuleDef, type ModuleId } from "@/lib/modules"
import { isOrphaned, scheduleName, tallyLabel, useSchedules } from "@/lib/overview"
import { Panel, RunStatus, Spark, Tag } from "@/components/ld"
import { MetricsDestinationsPanel } from "@/components/metrics-destinations-panel"
import { toneVar } from "@/components/ld/tone"
import type { ActionDefinition, ActionRun, DriftTrendSeries, GroupSummary, HostGroup, HostSummary, MemberModuleStatus, ScheduledAction, SyncRateSeries } from "@/lib/types"

/**
 * A group's Overview in four full-width parts, so no row leaves a hole
 * beside a shorter panel: the headline numbers in one strip, the members
 * (only the ones with a problem, beside what they get after the merge),
 * the runs (next and earlier in one list), and the settings folded into
 * one bar.
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
  settings,
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
  /** The settings form, GitOps and delete, shown when the bar opens. */
  settings: { form: ReactNode; gitops: ReactNode; danger: ReactNode }
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
  const notCollected = total - counts.in_sync - counts.out_of_sync - counts.error

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
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5 [&>*]:shrink-0">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-r-lg border border-line bg-line sm:grid-cols-3 xl:grid-cols-6" aria-label="headline numbers" data-testid="group-numbers">
        <Num
          label="members"
          value={total}
          sub={total ? (alsoHigher.length ? `${alsoHigher.length} also in a higher group` : "none in a higher group") : "no hosts yet"}
          onClick={() => onTab("members")}
        />
        <Num
          label="in sync"
          value={counts.in_sync}
          unit={total ? `${Math.round((counts.in_sync / total) * 100)}%` : undefined}
          tone={counts.in_sync && counts.in_sync === total ? "ok" : undefined}
          sub={notCollected > 0 ? `${notCollected} not collected yet` : total ? "every member checked" : "—"}
          onClick={() => router.push(`/hosts?group=${id}&status=in_sync`)}
        />
        <Num
          label="drifted"
          value={counts.out_of_sync}
          unit={counts.out_of_sync ? driftedMods.join(", ") || undefined : undefined}
          tone={counts.out_of_sync ? "warn" : undefined}
          onClick={() => router.push(`/hosts?group=${id}&status=out_of_sync`)}
        >
          <DriftBars trend={trend} />
        </Num>
        <Num
          label="failed"
          value={counts.error}
          unit={failedHosts[0]?.hostname}
          tone={counts.error ? "danger" : undefined}
          sub={failedHosts.length ? (firstError ?? `${plural(failedHosts.length, "host")} failed`) : "none"}
          onClick={() => router.push(`/hosts?group=${id}&status=error`)}
        />
        <Num
          label="sync success · 7d"
          value={ratePct == null ? "—" : `${ratePct}%`}
          unit={syncs.total ? `${syncs.ok} of ${syncs.total}` : undefined}
          tone={ratePct != null && ratePct < 90 ? "warn" : undefined}
          sub={syncs.total ? undefined : "no syncs in 7 days"}
        >
          {(rate?.points.length ?? 0) > 1 && (
            <Spark data={rate!.points.map((p) => Math.round((p.success_rate ?? 1) * 100))} tone={ratePct != null && ratePct < 90 ? "warn" : "ok"} h={14} fill={false} />
          )}
        </Num>
        <Num
          label="next run"
          value={next ? untilLabel(next.next_run_at) : "—"}
          small
          sub={next ? `${scheduleName(next)} · ${next.target_kind === "fleet" ? "whole fleet" : plural(total, "host")}` : coming.length ? "every schedule is paused" : "nothing scheduled"}
          onClick={() => onTab("schedules")}
        />
      </div>

      <Panel
        title="members"
        meta="worst first · outlined: this group declares it"
        actions={
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => onTab("members")}>
            members →
          </button>
        }
      >
        {members.length === 0 ? (
          <div className="px-[11px] py-2.5 text-[11.5px] text-text-3">No hosts in this group yet. The Members tab adds them.</div>
        ) : (
          <div className="grid lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
            <HealthMatrix members={members} byHost={byHost} declared={declared} loading={!cells} />
            <div className="min-w-0 border-t border-line-faint lg:border-l lg:border-t-0">
              <WhatMembersGet group={group} summary={summary} summaries={summaries} members={members} declared={declared} onModule={onModule} />
            </div>
          </div>
        )}
      </Panel>

      <RunsPanel id={id} coming={coming} runs={runs} catalog={catalog} members={total} onAll={() => onTab("activity")} />

      <SettingsBar group={group} settings={settings} />
    </div>
  )
}

/** One cell of the headline strip. */
function Num({
  label,
  value,
  unit,
  tone,
  sub,
  small,
  onClick,
  children,
}: {
  label: string
  value: ReactNode
  unit?: string
  tone?: Tone
  sub?: ReactNode
  small?: boolean
  onClick?: () => void
  children?: ReactNode
}) {
  const body = (
    <>
      <span className="tt text-[8.5px]">{label}</span>
      <span className="flex min-w-0 items-baseline gap-1.5">
        <span className={`mono num shrink-0 font-semibold leading-tight ${small ? "pt-0.5 text-[14px]" : "text-[18px]"}`} style={{ color: tone ? toneVar(tone) : "var(--text)" }}>
          {value}
        </span>
        {unit && (
          <span className="trunc text-[11px] text-text-3" title={unit}>
            {unit}
          </span>
        )}
      </span>
      {children}
      {sub && (
        <span className="trunc w-full text-[10.5px] text-text-3" title={typeof sub === "string" ? sub : undefined}>
          {sub}
        </span>
      )}
    </>
  )
  const cls = "flex min-w-0 flex-col gap-[3px] bg-surface px-[11px] py-2 text-left"
  return onClick ? (
    <button type="button" onClick={onClick} className={`row-hover border-0 ${cls}`}>
      {body}
    </button>
  ) : (
    <div className={cls}>{body}</div>
  )
}

/** Drifted checks per day over 14 days, as a 14-pixel bar row: drift is
 *  rare, so it rides under the "drifted" number instead of a panel. */
function DriftBars({ trend }: { trend: DriftTrendSeries | undefined }) {
  const days = useMemo(() => {
    const byDay = new Map((trend?.points ?? []).map((p) => [p.bucket.slice(0, 10), p]))
    const since = trend ? new Date(trend.since) : null
    if (!since) return []
    const out: { day: string; drifted: number; checks: number }[] = []
    for (let i = 1; i <= 14; i++) {
      const d = new Date(since.getTime() + i * 86_400_000).toISOString().slice(0, 10)
      const p = byDay.get(d)
      out.push({ day: d, drifted: p?.drifted_checks ?? 0, checks: p?.checks ?? 0 })
    }
    return out
  }, [trend])
  const checks = days.reduce((a, d) => a + d.checks, 0)
  const drifted = days.reduce((a, d) => a + d.drifted, 0)
  if (!trend || checks === 0) return <span className="text-[10.5px] text-text-3">{trend ? "no drift checks in 14 days" : " "}</span>
  const max = Math.max(1, ...days.map((d) => d.drifted))
  return (
    <span
      className="grid h-[14px] w-full items-end gap-[2px]"
      style={{ gridTemplateColumns: `repeat(${days.length}, minmax(0,1fr))` }}
      title={drifted ? `${drifted} of ${plural(checks, "check")} found drift in 14 days` : `${plural(checks, "check")} in 14 days, none found drift`}
      aria-label={drifted ? `${drifted} of ${plural(checks, "check")} found drift in 14 days` : `none of ${plural(checks, "check")} found drift in 14 days`}
      data-testid="drift-bars"
    >
      {days.map((d) => (
        <span
          key={d.day}
          className="block rounded-[1px]"
          style={{
            height: d.drifted ? `${Math.max(25, (d.drifted / max) * 100)}%` : 1,
            background: d.drifted ? "var(--warn)" : "var(--border-strong)",
          }}
        />
      ))}
    </span>
  )
}

const CELL_TONE: Record<string, Tone> = {
  in_sync: "ok",
  out_of_sync: "warn",
  error: "danger",
  pending: "sync",
  running: "sync",
  unknown: "idle",
}
const RANK: Record<string, number> = {
  error: 0,
  out_of_sync: 1,
  pending: 2,
  running: 2,
  unknown: 3,
  in_sync: 4,
}
const SAYS: Record<string, string> = {
  in_sync: "in sync",
  out_of_sync: "drifted",
  error: "failed",
  pending: "pending",
  running: "syncing",
  unknown: "unknown",
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

/**
 * Members down the side, the host's own status and then its modules
 * across the top, one cell each, worst first. Only members with a
 * problem show until "show all" is pressed: a wall of green rows hid the
 * few that mattered. In-sync cells are toned down for the same reason.
 *
 * A module shows when some member has reported it or this group declares
 * it. CA certificates never do: their deploy is an action, and nothing
 * records a per-host status for them, so the column was always empty.
 */
function HealthMatrix({ members, byHost, declared, loading }: { members: HostSummary[]; byHost: Map<number, MemberModuleStatus[]>; declared: ModuleDef[]; loading: boolean }) {
  const router = useRouter()
  const [all, setAll] = useState(false)
  const mine = new Set(declared.map((m) => m.id))
  const seen = new Set([...byHost.values()].flat().map((c) => moduleByAnyName(c.module_type)?.id))
  const mods = MODULES.filter((m) => m.syncModule !== null && (seen.has(m.id) || mine.has(m.id)))
  // A host's place is its worst module, not only its own status: one
  // module that could not be read on an otherwise green host still sorts up.
  const worst = (h: HostSummary) => Math.min(RANK[h.sync_status] ?? 3, ...(byHost.get(h.id) ?? []).map((c) => RANK[c.sync_status] ?? 3))
  const rows = [...members].sort((a, b) => worst(a) - worst(b) || a.hostname.localeCompare(b.hostname))
  const problems = rows.filter((h) => worst(h) < RANK.in_sync)
  const fine = rows.length - problems.length
  const shown = (all ? rows : problems).slice(0, MATRIX_CAP)
  const cols = `minmax(72px,130px) minmax(24px,40px) repeat(${mods.length}, minmax(24px,1fr))`
  return (
    <div className="flex min-w-0 flex-col gap-2 p-[11px]">
      <div className="overflow-x-auto">
        <div className="grid min-w-[300px] items-center gap-[3px] text-[10.5px]" style={{ gridTemplateColumns: cols }} data-testid="health-matrix">
          <span />
          <span className="tt text-center text-[8.5px]" title="The host's own status, rolled up from its modules or set on the host as a whole">
            host
          </span>
          {mods.map((m) => (
            <span key={m.id} className="mono trunc text-center text-[10px]" style={{ color: mine.has(m.id) ? "var(--text-2)" : "var(--text-faint)" }} title={m.label}>
              {SHORT[m.id]}
            </span>
          ))}
          {shown.map((h) => (
            <MatrixRow key={h.id} host={h} mods={mods} cells={byHost.get(h.id) ?? []} mine={mine} loading={loading} onOpen={(m) => router.push(`/hosts/${h.id}?tab=${m.hostTab}`)} />
          ))}
        </div>
      </div>
      {(fine > 0 || all) && (
        <div className="flex flex-wrap items-center gap-2 border-t border-dashed border-line pt-1.5 text-[11px] text-text-3">
          {!all && (
            <>
              <i className="block h-[7px] w-[7px] rounded-full" style={{ background: "var(--ok)" }} />
              {problems.length === 0 ? `All ${plural(fine, "member")} in sync on every module` : `${fine} more ${fine === 1 ? "member" : "members"} in sync on every module`}
            </>
          )}
          <button type="button" className="btn btn-sm btn-ghost ml-auto" aria-expanded={all} onClick={() => setAll(!all)}>
            {all ? "only problems ▴" : `show all ${rows.length} ▾`}
          </button>
        </div>
      )}
      {all && rows.length > MATRIX_CAP && <span className="text-[11px] text-text-3">The first {MATRIX_CAP} show here; the Members tab lists every one.</span>}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-text-3">
        {(
          [
            ["ok", "in sync", 0.45],
            ["warn", "drifted", 1],
            ["danger", "error", 1],
            ["idle", "unknown", 0.55],
          ] as [Tone, string, number][]
        ).map(([t, l, o]) => (
          <span key={l} className="inline-flex items-center gap-1.5">
            <i className="block h-[10px] w-[10px] rounded-[2px]" style={{ background: toneVar(t), opacity: o }} />
            {l}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <i className="block h-[10px] w-[10px] rounded-[2px] border border-dashed border-line-strong" />
          never collected
        </span>
        {!loading && shown.some((h) => hostOnly(h, byHost.get(h.id) ?? [])) && (
          <span className="inline-flex items-center gap-1.5">
            <i className="block h-[10px] w-[10px] rounded-[2px]" style={{ background: "var(--warn)", backgroundImage: "repeating-linear-gradient(135deg, transparent 0 3px, rgba(0,0,0,.28) 3px 5px)" }} />
            marked on the host, no module says so
          </span>
        )}
      </div>
    </div>
  )
}

/** The host is marked drifted or failed as a whole, and no module says
 *  so: a snapshot rollback does that, for one. Without a mark the row
 *  looks green for no reason. */
function hostOnly(host: HostSummary, cells: MemberModuleStatus[]) {
  const cellWorst = Math.min(RANK.in_sync, ...cells.map((c) => RANK[c.sync_status] ?? RANK.unknown))
  return (RANK[host.sync_status] ?? RANK.unknown) < Math.min(cellWorst, RANK.unknown)
}

function MatrixRow({
  host,
  mods,
  cells,
  mine,
  loading,
  onOpen,
}: {
  host: HostSummary
  mods: ModuleDef[]
  cells: MemberModuleStatus[]
  mine: Set<ModuleId>
  loading: boolean
  onOpen: (m: ModuleDef) => void
}) {
  const hostTone = CELL_TONE[host.sync_status] ?? "idle"
  const alone = !loading && hostOnly(host, cells)
  const hostSays = `${host.hostname}: ${SAYS[host.sync_status] ?? host.sync_status}${alone ? " on the host as a whole; no module check says so. A rollback or a whole-host check sets this, and the next check of each module settles it" : ""}`
  return (
    <>
      <Link href={`/hosts/${host.id}`} className="mono trunc text-[11px] text-text-2 hover:no-underline" title={host.ip_address}>
        {host.hostname}
      </Link>
      <Link
        href={`/hosts/${host.id}`}
        className="block h-[14px] rounded-[2px]"
        style={{
          background: toneVar(hostTone),
          opacity: hostTone === "ok" ? 0.45 : hostTone === "idle" ? 0.55 : 1,
          backgroundImage: alone ? "repeating-linear-gradient(135deg, transparent 0 3px, rgba(0,0,0,.28) 3px 5px)" : undefined,
        }}
        title={hostSays}
        aria-label={hostSays}
      />
      {mods.map((m) => {
        const c = cells.find((x) => m.statusTypes.includes(x.module_type))
        const tone = c ? (CELL_TONE[c.sync_status] ?? "idle") : null
        const what = c
          ? `${SAYS[c.sync_status] ?? c.sync_status}${c.checked_at ? ` · checked ${shortAgo(c.checked_at)} ago` : ""}${c.error_message ? ` · ${c.error_message}` : ""}`
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
              boxShadow: mine.has(m.id) && tone ? "inset 0 0 0 1px var(--text-3)" : undefined,
              opacity: tone === "ok" ? 0.45 : tone === "idle" ? 0.55 : 1,
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
 * Each module this group declares, with its item count and how many
 * members get it from this group alone versus from a higher-priority
 * group or a host override, which wins any conflict there. A row opens
 * the module in the Config tab.
 */
function WhatMembersGet({
  group,
  summary,
  summaries,
  members,
  declared,
  onModule,
}: {
  group: HostGroup
  summary: GroupSummary
  summaries: GroupSummary[]
  members: HostSummary[]
  declared: ModuleDef[]
  onModule: (m: ModuleId) => void
}) {
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
      if (over) beaten.push({ host: h.hostname, by: over.name })
    }
    return { m, beaten, alone: members.length - beaten.length }
  })
  return (
    <div className="flex min-w-0 flex-col" data-testid="what-members-get">
      <div className="flex items-baseline gap-2 px-[11px] pt-[9px]">
        <span className="tt text-[8.5px]">what they actually get</span>
        <span className="text-[10.5px] text-text-faint">after the merge</span>
      </div>
      {declared.length === 0 ? (
        <div className="px-[11px] py-2.5 text-[11.5px] text-text-3">This group declares nothing yet. Config adds the first item.</div>
      ) : (
        rows.map(({ m, beaten, alone }) => {
          const by = [...new Set(beaten.map((b) => b.by))]
          const why = beaten.length
            ? `${by.slice(0, 3).join(", ")}${by.length > 3 ? " and others" : ""} also ${by.length === 1 && by[0] !== "a host override" ? "sets" : "set"} it on ${plural(beaten.length, "member")} and ${by.length === 1 ? "wins" : "win"}`
            : "every member gets it from this group alone"
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => onModule(m.id)}
              className="row-hover grid items-center gap-x-2 gap-y-0.5 border-0 border-b border-line-faint bg-transparent px-[11px] py-1.5 text-left text-[11.5px] text-text"
              style={{ gridTemplateColumns: "minmax(80px,110px) minmax(60px,80px) minmax(0,1fr) auto" }}
              title={`Open ${m.label} in Config`}
            >
              <span className="trunc">{m.label}</span>
              <span className="mono num text-[10.5px] text-text-3">
                {summary.module_counts[m.countKey]} {m.unit}
              </span>
              <span className="flex h-[6px] overflow-hidden rounded-[3px] bg-surface-3">
                <i className="block h-full" style={{ width: `${(alone / members.length) * 100}%`, background: "var(--accent)" }} />
                <i className="block h-full" style={{ width: `${(beaten.length / members.length) * 100}%`, background: "var(--hold)" }} />
              </span>
              <span className="mono num text-[10.5px] text-text-3" title={`${alone} of ${members.length} members get ${m.label.toLowerCase()} from this group alone`}>
                {alone}/{members.length}
              </span>
              <span className="trunc col-start-3 col-end-5 text-[10.5px] text-text-faint" title={beaten.length ? beaten.map((b) => b.host).join(", ") : undefined}>
                {why}
              </span>
            </button>
          )
        })
      )}
      <div className="flex flex-wrap gap-x-3 gap-y-1 px-[11px] py-2 text-[10.5px] text-text-3">
        <span className="inline-flex items-center gap-1.5">
          <i className="block h-[9px] w-[9px] rounded-[2px]" style={{ background: "var(--accent)" }} />
          only this group sets it
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="block h-[9px] w-[9px] rounded-[2px]" style={{ background: "var(--hold)" }} />a higher group or host override wins
        </span>
      </div>
    </div>
  )
}

const RUN_ROW = "grid items-center gap-2.5 border-b border-line-faint px-[11px] py-[5px] text-[11.5px]"
const RUN_COLS = "64px 96px minmax(0,1fr) minmax(90px,150px) 64px"
const SEP = "tt border-b border-line-faint bg-surface-2 px-[11px] py-[3px] text-[8.5px]"

/** What runs next on the group and what ran on it lately, in one list. */
function RunsPanel({
  id,
  coming,
  runs,
  catalog,
  members,
  onAll,
}: {
  id: number
  coming: ScheduledAction[]
  runs: ActionRun[] | undefined
  catalog: ActionDefinition[] | undefined
  members: number
  onAll: () => void
}) {
  const upcoming = coming.slice(0, 3)
  const recent = (runs ?? []).slice(0, 5)
  return (
    <Panel
      title="runs"
      meta="next and earlier, on this group"
      actions={
        <button type="button" className="btn btn-sm btn-ghost" onClick={onAll}>
          all activity →
        </button>
      }
    >
      <div className="overflow-x-auto" data-testid="group-runs">
        <div className="min-w-[520px]">
          <div className={SEP}>next</div>
          {upcoming.length === 0 && <div className="border-b border-line-faint px-[11px] py-[7px] text-[11.5px] text-text-3">Nothing is scheduled on this group. Activity › Schedules adds one.</div>}
          {upcoming.map((s) => {
            const orphan = isOrphaned(s)
            return (
              <div key={s.id} className={RUN_ROW} style={{ gridTemplateColumns: RUN_COLS, opacity: s.enabled ? 1 : 0.62 }}>
                <span className="mono num whitespace-nowrap text-[10.5px] text-text-faint">{!s.enabled ? "paused" : orphan ? "skipped" : untilLabel(s.next_run_at)}</span>
                <span className="inline-flex items-center gap-1.5 text-[11px] text-text-3">
                  <i className="block h-[7px] w-[7px] rounded-full border border-text-3" />
                  {s.enabled ? "scheduled" : "paused"}
                </span>
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="trunc">{scheduleName(s)}</span>
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
                </span>
                <span className="mono trunc text-[10.5px] text-text-3">{s.target_kind === "fleet" ? "whole fleet" : plural(members, "host")}</span>
                <span className="tt text-right text-[8.5px]">schedule</span>
              </div>
            )
          })}
          <div className={SEP}>earlier</div>
          {runs && recent.length === 0 && <div className="px-[11px] py-[7px] text-[11.5px] text-text-3">No runs against this group yet. Run action… starts one.</div>}
          {recent.map((r) => (
            <Link key={r.id} href={`/groups/${id}/actions/runs/${r.id}`} className={`row-hover text-text hover:no-underline ${RUN_ROW}`} style={{ gridTemplateColumns: RUN_COLS }}>
              <span className="mono num whitespace-nowrap text-[10.5px] text-text-faint">{shortAgo(r.created_at)} ago</span>
              <RunStatus s={r.status} reason={r.pending_reason} />
              <span className="trunc">{catalog?.find((a) => a.key === r.action_key)?.name ?? r.action_key}</span>
              <span className="mono trunc text-[10.5px] text-text-3">{tallyLabel(r.host_counts)}</span>
              <span className="tt text-right text-[8.5px]">{r.scheduled_action_id ? "schedule" : r.alert_event_id ? "alert" : r.triggered_by_user_id ? "user" : "system"}</span>
            </Link>
          ))}
        </div>
      </div>
    </Panel>
  )
}

/**
 * Name, category, priority and GitOps on one line; "edit" opens the
 * settings form, GitOps, metrics destinations and delete. They are set
 * once and rarely touched, so they stay folded.
 */
function SettingsBar({ group, settings }: { group: HostGroup; settings: { form: ReactNode; gitops: ReactNode; danger: ReactNode } }) {
  const [open, setOpen] = useState(false)
  const kv = (k: string, v: ReactNode, mono = false) => (
    <span className="inline-flex min-w-0 items-baseline gap-1.5">
      <span className="text-[10.5px] text-text-faint">{k}</span>
      <span className={`trunc text-text ${mono ? "mono" : ""}`}>{v}</span>
    </span>
  )
  return (
    <div className="flex flex-col gap-3" data-testid="group-settings-row">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-r-lg border border-line bg-surface px-[11px] py-2 text-[11.5px]">
        <span className="tt text-[9.5px] text-text-2">settings</span>
        {kv("name", group.name, true)}
        {kv("category", group.category ?? "—")}
        {kv("priority", group.priority, true)}
        {kv("gitops", group.gitops_enabled ? "on" : "off")}
        <button type="button" className="btn btn-sm" aria-expanded={open} aria-label={open ? "close settings" : "edit settings"} onClick={() => setOpen(!open)}>
          {open ? "close ▴" : "edit ▾"}
        </button>
      </div>
      {open && (
        <div className="grid items-start gap-3 lg:grid-cols-2">
          {settings.form}
          <div className="flex min-w-0 flex-col gap-3">
            {settings.gitops}
            <MetricsDestinationsPanel scope="group" id={group.id} />
            {settings.danger}
          </div>
        </div>
      )}
    </div>
  )
}
