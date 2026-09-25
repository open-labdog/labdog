"use client"

import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { apiFetch, ApiError } from "@/lib/api"
import { collectHostState } from "@/lib/collect-state"
import { toast } from "sonner"
import { def, FIREWALL_ACTION, SYSTEMD_STATE, firewallBackendDef } from "@/lib/status"
import { Banner, CodeBlock, Empty, Facts, Panel, Table, Tag } from "@/components/ld"
import type { ModuleCurrentState } from "@/lib/types"

export type CollectedUser = { username: string; uid?: number | null; home?: string | null; shell?: string | null }
export type CollectedGroup = { groupname: string; gid?: number | null }

export function formatPorts(rule: { port_start: number | null; port_end: number | null }): string {
  if (rule.port_start == null) return "any"
  if (rule.port_end != null && rule.port_end !== rule.port_start) return `${rule.port_start}–${rule.port_end}`
  return String(rule.port_start)
}

export function FirewallBackendTag({ backend }: { backend: string | null | undefined }) {
  const d = firewallBackendDef(backend)
  return d ? <Tag tone={d.tone}>{d.label}</Tag> : <Tag>{backend}</Tag>
}

/**
 * What the host actually reported for one module, last time it was
 * collected — shaped however that collector's JSON comes back, not a
 * LabDog resource, so it is read-only and has no source/priority column.
 */
function ModuleStateView({
  moduleType,
  state,
  onManageUser,
  onManageGroup,
  userHasOverride,
  groupHasOverride,
}: {
  moduleType: string
  state: unknown
  onManageUser?: (u: CollectedUser) => void
  onManageGroup?: (g: CollectedGroup) => void
  userHasOverride?: (username: string) => boolean
  groupHasOverride?: (groupname: string) => boolean
}) {
  if (state && typeof state === "object" && "error" in (state as Record<string, unknown>)) {
    return <p className="text-xs text-warn">{String((state as Record<string, unknown>).error)}</p>
  }

  if (moduleType === "firewall" && Array.isArray(state)) {
    const rules = state as Array<{ action: string; protocol: string; direction: string; source_cidr?: string; destination_cidr?: string; port_start?: number; port_end?: number; comment?: string }>
    return (
      <Table
        cols={[
          { k: "action", label: "action", w: "80px", sortable: false, cell: (r) => <Tag tone={def(FIREWALL_ACTION, r.action).tone}>{r.action}</Tag> },
          { k: "protocol", label: "protocol", w: "70px", sortable: false, cell: (r) => <span className="mono uppercase text-[11px]">{r.protocol}</span> },
          { k: "direction", label: "direction", w: "80px", sortable: false, cell: (r) => <span className="capitalize">{r.direction}</span> },
          { k: "source", label: "source", w: "minmax(110px,1fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11px]" title={r.source_cidr ?? "any"}>{r.source_cidr ?? "any"}</span> },
          { k: "destination", label: "destination", w: "minmax(110px,1fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11px]" title={r.destination_cidr ?? "any"}>{r.destination_cidr ?? "any"}</span> },
          { k: "ports", label: "port(s)", w: "80px", sortable: false, cell: (r) => <span className="mono text-[11px]">{formatPorts({ port_start: r.port_start ?? null, port_end: r.port_end ?? null })}</span> },
          { k: "comment", label: "comment", w: "minmax(140px,1.4fr)", sortable: false, cell: (r) => <span className="trunc text-text-3" title={r.comment ?? ""}>{r.comment ?? "—"}</span> },
        ]}
        rows={rules}
        keyOf={(r) => `${r.direction}|${r.protocol}|${r.action}|${r.source_cidr ?? ""}|${r.destination_cidr ?? ""}|${r.port_start ?? ""}|${r.port_end ?? ""}|${r.comment ?? ""}`}
        empty="No firewall rules."
      />
    )
  }

  if (moduleType === "service" && Array.isArray(state)) {
    const services = state as Array<{ unit?: string; service_name?: string; active_state: string; sub_state?: string; description?: string; enabled?: boolean }>
    return (
      <Table
        cols={[
          { k: "service", label: "service", w: "minmax(160px,1fr)", sortable: false, cell: (s) => <span className="mono font-medium text-text">{s.unit ?? s.service_name}</span> },
          { k: "state", label: "state", w: "100px", sortable: false, cell: (s) => <Tag tone={def(SYSTEMD_STATE, s.sub_state ?? s.active_state).tone}>{s.sub_state ?? s.active_state}</Tag> },
          { k: "description", label: "description", w: "minmax(160px,1.6fr)", sortable: false, cell: (s) => <span className="trunc text-text-3">{s.description ?? (s.enabled !== undefined ? (s.enabled ? "enabled" : "disabled") : "—")}</span> },
        ]}
        rows={services}
        keyOf={(s) => s.unit ?? s.service_name ?? ""}
        empty="No services."
      />
    )
  }

  if (moduleType === "hosts_file" && Array.isArray(state)) {
    const entries = state as Array<{ ip_address: string; hostname: string; aliases: string[] }>
    return (
      <Table
        cols={[
          { k: "ip", label: "ip address", w: "140px", sortable: false, cell: (e) => <span className="mono text-[11px]">{e.ip_address}</span> },
          { k: "hostname", label: "hostname", w: "minmax(160px,1fr)", sortable: false, cell: (e) => <span className="text-text">{e.hostname}</span> },
          { k: "aliases", label: "aliases", w: "minmax(160px,1.4fr)", sortable: false, cell: (e) => <span className="text-text-3">{e.aliases?.join(", ") || "—"}</span> },
        ]}
        rows={entries}
        keyOf={(e) => `${e.ip_address}|${e.hostname}`}
        empty="No hosts file entries."
      />
    )
  }

  if (moduleType === "linux_user" && typeof state === "object" && state !== null) {
    const { users, groups } = state as { users: Array<Record<string, unknown>>; groups: Array<Record<string, unknown>> }
    const isSystemUid = (uid: unknown) => uid === 0 || uid === 65534
    const isSystemGid = (gid: unknown) => gid === 0 || gid === 65534
    const normalUsers = users?.filter((u) => !isSystemUid(u.uid)) ?? []
    const systemUsers = users?.filter((u) => isSystemUid(u.uid)) ?? []
    const normalGroups = groups?.filter((g) => !isSystemGid(g.gid)) ?? []
    const systemGroups = groups?.filter((g) => isSystemGid(g.gid)) ?? []
    return (
      <div className="flex flex-col gap-3.5">
        <div>
          <h4 className="tt mb-1.5">users ({normalUsers.length})</h4>
          <div className="flex flex-col gap-0.5">
            {normalUsers.map((u, i) => {
              const username = String(u.username ?? u.name ?? "")
              const uid = u.uid as number | undefined
              const managed = username && userHasOverride?.(username)
              return (
                <div key={i} className="flex items-center justify-between gap-2 py-0.5">
                  <div className="mono text-[11px] text-text-3">
                    {username} (uid={String(uid ?? "?")})
                    {managed && <span className="ml-2 text-[10px] text-ok">managed</span>}
                  </div>
                  {onManageUser && username && (
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => onManageUser({ username, uid: typeof uid === "number" ? uid : null, home: (u.home as string | undefined) ?? null, shell: (u.shell as string | undefined) ?? null })}>
                      {managed ? "edit" : "manage"}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
        <div>
          <h4 className="tt mb-1.5">groups ({normalGroups.length})</h4>
          <div className="flex flex-col gap-0.5">
            {normalGroups.map((g, i) => {
              const groupname = String(g.groupname ?? g.name ?? "")
              const gid = g.gid as number | undefined
              const managed = groupname && groupHasOverride?.(groupname)
              return (
                <div key={i} className="flex items-center justify-between gap-2 py-0.5">
                  <div className="mono text-[11px] text-text-3">
                    {groupname} (gid={String(gid ?? "?")})
                    {managed && <span className="ml-2 text-[10px] text-ok">managed</span>}
                  </div>
                  {onManageGroup && groupname && (
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => onManageGroup({ groupname, gid: typeof gid === "number" ? gid : null })}>
                      {managed ? "edit" : "manage"}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
        {(systemUsers.length > 0 || systemGroups.length > 0) && (
          <div className="border-t border-line pt-2">
            <h4 className="tt text-text-faint">system (read-only)</h4>
            <p className="mb-1.5 text-[11px] text-text-faint">Managed by the OS — not editable via LabDog.</p>
            {systemUsers.length > 0 && (
              <div className="mono flex flex-col gap-0.5 text-[11px] text-text-faint">
                {systemUsers.map((u, i) => <div key={`u-${i}`}>{String(u.username ?? u.name)} (uid={String(u.uid ?? "?")})</div>)}
              </div>
            )}
            {systemGroups.length > 0 && (
              <div className="mono mt-1 flex flex-col gap-0.5 text-[11px] text-text-faint">
                {systemGroups.map((g, i) => <div key={`g-${i}`}>{String(g.groupname ?? g.name)} (gid={String(g.gid ?? "?")})</div>)}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  if (moduleType === "package" && typeof state === "object" && state !== null) {
    const isNewFormat = !Array.isArray(state) && "packages" in (state as Record<string, unknown>)
    const packages = isNewFormat
      ? ((state as { packages: Array<{ name: string; version?: string; state?: string }> }).packages ?? [])
      : (Array.isArray(state) ? (state as Array<{ name: string; version?: string; state?: string }>) : [])
    const repos = isNewFormat ? ((state as { repos: Array<{ name: string; type: string; url: string; enabled?: boolean }> }).repos ?? []) : []
    return (
      <div className="flex flex-col gap-3.5">
        <div>
          <h4 className="tt mb-1.5">packages ({packages.length})</h4>
          <Table
            cols={[
              { k: "name", label: "package", w: "minmax(160px,1fr)", sortable: false, cell: (p) => <span className="mono font-medium text-text">{p.name}</span> },
              { k: "version", label: "version", w: "120px", sortable: false, cell: (p) => <span className="mono text-[11px] text-text-3">{p.version ?? "—"}</span> },
              { k: "state", label: "state", w: "100px", sortable: false, cell: (p) => <span className="text-text-3">{p.state ?? "—"}</span> },
            ]}
            rows={packages}
            keyOf={(p) => p.name}
            empty="No managed packages configured."
          />
        </div>
        <div>
          <h4 className="tt mb-1.5">repositories ({repos.length})</h4>
          <Table
            cols={[
              { k: "name", label: "name", w: "minmax(140px,1fr)", sortable: false, cell: (r) => <span className="text-text">{r.name}</span> },
              { k: "type", label: "type", w: "80px", sortable: false, cell: (r) => <Tag>{r.type}</Tag> },
              { k: "url", label: "url", w: "minmax(180px,1.6fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11px] text-text-3">{r.url}</span> },
              { k: "enabled", label: "enabled", w: "80px", sortable: false, cell: (r) => <span className="text-text-3">{r.enabled !== false ? "yes" : "no"}</span> },
            ]}
            rows={repos}
            keyOf={(r) => `${r.type}|${r.name}|${r.url}`}
            empty="No repositories detected."
          />
        </div>
      </div>
    )
  }

  if (moduleType === "resolver" && typeof state === "object" && state !== null) {
    const r = state as { nameservers?: string[]; search_domains?: string[]; options?: Record<string, unknown> }
    return (
      <Facts
        items={[
          { k: "nameservers", v: r.nameservers?.join(", ") || "none", mono: true },
          { k: "search domains", v: r.search_domains?.join(", ") || "none", mono: true },
          ...(r.options && Object.keys(r.options).length > 0
            ? [{ k: "options", v: Object.entries(r.options).map(([k, v]) => `${k}=${v}`).join(", "), mono: true, span: 2 }]
            : []),
        ]}
      />
    )
  }

  if (moduleType === "cron" && Array.isArray(state)) {
    const cronEntries = state as Array<Record<string, unknown>>
    return (
      <Table
        cols={[
          { k: "name", label: "name / command", w: "minmax(180px,1.2fr)", sortable: false, cell: (c) => <span className="mono font-medium text-text">{String(c.name ?? c.command ?? "—")}</span> },
          { k: "schedule", label: "schedule", w: "140px", sortable: false, cell: (c) => <span className="mono text-[11px] text-text-3">{[c.minute, c.hour, c.day, c.month, c.weekday].filter(Boolean).join(" ") || "—"}</span> },
          { k: "user", label: "user", w: "100px", sortable: false, cell: (c) => <span className="text-text-3">{String(c.user ?? "—")}</span> },
        ]}
        rows={cronEntries}
        keyOf={(c) => `${String(c.user ?? "")}|${String(c.name ?? c.command ?? "")}|${[c.minute, c.hour, c.day, c.month, c.weekday].join(" ")}`}
        empty="No cron jobs."
      />
    )
  }

  return <CodeBlock maxH={320}>{JSON.stringify(state, null, 2)}</CodeBlock>
}

/** Map module_type to the full drift-settings API path (without host_id). */
const DRIFT_SETTINGS_PATH: Record<string, string> = {
  firewall: "/api/drift/hosts/{id}/settings",
  service: "/api/services/hosts/{id}/drift-settings",
  hosts_file: "/api/hosts-mgmt/hosts/{id}/drift-settings",
  linux_user: "/api/linux-users/hosts/{id}/drift-settings",
  cron: "/api/cron/hosts/{id}/drift-settings",
  package: "/api/packages/hosts/{id}/drift-settings",
  resolver: "/api/resolver/hosts/{id}/drift-settings",
}

/** What the host last reported for one module, with Collect / drift-check controls. */
export function CurrentStateSection({
  moduleType,
  modules,
  hostId,
  onManageUser,
  onManageGroup,
  userHasOverride,
  groupHasOverride,
}: {
  moduleType: string
  modules: ModuleCurrentState[] | undefined
  hostId: number
  onManageUser?: (u: CollectedUser) => void
  onManageGroup?: (g: CollectedGroup) => void
  userHasOverride?: (username: string) => boolean
  groupHasOverride?: (groupname: string) => boolean
}) {
  const [collecting, setCollecting] = useState(false)
  const queryClient = useQueryClient()
  const mod = modules?.find((m) => m.module_type === moduleType)

  const handleCollect = async () => {
    setCollecting(true)
    try {
      const { notices } = await collectHostState(hostId, moduleType)
      await queryClient.invalidateQueries({ queryKey: ["host-current-state", hostId] })
      await queryClient.invalidateQueries({ queryKey: ["host", hostId] })
      for (const w of notices) toast.warning(w, { duration: 10000 })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Operation failed")
    }
    setCollecting(false)
  }

  const handleToggleDrift = async () => {
    const pathTemplate = DRIFT_SETTINGS_PATH[moduleType]
    if (!pathTemplate) return
    const path = pathTemplate.replace("{id}", String(hostId))
    try {
      await apiFetch(path, { method: "PUT", body: JSON.stringify({ drift_check_enabled: !mod?.drift_check_enabled }) })
      await queryClient.invalidateQueries({ queryKey: ["host-current-state", hostId] })
      await queryClient.invalidateQueries({ queryKey: ["host", hostId] })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Operation failed")
    }
  }

  return (
    <Panel
      title="current state"
      meta={mod?.collected_at ? `collected ${new Date(mod.collected_at).toLocaleString()}` : undefined}
      actions={
        <>
          {DRIFT_SETTINGS_PATH[moduleType] && (
            <button type="button" className="btn btn-sm btn-ghost" onClick={handleToggleDrift}>
              {mod?.drift_check_enabled ? "disable drift check" : "enable drift check"}
            </button>
          )}
          <button type="button" className="btn btn-sm" disabled={collecting} onClick={handleCollect}>
            {collecting ? "collecting…" : "collect"}
          </button>
        </>
      }
      pad={11}
    >
      {!mod || mod.collected_state == null ? (
        <Empty title="Not yet collected" note="Collect reads this module's live state from the host over SSH." />
      ) : (
        <ModuleStateView
          moduleType={moduleType}
          state={mod.collected_state}
          onManageUser={onManageUser}
          onManageGroup={onManageGroup}
          userHasOverride={userHasOverride}
          groupHasOverride={groupHasOverride}
        />
      )}
    </Panel>
  )
}

/** The nftables-not-installed banner shown atop the Rules tab. */
export function InstallFirewallSection({ hostId, queryClient }: { hostId: number; queryClient: ReturnType<typeof useQueryClient> }) {
  const [installing, setInstalling] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])

  const handleInstall = async () => {
    setInstalling(true)
    setStatus("Adding nftables package…")
    try {
      await apiFetch(`/api/hosts/${hostId}/packages`, {
        method: "POST",
        body: JSON.stringify({ package_name: "nftables", state: "present", package_manager: "auto", comment: "Installed by LabDog for firewall management" }),
      })
    } catch (e: unknown) {
      if (!(e && typeof e === "object" && "status" in e && (e as { status: number }).status === 409)) {
        if (mountedRef.current) { setStatus("Failed to add package"); setInstalling(false) }
        return
      }
    }

    if (!mountedRef.current) return
    setStatus("Installing nftables via package sync…")
    try {
      const syncResult = await apiFetch<{ id: number }>(`/api/packages/hosts/${hostId}/sync`, { method: "POST" })
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 2000))
        if (!mountedRef.current) return
        try {
          const job = await apiFetch<{ status: string }>(`/api/packages/jobs/${syncResult.id}`)
          if (job.status === "success" || job.status === "failed") break
        } catch { break }
      }
    } catch {
      if (mountedRef.current) { setStatus("Failed to sync packages"); setInstalling(false) }
      return
    }

    if (!mountedRef.current) return
    setStatus("Detecting firewall backend…")
    try { await collectHostState(hostId, "firewall") } catch { /* ignore */ }

    if (!mountedRef.current) return
    await queryClient.invalidateQueries({ queryKey: ["host-current-state", hostId] })
    await queryClient.invalidateQueries({ queryKey: ["host", hostId] })
    await queryClient.invalidateQueries({ queryKey: ["host-effective-packages", hostId] })
    if (!mountedRef.current) return
    setStatus(null)
    setInstalling(false)
  }

  return (
    <Banner
      tone="warn"
      action={
        <button type="button" className="btn btn-sm" disabled={installing} onClick={handleInstall}>
          {installing ? "installing…" : "install nftables"}
        </button>
      }
    >
      No supported firewall detected on this host.{status ? ` ${status}` : " Install nftables to enable firewall management."}
    </Banner>
  )
}
