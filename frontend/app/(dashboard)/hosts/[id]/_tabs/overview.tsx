"use client"

import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import Link from "next/link"
import { apiFetch, ApiError } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { Banner, Facts, Panel, Status, Table, Tag } from "@/components/ld"
import { HostMetricsSection } from "@/components/host-metrics-section"
import { FirewallBackendTag } from "./config/shared"
import type { Host, HostFullAuto, HostGroup, ModuleCurrentState, VMMapping } from "@/lib/types"

/**
 * Which alerts may change this host without asking — the host's side of
 * the full-auto rules, worked out by the server, plus what would hold
 * every alert back here right now. Hidden while the AI is off.
 */
function FullAutoSection({ hostId }: { hostId: number }) {
  const { data } = useQuery<HostFullAuto>({
    queryKey: ["host-full-auto", hostId],
    queryFn: () => apiFetch<HostFullAuto>(`/api/ai/full-auto/hosts/${hostId}`),
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
          {data.alert_actions.length > 0 && <Link href="/actions?tab=schedules" className="btn btn-sm btn-ghost hover:no-underline">schedules</Link>}
          {data.ai_enabled && <Link href="/settings?section=ai" className="btn btn-sm btn-ghost hover:no-underline">rules</Link>}
        </>
      }
      pad={11}
    >
      <div className="flex flex-col gap-2 text-[11.5px]" data-testid="host-full-auto">
        {data.alert_actions.length > 0 && (
          <div className="flex flex-col gap-1" data-testid="host-alert-actions">
            <span className="text-text-3">Alerts on this host run these actions:</span>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {data.alert_actions.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-1.5">
                  <span className="text-text">{a.action_name ?? a.action_key}</span>
                  <span className="text-text-faint">when</span>
                  <span className="mono">{a.alert_match.split("\n").filter((l) => l.trim() && !l.trim().startsWith("#")).join(" · ")}</span>
                  {!a.enabled && <Tag>disabled</Tag>}
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
          <div className="flex flex-col gap-1" style={{ color: "var(--warn)" }}>
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

function ProxmoxVMSection({ hostId }: { hostId: number }) {
  const [expanded, setExpanded] = useState<boolean | null>(null)
  const queryClient = useQueryClient()

  const { data: mapping, isLoading, error } = useQuery<VMMapping | null>({
    queryKey: ["host-vm-mapping", hostId],
    queryFn: async () => {
      try {
        return await apiFetch<VMMapping>(`/api/proxmox/hosts/${hostId}/vm-mapping`)
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null
        throw e
      }
    },
    retry: false,
  })

  const open = expanded ?? Boolean(mapping)

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

function SyncStatusMessage({ host, modules }: { host: Host; modules: ModuleCurrentState[] | undefined }) {
  if (host.sync_status === "in_sync") return <Banner tone="ok">All modules are in sync with the desired configuration.</Banner>
  if (host.sync_status === "out_of_sync") {
    const names = (modules?.filter((m) => m.sync_status === "out_of_sync") ?? []).map((m) => m.module_type).join(", ")
    return <Banner tone="warn">Configuration drift detected — {names || "one or more modules"} out of sync.</Banner>
  }
  if (host.sync_status === "pending") return <Banner tone="sync" pulse>A sync operation is currently in progress.</Banner>
  return <Banner tone="idle">Sync status has not been checked yet. Run a drift check or collect state to determine status.</Banner>
}

export function OverviewTab({ hostId, host, groups }: { hostId: number; host: Host | undefined; groups: HostGroup[] | undefined }) {
  const queryClient = useQueryClient()
  const { data: currentState } = useQuery<ModuleCurrentState[]>({
    queryKey: ["host-current-state", hostId],
    queryFn: () => apiFetch<ModuleCurrentState[]>(`/api/hosts/${hostId}/current-state`),
  })

  const [picking, setPicking] = useState(false)
  const [q, setQ] = useState("")
  const membershipMutation = useApiMutation({
    mutationFn: (data: { group_ids: number[] }) => apiFetch(`/api/hosts/${hostId}`, { method: "PUT", body: JSON.stringify(data) }),
    invalidateKeys: [
      ["host", hostId], ["host-effective-rules", hostId], ["host-effective-services", hostId],
      ["host-effective-hosts-entries", hostId], ["host-effective-linux-users", hostId], ["host-effective-linux-groups", hostId],
      ["host-effective-cron-jobs", hostId], ["host-effective-packages", hostId], ["host-effective-repos", hostId], ["host-effective-resolver", hostId],
    ],
  })

  const memberIds = useMemo(() => host?.group_ids ?? [], [host?.group_ids])
  const members = useMemo(() => (groups ?? []).filter((g) => memberIds.includes(g.id)).sort((a, b) => a.priority - b.priority), [groups, memberIds])
  const avail = useMemo(() => (groups ?? []).filter((g) => !memberIds.includes(g.id)), [groups, memberIds])
  const ql = q.trim().toLowerCase()
  const filtered = ql ? avail.filter((g) => g.name.toLowerCase().includes(ql) || (g.category ?? "").toLowerCase().includes(ql)) : avail

  function add(ids: number[]) {
    if (!ids.length) return
    membershipMutation.mutate({ group_ids: [...memberIds, ...ids] })
  }
  function remove(id: number) {
    membershipMutation.mutate({ group_ids: memberIds.filter((gid) => gid !== id) })
  }

  async function toggleDrift() {
    if (!host) return
    await apiFetch(`/api/hosts/${hostId}`, { method: "PUT", body: JSON.stringify({ drift_check_enabled: !host.drift_check_enabled }) })
    await queryClient.invalidateQueries({ queryKey: ["host", hostId] })
  }

  if (!host) return null

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <Panel title="host" pad={11}>
        <div className="flex flex-col gap-3">
          <HostMetricsSection hostId={hostId} />
          <Facts
            items={[
              { k: "hostname", v: host.hostname, mono: true },
              { k: "ip address", v: host.ip_address, mono: true },
              { k: "ssh port", v: host.ssh_port, mono: true },
              { k: "management ip", v: host.labdog_source_ip ?? "not yet detected", mono: !!host.labdog_source_ip },
              { k: "firewall backend", v: <FirewallBackendTag backend={host.firewall_backend} /> },
              { k: "sync status", v: <Status s={host.sync_status} /> },
              { k: "last sync", v: host.last_sync_at ? new Date(host.last_sync_at).toLocaleString() : "never" },
              { k: "last drift check", v: host.last_drift_check_at ? new Date(host.last_drift_check_at).toLocaleString() : "never" },
              { k: "last verified", v: host.last_verified_at ? new Date(host.last_verified_at).toLocaleString() : "never" },
              { k: "os", v: host.os_pretty_name ?? "not collected" },
              ...(host.kernel_version ? [{ k: "kernel", v: host.kernel_version, mono: true }] : []),
              ...(host.default_nic ? [{ k: "default nic", v: host.default_nic, mono: true }] : []),
              { k: "drift monitoring", v: <button type="button" className="cursor-pointer border-0 bg-transparent p-0" onClick={toggleDrift}><Tag tone={host.drift_check_enabled ? "ok" : undefined}>{host.drift_check_enabled ? "enabled" : "disabled"}</Tag></button> },
            ]}
          />
        </div>
      </Panel>

      <ProxmoxVMSection hostId={hostId} />
      <FullAutoSection hostId={hostId} />
      <SyncStatusMessage host={host} modules={currentState} />

      <Panel title="group memberships" meta={`${plural(members.length, "group")}`} actions={!picking && avail.length > 0 && <button type="button" className="btn btn-sm" onClick={() => setPicking(true)}>+ add to group</button>} pad={0}>
        {picking && (
          <div className="border-b border-line bg-surface-2">
            <div className="flex items-center gap-1.5 border-b border-line-faint p-[7px]">
              <input className="inp" autoFocus value={q} placeholder="filter groups…" onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} aria-label="filter groups to add" />
              <button type="button" className="btn btn-sm" disabled={!filtered.length || membershipMutation.isPending} onClick={() => add(filtered.map((g) => g.id))}>add {filtered.length}</button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setPicking(false); setQ("") }}>done</button>
            </div>
            <div className="scroll max-h-[168px] p-1">
              {filtered.length === 0 && <div className="px-[5px] py-1.5 text-[11.5px] text-text-3">{avail.length ? "No match." : "Already a member of every group."}</div>}
              {filtered.map((g) => (
                <label key={g.id} className="flex cursor-pointer items-center gap-2 rounded px-[5px] py-[3px]">
                  <input type="checkbox" checked={false} disabled={membershipMutation.isPending} onChange={() => add([g.id])} style={{ accentColor: "var(--accent)" }} aria-label={`add ${g.name}`} />
                  <span className="flex-1 text-[11.5px]">{g.name}</span>
                  <span className="text-[10.5px] text-text-faint">{g.category ?? ""}</span>
                </label>
              ))}
            </div>
          </div>
        )}
        <Table<HostGroup>
          cols={[
            { k: "name", label: "name", w: "minmax(140px,1fr)", sortable: false, cell: (g) => <Link href={`/groups/${g.id}`} className="mono font-medium text-ld-accent hover:no-underline">{g.name}</Link> },
            { k: "category", label: "category", w: "minmax(100px,0.8fr)", sortable: false, cell: (g) => <span className="text-text-3">{g.category ?? "—"}</span> },
            { k: "priority", label: "priority", w: "76px", sortable: false, cell: (g) => <span className="mono num">{g.priority}</span> },
            { k: "description", label: "description", w: "minmax(140px,1.2fr)", sortable: false, cell: (g) => <span className="trunc text-text-3">{g.description ?? "—"}</span> },
            { k: "rm", label: "", w: "80px", right: true, sortable: false, cell: (g) => <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={membershipMutation.isPending} onClick={() => remove(g.id)}>remove</button> },
          ]}
          rows={members}
          keyOf={(g) => g.id}
          empty="This host is not a member of any groups."
        />
      </Panel>
    </div>
  )
}
