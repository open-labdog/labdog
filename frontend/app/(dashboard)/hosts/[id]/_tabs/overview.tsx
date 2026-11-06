"use client"

import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import Link from "next/link"
import { apiFetch, ApiError } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural, shortAgo } from "@/lib/fleet"
import { moduleByAnyName } from "@/lib/modules"
import { useSchedules } from "@/lib/overview"
import { hostSchedules, useHostAlerts, useHostFeed, useHostRuns } from "@/lib/host-overview"
import { Dot, Facts, Panel, Status, Tag } from "@/components/ld"
import { HostMetricsSection } from "@/components/host-metrics-section"
import { InvestigateButton } from "@/components/investigate-button"
import { FirewallBackendTag } from "./config/shared"
import { FailedRunBanner, ScheduledRunsPanel } from "./overview-runs"
import { ConfigLayersPanel, HostActivityPanel, HostAlertsPanel } from "./overview-side"
import type { GroupSummary, Host, HostAlerts, HostGroup, HostSummary, ModuleCurrentState, VMMapping } from "@/lib/types"

/**
 * What alerts may do to this host — the actions on the list whose scope
 * covers it, and which alerts may change it without asking (the host's
 * side of the full-auto label or rules), plus what would hold every
 * alert's full auto back here right now. Hidden when alerts can do
 * neither.
 */
function AlertsSection({ hostId }: { hostId: number }) {
  const { data } = useQuery<HostAlerts>({
    queryKey: ["host-alerts", hostId],
    queryFn: () => apiFetch<HostAlerts>(`/api/alerts/hosts/${hostId}`),
    retry: false,
  })
  if (!data || (!data.ai_enabled && data.alert_actions.length === 0)) return null
  const any = data.ai_enabled && data.rules.length > 0
  return (
    <Panel
      title="automatic fixes"
      meta={
        [
          data.alert_actions.length > 0 ? plural(data.alert_actions.length, "alert action") : null,
          data.ai_enabled ? (any ? (data.blockers.length ? "full auto held back" : `full auto: ${plural(data.rules.length, "rule")}`) : "no full auto") : null,
        ]
          .filter(Boolean)
          .join(" · ")
      }
      actions={
        <>
          <Link href="/alerts?tab=handling" className="btn btn-sm btn-ghost hover:no-underline">alert handling</Link>
          {data.ai_enabled && <Link href="/settings?section=ai" className="btn btn-sm btn-ghost hover:no-underline">rules</Link>}
        </>
      }
      pad={11}
    >
      <div className="flex flex-col gap-2 text-[11.5px]" data-testid="host-full-auto">
        {data.alert_actions.length > 0 && (
          <div className="flex flex-col gap-1" data-testid="host-alert-actions">
            <span className="text-text-3">
              Alerts labelled <span className="mono">labdog_action</span> may run these actions here:
            </span>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {data.alert_actions.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-1.5">
                  <span className="mono text-text">{a.action_key}</span>
                  {a.action_name && <span className="text-text-3">{a.action_name}</span>}
                  <span className="text-text-faint">· {a.scope}</span>
                  {!a.enabled && <Tag>paused</Tag>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {!data.ai_enabled ? null : !any ? (
          <span className="text-text-3">
            No alert may change this host without asking{data.rules_enabled ? ": no full-auto rule covers it." : "."}
          </span>
        ) : (
          <>
            <span className="text-text-3">
              {data.rules_enabled ? "Alerts matching these rules may change this host without asking:" : "Alerts Grafana labels like this may change this host without asking:"}
            </span>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {data.rules.map((r) => (
                <li key={r} className="mono">{r}</li>
              ))}
            </ul>
          </>
        )}
        {any && data.blockers.length > 0 && (
          <div className="flex flex-col gap-1" style={{ color: "var(--warn)" }} data-testid="host-alert-blockers">
            <span>None can right now:</span>
            <ul className="m-0 flex list-disc flex-col gap-0.5 pl-4">
              {data.blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  )
}

async function fetchMapping(hostId: number): Promise<VMMapping | null> {
  try {
    return await apiFetch<VMMapping>(`/api/proxmox/hosts/${hostId}/vm-mapping`)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null
    throw e
  }
}

/** Collapsed at the foot of the tab: the mapping itself is a fact line in
 *  the host card, and this is where it is discovered or inspected. */
function ProxmoxVMSection({ hostId }: { hostId: number }) {
  const [open, setExpanded] = useState(false)
  const queryClient = useQueryClient()

  const { data: mapping, isLoading, error } = useQuery<VMMapping | null>({
    queryKey: ["host-vm-mapping", hostId],
    queryFn: () => fetchMapping(hostId),
    retry: false,
  })

  const discoverMutation = useMutation({
    mutationFn: async () => {
      try {
        return await apiFetch<VMMapping>(`/api/proxmox/hosts/${hostId}/discover`, { method: "POST" })
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null
        throw e
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["host-vm-mapping", hostId] }),
  })

  return (
    <Panel
      title="vm mapping"
      meta={!open && mapping ? `${mapping.vm_name} (vmid ${mapping.vmid})` : !open && !isLoading && !mapping ? "no mapping" : undefined}
      actions={
        <>
          <button type="button" className="btn btn-sm btn-ghost" disabled={discoverMutation.isPending || isLoading} onClick={() => discoverMutation.mutate()}>
            {discoverMutation.isPending ? "scanning…" : "discover"}
          </button>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setExpanded(!open)}>{open ? "collapse" : "expand"}</button>
        </>
      }
      pad={open ? 11 : 0}
    >
      {open && (
        <>
          {isLoading && <span className="text-[11.5px] text-text-3">Loading…</span>}
          {!isLoading && error && <span className="text-[11.5px] text-danger">Failed to load VM mapping</span>}
          {discoverMutation.error && <div className="mb-2 text-[11px] text-warn">{(discoverMutation.error as Error).message}</div>}
          {!isLoading && !error && mapping === null && !discoverMutation.isPending && (
            <span className="text-[11.5px] text-text-3">No VM mapping found. Discover scans Proxmox nodes for this host.</span>
          )}
          {!isLoading && !error && mapping && (
            <Facts
              items={[
                { k: "vm name", v: mapping.vm_name, mono: true },
                { k: "vmid", v: mapping.vmid, mono: true },
                { k: "proxmox node", v: mapping.pve_node_name, mono: true },
                { k: "discovered", v: new Date(mapping.discovered_at).toLocaleString() },
              ]}
            />
          )}
        </>
      )}
    </Panel>
  )
}

/** Module health as one line: "6 modules in sync" nearly every day; on the
 *  rare day a check finds drift it turns amber and names the module. */
function ConfigLine({ host, modules, onConfig }: { host: Host; modules: ModuleCurrentState[] | undefined; onConfig: () => void }) {
  const rows = modules ?? []
  const checked = rows.map((m) => m.collected_at).filter((t): t is string => !!t).sort().at(-1) ?? host.last_drift_check_at
  const ago = checked ? <span className="text-text-faint">· checked {shortAgo(checked)} ago</span> : null
  const label = (t: string) => moduleByAnyName(t)?.label.toLowerCase() ?? t
  const drifted = rows.filter((m) => m.sync_status === "out_of_sync")
  const errors = rows.filter((m) => m.sync_status === "error" || m.error_message)
  const link = "text-[11px] text-ld-accent hover:no-underline"
  if (host.sync_status === "pending") {
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5" style={{ color: "var(--sync)" }}>
        <Dot tone="sync" pulse /> a sync is running
      </span>
    )
  }
  if (drifted.length) {
    const sync = [...new Set(drifted.map((m) => moduleByAnyName(m.module_type)?.syncModule).filter((m): m is string => !!m))]
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <span className="inline-flex items-center gap-1.5 text-warn">
          <Dot tone="warn" /> {drifted.map((m) => label(m.module_type)).join(", ")} drifted
        </span>
        {ago}
        <Link href={`/plans?hosts=${host.id}${sync.length ? `&modules=${sync.join(",")}` : ""}`} className={link}>
          plan sync →
        </Link>
        <InvestigateButton icon ghost label="Explain drift with AI" subject={{ kind: "drift", host_id: host.id }} about={`the drift on ${host.hostname}`} sends={`what LabDog's last checks found on ${host.hostname} and what a sync would change`} />
      </span>
    )
  }
  if (errors.length) {
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <span className="inline-flex items-center gap-1.5 text-danger-ink" title={errors[0].error_message ?? undefined}>
          <Dot tone="danger" /> {plural(errors.length, "module")} could not be checked
        </span>
        {ago}
        <button type="button" className={`border-0 bg-transparent p-0 ${link}`} onClick={onConfig}>
          config →
        </button>
        <InvestigateButton icon ghost label="Ask AI why modules could not be checked" subject={{ kind: "drift", host_id: host.id }} about={`why ${host.hostname}'s modules could not be checked`} sends={`the errors from LabDog's last checks on ${host.hostname}`} />
      </span>
    )
  }
  const ok = rows.filter((m) => m.sync_status === "in_sync").length
  if (!ok) return <span className="text-text-3">not checked yet · collect all reads every module</span>
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1.5 text-ok">
        <Dot tone="ok" /> {plural(ok, "module")} in sync
      </span>
      {ago}
      <button type="button" className={`border-0 bg-transparent p-0 ${link}`} onClick={onConfig}>
        config →
      </button>
    </span>
  )
}

export type HostTabTarget = "config" | "metrics" | "actions" | "schedules"

/**
 * A host's Overview: how its scheduled work is going first (a banner when
 * its own result in a schedule's newest run failed, then every schedule
 * that reaches it), then the host card, where drift is one line, and what
 * happened on it lately. The VM mapping and automatic fixes sit at the foot.
 */
export function OverviewTab({ hostId, host, groups, goTab }: { hostId: number; host: Host | undefined; groups: HostGroup[] | undefined; goTab: (t: HostTabTarget) => void }) {
  const queryClient = useQueryClient()
  const [allFacts, setAllFacts] = useState(false)
  const { data: currentState } = useQuery<ModuleCurrentState[]>({
    queryKey: ["host-current-state", hostId],
    queryFn: () => apiFetch<ModuleCurrentState[]>(`/api/hosts/${hostId}/current-state`),
  })
  const { data: schedules } = useSchedules()
  const runs = useHostRuns(hostId)
  const { data: summaries } = useQuery<GroupSummary[]>({ queryKey: ["groups-summary"], queryFn: () => apiFetch<GroupSummary[]>("/api/groups/summary") })
  const { data: hostSummaries } = useQuery<HostSummary[]>({ queryKey: ["hosts-summary"], queryFn: () => apiFetch<HostSummary[]>("/api/hosts/summary") })
  const { data: mapping } = useQuery<VMMapping | null>({ queryKey: ["host-vm-mapping", hostId], queryFn: () => fetchMapping(hostId), retry: false })
  const feed = useHostFeed(hostId, host?.hostname ?? "", runs.data, schedules)
  const alerts = useHostAlerts(hostId)

  const membershipMutation = useApiMutation({
    mutationFn: (data: { group_ids: number[] }) => apiFetch(`/api/hosts/${hostId}`, { method: "PUT", body: JSON.stringify(data) }),
    invalidateKeys: [
      ["host", hostId], ["hosts-summary"], ["host-effective-rules", hostId], ["host-effective-services", hostId],
      ["host-effective-hosts-entries", hostId], ["host-effective-linux-users", hostId], ["host-effective-linux-groups", hostId],
      ["host-effective-cron-jobs", hostId], ["host-effective-packages", hostId], ["host-effective-repos", hostId], ["host-effective-resolver", hostId],
    ],
  })

  const memberIds = useMemo(() => host?.group_ids ?? [], [host?.group_ids])
  const members = useMemo(() => (groups ?? []).filter((g) => memberIds.includes(g.id)), [groups, memberIds])
  const avail = useMemo(() => (groups ?? []).filter((g) => !memberIds.includes(g.id)), [groups, memberIds])
  const reaching = useMemo(() => (host ? hostSchedules(schedules ?? [], host, groups ?? [], runs.data ?? []) : []), [host, schedules, groups, runs.data])
  const failing = reaching.filter((h) => h.streak > 0 && h.last)

  async function toggleDrift() {
    if (!host) return
    await apiFetch(`/api/hosts/${hostId}`, { method: "PUT", body: JSON.stringify({ drift_check_enabled: !host.drift_check_enabled }) })
    await queryClient.invalidateQueries({ queryKey: ["host", hostId] })
  }

  if (!host) return null

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <FailedRunBanner failing={failing} hostId={hostId} hostname={host.hostname} />
      <ScheduledRunsPanel schedules={reaching} hostId={hostId} hostname={host.hostname} loading={runs.isLoading} onAll={() => goTab("schedules")} />

      <div className="grid items-start gap-3 lg:grid-cols-[minmax(320px,0.9fr)_minmax(0,1.1fr)]">
        <Panel
          title="host"
          meta={[host.os_pretty_name ?? "os not collected", host.kernel_version].filter(Boolean).join(" · ")}
          actions={<button type="button" className="btn btn-sm btn-ghost" onClick={() => goTab("metrics")}>metrics →</button>}
          pad={11}
        >
          <div className="flex flex-col gap-3">
            <HostMetricsSection hostId={hostId} />
            <Facts
              min={150}
              items={[
                { k: "address", v: `${host.ip_address}:${host.ssh_port}`, mono: true },
                { k: "firewall", v: <FirewallBackendTag backend={host.firewall_backend} /> },
                { k: "proxmox", v: mapping ? `${mapping.pve_node_name} / vmid ${mapping.vmid}` : "not mapped", mono: !!mapping },
                { k: "last verified", v: host.last_verified_at ? <span title={new Date(host.last_verified_at).toLocaleString()}>{shortAgo(host.last_verified_at)} ago</span> : "never" },
              ]}
            />
            <div className="flex flex-col gap-0.5 text-[11.5px]" data-testid="host-config-line">
              <span className="tt text-[8.5px]">config</span>
              <ConfigLine host={host} modules={currentState} onConfig={() => goTab("config")} />
            </div>
            <button type="button" className="btn btn-sm btn-ghost self-start" aria-expanded={allFacts} onClick={() => setAllFacts(!allFacts)}>
              {allFacts ? "fewer facts ▴" : "all facts ▾"}
            </button>
            {allFacts && (
              <Facts
                items={[
                  { k: "hostname", v: host.hostname, mono: true },
                  { k: "management ip", v: host.labdog_source_ip ?? "not yet detected", mono: !!host.labdog_source_ip },
                  { k: "sync status", v: <Status s={host.sync_status} /> },
                  { k: "last sync", v: host.last_sync_at ? new Date(host.last_sync_at).toLocaleString() : "never" },
                  { k: "last drift check", v: host.last_drift_check_at ? new Date(host.last_drift_check_at).toLocaleString() : "never" },
                  ...(host.kernel_version ? [{ k: "kernel", v: host.kernel_version, mono: true }] : []),
                  ...(host.default_nic ? [{ k: "default nic", v: host.default_nic, mono: true }] : []),
                  { k: "drift monitoring", v: <button type="button" className="cursor-pointer border-0 bg-transparent p-0" onClick={toggleDrift}><Tag tone={host.drift_check_enabled ? "ok" : undefined}>{host.drift_check_enabled ? "enabled" : "disabled"}</Tag></button> },
                ]}
              />
            )}
          </div>
        </Panel>

        <div className="flex min-w-0 flex-col gap-3">
          <HostActivityPanel items={feed.items} loading={feed.isLoading || runs.isLoading} onAll={() => goTab("actions")} />
          <HostAlertsPanel alerts={alerts.data} />
          <ConfigLayersPanel
            overrides={hostSummaries?.find((h) => h.id === hostId)?.override_counts}
            members={members}
            summaries={summaries}
            available={avail}
            busy={membershipMutation.isPending}
            onAdd={(ids) => { if (ids.length) membershipMutation.mutate({ group_ids: [...memberIds, ...ids] }) }}
            onRemove={(id) => membershipMutation.mutate({ group_ids: memberIds.filter((gid) => gid !== id) })}
          />
        </div>
      </div>

      <ProxmoxVMSection hostId={hostId} />
      <AlertsSection hostId={hostId} />
    </div>
  )
}
