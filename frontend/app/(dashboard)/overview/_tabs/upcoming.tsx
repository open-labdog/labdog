"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { Dot, Empty, Panel, Tag, type Tone } from "@/components/ld"
import { toneVar } from "@/components/ld/tone"
import { useNow } from "@/hooks/use-now"
import { apiFetch } from "@/lib/api"
import { can, useAuth } from "@/lib/auth"
import { cronToHuman } from "@/lib/cron"
import { plural, shortAgo, untilLabel } from "@/lib/fleet"
import { avgDuration, clock, durationLabel, failStreak, isOrphaned, outcomeTone, overlaps, reach, scheduleName, scheduleTarget, type Overlap } from "@/lib/overview"
import type {
  ActionPack,
  AIProvider,
  AIUsageSummary,
  CACertExpiry,
  GitRepository,
  GrafanaInstance,
  Host,
  HostGroup,
  MetricsStatus,
  ProxmoxNode,
  ScheduledAction,
} from "@/lib/types"
import { History, HistoryLegend, muted } from "./parts"

const HOUR = 3_600_000
const DAY = 24 * HOUR

/** Seven days from today's midnight: what ran today and what will run. */
function Timeline({ schedules, hosts, clashes, now }: { schedules: ScheduledAction[]; hosts: Host[]; clashes: Overlap[]; now: number }) {
  const start = new Date(now)
  start.setHours(0, 0, 0, 0)
  const t0 = start.getTime()
  const span = 7 * DAY
  const pos = (t: number) => `${(((t - t0) / span) * 100).toFixed(2)}%`
  const fleet = Math.max(hosts.length, 1)
  const lanes = [...schedules].sort((a, b) => Number(b.enabled) - Number(a.enabled) || (a.next_run_at ?? "~").localeCompare(b.next_run_at ?? "~"))
  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-[560px] text-[11px]" style={{ gridTemplateColumns: "170px minmax(0,1fr)" }}>
        <span />
        <div className="relative h-[18px] border-l border-line">
          {Array.from({ length: 7 }, (_, i) => (
            <span key={i} className="mono absolute top-0.5 h-full border-l border-line-faint pl-1 text-[10px] text-text-faint" style={{ left: pos(t0 + i * DAY) }}>
              {new Date(t0 + i * DAY).toLocaleDateString([], { weekday: "short", day: "numeric" })}
            </span>
          ))}
        </div>
        {lanes.map((s) => {
          const n = reach(s, hosts).length
          const ht = Math.max(5, (n / fleet) * 18)
          const big = fleet > 1 && n > fleet / 2
          const today = (s.recent_runs ?? []).filter((r) => new Date(r.created_at).getTime() >= t0)
          const mine = clashes.filter((c) => c.second.id === s.id || c.first.id === s.id)
          return (
            <div key={s.id} className="contents">
              <span className="trunc border-b border-line-faint px-2.5 py-1.5" style={{ color: s.enabled ? "var(--text-2)" : "var(--text-faint)" }} title={scheduleName(s)}>
                {scheduleName(s)}
                {!s.enabled ? " · paused" : isOrphaned(s) ? " · skipped" : ""}
              </span>
              <div className="relative h-7 border-b border-l border-line-faint border-l-line">
                {mine.flatMap((c) =>
                  c.times.map((t) => (
                    <span
                      key={`${c.first.id}:${c.second.id}:${t}`}
                      className="absolute bottom-0.5 top-0.5 w-3.5 rounded-[3px]"
                      style={{ left: pos(t), marginLeft: -7, background: "var(--warn-soft)", outline: "1px solid var(--warn)" }}
                      title={`${clock(t, true)}: shares ${c.shared.join(", ")} with ${scheduleName(c.first.id === s.id ? c.second : c.first)}`}
                    />
                  )),
                )}
                {today.map((r) => (
                  <span
                    key={r.id}
                    className="absolute bottom-[5px] w-1 rounded-[1px]"
                    style={{ left: pos(new Date(r.created_at).getTime()), marginLeft: -2, height: ht, background: toneVar(outcomeTone(r.status)), opacity: 0.7 }}
                    title={`${clock(new Date(r.created_at).getTime())} · ${r.status}`}
                  />
                ))}
                {s.enabled &&
                  !isOrphaned(s) &&
                  (s.upcoming_runs ?? []).map((t) => {
                    const at = new Date(t).getTime()
                    return (
                      <span
                        key={t}
                        className="absolute bottom-[5px] w-1 rounded-[1px]"
                        style={{ left: pos(at), marginLeft: -2, height: ht, background: big ? "var(--accent)" : "var(--text-2)" }}
                        title={`${clock(at, true)} · ${plural(n, "host")}`}
                      />
                    )
                  })}
                <span className="absolute bottom-0 top-0 border-l-[1.5px] border-ld-accent" style={{ left: pos(now) }} title="now" />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ScheduleTable({ schedules, hosts, go }: { schedules: ScheduledAction[]; hosts: Host[]; go: (href: string) => void }) {
  const fleet = hosts.length
  const rows = [...schedules].sort((a, b) => Number(b.enabled) - Number(a.enabled) || (a.next_run_at ?? "~").localeCompare(b.next_run_at ?? "~"))
  return (
    <div data-testid="schedule-table" className="overflow-x-auto">
      <div className="min-w-[620px]">
        <div className="tt grid gap-2.5 border-b border-line px-[11px] py-1.5 text-[8.5px]" style={{ gridTemplateColumns: COLS }}>
          <span>schedule</span>
          <span>when</span>
          <span className="text-right">next</span>
          <span className="text-right">hosts</span>
          <span>last 8</span>
          <span className="text-right">avg</span>
        </div>
        {rows.map((s) => {
          const radius = reach(s, hosts).length
          const failing = failStreak(s.recent_runs) > 0
          const orphan = isOrphaned(s)
          return (
            <div
              key={s.id}
              className="grid items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]"
              style={{ gridTemplateColumns: COLS, boxShadow: failing || (orphan && s.enabled) ? "inset 2px 0 0 var(--danger)" : undefined, opacity: s.enabled ? 1 : 0.7 }}
            >
              <div className="min-w-0">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="trunc text-xs font-medium text-text">{scheduleName(s)}</span>
                  {/* The flag is stored for every schedule, but only a destructive action snapshots. */}
                  {s.destructive && s.snapshot_enabled && (
                    <Tag tone="ok" title="Proxmox snapshot before running">
                      snap
                    </Tag>
                  )}
                  {!s.enabled && <Tag>paused</Tag>}
                  {orphan && (
                    <Tag tone="danger" title="No enabled pack provides this action, so the scheduler skips it">
                      action missing
                    </Tag>
                  )}
                </div>
                <div className="trunc text-[10.5px] text-text-3">{scheduleTarget(s)}</div>
              </div>
              <span className="trunc text-[11.5px] text-text-2" title={s.schedule_cron ?? undefined}>
                {s.schedule_cron ? cronToHuman(s.schedule_cron) : "manual"}
              </span>
              {orphan && s.enabled ? (
                <span className="text-right text-[11px] text-danger-ink" title="The scheduler skips a schedule whose action no pack provides">
                  skipped
                </span>
              ) : (
                <span className="mono num text-right text-[11px] text-text-2">{s.enabled && s.next_run_at ? untilLabel(s.next_run_at).replace(/^in /, "") : "—"}</span>
              )}
              <span className="text-right">
                <Tag tone={radius > fleet / 2 && fleet > 1 ? "warn" : undefined} title={`${radius} of ${fleet} hosts — unattended`}>
                  {radius}
                </Tag>
              </span>
              <button
                type="button"
                className="flex border-0 bg-transparent p-0"
                onClick={() => s.recent_runs?.[0] && go(`/actions/runs/${s.recent_runs[0].id}`)}
                title={s.recent_runs?.length ? `last run ${shortAgo(s.recent_runs[0].created_at)} ago` : "never run"}
              >
                <History runs={s.recent_runs} />
              </button>
              <span className="mono num text-right text-[11px] text-text-3">{durationLabel(avgDuration(s.recent_runs))}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const COLS = "minmax(150px,1.6fr) minmax(110px,1fr) 64px 44px 76px 52px"

export function UpcomingTab({
  schedules,
  hosts,
  groups,
  wide,
  go,
}: {
  schedules: ScheduledAction[]
  hosts: Host[]
  groups: HostGroup[]
  wide: boolean
  go: (href: string) => void
}) {
  const { data: certs } = useQuery<CACertExpiry[]>({
    queryKey: ["dashboard", "ca-cert-expiries"],
    queryFn: () => apiFetch<CACertExpiry[]>("/api/dashboard/ca-cert-expiries"),
    retry: false,
    refetchInterval: 300_000,
  })
  const now = useNow()
  const clashes = overlaps(schedules, hosts, now)
  // Where a certificate is declared: its first scope's CA-certs editor.
  const certHref = (c: CACertExpiry) => {
    const [kind, ...rest] = (c.scopes[0] ?? "").split(" ")
    const name = rest.join(" ")
    if (kind === "group") {
      const g = groups.find((x) => x.name === name)
      if (g) return `/groups/${g.id}?tab=config&module=ca-certs`
    }
    if (kind === "host") {
      const h = hosts.find((x) => x.hostname === name)
      if (h) return `/hosts/${h.id}?tab=ca-certs`
    }
    return "/groups"
  }

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <Panel
        title="next 7 days"
        meta="one lane per schedule · mark height = hosts reached"
        actions={
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/actions?tab=schedules")}>
            schedules →
          </button>
        }
      >
        {schedules.length === 0 ? <Empty title="Nothing scheduled" note="Schedules are actions with a cron. Create one from Operations · Actions." /> : <Timeline schedules={schedules} hosts={hosts} clashes={clashes} now={now} />}
        {clashes.slice(0, 4).map((c) => (
          <div key={`${c.first.id}:${c.second.id}`} className="flex items-start gap-2.5 border-t border-line-faint px-[11px] py-2 text-[11.5px] text-text-2">
            <span className="mt-1">
              <Dot tone="warn" />
            </span>
            <span>
              <span className="mono">{clock(c.at, true)}</span>
              {c.times.length > 1 ? ` and ${c.times.length - 1} more time${c.times.length === 2 ? "" : "s"} this week` : ""}: <b className="font-medium text-text">{scheduleName(c.first)}</b> and{" "}
              <b className="font-medium text-text">{scheduleName(c.second)}</b> both reach <span className="mono">{c.shared.slice(0, 3).join(", ")}</span>
              {c.shared.length > 3 ? ` and ${c.shared.length - 3} more` : ""}. {scheduleName(c.second)} waits until {scheduleName(c.first)} is done there
              {c.wait != null ? `, usually about ${durationLabel(c.wait)}` : ""}.
            </span>
          </div>
        ))}
        {clashes.length > 4 && <div className={muted}>{clashes.length - 4} more overlapping pairs this week.</div>}
      </Panel>

      <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1.6fr) minmax(0,1fr)" : "minmax(0,1fr)", alignItems: "start" }}>
        <Panel title="scheduled" meta="soonest first · scope + blast radius">
          {schedules.length === 0 ? (
            <Empty title="Nothing scheduled" note="Schedules are actions with a cron. Create one from Operations · Actions." />
          ) : (
            <>
              <ScheduleTable schedules={schedules} hosts={hosts} go={go} />
              <HistoryLegend />
              <div className="px-[11px] pb-[9px] text-[11.5px] text-text-3">Scheduled runs apply unattended. Snapshot-backed ones are reversible.</div>
            </>
          )}
        </Panel>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="other dates" meta="certificates running out">
            {!certs?.length ? (
              <div className={muted}>No CA certificate with an expiry is deployed.</div>
            ) : (
              certs.slice(0, 6).map((c) => {
                const days = Math.floor((new Date(c.not_after).getTime() - now) / DAY)
                const tone: Tone = days < 0 ? "danger" : days < 30 ? "warn" : "idle"
                return (
                  <Link key={c.fingerprint_sha256} href={certHref(c)} className="row-hover flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px] hover:no-underline">
                    <Tag tone={tone === "idle" ? undefined : tone}>cert</Tag>
                    <div className="min-w-0 flex-1">
                      <div className="trunc text-xs text-text">
                        <span className="mono">{c.name}</span> {days < 0 ? `expired ${-days} days ago` : `expires in ${days} days`}
                      </div>
                      <div className="trunc text-[10.5px] text-text-3" title={c.scopes.join(", ")}>
                        on {plural(c.host_count, "host")} via {c.scopes.slice(0, 2).join(", ")}
                        {c.scopes.length > 2 ? ` +${c.scopes.length - 2}` : ""}
                      </div>
                    </div>
                  </Link>
                )
              })
            )}
          </Panel>
          <IntegrationHealth schedules={schedules} />
        </div>
      </div>
    </div>
  )
}

function IntegrationHealth({ schedules }: { schedules: ScheduledAction[] }) {
  const { user } = useAuth()
  const q = { retry: false, refetchInterval: 60_000 }
  // AI providers and usage need ai.use, which a viewer lacks.
  const ai = { ...q, enabled: can(user, "ai.use") }
  const { data: nodes } = useQuery<ProxmoxNode[]>({ queryKey: ["proxmox-nodes"], queryFn: () => apiFetch<ProxmoxNode[]>("/api/proxmox/nodes"), ...q })
  const { data: grafana } = useQuery<GrafanaInstance[]>({ queryKey: ["grafana-instances"], queryFn: () => apiFetch<GrafanaInstance[]>("/api/grafana/instances"), ...q })
  const { data: repos } = useQuery<GitRepository[]>({ queryKey: ["git-repos"], queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"), ...q })
  const { data: packs } = useQuery<ActionPack[]>({ queryKey: ["action-packs"], queryFn: () => apiFetch<ActionPack[]>("/api/action-packs"), ...q })
  const { data: providers } = useQuery<AIProvider[]>({ queryKey: ["ai-providers"], queryFn: () => apiFetch<AIProvider[]>("/api/ai/providers"), ...ai })
  const { data: usage } = useQuery<AIUsageSummary>({ queryKey: ["ai-usage"], queryFn: () => apiFetch<AIUsageSummary>("/api/ai/usage"), ...ai })
  const { data: metrics } = useQuery<MetricsStatus>({ queryKey: ["metrics-status"], queryFn: () => apiFetch<MetricsStatus>("/api/metrics/status"), ...q })

  const enabled = schedules.filter((s) => s.enabled)
  const snapping = enabled.filter((s) => s.destructive && s.snapshot_enabled).length
  const byPack = (name: string) => enabled.filter((s) => s.pack_name === name).length
  // Built-ins carry the pseudo-pack "_builtin"; no repository serves them.
  const packSchedules = enabled.filter((s) => s.pack_name && !s.action_key.startsWith("_builtin.")).length
  const failedPacks = (packs ?? []).filter((p) => p.enabled && p.last_sync_status === "failed")
  const enabledProviders = (providers ?? []).filter((p) => p.enabled)
  const lastRepoSync = repos?.map((r) => r.last_sync_at).filter(Boolean).sort().at(-1) ?? null

  const rows: { name: string; detail: string; state: Tone; meta: string; href: string }[] = [
    {
      name: "Proxmox",
      detail: nodes?.length ? nodes.map((n) => n.name).join(", ") : "not configured",
      state: nodes?.length ? "ok" : snapping ? "warn" : "idle",
      meta: nodes?.length ? (snapping ? `snapshots for ${plural(snapping, "schedule")}` : `${plural(nodes.length, "node")}`) : snapping ? `${plural(snapping, "schedule")} expect snapshots` : "snapshots unavailable",
      href: "/hypervisors",
    },
    {
      name: "Action packs",
      detail: packs?.length ? packs.filter((p) => p.enabled).map((p) => p.name).join(", ") || "all disabled" : "none",
      state: failedPacks.length ? "warn" : packs?.some((p) => p.enabled) ? "ok" : "idle",
      meta: failedPacks.length
        ? `${failedPacks.map((p) => p.name).join(", ")} failed to sync${byPack(failedPacks[0].name) ? ` · ${plural(byPack(failedPacks[0].name), "schedule")}` : ""}`
        : packSchedules
          ? `for ${plural(packSchedules, "schedule")}`
          : "no pack schedules",
      href: "/actions?tab=packs",
    },
    {
      name: "Git remotes",
      detail: repos?.length ? repos.map((r) => r.name).join(", ") : "none",
      state: repos?.length ? (repos.every((r) => r.last_sync_at) ? "ok" : "warn") : "idle",
      meta: repos?.length ? (lastRepoSync ? `synced ${shortAgo(lastRepoSync)} ago` : "never synced") : "no GitOps",
      href: "/git-repos",
    },
    {
      name: "Grafana / Mimir / Loki",
      detail: grafana?.length ? grafana.map((g) => `${g.kind}: ${g.name}`).join(" · ") : "not configured",
      state: grafana?.length ? "ok" : "idle",
      meta: grafana?.length ? "alerts, metrics + logs" : "no host metrics",
      href: "/grafana",
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
    <Panel title="integration health" meta="what scheduled runs depend on">
      {rows.map((i) => (
        <Link key={i.name} href={i.href} className="row-hover flex items-center gap-2.5 border-b border-line-faint px-[11px] py-2 hover:no-underline">
          <Dot tone={i.state} />
          <div className="min-w-0 flex-1">
            <div className="trunc text-xs text-text">{i.name}</div>
            <div className="mono trunc text-[10.5px] text-text-3">{i.detail}</div>
          </div>
          <span className="mono trunc max-w-[170px] text-right text-[10.5px]" style={{ color: i.state === "warn" ? "var(--warn)" : "var(--text-3)" }} title={i.meta}>
            {i.meta}
          </span>
        </Link>
      ))}
    </Panel>
  )
}
