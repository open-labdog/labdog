"use client"

import { useQuery } from "@tanstack/react-query"
import { InvestigateButton, METRIC_WORD } from "@/components/investigate-button"
import { Meter, Panel, Spark, Status, StatusBar, type Tone } from "@/components/ld"
import { apiFetch } from "@/lib/api"
import { ageLabel, countStatuses, daysSince, plural, staleHosts, STALE_DAYS, STATUS, STATUS_ORDER } from "@/lib/fleet"
import { moduleByAnyName, MODULES } from "@/lib/modules"
import { nextCodename } from "@/lib/os-upgrade-paths"
import type { DriftCoverage, DriftTrendSeries, FleetMetrics, Host, HostGroup, ModuleStatusCount, ProxmoxNode, SyncStatus, VMMapping } from "@/lib/types"
import { Fraction, muted, StackBar } from "./parts"

/** Releases the OS-upgrade action can move a host to today. The upgrade
 *  map also names what comes after them (forky, the interim oracular);
 *  suggesting those would point at a release that is not out, or not LTS. */
const RELEASED = new Set(["bookworm", "trixie", "jammy", "noble"])

const names = (hs: Host[], n = 3) => `${hs.slice(0, n).map((h) => h.hostname).join(", ")}${hs.length > n ? ` +${hs.length - n}` : ""}`

/** Compare dotted kernel versions ("6.8.0-45", "5.10.0-28") numerically. */
function kernelCmp(a: string, b: string): number {
  const pa = a.split(/[.-]/).map((x) => parseInt(x, 10) || 0)
  const pb = b.split(/[.-]/).map((x) => parseInt(x, 10) || 0)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) - (pb[i] ?? 0)
  return 0
}

const statusParts = (c: Record<string, number>) =>
  STATUS_ORDER.map((k) => ({ n: c[k] ?? 0, tone: STATUS[k].tone, label: STATUS[k].label }))

export function StateTab({
  hosts,
  groups,
  coverage,
  wide,
  go,
}: {
  hosts: Host[]
  groups: HostGroup[]
  coverage: DriftCoverage | undefined
  wide: boolean
  go: (href: string) => void
}) {
  const q = { retry: false, refetchInterval: 60_000 }
  const { data: nodes } = useQuery<ProxmoxNode[]>({ queryKey: ["proxmox-nodes"], queryFn: () => apiFetch<ProxmoxNode[]>("/api/proxmox/nodes"), ...q })
  const { data: mappings } = useQuery<VMMapping[]>({ queryKey: ["proxmox", "vm-mappings"], queryFn: () => apiFetch<VMMapping[]>("/api/proxmox/vm-mappings"), ...q })
  const { data: modules } = useQuery<ModuleStatusCount[]>({ queryKey: ["dashboard", "module-status"], queryFn: () => apiFetch<ModuleStatusCount[]>("/api/dashboard/module-status"), ...q })
  const { data: metrics } = useQuery<FleetMetrics>({ queryKey: ["grafana", "fleet-metrics"], queryFn: () => apiFetch<FleetMetrics>("/api/grafana/fleet/metrics"), ...q })
  const { data: trend } = useQuery<DriftTrendSeries>({
    queryKey: ["dashboard", "drift-trend", 14],
    queryFn: () => apiFetch<DriftTrendSeries>("/api/dashboard/drift-trend?days=14&granularity=day"),
    ...q,
  })
  const trendData = (trend?.points ?? []).map((p) => p.drifted_checks)
  const trendChecks = (trend?.points ?? []).reduce((a, p) => a + p.checks, 0)
  const trendDrifted = trendData.reduce((a, b) => a + b, 0)

  const total = hosts.length
  const counts = countStatuses(hosts)
  const stale = staleHosts(hosts)
  const mapped = new Set((mappings ?? []).map((m) => m.host_id))
  const unmapped = hosts.filter((h) => !mapped.has(h.id))
  const unpinned = hosts.filter((h) => !h.ssh_host_key_entry)
  const checked = coverage?.any_enabled_hosts ?? hosts.filter((h) => h.drift_check_enabled).length

  // Releases, biggest first; the not-yet-collected ones last.
  const releases = new Map<string, Host[]>()
  for (const h of hosts) {
    const k = h.os_pretty_name ?? ""
    releases.set(k, [...(releases.get(k) ?? []), h])
  }
  const osRows = [...releases.entries()].sort((a, b) => (a[0] === "" ? 1 : b[0] === "" ? -1 : b[1].length - a[1].length))
  const kernels = hosts.map((h) => h.kernel_release ?? h.kernel_version).filter((k): k is string => !!k)
  const oldest = hosts.filter((h) => h.kernel_release ?? h.kernel_version).sort((a, b) => kernelCmp(a.kernel_release ?? a.kernel_version ?? "", b.kernel_release ?? b.kernel_version ?? ""))[0]
  const fw = { nftables: 0, iptables: 0, unknown: 0 } as Record<string, number>
  for (const h of hosts) fw[h.firewall_backend] = (fw[h.firewall_backend] ?? 0) + 1

  // Hosts per module and status; a module none of the hosts carries is left out.
  const byModule = new Map<string, Record<string, number>>()
  for (const r of modules ?? []) {
    const id = moduleByAnyName(r.module_type)?.id ?? r.module_type
    const row = byModule.get(id) ?? {}
    row[r.sync_status] = (row[r.sync_status] ?? 0) + r.count
    byModule.set(id, row)
  }
  const moduleRows = [...MODULES.map((m) => m.id as string), ...[...byModule.keys()].filter((k) => !MODULES.some((m) => m.id === k))].filter((k) => byModule.has(k))

  // The four fullest resources across the fleet.
  const hot = (metrics?.hosts ?? [])
    .flatMap((m) => [
      { host: m.hostname, id: m.host_id, what: "disk /", metric: "disk" as const, pct: m.disk },
      { host: m.hostname, id: m.host_id, what: "memory", metric: "memory" as const, pct: m.memory },
      { host: m.hostname, id: m.host_id, what: "cpu", metric: "cpu" as const, pct: m.cpu },
    ])
    .filter((r): r is typeof r & { pct: number } => r.pct != null)
    .sort((a, b) => b.pct - a.pct)
    .slice(0, 4)

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <StatusBar counts={counts} onPick={(k) => go(`/hosts?status=${k}`)} />

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }} aria-label="protection coverage">
        <Fraction
          label="snapshot before changes"
          n={nodes?.length ? total - unmapped.length : 0}
          total={total}
          note={!nodes?.length ? "Proxmox is not set up" : unmapped.length ? `no Proxmox VM: ${names(unmapped)}` : "every host maps to a VM"}
          href="/hypervisors"
          go={go}
        />
        <Fraction label="drift checks on" n={checked} total={total} note={checked === total ? "every host is checked" : `${plural(total - checked, "host")} with nothing checked`} href="/hosts" go={go} />
        <Fraction
          label={`verified in ${STALE_DAYS} days`}
          n={total - stale.length}
          total={total}
          note={stale.length ? stale.slice(0, 3).map((h) => `${h.hostname}: ${ageLabel(h.last_verified_at)}`).join(", ") : "every host"}
          go={go}
        />
        <Fraction label="ssh host key pinned" n={total - unpinned.length} total={total} note={unpinned.length ? `not pinned: ${names(unpinned)}` : "all pinned"} href="/hosts" go={go} />
      </div>

      <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1fr) minmax(0,1fr)" : "minmax(0,1fr)", alignItems: "start" }}>
        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="operating systems" meta="from collected facts">
            {osRows.length === 0 ? (
              <div className={muted}>No hosts yet.</div>
            ) : (
              osRows.map(([name, hs]) => {
                const codename = hs[0].os_codename
                const target = nextCodename(codename)
                const offer = target && RELEASED.has(target) ? target : null
                return (
                  <div key={name || "none"} className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]">
                    <div className="min-w-0 flex-1">
                      <div className="trunc text-xs" style={{ color: name ? "var(--text)" : "var(--text-3)" }}>
                        {name || "not collected yet"}
                      </div>
                      {offer && (
                        <div className="trunc text-[10.5px] text-text-3">
                          → {offer} available · {names(hs)}
                        </div>
                      )}
                    </div>
                    <span className="h-[5px] w-24 shrink-0 overflow-hidden rounded-[3px] bg-surface-3">
                      <i className="block h-full" style={{ width: `${(hs.length / total) * 100}%`, background: name ? "var(--accent)" : "var(--idle)" }} />
                    </span>
                    <span className="mono num w-7 text-right text-[11.5px] text-text-2">{hs.length}</span>
                  </div>
                )
              })
            )}
            <div className="flex flex-col gap-1 px-[11px] py-2 text-[11px] text-text-3">
              <span>
                <span className="tt mr-2">kernels</span>
                {new Set(kernels).size} version{new Set(kernels).size === 1 ? "" : "s"}
                {oldest && new Set(kernels).size > 1 && (
                  <>
                    {" · oldest "}
                    <span className="mono">{oldest.kernel_release ?? oldest.kernel_version}</span> on <span className="mono">{oldest.hostname}</span>
                  </>
                )}
              </span>
              <span>
                <span className="tt mr-2">firewall</span>
                nftables {fw.nftables ?? 0} · iptables {fw.iptables ?? 0}
                {fw.unknown ? ` · not detected ${fw.unknown}` : ""}
              </span>
            </div>
          </Panel>

          {metrics?.configured && (
            <Panel title="resource hot spots" meta={metrics.error ? "Mimir did not answer" : "fullest first"}>
              {metrics.error ? (
                <div className={muted}>{metrics.error}</div>
              ) : hot.length === 0 ? (
                <div className={muted}>Mimir has no series with a labdog_host_id label yet.</div>
              ) : (
                <div className="flex flex-col gap-2.5 p-[11px]">
                  {hot.map((r) => (
                    <div key={`${r.id}:${r.what}`} className="flex items-end gap-2" data-testid="hot-spot">
                      <button type="button" className="flex min-w-0 flex-1 border-0 bg-transparent p-0 text-left" onClick={() => go(`/hosts/${r.id}?tab=metrics`)}>
                        <Meter label={`${r.host} · ${r.what}`} pct={r.pct} />
                      </button>
                      <InvestigateButton
                        icon
                        ghost
                        subject={{ kind: "resource", host_id: r.id, metric: r.metric }}
                        about={`${r.host}'s ${METRIC_WORD[r.metric]}`}
                        sends={`${r.host}'s current and recent ${METRIC_WORD[r.metric]} use`}
                      />
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          )}

          <Panel
            title="stale hosts"
            meta={`${stale.length} not verified in ${STALE_DAYS} days`}
            actions={
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/hosts")}>
                hosts list →
              </button>
            }
          >
            {stale.length === 0 ? (
              <div className={muted}>Every host was verified in the last {STALE_DAYS} days.</div>
            ) : (
              stale.map((h) => {
                const d = daysSince(h.last_verified_at)
                return (
                  <button
                    key={h.id}
                    type="button"
                    className="row-hover flex w-full items-center gap-2.5 border-0 border-b border-line-faint bg-transparent px-[11px] py-[7px] text-left"
                    onClick={() => go(`/hosts/${h.id}`)}
                  >
                    <span className="mono trunc flex-1 text-xs text-text">{h.hostname}</span>
                    <Status s={h.sync_status} />
                    <span className="mono num w-11 shrink-0 text-right text-[11px]" style={{ color: d === null || d > 60 ? "var(--danger)" : "var(--warn)" }} title="last verified">
                      {ageLabel(h.last_verified_at)}
                    </span>
                  </button>
                )
              })
            )}
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="by group" meta="priority high → low">
            {[...groups]
              .sort((a, b) => b.priority - a.priority)
              .map((g) => {
                const hs = hosts.filter((h) => h.group_ids.includes(g.id))
                return (
                  <button
                    key={g.id}
                    type="button"
                    className="row-hover flex w-full items-center gap-2.5 border-0 border-b border-line-faint bg-transparent px-[11px] py-[7px] text-left"
                    onClick={() => go(`/hosts?group=${g.id}`)}
                  >
                    <span className="mono trunc w-[110px] shrink-0 text-xs text-text">{g.name}</span>
                    <span className="mono num w-8 shrink-0 text-[10.5px] text-text-3">p{g.priority}</span>
                    <StackBar parts={statusParts(countStatuses(hs))} />
                    <span className="mono num w-7 shrink-0 text-right text-[11.5px] text-text-2">{hs.length}</span>
                  </button>
                )
              })}
            {groups.length === 0 ? <div className={muted}>No groups yet.</div> : <Legend />}
          </Panel>

          <Panel
            title="drift"
            meta="drifted checks · 14 days"
            actions={
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => go("/drift")}>
                findings →
              </button>
            }
          >
            <div className="flex items-center gap-3 px-[11px] py-2">
              <span className="shrink-0 text-[11.5px] text-text-3">
                <span className="mono num font-semibold" style={{ color: counts.out_of_sync ? "var(--warn)" : "var(--text-3)" }}>
                  {counts.out_of_sync}
                </span>{" "}
                host{counts.out_of_sync === 1 ? "" : "s"} drifted now ·{" "}
                {trendChecks === 0 ? "no drift checks ran in 14 days" : `${trendDrifted} of ${trendChecks} checks found drift`}
              </span>
              {trendData.length > 1 && <Spark data={trendData} tone="warn" h={22} />}
            </div>
          </Panel>

          <Panel title="modules across the fleet" meta="hosts per status">
            {moduleRows.length === 0 ? (
              <div className={muted}>No module has been collected yet.</div>
            ) : (
              <>
                {moduleRows.map((id) => {
                  const c = byModule.get(id)!
                  const n = Object.values(c).reduce((a, b) => a + b, 0)
                  return (
                    <div key={id} className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]">
                      <span className="trunc w-[110px] shrink-0 text-xs text-text">{MODULES.find((m) => m.id === id)?.label ?? id}</span>
                      <StackBar parts={statusParts(c)} />
                      <span className="mono num w-7 shrink-0 text-right text-[11.5px] text-text-2">{n}</span>
                    </div>
                  )
                })}
                <div className="px-[11px] py-2 text-[10.5px] text-text-3">Red here is usually a collection that failed (SSH), not drift.</div>
              </>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 px-[11px] py-2 text-[10.5px] text-text-3">
      {STATUS_ORDER.map((k: SyncStatus) => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <i className="block h-[7px] w-[7px] rounded-[2px]" style={{ background: `var(--${STATUS[k].tone as Tone})` }} />
          {STATUS[k].label}
        </span>
      ))}
    </div>
  )
}
