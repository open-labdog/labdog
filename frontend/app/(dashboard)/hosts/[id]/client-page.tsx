"use client"

import { useState, useEffect, type FormEvent } from "react"
import { useParams, useSearchParams } from "next/navigation"
import Link from "next/link"
import { TerminalIcon, RefreshCwIcon, ArrowUpFromLineIcon, X, ShieldIcon, ShieldCheckIcon, PlayIcon, ChevronDownIcon, ChevronRightIcon, CheckCircleIcon, AlertTriangleIcon, XCircleIcon, Loader2Icon, HelpCircleIcon } from "lucide-react"
import { SshTerminal } from "@/components/ssh-terminal"
import { useQueryClient, useQuery, useMutation } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  SyncStatusBadge,
  FirewallBadge,
  ItemStateBadge,
  PackageStateBadge,
  EnabledBadge,
  SystemdStateBadge,
} from "@/components/status-badge"
import { DataTable } from "@/components/ui/data-table"
import { GroupMultiSelect } from "@/components/group-multi-select"
import { HostCombobox } from "@/components/host-combobox"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { useApiMutation } from "@/lib/mutations"
import { TableSkeleton, CardSkeleton } from "@/components/ui/skeleton"
import { ActionsTab } from "@/components/actions-tab"
import { ModuleDiffView, moduleLabel } from "@/components/module-diff-view"
import { HostMetricsSection } from "@/components/host-metrics-section"
import { ScheduledActionsSection } from "@/components/scheduled-actions/scheduled-actions-section"
import { apiFetch, API_BASE, ApiError } from "@/lib/api"
import { toast } from "sonner"
import { useHostQueries, useHostDialogs } from "@/hooks/use-host-detail"
import type { CACertActionRun, EffectiveCACert, EffectiveCronJob, EffectiveFirewallRule, EffectiveHostsEntry, EffectiveLinuxGroup, EffectiveLinuxUser, EffectivePackage, EffectiveResolverConfig, EffectiveService, HostsEntry, LinuxGroup, LinuxUser, LiveService, ModuleDiff, PackageRepository, ServiceCommandResult, VMMapping } from "@/lib/types"

function ActionBadge({ action }: { action: string }) {
  const config: Record<string, string> = {
    allow: "bg-green-600 text-white",
    deny: "bg-red-600 text-white",
    reject: "bg-amber-600 text-white",
  }
  return (
    <Badge className={config[action] ?? ""}>
      {action.charAt(0).toUpperCase() + action.slice(1)}
    </Badge>
  )
}

function formatPorts(rule: { port_start: number | null; port_end: number | null }): string {
  if (rule.port_start == null) return "—"
  if (rule.port_end != null && rule.port_end !== rule.port_start) {
    return `${rule.port_start}–${rule.port_end}`
  }
  return String(rule.port_start)
}

function cronToHuman(schedule: string): string {
  const s = schedule.trim()
  if (s === "* * * * *") return "Every minute"
  if (s === "0 * * * *") return "Every hour"
  if (s === "0 0 * * *") return "Every day at midnight"
  // 0 N * * *  => Every day at N:00
  const dailyMatch = s.match(/^0\s+(\d+)\s+\*\s+\*\s+\*$/)
  if (dailyMatch) return `Every day at ${dailyMatch[1]}:00`
  // */N * * * *  => Every N minutes
  const everyNMin = s.match(/^\*\/(\d+)\s+\*\s+\*\s+\*\s+\*$/)
  if (everyNMin) return `Every ${everyNMin[1]} minutes`
  return s
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[12rem_1fr] items-center gap-x-4 gap-y-1 py-2 border-b border-slate-800 last:border-0">
      <span className="text-slate-400 text-sm">{label}</span>
      <span className="text-white text-sm">{children}</span>
    </div>
  )
}

type CollectedUser = { username: string; uid?: number | null; home?: string | null; shell?: string | null }
type CollectedGroup = { groupname: string; gid?: number | null }

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
  // Handle error objects from collectors
  if (state && typeof state === "object" && "error" in (state as Record<string, unknown>)) {
    return <p className="text-amber-400 text-sm">{String((state as Record<string, unknown>).error)}</p>
  }

  if (moduleType === "firewall" && Array.isArray(state)) {
    const rules = state as Array<{ action: string; protocol: string; direction: string; source_cidr?: string; destination_cidr?: string; port_start?: number; port_end?: number; comment?: string }>
    return (
      <DataTable
        tableId="current-state-firewall"
        data={rules}
        getRowKey={(_, i) => i}
        emptyMessage="No firewall rules."
        // This table has fewer columns than Effective Rules (no Priority/Group/Actions), so Source/Destination get room for a full IPv4 CIDR (up to 18 chars); wider values still truncate with full text on hover via title.
        columns={[
          { key: "action", label: "Action", accessor: (r) => r.action, cell: (r) => <ActionBadge action={r.action} />, defaultWidth: 90, filter: { type: "enum", options: [{label:"Allow",value:"allow"},{label:"Deny",value:"deny"},{label:"Reject",value:"reject"}] } },
          { key: "protocol", label: "Protocol", accessor: (r) => r.protocol, cell: (r) => <span className="text-slate-300 uppercase text-xs">{r.protocol}</span>, defaultWidth: 90, filter: { type: "enum", options: [{label:"TCP",value:"tcp"},{label:"UDP",value:"udp"},{label:"ICMP",value:"icmp"},{label:"Any",value:"any"}] } },
          { key: "direction", label: "Direction", accessor: (r) => r.direction, cell: (r) => <span className="text-slate-300 capitalize text-xs">{r.direction}</span>, defaultWidth: 100, filter: { type: "enum", options: [{label:"Input",value:"input"},{label:"Output",value:"output"}] } },
          { key: "source", label: "Source", accessor: (r) => r.source_cidr ?? "any", cell: (r) => <span className="font-mono text-slate-300 text-xs inline-block max-w-[140px] truncate align-middle" title={r.source_cidr ?? "any"}>{r.source_cidr ?? "any"}</span>, defaultWidth: 140, filter: { type: "text" } },
          { key: "destination", label: "Destination", accessor: (r) => r.destination_cidr ?? "any", cell: (r) => <span className="font-mono text-slate-300 text-xs inline-block max-w-[140px] truncate align-middle" title={r.destination_cidr ?? "any"}>{r.destination_cidr ?? "any"}</span>, defaultWidth: 140, filter: { type: "text" } },
          { key: "ports", label: "Port(s)", cell: (r) => <span className="font-mono text-slate-300 text-xs">{r.port_start ? (r.port_end && r.port_end !== r.port_start ? `${r.port_start}-${r.port_end}` : `${r.port_start}`) : "any"}</span>, defaultWidth: 90 },
          // Last column with room to spare; widened so the "Managed by LabDog: …" comment every host rule carries is readable (long values still truncate with full text on hover).
          { key: "comment", label: "Comment", accessor: (r) => r.comment ?? "", cell: (r) => <span className="text-slate-400 text-xs truncate max-w-[280px] inline-block align-middle" title={r.comment ?? ""}>{r.comment ?? "—"}</span>, defaultWidth: 280 },
        ]}
      />
    )
  }

  if (moduleType === "service" && Array.isArray(state)) {
    const services = state as Array<{ unit?: string; service_name?: string; active_state: string; sub_state?: string; description?: string; enabled?: boolean }>
    return (
      <DataTable
        tableId="current-state-service"
        data={services}
        getRowKey={(_, i) => i}
        emptyMessage="No services."
        columns={[
          { key: "service", label: "Service", accessor: (s) => s.unit ?? s.service_name ?? "", cell: (s) => <span className="font-mono text-white text-sm">{s.unit ?? s.service_name}</span>, defaultWidth: 200, filter: { type: "text", placeholder: "e.g. nginx" } },
          { key: "state", label: "State", accessor: (s) => s.sub_state ?? s.active_state, cell: (s) => <SystemdStateBadge state={s.active_state} label={s.sub_state ?? s.active_state} />, defaultWidth: 110, filter: { type: "enum", from: "accessor" } },
          { key: "description", label: "Description", accessor: (s) => s.description ?? (s.enabled !== undefined ? (s.enabled ? "Enabled" : "Disabled") : ""), cell: (s) => <span className="text-slate-400 text-sm truncate max-w-xs">{s.description ?? (s.enabled !== undefined ? (s.enabled ? "Enabled" : "Disabled") : "—")}</span>, defaultWidth: 260 },
        ]}
      />
    )
  }

  if (moduleType === "hosts_file" && Array.isArray(state)) {
    const entries = state as Array<{ ip_address: string; hostname: string; aliases: string[] }>
    return (
      <DataTable
        tableId="current-state-hosts_file"
        data={entries}
        getRowKey={(_, i) => i}
        emptyMessage="No hosts file entries."
        columns={[
          { key: "ip_address", label: "IP Address", accessor: (e) => e.ip_address, cell: (e) => <span className="font-mono text-slate-300 text-sm">{e.ip_address}</span>, defaultWidth: 140, filter: { type: "text", placeholder: "e.g. 10.0.1" } },
          { key: "hostname", label: "Hostname", accessor: (e) => e.hostname, cell: (e) => <span className="text-white">{e.hostname}</span>, defaultWidth: 180, filter: { type: "text", placeholder: "e.g. web-01" } },
          { key: "aliases", label: "Aliases", accessor: (e) => e.aliases?.join(", ") ?? "", cell: (e) => <span className="text-slate-400 text-sm">{e.aliases?.join(", ") || "—"}</span>, defaultWidth: 200 },
        ]}
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
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-medium text-slate-300 mb-2">Users ({normalUsers.length})</h4>
          <div className="space-y-0.5">
            {normalUsers.map((u, i) => {
              const username = String(u.username ?? u.name ?? "")
              const uid = u.uid as number | undefined
              const managed = username && userHasOverride?.(username)
              return (
                <div key={i} className="flex items-center justify-between gap-2 py-0.5">
                  <div className="font-mono text-xs text-slate-400">
                    {username} (uid={String(uid ?? "?")})
                    {managed && <span className="ml-2 text-[10px] text-green-500 font-sans">Managed</span>}
                  </div>
                  {onManageUser && username && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 px-2 text-xs text-slate-300 hover:text-white hover:bg-slate-800"
                      onClick={() => onManageUser({
                        username,
                        uid: typeof uid === "number" ? uid : null,
                        home: (u.home as string | undefined) ?? null,
                        shell: (u.shell as string | undefined) ?? null,
                      })}
                    >
                      {managed ? "Edit" : "Manage"}
                    </Button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
        <div>
          <h4 className="text-sm font-medium text-slate-300 mb-2">Groups ({normalGroups.length})</h4>
          <div className="space-y-0.5">
            {normalGroups.map((g, i) => {
              const groupname = String(g.groupname ?? g.name ?? "")
              const gid = g.gid as number | undefined
              const managed = groupname && groupHasOverride?.(groupname)
              return (
                <div key={i} className="flex items-center justify-between gap-2 py-0.5">
                  <div className="font-mono text-xs text-slate-400">
                    {groupname} (gid={String(gid ?? "?")})
                    {managed && <span className="ml-2 text-[10px] text-green-500 font-sans">Managed</span>}
                  </div>
                  {onManageGroup && groupname && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 px-2 text-xs text-slate-300 hover:text-white hover:bg-slate-800"
                      onClick={() => onManageGroup({
                        groupname,
                        gid: typeof gid === "number" ? gid : null,
                      })}
                    >
                      {managed ? "Edit" : "Manage"}
                    </Button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
        {(systemUsers.length > 0 || systemGroups.length > 0) && (
          <div className="pt-2 border-t border-slate-800">
            <h4 className="text-xs font-medium text-slate-500 mb-1">System (read-only)</h4>
            <p className="text-xs text-slate-600 mb-2">Managed by the OS — not editable via LabDog.</p>
            {systemUsers.length > 0 && (
              <div className="font-mono text-xs text-slate-500 space-y-0.5">
                {systemUsers.map((u, i) => (
                  <div key={`u-${i}`}>{String(u.username ?? u.name)} (uid={String(u.uid ?? "?")})</div>
                ))}
              </div>
            )}
            {systemGroups.length > 0 && (
              <div className="font-mono text-xs text-slate-500 space-y-0.5 mt-1">
                {systemGroups.map((g, i) => (
                  <div key={`g-${i}`}>{String(g.groupname ?? g.name)} (gid={String(g.gid ?? "?")})</div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  if (moduleType === "package" && typeof state === "object" && state !== null) {
    // New format: {packages: [...], repos: [...]}
    // Legacy format: [...] (array of packages only)
    const isNewFormat = !Array.isArray(state) && "packages" in (state as Record<string, unknown>)
    const packages = isNewFormat
      ? ((state as { packages: Array<{ name: string; version?: string; state?: string }> }).packages ?? [])
      : (Array.isArray(state) ? state as Array<{ name: string; version?: string; state?: string }> : [])
    const repos = isNewFormat
      ? ((state as { repos: Array<{ name: string; type: string; url: string; enabled?: boolean }> }).repos ?? [])
      : []

    return (
      <div className="space-y-5">
        <div>
          <h4 className="text-sm font-medium text-slate-300 mb-2">Packages ({packages.length})</h4>
          {packages.length > 0 ? (
            <DataTable
              tableId="current-state-package-packages"
              data={packages}
              getRowKey={(_, i) => i}
              emptyMessage="No managed packages configured."
              columns={[
                { key: "name", label: "Package", accessor: (p) => p.name, cell: (p) => <span className="font-mono text-white text-sm">{p.name}</span>, defaultWidth: 200, filter: { type: "text", placeholder: "e.g. curl" } },
                { key: "version", label: "Version", accessor: (p) => p.version ?? "", cell: (p) => <span className="font-mono text-slate-300 text-xs">{p.version ?? "—"}</span>, defaultWidth: 140, filter: { type: "text" } },
                { key: "state", label: "State", accessor: (p) => p.state ?? "", cell: (p) => <span className="text-slate-400">{p.state ?? "—"}</span>, defaultWidth: 110, filter: { type: "enum", from: "accessor" } },
              ]}
            />
          ) : (
            <p className="text-slate-500 text-sm">No managed packages configured.</p>
          )}
        </div>
        <div>
          <h4 className="text-sm font-medium text-slate-300 mb-2">Repositories ({repos.length})</h4>
          {repos.length > 0 ? (
            <DataTable
              tableId="current-state-package-repos"
              data={repos}
              getRowKey={(_, i) => i}
              emptyMessage="No repositories detected."
              columns={[
                { key: "name", label: "Name", accessor: (r) => r.name, cell: (r) => <span className="text-white text-sm">{r.name}</span>, defaultWidth: 160, filter: { type: "text" } },
                { key: "type", label: "Type", accessor: (r) => r.type, cell: (r) => <Badge variant="outline" className="text-xs font-mono">{r.type}</Badge>, defaultWidth: 90, filter: { type: "enum", from: "accessor" } },
                { key: "url", label: "URL", accessor: (r) => r.url, cell: (r) => <span className="font-mono text-slate-300 text-xs max-w-xs truncate">{r.url}</span>, defaultWidth: 260, filter: { type: "text", placeholder: "e.g. github.com" } },
                { key: "enabled", label: "Enabled", accessor: (r) => r.enabled !== false, cell: (r) => <span className="text-slate-400">{r.enabled !== false ? "Yes" : "No"}</span>, defaultWidth: 90, filter: { type: "boolean" } },
              ]}
            />
          ) : (
            <p className="text-slate-500 text-sm">No repositories detected.</p>
          )}
        </div>
      </div>
    )
  }

  if (moduleType === "resolver" && typeof state === "object" && state !== null) {
    const r = state as { nameservers?: string[]; search_domains?: string[]; options?: Record<string, unknown> }
    return (
      <div className="space-y-2 text-sm">
        <div><span className="text-slate-400">Nameservers:</span> <span className="font-mono text-white">{r.nameservers?.join(", ") || "none"}</span></div>
        <div><span className="text-slate-400">Search domains:</span> <span className="font-mono text-white">{r.search_domains?.join(", ") || "none"}</span></div>
        {r.options && Object.keys(r.options).length > 0 && (
          <div><span className="text-slate-400">Options:</span> <span className="font-mono text-white">{Object.entries(r.options).map(([k, v]) => `${k}=${v}`).join(", ")}</span></div>
        )}
      </div>
    )
  }

  if (moduleType === "cron" && Array.isArray(state)) {
    const cronEntries = state as Array<Record<string, unknown>>
    return (
      <DataTable
        tableId="current-state-cron"
        data={cronEntries}
        getRowKey={(_, i) => i}
        emptyMessage="No cron jobs."
        columns={[
          { key: "name", label: "Name/Command", accessor: (c) => String(c.name ?? c.command ?? ""), cell: (c) => <span className="font-mono text-white text-sm">{String(c.name ?? c.command ?? "—")}</span>, defaultWidth: 220, filter: { type: "text", placeholder: "e.g. backup" } },
          { key: "schedule", label: "Schedule", accessor: (c) => [c.minute, c.hour, c.day, c.month, c.weekday].filter(Boolean).join(" ") || "—", cell: (c) => <span className="font-mono text-slate-300 text-xs">{[c.minute, c.hour, c.day, c.month, c.weekday].filter(Boolean).join(" ") || "—"}</span>, defaultWidth: 140, filter: { type: "text", placeholder: "e.g. */5" } },
          { key: "user", label: "User", accessor: (c) => String(c.user ?? ""), cell: (c) => <span className="text-slate-400">{String(c.user ?? "—")}</span>, defaultWidth: 110, filter: { type: "text", placeholder: "e.g. root" } },
        ]}
      />
    )
  }

  // Fallback: render as JSON
  return (
    <pre className="text-xs font-mono text-slate-400 overflow-x-auto max-h-96">
      {JSON.stringify(state, null, 2)}
    </pre>
  )
}

// Map module_type to the full drift-settings API path (without host_id)
const DRIFT_SETTINGS_PATH: Record<string, string> = {
  firewall: "/api/drift/hosts/{id}/settings",
  service: "/api/services/hosts/{id}/drift-settings",
  hosts_file: "/api/hosts-mgmt/hosts/{id}/drift-settings",
  linux_user: "/api/linux-users/hosts/{id}/drift-settings",
  cron: "/api/cron/hosts/{id}/drift-settings",
  package: "/api/packages/hosts/{id}/drift-settings",
  resolver: "/api/resolver/hosts/{id}/drift-settings",
}

function CurrentStateSection({
  moduleType,
  modules,
  hostId,
  onManageUser,
  onManageGroup,
  userHasOverride,
  groupHasOverride,
}: {
  moduleType: string
  modules: import("@/lib/types").ModuleCurrentState[] | undefined
  hostId: number
  onManageUser?: (u: CollectedUser) => void
  onManageGroup?: (g: CollectedGroup) => void
  userHasOverride?: (username: string) => boolean
  groupHasOverride?: (groupname: string) => boolean
}) {
  const [collecting, setCollecting] = useState(false)
  const queryClient = useQueryClient()
  const mod = modules?.find(m => m.module_type === moduleType)

  const handleCollect = async () => {
    setCollecting(true)
    try {
      await apiFetch(`/api/hosts/${hostId}/collect-state?module=${moduleType}`, { method: "POST" })
      await queryClient.invalidateQueries({ queryKey: ["host-current-state", hostId] })
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Operation failed") }
    setCollecting(false)
  }

  const handleToggleDrift = async () => {
    const pathTemplate = DRIFT_SETTINGS_PATH[moduleType]
    if (!pathTemplate) return
    const path = pathTemplate.replace("{id}", String(hostId))
    try {
      await apiFetch(path, {
        method: "PUT",
        body: JSON.stringify({ drift_check_enabled: !mod?.drift_check_enabled }),
      })
      await queryClient.invalidateQueries({ queryKey: ["host-current-state", hostId] })
      await queryClient.invalidateQueries({ queryKey: ["host", hostId] })
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Operation failed") }
  }

  return (
    <div className="mt-6 border-t border-slate-700 pt-6">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <h3 className="text-base font-semibold text-white">Current State</h3>
          {mod?.collected_at && (
            <span className="text-xs text-slate-400">
              collected {new Date(mod.collected_at).toLocaleString()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {DRIFT_SETTINGS_PATH[moduleType] && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleToggleDrift}
              className={mod?.drift_check_enabled ? "text-green-400 hover:text-green-300" : "text-slate-500 hover:text-white"}
            >
              {mod?.drift_check_enabled ? "Disable Drift Check" : "Enable Drift Check"}
            </Button>
          )}
          <Button variant="outline" size="sm" disabled={collecting} onClick={handleCollect}>
            <RefreshCwIcon className={`w-3.5 h-3.5 mr-1 ${collecting ? "animate-spin" : ""}`} />
            {collecting ? "Collecting..." : "Collect"}
          </Button>
        </div>
      </div>
      {!mod || mod.collected_state == null ? (
        <p className="text-slate-400 text-sm">Not yet collected.</p>
      ) : (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
          <ModuleStateView
            moduleType={moduleType}
            state={mod.collected_state}
            onManageUser={onManageUser}
            onManageGroup={onManageGroup}
            userHasOverride={userHasOverride}
            groupHasOverride={groupHasOverride}
          />
        </div>
      )}
    </div>
  )
}

function InstallFirewallSection({ hostId, queryClient }: { hostId: number; queryClient: ReturnType<typeof useQueryClient> }) {
  const [installing, setInstalling] = useState(false)
  const [status, setStatus] = useState<string | null>(null)

  const handleInstall = async () => {
    setInstalling(true)
    setStatus("Adding nftables package...")
    try {
      // 1. Add nftables as a host package override
      await apiFetch(`/api/hosts/${hostId}/packages`, {
        method: "POST",
        body: JSON.stringify({
          package_name: "nftables",
          state: "present",
          package_manager: "auto",
          comment: "Installed by LabDog for firewall management",
        }),
      })
    } catch (e: unknown) {
      // 409 = already exists, continue
      if (!(e && typeof e === "object" && "status" in e && (e as { status: number }).status === 409)) {
        setStatus("Failed to add package")
        setInstalling(false)
        return
      }
    }

    // 2. Trigger package sync and wait for completion
    setStatus("Installing nftables via package sync...")
    try {
      const syncResult = await apiFetch<{ id: number }>(`/api/packages/hosts/${hostId}/sync`, { method: "POST" })
      // Poll sync job status until done
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 2000))
        try {
          const job = await apiFetch<{ status: string }>(`/api/packages/jobs/${syncResult.id}`)
          if (job.status === "success" || job.status === "failed") break
        } catch { break }
      }
    } catch {
      setStatus("Failed to sync packages")
      setInstalling(false)
      return
    }

    // 3. Re-collect firewall state to detect the new backend
    setStatus("Detecting firewall backend...")
    try {
      await apiFetch(`/api/hosts/${hostId}/collect-state?module=firewall`, { method: "POST" })
    } catch { /* ignore */ }

    await queryClient.invalidateQueries({ queryKey: ["host-current-state", hostId] })
    await queryClient.invalidateQueries({ queryKey: ["host", hostId] })
    await queryClient.invalidateQueries({ queryKey: ["host-effective-packages", hostId] })
    setStatus(null)
    setInstalling(false)
  }

  return (
    <div className="rounded-lg border border-amber-700/50 bg-amber-950/20 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-amber-400">No Firewall Detected</h3>
          <p className="text-xs text-slate-400 mt-1">
            This host has no supported firewall. Install nftables to enable firewall management.
          </p>
          {status && <p className="text-xs text-slate-300 mt-2">{status}</p>}
        </div>
        <Button
          size="sm"
          disabled={installing}
          onClick={handleInstall}
        >
          <ShieldIcon className={`w-4 h-4 mr-1 ${installing ? "animate-pulse" : ""}`} />
          {installing ? "Installing..." : "Install nftables"}
        </Button>
      </div>
    </div>
  )
}

function ProxmoxVMSection({
  hostId,
  queryClient,
}: {
  hostId: number
  queryClient: ReturnType<typeof useQueryClient>
}) {
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null)

  const {
    data: mapping,
    isLoading,
    error,
  } = useQuery<VMMapping | null>({
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

  const expanded = userExpanded ?? Boolean(mapping)

  const discoverMutation = useMutation({
    mutationFn: async () => {
      try {
        return await apiFetch<VMMapping>(`/api/proxmox/hosts/${hostId}/discover`, {
          method: "POST",
        })
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null
        throw e
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["host-vm-mapping", hostId] })
    },
  })

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-0">
      <div
        className="flex items-center justify-between pb-3 mb-1 border-b border-slate-800 cursor-pointer"
        onClick={() => setUserExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          {expanded ? <ChevronDownIcon className="w-4 h-4 text-slate-400" /> : <ChevronRightIcon className="w-4 h-4 text-slate-400" />}
          <h3 className="text-sm font-semibold text-slate-200">VM Mapping</h3>
          {!expanded && mapping && (
            <span className="text-slate-400 text-xs ml-1">{mapping.vm_name} (VMID {mapping.vmid})</span>
          )}
          {!expanded && !isLoading && !mapping && (
            <span className="text-slate-400 text-xs ml-1">No mapping</span>
          )}
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={discoverMutation.isPending || isLoading}
          onClick={(e) => { e.stopPropagation(); discoverMutation.mutate() }}
        >
          <RefreshCwIcon className={`w-3 h-3 mr-1 ${discoverMutation.isPending ? "animate-spin" : ""}`} />
          {discoverMutation.isPending ? "Scanning..." : "Discover"}
        </Button>
      </div>

      {expanded && (
        <>
          {isLoading && (
            <div className="py-3 text-slate-400 text-sm flex items-center gap-2">
              <Loader2Icon className="w-4 h-4 animate-spin" />
              Loading...
            </div>
          )}

          {!isLoading && error && (
            <div className="py-3 text-red-400 text-sm">Failed to load VM mapping</div>
          )}

          {discoverMutation.error && (
            <div className="py-2 text-amber-400 text-xs">{(discoverMutation.error as Error).message}</div>
          )}

          {!isLoading && !error && mapping === null && !discoverMutation.isPending && (
            <div className="py-3 text-slate-400 text-sm">
              No VM mapping found. Click Discover to scan Proxmox nodes for this host.
            </div>
          )}

          {!isLoading && !error && mapping && (
            <>
              <InfoRow label="VM Name">
                <span className="font-mono">{mapping.vm_name}</span>
              </InfoRow>
              <InfoRow label="VMID">
                <span className="font-mono">{mapping.vmid}</span>
              </InfoRow>
              <InfoRow label="Proxmox Node">
                <span className="font-mono">{mapping.pve_node_name}</span>
              </InfoRow>
              <InfoRow label="Discovered">
                {new Date(mapping.discovered_at).toLocaleString()}
              </InfoRow>
            </>
          )}
        </>
      )}
    </div>
  )
}

function SyncStatusMessage({
  host,
  modules,
}: {
  host: import("@/lib/types").Host
  modules: import("@/lib/types").ModuleCurrentState[] | undefined
}) {
  const outOfSync = modules?.filter(m => m.sync_status === "out_of_sync") ?? []

  if (host.sync_status === "in_sync") {
    return (
      <div className="rounded-lg border border-green-700/50 bg-green-950/20 px-4 py-3 flex items-center gap-2">
        <CheckCircleIcon className="w-4 h-4 text-green-400 shrink-0" />
        <span className="text-green-400 text-sm">All modules are in sync with the desired configuration.</span>
      </div>
    )
  }

  if (host.sync_status === "out_of_sync") {
    const names = outOfSync.map(m => m.module_type).join(", ")
    return (
      <div className="rounded-lg border border-amber-700/50 bg-amber-950/20 px-4 py-3 flex items-center gap-2">
        <AlertTriangleIcon className="w-4 h-4 text-amber-400 shrink-0" />
        <span className="text-amber-400 text-sm">
          Configuration drift detected. {outOfSync.length} module(s) out of sync: {names}.
        </span>
      </div>
    )
  }

  if (host.sync_status === "pending") {
    return (
      <div className="rounded-lg border border-blue-700/50 bg-blue-950/20 px-4 py-3 flex items-center gap-2">
        <Loader2Icon className="w-4 h-4 text-blue-400 shrink-0 animate-spin" />
        <span className="text-blue-400 text-sm">A sync operation is currently in progress.</span>
      </div>
    )
  }

  // unknown
  return (
    <div className="rounded-lg border border-slate-700/50 bg-slate-900/50 px-4 py-3 flex items-center gap-2">
      <HelpCircleIcon className="w-4 h-4 text-slate-400 shrink-0" />
      <span className="text-slate-400 text-sm">
        Sync status has not been checked yet. Run a drift check or collect state to determine status.
      </span>
    </div>
  )
}

type HostTab = "overview" | "groups" | "rules" | "services" | "hosts-file" | "users" | "cron-jobs" | "packages" | "ca-certs" | "dns" | "actions" | "schedules"

export default function HostDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()
  const searchParams = useSearchParams()
  const initialTab = (searchParams.get("tab") as HostTab) || "overview"
  const [activeTab, setActiveTab] = useState<HostTab>(initialTab)

  const {
    host: hostQuery, effectiveRules: effectiveRulesQuery, effectivePolicies: effectivePoliciesQuery, showRulesLoading, sshKeys: sshKeysQuery, groups: groupsQuery,
    effectiveServices: effectiveServicesQuery, showServicesLoading, hostOverrides: hostOverridesQuery,
    effectiveHosts: effectiveHostsQuery, showHostsEntriesLoading, hostHostsOverrides: hostHostsOverridesQuery,
    effectiveLinuxUsers: effectiveLinuxUsersQuery, showLinuxUsersLoading,
    effectiveLinuxGroups: effectiveLinuxGroupsQuery, showLinuxGroupsLoading,
    hostLinuxUserOverrides: hostLinuxUserOverridesQuery, hostLinuxGroupOverrides: hostLinuxGroupOverridesQuery,
    effectiveCronJobs: effectiveCronJobsQuery, showCronJobsLoading, hostCronOverrides: hostCronOverridesQuery,
    effectivePackages: effectivePackagesQuery, showPackagesLoading, hostPackageOverrides: hostPackageOverridesQuery, effectiveRepos: effectiveReposQuery,
    effectiveCACerts: effectiveCACertsQuery, showCACertsLoading, hostCACertOverrides: hostCACertOverridesQuery, hostCACertRuns: hostCACertRunsQuery,
    effectiveResolver: effectiveResolverQuery, showResolverLoading, hostResolverOverride: hostResolverOverrideQuery,
    currentState: currentStateQuery,
  } = useHostQueries(id, activeTab)

  const host = hostQuery.data
  const hostLoading = hostQuery.isLoading
  const hostError = hostQuery.error
  const effectiveRules = effectiveRulesQuery.data
  const rulesLoading = effectiveRulesQuery.isLoading
  const rulesError = effectiveRulesQuery.error
  const effectivePolicies = effectivePoliciesQuery.data
  const sshKeys = sshKeysQuery.data
  const groups = groupsQuery.data
  const effectiveServices = effectiveServicesQuery.data
  const servicesLoading = effectiveServicesQuery.isLoading
  const servicesError = effectiveServicesQuery.error
  const hostOverrides = hostOverridesQuery.data
  const effectiveHosts = effectiveHostsQuery.data
  const hostsEntriesLoading = effectiveHostsQuery.isLoading
  const hostsEntriesError = effectiveHostsQuery.error
  const hostHostsOverrides = hostHostsOverridesQuery.data
  const effectiveLinuxUsers = effectiveLinuxUsersQuery.data
  const linuxUsersLoading = effectiveLinuxUsersQuery.isLoading
  const linuxUsersError = effectiveLinuxUsersQuery.error
  const effectiveLinuxGroups = effectiveLinuxGroupsQuery.data
  const linuxGroupsLoading = effectiveLinuxGroupsQuery.isLoading
  const linuxGroupsError = effectiveLinuxGroupsQuery.error
  const hostLinuxUserOverrides = hostLinuxUserOverridesQuery.data
  const hostLinuxGroupOverrides = hostLinuxGroupOverridesQuery.data
  const effectiveCronJobs = effectiveCronJobsQuery.data
  const cronJobsLoading = effectiveCronJobsQuery.isLoading
  const cronJobsError = effectiveCronJobsQuery.error
  const hostCronOverrides = hostCronOverridesQuery.data
  const effectivePackages = effectivePackagesQuery.data
  const packagesLoading = effectivePackagesQuery.isLoading
  const packagesError = effectivePackagesQuery.error
  const hostPackageOverrides = hostPackageOverridesQuery.data
  const effectiveRepos = effectiveReposQuery.data
  const effectiveCACerts = effectiveCACertsQuery.data
  const caCertsLoading = effectiveCACertsQuery.isLoading
  const caCertsError = effectiveCACertsQuery.error
  const hostCACertOverrides = hostCACertOverridesQuery.data
  const hostCACertRuns = hostCACertRunsQuery.data
  const effectiveResolver = effectiveResolverQuery.data
  const resolverLoading = effectiveResolverQuery.isLoading
  const resolverError = effectiveResolverQuery.error
  // 200+null is the "no resolver applies to this host" signal — distinct
  // from the loading state (data === undefined) and from a real error.
  const resolverNotConfigured = !resolverLoading && !resolverError && effectiveResolver === null
  const hostResolverOverride = hostResolverOverrideQuery.data

  const {
    editOpen, setEditOpen,
    fwDialogOpen, setFwDialogOpen,
    svcDialogOpen, setSvcDialogOpen,
    hostsDialogOpen, setHostsDialogOpen,
    luDialogOpen, setLuDialogOpen,
    lgDialogOpen, setLgDialogOpen,
    cjDialogOpen, setCjDialogOpen,
    ppDialogOpen, setPpDialogOpen,
    caDialogOpen, setCaDialogOpen,
    protectedConfirmOpen, setProtectedConfirmOpen,
  } = useHostDialogs()

  const [terminalOpen, setTerminalOpen] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [trustKeyOpen, setTrustKeyOpen] = useState(false)
  const [trustingKey, setTrustingKey] = useState(false)

  // Preview-then-confirm flow for manual syncs (per-module + "Sync All").
  // `scope === "module"` previews one module (apply via its own endpoint);
  // `scope === "all"` previews every module (apply via the coalesced bulk
  // endpoint with a single pollable job).
  const [syncPreview, setSyncPreview] = useState<{
    scope: "module" | "all"
    tabKey: string | null
    module: string | null
    loading: boolean
    error: string | null
    diffs: ModuleDiff[] | null
  } | null>(null)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [bulkJob, setBulkJob] = useState<{ id: number; status: string } | null>(null)

  const tabQueryKeys: Record<string, string[][]> = {
    overview: [["host", String(id)], ["host-current-state", String(id)], ["host-metrics", String(id)]],
    groups: [["host", String(id)], ["groups"]],
    rules: [["host-effective-rules", String(id)], ["host-firewall-overrides", String(id)], ["host-current-state", String(id)]],
    services: [["host-effective-services", String(id)], ["host-service-overrides", String(id)]],
    "hosts-file": [["host-effective-hosts-entries", String(id)], ["host-hosts-overrides", String(id)]],
    users: [["host-effective-linux-users", String(id)], ["host-effective-linux-groups", String(id)]],
    "cron-jobs": [["host-effective-cron-jobs", String(id)], ["host-cron-overrides", String(id)]],
    packages: [["host-effective-packages", String(id)], ["host-package-overrides", String(id)], ["host-effective-repos", String(id)]],
    "ca-certs": [["host-effective-ca-certs", String(id)], ["host-ca-cert-overrides", String(id)], ["host-ca-cert-runs", String(id)]],
    dns: [["host-effective-resolver", String(id)], ["host-resolver-override", String(id)]],
    actions: [["actions-catalog"], ["action-runs", "host", String(id)]],
  }

  const moduleSyncEndpoints: Record<string, string> = {
    rules: `/api/sync/hosts/${id}/sync`,
    services: `/api/services/hosts/${id}/sync`,
    "hosts-file": `/api/hosts-mgmt/hosts/${id}/sync`,
    users: `/api/linux-users/hosts/${id}/sync`,
    "cron-jobs": `/api/cron/hosts/${id}/sync`,
    packages: `/api/packages/hosts/${id}/sync`,
    dns: `/api/resolver/hosts/${id}/sync`,
  }

  // Tab key (used by moduleSyncEndpoints / tabQueryKeys) → canonical
  // module name understood by the preview/bulk endpoints.
  const tabToModule: Record<string, string> = {
    rules: "firewall",
    services: "services",
    "hosts-file": "hosts-file",
    users: "linux-users",
    "cron-jobs": "cron",
    packages: "packages",
    dns: "resolver",
  }

  const openModulePreview = async (tabKey: string) => {
    const moduleName = tabToModule[tabKey]
    setApplyError(null)
    setBulkJob(null)
    setSyncPreview({ scope: "module", tabKey, module: moduleName, loading: true, error: null, diffs: null })
    try {
      const diffs = await apiFetch<ModuleDiff[]>(`/api/sync/hosts/${id}/preview`, {
        method: "POST",
        body: JSON.stringify({ module_filter: [moduleName] }),
      })
      setSyncPreview((p) => (p ? { ...p, loading: false, diffs } : p))
    } catch (e) {
      setSyncPreview((p) =>
        p ? { ...p, loading: false, error: e instanceof Error ? e.message : "Preview failed" } : p
      )
    }
  }

  const openSyncAllPreview = async () => {
    setApplyError(null)
    setBulkJob(null)
    setSyncPreview({ scope: "all", tabKey: null, module: null, loading: true, error: null, diffs: null })
    try {
      const diffs = await apiFetch<ModuleDiff[]>(`/api/sync/hosts/${id}/preview`, {
        method: "POST",
        body: JSON.stringify({ module_filter: null }),
      })
      setSyncPreview((p) => (p ? { ...p, loading: false, diffs } : p))
    } catch (e) {
      setSyncPreview((p) =>
        p ? { ...p, loading: false, error: e instanceof Error ? e.message : "Preview failed" } : p
      )
    }
  }

  const closeSyncPreview = () => {
    if (applying) return
    setSyncPreview(null)
    setApplyError(null)
    setBulkJob(null)
  }

  const applyModuleSync = async () => {
    if (!syncPreview?.tabKey) return
    const tabKey = syncPreview.tabKey
    setApplying(true)
    setApplyError(null)
    try {
      await apiFetch(moduleSyncEndpoints[tabKey], { method: "POST" })
      for (const key of tabQueryKeys[tabKey] ?? []) await queryClient.invalidateQueries({ queryKey: key })
      await queryClient.invalidateQueries({ queryKey: ["host", id] })
      setSyncPreview(null)
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "Sync failed")
    }
    setApplying(false)
  }

  const applyBulkSync = async () => {
    // Apply exactly what the preview showed would change, and never a
    // module whose current state could not be read (errored preview).
    // Sending null would apply every module — including firewall when
    // its diff errored, i.e. blind. So target the cleanly-previewed,
    // changed modules only.
    const modulesToApply = (syncPreview?.diffs ?? [])
      .filter((d) => d.has_changes && !d.error)
      .map((d) => d.module)
    if (modulesToApply.length === 0) return
    setApplying(true)
    setApplyError(null)
    try {
      const resp = await apiFetch<{ job_id: number; status: string }>(`/api/sync/hosts/${id}/bulk`, {
        method: "POST",
        body: JSON.stringify({ module_filter: modulesToApply }),
      })
      setBulkJob({ id: resp.job_id, status: resp.status })
      // Terminal state + cache invalidation handled by the polling effect.
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "Sync failed")
      setApplying(false)
    }
  }

  // Poll the coalesced bulk SyncJob until it reaches a terminal state.
  useEffect(() => {
    if (!bulkJob) return
    if (bulkJob.status === "success" || bulkJob.status === "failed") return
    let cancelled = false
    const poll = async () => {
      try {
        const data = await apiFetch<{ id: number; status: string }>(`/api/sync/jobs/${bulkJob.id}`)
        if (cancelled) return
        setBulkJob({ id: data.id, status: data.status })
        if (data.status === "success" || data.status === "failed") {
          setApplying(false)
          await queryClient.invalidateQueries({ queryKey: ["host", id] })
          await queryClient.invalidateQueries({ queryKey: ["host-current-state", id] })
        }
      } catch {
        /* transient — keep polling */
      }
    }
    const interval = setInterval(poll, 3000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [bulkJob, id, queryClient])

  const [editHostname, setEditHostname] = useState("")
  const [editIp, setEditIp] = useState("")
  const [editSshPort, setEditSshPort] = useState(22)
  const [editSshUser, setEditSshUser] = useState("root")
  const [editSshKeyId, setEditSshKeyId] = useState<number | null>(null)
  const [editGroups, setEditGroups] = useState<number[]>([])
  const editMutation = useApiMutation({
    mutationFn: (data: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    invalidateKeys: [["host", id], ["host-effective-rules", id]],
    onSuccess: () => setEditOpen(false),
  })
  const [addGroupOpen, setAddGroupOpen] = useState(false)
  const [addGroupSearch, setAddGroupSearch] = useState("")
  const [addGroupSelected, setAddGroupSelected] = useState<Set<number>>(new Set())
  const [removeGroupConfirm, setRemoveGroupConfirm] = useState<number | null>(null)
  const [selectedGroupIds, setSelectedGroupIds] = useState<Set<number>>(new Set())
  const [bulkRemoveGroupConfirm, setBulkRemoveGroupConfirm] = useState(false)
  const groupMembershipMutation = useApiMutation({
    mutationFn: (data: { group_ids: number[] }) =>
      apiFetch(`/api/hosts/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    invalidateKeys: [
      ["host", id],
      ["host-effective-rules", id],
      ["host-effective-services", id],
      ["host-effective-hosts-entries", id],
      ["host-effective-linux-users", id],
      ["host-effective-linux-groups", id],
      ["host-effective-cron-jobs", id],
      ["host-effective-packages", id],
      ["host-effective-repos", id],
      ["host-effective-resolver", id],
    ],
  })

  const [confirmState, setConfirmState] = useState<{
    open: boolean
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
    confirmLabel?: string
    variant?: "default" | "destructive"
  } | null>(null)



  const [hostsPreview, setHostsPreview] = useState<string | null>(null)
  const [hostsPreviewLoading, setHostsPreviewLoading] = useState(false)
  const [hostsPreviewError, setHostsPreviewError] = useState<string | null>(null)

  const [hostsMode, setHostsMode] = useState<"literal" | "host">("literal")
  const [hostsRefId, setHostsRefId] = useState<number | null>(null)
  const [hostsIp, setHostsIp] = useState("")
  const [hostsHostname, setHostsHostname] = useState("")
  const [hostsAliases, setHostsAliases] = useState("")
  const [hostsComment, setHostsComment] = useState("")
  const [hostsPriority, setHostsPriority] = useState(100)
  const [hostsEditingId, setHostsEditingId] = useState<number | null>(null)
  const hostsSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/hosts-entries`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-hosts-entries", id], ["host-hosts-overrides", id]],
    onSuccess: () => setHostsDialogOpen(false),
  })

  const hostsUpdateMutation = useApiMutation({
    mutationFn: ({ entryId, payload }: { entryId: number; payload: Record<string, unknown> }) =>
      apiFetch(`/api/hosts/${id}/hosts-entries/${entryId}`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-hosts-entries", id], ["host-hosts-overrides", id]],
    onSuccess: () => setHostsDialogOpen(false),
  })

  const hostsDeleteMutation = useApiMutation({
    mutationFn: (entryId: number) =>
      apiFetch(`/api/hosts/${id}/hosts-entries/${entryId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-hosts-entries", id], ["host-hosts-overrides", id]],
  })

  const [luUsername, setLuUsername] = useState("")
  const [luUid, setLuUid] = useState("")
  const [luShell, setLuShell] = useState("/bin/bash")
  const [luHomeDir, setLuHomeDir] = useState("")
  const [luState, setLuState] = useState<"present" | "absent">("present")
  const [luComment, setLuComment] = useState("")
  const [luSudoRule, setLuSudoRule] = useState("")
  const [luAuthorizedKeys, setLuAuthorizedKeys] = useState("")
  const [luSupplementaryGroups, setLuSupplementaryGroups] = useState("")
  const [luPriority, setLuPriority] = useState(100)
  const [luEditingId, setLuEditingId] = useState<number | null>(null)
  const luSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/linux-users`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-linux-users", id], ["host-linux-user-overrides", id]],
    onSuccess: () => setLuDialogOpen(false),
  })
  const luUpdateMutation = useApiMutation({
    mutationFn: ({ overrideId, payload }: { overrideId: number; payload: Record<string, unknown> }) =>
      apiFetch(`/api/hosts/${id}/linux-users/${overrideId}`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-linux-users", id], ["host-linux-user-overrides", id]],
    onSuccess: () => setLuDialogOpen(false),
  })

  const luDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/linux-users/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-linux-users", id], ["host-linux-user-overrides", id]],
  })

  const [cjName, setCjName] = useState("")
  const [cjUser, setCjUser] = useState("root")
  const [cjSchedule, setCjSchedule] = useState("")
  const [cjCommand, setCjCommand] = useState("")
  const [cjState, setCjState] = useState<"present" | "absent">("present")
  const [cjPriority, setCjPriority] = useState(100)
  const [cjComment, setCjComment] = useState("")
  const [cjEnvVars, setCjEnvVars] = useState<{ key: string; value: string }[]>([])
  const [cjEditingId, setCjEditingId] = useState<number | null>(null)
  const cjSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/cron-jobs`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-cron-jobs", id], ["host-cron-overrides", id]],
    onSuccess: () => setCjDialogOpen(false),
  })

  const cjUpdateMutation = useApiMutation({
    mutationFn: ({ overrideId, payload }: { overrideId: number; payload: Record<string, unknown> }) =>
      apiFetch(`/api/hosts/${id}/cron-jobs/${overrideId}`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-cron-jobs", id], ["host-cron-overrides", id]],
    onSuccess: () => setCjDialogOpen(false),
  })

  const cjDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/cron-jobs/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-cron-jobs", id], ["host-cron-overrides", id]],
  })

  const [ppName, setPpName] = useState("")
  const [ppVersion, setPpVersion] = useState("")
  const [ppState, setPpState] = useState<"present" | "absent" | "latest">("present")
  const [ppManager, setPpManager] = useState<"auto" | "apt" | "dnf" | "yum">("auto")
  const [ppComment, setPpComment] = useState("")
  const [ppHold, setPpHold] = useState(false)
  const [ppEditingId, setPpEditingId] = useState<number | null>(null)
  const ppSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/packages`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-packages", id], ["host-package-overrides", id]],
    onSuccess: () => setPpDialogOpen(false),
  })

  const ppUpdateMutation = useApiMutation({
    mutationFn: ({ overrideId, payload }: { overrideId: number; payload: Record<string, unknown> }) =>
      apiFetch(`/api/hosts/${id}/packages/${overrideId}`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-packages", id], ["host-package-overrides", id]],
    onSuccess: () => setPpDialogOpen(false),
  })

  const ppDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/packages/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-packages", id], ["host-package-overrides", id]],
  })

  function openPpDialog() {
    setPpName("")
    setPpVersion("")
    setPpState("present")
    setPpManager("auto")
    setPpComment("")
    setPpHold(false)
    setPpEditingId(null)
    ppSaveMutation.reset()
    ppUpdateMutation.reset()
    setPpDialogOpen(true)
  }

  function openPpEditDialog(pkg: EffectivePackage) {
    const override = pkg.source === "host"
      ? hostPackageOverrides?.find(o => o.package_name === pkg.package_name)
      : null
    setPpName(pkg.package_name)
    setPpVersion(pkg.version ?? "")
    setPpState(pkg.state)
    setPpManager(pkg.package_manager)
    setPpComment(override?.comment ?? "")
    setPpHold(pkg.hold)
    setPpEditingId(override?.id ?? null)
    ppSaveMutation.reset()
    ppUpdateMutation.reset()
    setPpDialogOpen(true)
  }

  function closePpDialog() {
    setPpDialogOpen(false)
    setPpEditingId(null)
  }

  function handlePpSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const payload = {
      package_name: ppName, version: ppVersion || null, state: ppState,
      package_manager: ppManager, comment: ppComment || null, hold: ppHold,
    }
    if (ppEditingId != null) {
      ppUpdateMutation.mutate({ overrideId: ppEditingId, payload })
    } else {
      ppSaveMutation.mutate(payload)
    }
  }

  function handlePpDelete(packageName: string) {
    setConfirmState({
      open: true,
      title: "Delete Package Override",
      description: `Delete host package override for "${packageName}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostPackageOverrides?.find(o => o.package_name === packageName)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await ppDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  const [caName, setCaName] = useState("")
  const [caPem, setCaPem] = useState("")
  const [caComment, setCaComment] = useState("")
  const [caState, setCaState] = useState<"present" | "absent">("present")
  const [caEditingId, setCaEditingId] = useState<number | null>(null)
  const [caDeployConfirm, setCaDeployConfirm] = useState(false)
  const caSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/ca-certs`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-ca-certs", id], ["host-ca-cert-overrides", id]],
    onSuccess: () => setCaDialogOpen(false),
  })

  const caUpdateMutation = useApiMutation({
    mutationFn: ({ overrideId, payload }: { overrideId: number; payload: Record<string, unknown> }) =>
      apiFetch(`/api/hosts/${id}/ca-certs/${overrideId}`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-ca-certs", id], ["host-ca-cert-overrides", id]],
    onSuccess: () => setCaDialogOpen(false),
  })

  const caDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/ca-certs/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-ca-certs", id], ["host-ca-cert-overrides", id]],
  })

  const caDeployMutation = useApiMutation({
    mutationFn: () =>
      apiFetch(`/api/ca-certs/hosts/${id}/deploy`, { method: "POST" }),
    invalidateKeys: [["host-ca-cert-runs", id]],
    onSuccess: () => setCaDeployConfirm(false),
  })

  function openCaDialog() {
    setCaName("")
    setCaPem("")
    setCaComment("")
    setCaState("present")
    setCaEditingId(null)
    caSaveMutation.reset()
    caUpdateMutation.reset()
    setCaDialogOpen(true)
  }

  function openCaEditDialog(cert: EffectiveCACert) {
    const override = cert.source === "host"
      ? hostCACertOverrides?.find(o => o.fingerprint_sha256 === cert.fingerprint_sha256)
      : null
    setCaName(cert.name)
    setCaPem(cert.pem_content)
    setCaComment(override?.comment ?? "")
    setCaState(cert.state)
    setCaEditingId(override?.id ?? null)
    caSaveMutation.reset()
    caUpdateMutation.reset()
    setCaDialogOpen(true)
  }

  function closeCaDialog() {
    setCaDialogOpen(false)
    setCaEditingId(null)
  }

  function handleCaSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const payload = {
      name: caName,
      pem_content: caPem,
      state: caState,
      comment: caComment || null,
    }
    if (caEditingId != null) {
      caUpdateMutation.mutate({ overrideId: caEditingId, payload })
    } else {
      caSaveMutation.mutate(payload)
    }
  }

  function handleCaDelete(fingerprint: string, name: string) {
    setConfirmState({
      open: true,
      title: "Delete CA Certificate Override",
      description: `Delete host CA certificate override "${name}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostCACertOverrides?.find(o => o.fingerprint_sha256 === fingerprint)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await caDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  // Resolver override state
  const [resolverDialogOpen, setResolverDialogOpen] = useState(false)
  const [resolverType, setResolverType] = useState<"resolv_conf" | "systemd_resolved" | "networkmanager">("resolv_conf")
  const [resolverNameservers, setResolverNameservers] = useState<string[]>([])
  const [resolverNsInput, setResolverNsInput] = useState("")
  const [resolverSearchDomains, setResolverSearchDomains] = useState<string[]>([])
  const [resolverSdInput, setResolverSdInput] = useState("")
  const [resolverOptions, setResolverOptions] = useState<{ key: string; value: string }[]>([])
  const [resolverDnsOverTls, setResolverDnsOverTls] = useState(false)

  const resolverSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/resolver`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-resolver", id], ["host-resolver-override", id]],
    onSuccess: () => setResolverDialogOpen(false),
  })

  const resolverDeleteMutation = useApiMutation({
    mutationFn: () =>
      apiFetch(`/api/hosts/${id}/resolver`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-resolver", id], ["host-resolver-override", id]],
  })

  function openResolverEditDialog() {
    const source: EffectiveResolverConfig | undefined = effectiveResolver
    if (source) {
      setResolverType(source.resolver_type)
      setResolverNameservers([...source.nameservers])
      setResolverSearchDomains([...source.search_domains])
      setResolverOptions(Object.entries(source.options).map(([key, value]) => ({ key, value: String(value) })))
      setResolverDnsOverTls(source.dns_over_tls)
    } else {
      setResolverType("resolv_conf")
      setResolverNameservers([])
      setResolverSearchDomains([])
      setResolverOptions([])
      setResolverDnsOverTls(false)
    }
    setResolverNsInput("")
    setResolverSdInput("")
    resolverSaveMutation.reset()
    setResolverDialogOpen(true)
  }

  function handleResolverSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const optionsObj: Record<string, number | string> = {}
    for (const o of resolverOptions) {
      const k = o.key.trim()
      if (!k) continue
      const numVal = Number(o.value)
      optionsObj[k] = !isNaN(numVal) && o.value.trim() !== "" ? numVal : o.value
    }
    resolverSaveMutation.mutate({
      nameservers: resolverNameservers,
      search_domains: resolverSearchDomains,
      options: optionsObj,
      resolver_type: resolverType,
      dns_over_tls: resolverDnsOverTls,
    })
  }

  function handleResolverDeleteOverride() {
    setConfirmState({
      open: true,
      title: "Delete Resolver Override",
      description: "Remove this host's DNS resolver override? The host will revert to inheriting from the group.",
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await resolverDeleteMutation.mutateAsync(undefined as never) } finally { setConfirmState(null) }
      },
    })
  }

  function addResolverNameserver() {
    const val = resolverNsInput.trim()
    if (val && !resolverNameservers.includes(val)) {
      setResolverNameservers([...resolverNameservers, val])
      setResolverNsInput("")
    }
  }

  function addResolverSearchDomain() {
    const val = resolverSdInput.trim()
    if (val && !resolverSearchDomains.includes(val)) {
      setResolverSearchDomains([...resolverSearchDomains, val])
      setResolverSdInput("")
    }
  }

  function openCjDialog() {
    setCjName("")
    setCjUser("root")
    setCjSchedule("")
    setCjCommand("")
    setCjState("present")
    setCjPriority(100)
    setCjComment("")
    setCjEnvVars([])
    setCjEditingId(null)
    cjSaveMutation.reset()
    cjUpdateMutation.reset()
    setCjDialogOpen(true)
  }

  function openCjEditDialog(job: EffectiveCronJob) {
    const override = job.source === "host"
      ? hostCronOverrides?.find(o => o.name === job.name && o.user === job.user)
      : null
    setCjName(job.name)
    setCjUser(job.user)
    setCjSchedule(job.schedule)
    setCjCommand(job.command)
    setCjState(job.state)
    setCjPriority(job.priority)
    setCjComment(job.comment ?? "")
    setCjEnvVars(Object.entries(job.environment).map(([key, value]) => ({ key, value })))
    setCjEditingId(override?.id ?? null)
    cjSaveMutation.reset()
    cjUpdateMutation.reset()
    setCjDialogOpen(true)
  }

  function closeCjDialog() {
    setCjDialogOpen(false)
    setCjEditingId(null)
  }

  function handleCjSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const env: Record<string, string> = {}
    for (const v of cjEnvVars) { const k = v.key.trim(); if (k) env[k] = v.value }
    const payload = {
      name: cjName, user: cjUser, schedule: cjSchedule, command: cjCommand,
      state: cjState, priority: cjPriority, comment: cjComment || null, environment: env,
    }
    if (cjEditingId != null) {
      cjUpdateMutation.mutate({ overrideId: cjEditingId, payload })
    } else {
      cjSaveMutation.mutate(payload)
    }
  }

  function handleCjDelete(name: string, user: string) {
    setConfirmState({
      open: true,
      title: "Delete Cron Job Override",
      description: `Delete host cron job override for "${name}" (user: ${user})? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostCronOverrides?.find(o => o.name === name && o.user === user)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await cjDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  const [lgGroupname, setLgGroupname] = useState("")
  const [lgGid, setLgGid] = useState("")
  const [lgState, setLgState] = useState<"present" | "absent">("present")
  const [lgPriority, setLgPriority] = useState(100)
  const [lgEditingId, setLgEditingId] = useState<number | null>(null)
  const lgSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/linux-groups`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-linux-groups", id], ["host-linux-group-overrides", id]],
    onSuccess: () => setLgDialogOpen(false),
  })
  const lgUpdateMutation = useApiMutation({
    mutationFn: ({ overrideId, payload }: { overrideId: number; payload: Record<string, unknown> }) =>
      apiFetch(`/api/hosts/${id}/linux-groups/${overrideId}`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-linux-groups", id], ["host-linux-group-overrides", id]],
    onSuccess: () => setLgDialogOpen(false),
  })

  const lgDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/linux-groups/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-linux-groups", id], ["host-linux-group-overrides", id]],
  })

  function openLuDialog() {
    setLuEditingId(null)
    setLuUsername("")
    setLuUid("")
    setLuShell("/bin/bash")
    setLuHomeDir("")
    setLuState("present")
    setLuComment("")
    setLuSudoRule("")
    setLuAuthorizedKeys("")
    setLuSupplementaryGroups("")
    setLuPriority(100)
    luSaveMutation.reset()
    luUpdateMutation.reset()
    setLuDialogOpen(true)
  }

  function openLuEditDialog(override: LinuxUser) {
    setLuEditingId(override.id)
    setLuUsername(override.username)
    setLuUid(override.uid != null ? String(override.uid) : "")
    setLuShell(override.shell)
    setLuHomeDir(override.home_dir ?? "")
    setLuState(override.state)
    setLuComment(override.comment ?? "")
    setLuSudoRule(override.sudo_rule ?? "")
    setLuAuthorizedKeys(override.authorized_keys.join("\n"))
    setLuSupplementaryGroups(override.supplementary_groups.join(", "))
    setLuPriority(override.priority)
    luSaveMutation.reset()
    luUpdateMutation.reset()
    setLuDialogOpen(true)
  }

  function handleLuSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const payload = {
      username: luUsername, uid: luUid ? Number(luUid) : null, shell: luShell,
      home_dir: luHomeDir || null, state: luState, comment: luComment || null,
      sudo_rule: luSudoRule || null,
      authorized_keys: luAuthorizedKeys.split("\n").map((k) => k.trim()).filter(Boolean),
      supplementary_groups: luSupplementaryGroups.split(",").map((g) => g.trim()).filter(Boolean),
      priority: luPriority,
    }
    if (luEditingId != null) {
      luUpdateMutation.mutate({ overrideId: luEditingId, payload })
    } else {
      luSaveMutation.mutate(payload)
    }
  }

  function handleLuEdit(username: string) {
    const override = hostLinuxUserOverrides?.find(o => o.username === username)
    if (override) openLuEditDialog(override)
  }

  function handleLuDelete(username: string) {
    setConfirmState({
      open: true,
      title: "Delete User Override",
      description: `Delete host user override for "${username}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostLinuxUserOverrides?.find(o => o.username === username)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await luDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  function openLgDialog() {
    setLgEditingId(null)
    setLgGroupname("")
    setLgGid("")
    setLgState("present")
    setLgPriority(100)
    lgSaveMutation.reset()
    lgUpdateMutation.reset()
    setLgDialogOpen(true)
  }

  function openLgEditDialog(override: LinuxGroup) {
    setLgEditingId(override.id)
    setLgGroupname(override.groupname)
    setLgGid(override.gid != null ? String(override.gid) : "")
    setLgState(override.state)
    setLgPriority(override.priority)
    lgSaveMutation.reset()
    lgUpdateMutation.reset()
    setLgDialogOpen(true)
  }

  function handleLgSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const payload = {
      groupname: lgGroupname, gid: lgGid ? Number(lgGid) : null, state: lgState, priority: lgPriority,
    }
    if (lgEditingId != null) {
      lgUpdateMutation.mutate({ overrideId: lgEditingId, payload })
    } else {
      lgSaveMutation.mutate(payload)
    }
  }

  function handleLgEdit(groupname: string) {
    const override = hostLinuxGroupOverrides?.find(o => o.groupname === groupname)
    if (override) openLgEditDialog(override)
  }

  function manageCollectedUser(collected: { username: string; uid?: number | null; home?: string | null; shell?: string | null }) {
    const existing = hostLinuxUserOverrides?.find(o => o.username === collected.username)
    if (existing) {
      openLuEditDialog(existing)
      return
    }
    setLuEditingId(null)
    setLuUsername(collected.username)
    setLuUid(collected.uid != null ? String(collected.uid) : "")
    setLuShell(collected.shell || "/bin/bash")
    setLuHomeDir(collected.home ?? "")
    setLuState("present")
    setLuComment("")
    setLuSudoRule("")
    setLuAuthorizedKeys("")
    setLuSupplementaryGroups("")
    setLuPriority(100)
    luSaveMutation.reset()
    luUpdateMutation.reset()
    setLuDialogOpen(true)
  }

  function manageCollectedGroup(collected: { groupname: string; gid?: number | null }) {
    const existing = hostLinuxGroupOverrides?.find(o => o.groupname === collected.groupname)
    if (existing) {
      openLgEditDialog(existing)
      return
    }
    setLgEditingId(null)
    setLgGroupname(collected.groupname)
    setLgGid(collected.gid != null ? String(collected.gid) : "")
    setLgState("present")
    setLgPriority(100)
    lgSaveMutation.reset()
    lgUpdateMutation.reset()
    setLgDialogOpen(true)
  }

  function handleLgDelete(groupname: string) {
    setConfirmState({
      open: true,
      title: "Delete Group Override",
      description: `Delete host group override for "${groupname}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostLinuxGroupOverrides?.find(o => o.groupname === groupname)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await lgDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  function openHostsDialog() {
    setHostsMode("literal")
    setHostsRefId(null)
    setHostsIp("")
    setHostsHostname("")
    setHostsAliases("")
    setHostsComment("")
    setHostsPriority(100)
    setHostsEditingId(null)
    hostsSaveMutation.reset()
    hostsUpdateMutation.reset()
    setHostsDialogOpen(true)
  }

  function openHostsEditDialog(entry: EffectiveHostsEntry) {
    const override = entry.source === "host"
      ? hostHostsOverrides?.find((o) => o.hostname === entry.hostname && o.ip_address === entry.ip_address)
      : null
    setHostsMode(override?.host_ref_id != null ? "host" : "literal")
    setHostsRefId(override?.host_ref_id ?? null)
    setHostsIp(entry.ip_address)
    setHostsHostname(entry.hostname)
    setHostsAliases(entry.aliases.join(", "))
    setHostsComment(entry.comment ?? "")
    setHostsPriority(override?.priority ?? 100)
    setHostsEditingId(override?.id ?? null)
    hostsSaveMutation.reset()
    hostsUpdateMutation.reset()
    setHostsDialogOpen(true)
  }

  function closeHostsDialog() {
    setHostsDialogOpen(false)
    setHostsEditingId(null)
  }

  function handleHostsSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const isRef = hostsMode === "host"
    const payload: Record<string, unknown> = {
      ip_address: isRef ? null : hostsIp,
      hostname: isRef ? null : hostsHostname,
      host_ref_id: isRef ? hostsRefId : null,
      aliases: hostsAliases.split(",").map((a) => a.trim()).filter(Boolean),
      comment: hostsComment || null, priority: hostsPriority,
    }
    if (hostsEditingId != null) {
      hostsUpdateMutation.mutate({ entryId: hostsEditingId, payload })
    } else {
      hostsSaveMutation.mutate(payload)
    }
  }

  function handleHostsEntryDelete(entry: HostsEntry) {
    setConfirmState({
      open: true,
      title: "Delete Hosts Entry",
      description: `Delete hosts entry "${entry.ip_address} ${entry.hostname}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await hostsDeleteMutation.mutateAsync(entry.id) } finally { setConfirmState(null) }
      },
    })
  }

  async function fetchHostsPreview() {
    setHostsPreviewLoading(true)
    setHostsPreviewError(null)
    try {
      const res = await fetch(`${API_BASE}/api/hosts/${id}/hosts-file-preview`, {
        credentials: "include",
      })
      if (!res.ok) throw new Error("Failed to load preview")
      const text = await res.text()
      setHostsPreview(text)
    } catch (err) {
      setHostsPreviewError(err instanceof Error ? err.message : "Failed to load preview")
    } finally {
      setHostsPreviewLoading(false)
    }
  }

  const [svcName, setSvcName] = useState("")
  const [svcEditorMode, setSvcEditorMode] = useState<"add" | "edit">("add")
  const [svcEditRuleId, setSvcEditRuleId] = useState<number | null>(null)
  const [svcDeployMode, setSvcDeployMode] = useState<"full" | "override">("override")
  const [svcUnitContent, setSvcUnitContent] = useState("")
  const [svcState, setSvcState] = useState<"running" | "stopped">("running")
  const [svcEnabled, setSvcEnabled] = useState(true)
  const [svcOriginalUnit, setSvcOriginalUnit] = useState<string | null>(null)
  const [svcOriginalLoading, setSvcOriginalLoading] = useState(false)
  const [svcOriginalAttempted, setSvcOriginalAttempted] = useState(false)
  const svcSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) => {
      if (svcEditorMode === "edit" && svcEditRuleId !== null) {
        return apiFetch(`/api/hosts/${id}/services/${svcEditRuleId}`, { method: "PUT", body: JSON.stringify(payload) })
      }
      return apiFetch(`/api/hosts/${id}/services`, { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["host-effective-services", id], ["host-service-overrides", id]],
    onSuccess: () => setSvcDialogOpen(false),
  })

  const svcDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/services/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-services", id], ["host-service-overrides", id]],
  })

  // Firewall rule override state
  const [fwAction, setFwAction] = useState("allow")
  const [fwProtocol, setFwProtocol] = useState("tcp")
  const [fwDirection, setFwDirection] = useState("input")
  const [fwSourceMode, setFwSourceMode] = useState<"cidr" | "host">("cidr")
  const [fwDestMode, setFwDestMode] = useState<"cidr" | "host">("cidr")
  const [fwSourceCidr, setFwSourceCidr] = useState("")
  const [fwDestCidr, setFwDestCidr] = useState("")
  const [fwSourceHostId, setFwSourceHostId] = useState<number | null>(null)
  const [fwDestHostId, setFwDestHostId] = useState<number | null>(null)
  const [fwPortStart, setFwPortStart] = useState("")
  const [fwPortEnd, setFwPortEnd] = useState("")
  const [fwPriority, setFwPriority] = useState("0")
  const [fwComment, setFwComment] = useState("")
  const [fwEditingId, setFwEditingId] = useState<number | null>(null)

  const fwCreateMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/firewall-rules`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-rules", id], ["host-firewall-overrides", id]],
    onSuccess: () => setFwDialogOpen(false),
  })

  const fwUpdateMutation = useApiMutation({
    mutationFn: ({ ruleId, payload }: { ruleId: number; payload: Record<string, unknown> }) =>
      apiFetch(`/api/hosts/${id}/firewall-rules/${ruleId}`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-rules", id], ["host-firewall-overrides", id]],
    onSuccess: () => setFwDialogOpen(false),
  })

  const fwDeleteMutation = useApiMutation({
    mutationFn: (ruleId: number) =>
      apiFetch(`/api/hosts/${id}/firewall-rules/${ruleId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-rules", id], ["host-firewall-overrides", id]],
  })

  function openFwAddDialog() {
    setFwAction("allow")
    setFwProtocol("tcp")
    setFwDirection("input")
    setFwSourceMode("cidr")
    setFwDestMode("cidr")
    setFwSourceCidr("")
    setFwDestCidr("")
    setFwSourceHostId(null)
    setFwDestHostId(null)
    setFwPortStart("")
    setFwPortEnd("")
    setFwPriority("0")
    setFwComment("")
    setFwEditingId(null)
    fwCreateMutation.reset()
    fwUpdateMutation.reset()
    setFwDialogOpen(true)
  }

  function openFwEditDialog(rule: EffectiveFirewallRule) {
    setFwAction(rule.action)
    setFwProtocol(rule.protocol)
    setFwDirection(rule.direction)
    setFwSourceMode(rule.source_host_id != null ? "host" : "cidr")
    setFwDestMode(rule.destination_host_id != null ? "host" : "cidr")
    setFwSourceCidr(rule.source_cidr ?? "")
    setFwDestCidr(rule.destination_cidr ?? "")
    setFwSourceHostId(rule.source_host_id ?? null)
    setFwDestHostId(rule.destination_host_id ?? null)
    setFwPortStart(rule.port_start != null ? String(rule.port_start) : "")
    setFwPortEnd(rule.port_end != null ? String(rule.port_end) : "")
    setFwPriority(String(rule.priority))
    setFwComment(rule.comment ?? "")
    setFwEditingId(rule.source === "host" ? rule.rule_id : null)
    fwCreateMutation.reset()
    fwUpdateMutation.reset()
    setFwDialogOpen(true)
  }

  function closeFwDialog() {
    setFwDialogOpen(false)
    setFwEditingId(null)
  }

  function handleFwSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const payload = {
      action: fwAction,
      protocol: fwProtocol,
      direction: fwDirection,
      source_cidr: fwSourceMode === "cidr" ? (fwSourceCidr || null) : null,
      source_host_id: fwSourceMode === "host" ? fwSourceHostId : null,
      destination_cidr: fwDestMode === "cidr" ? (fwDestCidr || null) : null,
      destination_host_id: fwDestMode === "host" ? fwDestHostId : null,
      port_start: fwPortStart ? Number(fwPortStart) : null,
      port_end: fwPortEnd ? Number(fwPortEnd) : null,
      priority: Number(fwPriority),
      comment: fwComment || null,
    }
    if (fwEditingId != null) {
      fwUpdateMutation.mutate({ ruleId: fwEditingId, payload })
    } else {
      fwCreateMutation.mutate(payload)
    }
  }

  function handleFwDelete(ruleId: number) {
    setConfirmState({
      open: true,
      title: "Delete Firewall Rule Override",
      description: "This will remove the host-level firewall rule override. The change will take effect on the next sync.",
      variant: "destructive",
      confirmLabel: "Delete",
      action: async () => {
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await fwDeleteMutation.mutateAsync(ruleId) } finally { setConfirmState(null) }
      },
    })
  }

  // Live inventory state
  const [inventoryLoaded, setInventoryLoaded] = useState(false)
  const [inventoryLoading, setInventoryLoading] = useState(false)
  const [inventory, setInventory] = useState<LiveService[]>([])
  const [inventoryError, setInventoryError] = useState<string | null>(null)
  const [inventoryFilter, setInventoryFilter] = useState("")
  const [inventoryHideSystem, setInventoryHideSystem] = useState(true)
  const [pendingAction, setPendingAction] = useState<{ service: string; action: string } | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [actionResult, setActionResult] = useState<{ success: boolean; message: string } | null>(null)
  const [protectedTarget, setProtectedTarget] = useState<{ service: string; action: string } | null>(null)

  const [inventoryHideManaged, setInventoryHideManaged] = useState(true)

  function openSvcDialog() {
    setSvcName("")
    setSvcEditorMode("add")
    setSvcEditRuleId(null)
    setSvcDeployMode("override")
    setSvcUnitContent("")
    setSvcState("running")
    setSvcEnabled(true)
    setSvcOriginalUnit(null)
    setSvcOriginalAttempted(false)
    svcSaveMutation.reset()
    setSvcDialogOpen(true)
  }

  async function openSvcEditFromEffective(svc: EffectiveService) {
    setSvcName(svc.service_name)
    setSvcDeployMode(svc.deploy_mode)
    setSvcUnitContent(svc.unit_content ?? "")
    setSvcState(svc.state === "stopped" ? "stopped" : "running")
    setSvcEnabled(svc.enabled)
    setSvcOriginalUnit(null)
    setSvcOriginalLoading(true)
    setSvcOriginalAttempted(true)
    svcSaveMutation.reset()

    if (svc.source === "host") {
      const override = hostOverrides?.find(o => o.service_name === svc.service_name)
      setSvcEditorMode("edit")
      setSvcEditRuleId(override?.id ?? null)
    } else {
      setSvcEditorMode("add")
      setSvcEditRuleId(null)
    }

    setSvcDialogOpen(true)

    const unitName = svc.service_name.endsWith(".service") ? svc.service_name : `${svc.service_name}.service`
    try {
      const res = await apiFetch<{ content: string }>(`/api/services/hosts/${id}/unit-file/${unitName}`)
      setSvcOriginalUnit(res.content)
    } catch {
      setSvcOriginalUnit(null)
    } finally {
      setSvcOriginalLoading(false)
    }
  }

  async function openSvcEdit(svc: LiveService) {
    const serviceName = svc.unit.replace(/\.service$/, "")
    setSvcName(serviceName)
    setSvcOriginalUnit(null)
    setSvcOriginalLoading(true)
    setSvcOriginalAttempted(true)
    svcSaveMutation.reset()

    if (svc.is_managed) {
      setSvcEditorMode("edit")
      const matchingEffective = effectiveServices?.find(
        (es) => es.service_name === svc.unit || es.service_name === serviceName
      )
      const matchingOverride = hostOverrides?.find(
        (o) => o.service_name === svc.unit || o.service_name === serviceName
      )
      setSvcEditRuleId(matchingOverride?.id ?? null)
      setSvcDeployMode(matchingEffective?.deploy_mode ?? "override")
      setSvcUnitContent(matchingEffective?.unit_content ?? "")
      setSvcState(matchingEffective?.state === "stopped" ? "stopped" : "running")
      setSvcEnabled(matchingEffective?.enabled ?? true)
    } else {
      setSvcEditorMode("add")
      setSvcEditRuleId(null)
      setSvcDeployMode("override")
      setSvcUnitContent("")
      setSvcState("running")
      setSvcEnabled(true)
    }

    setSvcDialogOpen(true)

    try {
      const res = await apiFetch<{ content: string }>(`/api/services/hosts/${id}/unit-file/${svc.unit}`)
      setSvcOriginalUnit(res.content)
    } catch {
      setSvcOriginalUnit(null)
    } finally {
      setSvcOriginalLoading(false)
    }
  }

  function handleSvcSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const base = {
      deploy_mode: svcDeployMode,
      unit_content: svcUnitContent || null,
      state: svcState,
      enabled: svcEnabled,
    }
    if (svcEditorMode === "edit") {
      svcSaveMutation.mutate(base)
    } else {
      svcSaveMutation.mutate({ service_name: svcName, ...base })
    }
  }

  function handleSvcDelete(serviceName: string) {
    setConfirmState({
      open: true,
      title: "Delete Service Override",
      description: `Delete host override for "${serviceName}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostOverrides?.find(o => o.service_name === serviceName)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await svcDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  async function loadInventory() {
    setInventoryLoading(true)
    setInventoryError(null)
    setActionResult(null)
    try {
      const data = await apiFetch<LiveService[]>(`/api/services/hosts/${id}/inventory`)
      setInventory(data)
      setInventoryLoaded(true)
    } catch (err) {
      setInventoryError(err instanceof Error ? err.message : "Failed to load inventory")
    } finally {
      setInventoryLoading(false)
    }
  }

  async function executeCommand(serviceName: string, action: string) {
    setActionLoading(true)
    setActionResult(null)
    setPendingAction({ service: serviceName, action })
    try {
      const result = await apiFetch<ServiceCommandResult>(`/api/services/hosts/${id}/command`, {
        method: "POST",
        body: JSON.stringify({ service_name: serviceName, action }),
      })
      if (result.success) {
        setActionResult({ success: true, message: `${action} ${serviceName}: success` })
        await loadInventory()
      } else {
        setActionResult({ success: false, message: `${action} ${serviceName} failed: ${result.stderr}` })
      }
    } catch (err) {
      setActionResult({ success: false, message: err instanceof Error ? err.message : "Command failed" })
    } finally {
      setActionLoading(false)
      setPendingAction(null)
    }
  }

  function handleActionClick(service: LiveService, action: string) {
    if (service.is_protected) {
      setProtectedTarget({ service: service.unit, action })
      setProtectedConfirmOpen(true)
    } else {
      const actionLabel = action.charAt(0).toUpperCase() + action.slice(1)
      setConfirmState({
        open: true,
        title: `${actionLabel} Service`,
        description: `${actionLabel} ${service.unit}?`,
        confirmLabel: actionLabel,
        variant: action === "stop" || action === "restart" ? "destructive" : "default",
        action: async () => {
          setConfirmState(null)
          await executeCommand(service.unit, action)
        },
      })
    }
  }

  const filteredInventory = inventory.filter(
    (svc) =>
      (!inventoryHideSystem || !svc.is_system) &&
      (!inventoryHideManaged || !svc.is_managed) &&
      (svc.unit.toLowerCase().includes(inventoryFilter.toLowerCase()) ||
      svc.description.toLowerCase().includes(inventoryFilter.toLowerCase()))
  )

  useEffect(() => {
    if (editOpen && host) {
      setEditHostname(host.hostname)
      setEditIp(host.ip_address)
      setEditSshPort(host.ssh_port)
      setEditSshUser(host.ssh_user)
      setEditSshKeyId(host.ssh_key_id)
      setEditGroups(host.group_ids ?? [])
      editMutation.reset()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editOpen, host])

  function handleEditSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    editMutation.mutate({
      hostname: editHostname, ip_address: editIp, ssh_port: editSshPort,
      ssh_user: editSshUser, ssh_key_id: editSshKeyId, group_ids: editGroups,
    })
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Hosts", href: "/hosts" }, { label: host?.hostname ?? "Host" }]} />
      {/* Host Info */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">
            {hostLoading ? "Loading…" : host?.hostname ?? `Host #${id}`}
          </h1>
          <p className="text-slate-400 text-sm">Host details and configuration management</p>
        </div>
        {host && (
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!host.ssh_key_id}
              title={host.ssh_key_id ? "Open terminal" : "No SSH key assigned"}
              onClick={() => setTerminalOpen(true)}
            >
              <TerminalIcon className="w-4 h-4 mr-1" />
              Terminal
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={refreshing}
              title="Refresh data for current tab"
              onClick={async () => {
                setRefreshing(true)
                for (const key of tabQueryKeys[activeTab] ?? []) {
                  await queryClient.invalidateQueries({ queryKey: key })
                }
                await queryClient.invalidateQueries({ queryKey: ["host", String(id)] })
                setRefreshing(false)
              }}
            >
              <RefreshCwIcon className={`w-4 h-4 mr-1 ${refreshing ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            {activeTab === "overview" && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={collecting || !host.ssh_key_id}
                  title={host.ssh_key_id ? "Collect current state for all modules" : "No SSH key assigned"}
                  onClick={async () => {
                    setCollecting(true)
                    try {
                      await apiFetch(`/api/hosts/${id}/collect-state`, { method: "POST" })
                      await queryClient.invalidateQueries({ queryKey: ["host-current-state", id] })
                      await queryClient.invalidateQueries({ queryKey: ["host", id] })
                    } catch { /* ignore */ }
                    setCollecting(false)
                  }}
                >
                  <RefreshCwIcon className={`w-4 h-4 mr-1 ${collecting ? "animate-spin" : ""}`} />
                  {collecting ? "Collecting..." : "Collect All"}
                </Button>
                <Button
                  size="sm"
                  disabled={!host.ssh_key_id || !!syncPreview}
                  title={host.ssh_key_id ? "Preview and sync all modules to this host" : "No SSH key assigned"}
                  onClick={openSyncAllPreview}
                >
                  <ArrowUpFromLineIcon className={`w-4 h-4 mr-1`} />
                  Sync All
                </Button>
                {!host.ssh_host_key_entry && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-amber-500 text-amber-400 hover:bg-amber-950"
                    disabled={trustingKey}
                    title="Accept whatever SSH host key this host presents on next connect (TOFU)"
                    onClick={() => setTrustKeyOpen(true)}
                  >
                    <AlertTriangleIcon className="w-4 h-4 mr-1" />
                    Trust new host key
                  </Button>
                )}
              </>
            )}
          <Dialog open={editOpen} onOpenChange={setEditOpen}>
            <DialogTrigger render={<Button variant="outline" size="sm" />}>
              Edit
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Edit Host</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleEditSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="edit-hostname">Hostname</Label>
                  <Input
                    id="edit-hostname"
                    type="text"
                    value={editHostname}
                    onChange={(e) => setEditHostname(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="edit-ip">IP Address</Label>
                  <Input
                    id="edit-ip"
                    type="text"
                    value={editIp}
                    onChange={(e) => setEditIp(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="edit-ssh-port">SSH Port</Label>
                  <Input
                    id="edit-ssh-port"
                    type="number"
                    value={editSshPort}
                    onChange={(e) => setEditSshPort(Number(e.target.value))}
                    required
                    min={1}
                    max={65535}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="edit-ssh-user">SSH User</Label>
                  <Input
                    id="edit-ssh-user"
                    type="text"
                    value={editSshUser}
                    onChange={(e) => setEditSshUser(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="edit-ssh-key">SSH Key</Label>
                  <select
                    id="edit-ssh-key"
                    value={editSshKeyId ?? ""}
                    onChange={(e) => setEditSshKeyId(e.target.value ? Number(e.target.value) : null)}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="">No SSH key</option>
                    {sshKeys?.map((key) => (
                      <option key={key.id} value={key.id}>
                        {key.name}{key.is_default ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                </div>

                {groups && groups.length > 0 && (
                  <GroupMultiSelect
                    groups={groups}
                    selected={editGroups}
                    onChange={setEditGroups}
                  />
                )}

                {editMutation.error && (
                  <p className="text-sm text-red-400">{editMutation.error.message}</p>
                )}

                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setEditOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={editMutation.isPending}>
                    {editMutation.isPending ? "Saving..." : "Save Changes"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
          </div>
        )}
      </div>

      <div role="tablist" className="flex gap-1 border-b border-slate-700 flex-wrap">
        <button
          role="tab"
          aria-selected={activeTab === "overview"}
          onClick={() => setActiveTab("overview")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "overview"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Overview
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "groups"}
          onClick={() => setActiveTab("groups")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "groups"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Groups
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "rules"}
          onClick={() => setActiveTab("rules")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "rules"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Rules
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "services"}
          onClick={() => setActiveTab("services")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "services"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Services
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "hosts-file"}
          onClick={() => setActiveTab("hosts-file")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "hosts-file"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Hosts File
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "users"}
          onClick={() => setActiveTab("users")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "users"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Users
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "cron-jobs"}
          onClick={() => setActiveTab("cron-jobs")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "cron-jobs"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Cron Jobs
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "packages"}
          onClick={() => setActiveTab("packages")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "packages"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Packages
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "ca-certs"}
          onClick={() => setActiveTab("ca-certs")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "ca-certs"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          CA Certs
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "dns"}
          onClick={() => setActiveTab("dns")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "dns"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          DNS Resolver
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "actions"}
          onClick={() => setActiveTab("actions")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "actions"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Actions
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "schedules"}
          onClick={() => setActiveTab("schedules")}
          className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
            activeTab === "schedules"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Schedules
        </button>
      </div>

      {(() => {
        const errors = currentStateQuery.data?.filter(m => m.error_message) ?? []
        if (!errors.length) return null
        const byMessage = new Map<string, string[]>()
        for (const m of errors) {
          const msg = m.error_message!
          const existing = byMessage.get(msg)
          if (existing) {
            existing.push(m.module_type)
          } else {
            byMessage.set(msg, [m.module_type])
          }
        }
        const detail =
          byMessage.size === 1
            ? byMessage.keys().next().value
            : [...byMessage.entries()]
                .map(([msg, mods]) => `${msg} (${mods.join(", ")})`)
                .join("; ")
        return (
          <div className="rounded-lg border border-red-700/50 bg-red-950/20 px-4 py-3 flex items-center gap-2">
            <XCircleIcon className="w-4 h-4 text-red-400 shrink-0" />
            <span className="text-red-400 text-sm">
              Sync check encountered errors.{detail ? ` ${detail}` : ""}
            </span>
          </div>
        )
      })()}

      {activeTab === "overview" && (
        <>
          {hostError && (
            <div className="text-red-400">Failed to load host details</div>
          )}

          {host && (
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-0">
              <HostMetricsSection hostId={Number(id)} />
              <InfoRow label="Hostname">{host.hostname}</InfoRow>
              <InfoRow label="IP Address">
                <span className="font-mono">{host.ip_address}</span>
              </InfoRow>
              <InfoRow label="SSH Port">
                <span className="font-mono">{host.ssh_port}</span>
              </InfoRow>
              <InfoRow label="Management IP">
                <span className="font-mono">{host.labdog_source_ip ?? "Not yet detected"}</span>
              </InfoRow>
              <InfoRow label="Firewall Backend">
                <FirewallBadge backend={host.firewall_backend} />
              </InfoRow>
              <InfoRow label="Sync Status">
                <SyncStatusBadge status={host.sync_status} />
              </InfoRow>
              <InfoRow label="Last Sync">
                {host.last_sync_at
                  ? new Date(host.last_sync_at).toLocaleString()
                  : "Never"}
              </InfoRow>
              <InfoRow label="Last Drift Check">
                {host.last_drift_check_at
                  ? new Date(host.last_drift_check_at).toLocaleString()
                  : "Never"}
              </InfoRow>
              <InfoRow label="OS">
                {host.os_pretty_name ?? (
                  <span className="text-slate-500 italic text-sm">Not collected</span>
                )}
              </InfoRow>
              {host.kernel_version && (
                <InfoRow label="Kernel">
                  <span className="font-mono text-sm">{host.kernel_version}</span>
                </InfoRow>
              )}
              {host.default_nic && (
                <InfoRow label="Default NIC">
                  <span className="font-mono text-sm">{host.default_nic}</span>
                </InfoRow>
              )}
              <InfoRow label="Drift Monitoring">
                <Badge
                  variant={host.drift_check_enabled ? "default" : "outline"}
                  className={host.drift_check_enabled ? "bg-green-700 text-white cursor-pointer" : "cursor-pointer"}
                  render={
                    <button
                      type="button"
                      onClick={async () => {
                        await apiFetch(`/api/hosts/${id}`, {
                          method: "PUT",
                          body: JSON.stringify({ drift_check_enabled: !host.drift_check_enabled }),
                        })
                        await queryClient.invalidateQueries({ queryKey: ["host", id] })
                      }}
                    />
                  }
                >
                  {host.drift_check_enabled ? "Enabled" : "Disabled"}
                </Badge>
              </InfoRow>
            </div>
          )}
          {host && <ProxmoxVMSection hostId={id} queryClient={queryClient} />}
          {host && <SyncStatusMessage host={host} modules={currentStateQuery.data} />}
            </>
      )}

      {activeTab === "groups" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Group Memberships</h2>
              <p className="text-slate-400 text-sm mt-1">
                Groups this host belongs to. Rules, services, and other configurations are inherited from these groups.
              </p>
            </div>
            <Button size="sm" onClick={() => { setAddGroupOpen(true); setAddGroupSelected(new Set()); setAddGroupSearch("") }}>
              Add to Group
            </Button>
          </div>

          {host && groups && (() => {
            const memberGroups = groups.filter(g => (host.group_ids ?? []).includes(g.id))
              .sort((a, b) => a.priority - b.priority)
            return memberGroups.length === 0 ? (
              <div className="text-center py-12 text-slate-400">
                This host is not a member of any groups.
              </div>
            ) : (
              <>
                {selectedGroupIds.size > 0 && (
                  <div className="flex items-center gap-3 px-4 py-2 bg-slate-800 rounded-lg border border-slate-700">
                    <span className="text-sm text-slate-300">{selectedGroupIds.size} selected</span>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => setBulkRemoveGroupConfirm(true)}
                      disabled={groupMembershipMutation.isPending}
                    >
                      Remove Selected
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setSelectedGroupIds(new Set())}>
                      Clear
                    </Button>
                  </div>
                )}
                <DataTable<import("@/lib/types").HostGroup>
                  tableId="host-member-groups"
                  data={memberGroups}
                  getRowKey={(g) => g.id}
                  emptyMessage="This host is not a member of any groups."
                  columns={[
                    {
                      key: "select",
                      label: "",
                      cell: (g) => (
                        <input
                          type="checkbox"
                          checked={selectedGroupIds.has(g.id)}
                          onChange={() => {
                            setSelectedGroupIds(prev => {
                              const next = new Set(prev)
                              if (next.has(g.id)) next.delete(g.id)
                              else next.add(g.id)
                              return next
                            })
                          }}
                          className="rounded border-slate-600"
                          aria-label={`Select ${g.name}`}
                        />
                      ),
                      defaultWidth: 40,
                      resizable: false,
                      sortable: false,
                    },
                    { key: "name", label: "Name", accessor: (g) => g.name, cell: (g) => <Link href={`/groups/${g.id}`} className="text-blue-400 hover:underline">{g.name}</Link>, defaultWidth: 180, filter: { type: "text" } },
                    { key: "category", label: "Category", accessor: (g) => g.category ?? "", cell: (g) => <span className="text-slate-300">{g.category ?? "\u2014"}</span>, defaultWidth: 140, filter: { type: "enum", from: "accessor" } },
                    { key: "priority", label: "Priority", accessor: (g) => g.priority, cell: (g) => <span className="text-slate-300">{g.priority}</span>, defaultWidth: 90 },
                    { key: "description", label: "Description", accessor: (g) => g.description ?? "", cell: (g) => <span className="text-slate-400">{g.description ?? "\u2014"}</span>, defaultWidth: 220 },
                    {
                      key: "actions",
                      label: "Actions",
                      cell: (g) => (
                        <Button variant="ghost" size="sm" className="text-red-400 hover:text-red-300" onClick={() => setRemoveGroupConfirm(g.id)}>
                          Remove
                        </Button>
                      ),
                      defaultWidth: 100,
                      resizable: false,
                      sortable: false,
                    },
                  ]}
                />
              </>
            )
          })()}

          {/* Add to Group Dialog */}
          <Dialog open={addGroupOpen} onOpenChange={setAddGroupOpen}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Add to Group</DialogTitle>
              </DialogHeader>
              <div className="mt-2 space-y-3">
                <Input
                  placeholder="Search by name or category..."
                  value={addGroupSearch}
                  onChange={(e) => setAddGroupSearch(e.target.value)}
                />

                {(() => {
                  const currentIds = host?.group_ids ?? []
                  const available = (groups ?? []).filter(g => !currentIds.includes(g.id))
                  const q = addGroupSearch.toLowerCase()
                  const filtered = q
                    ? available.filter(g => g.name.toLowerCase().includes(q) || (g.category ?? "").toLowerCase().includes(q))
                    : available

                  return filtered.length === 0 ? (
                    <p className="text-slate-400 text-sm py-4 text-center">
                      {addGroupSearch ? "No matching groups found." : "This host already belongs to all groups."}
                    </p>
                  ) : (
                    <div className="max-h-[360px] overflow-y-auto">
                      <DataTable<import("@/lib/types").HostGroup>
                        tableId="host-add-groups-picker"
                        data={filtered}
                        getRowKey={(g) => g.id}
                        emptyMessage="No groups available."
                        onRowClick={(g) => {
                          setAddGroupSelected(prev => {
                            const next = new Set(prev)
                            if (next.has(g.id)) next.delete(g.id)
                            else next.add(g.id)
                            return next
                          })
                        }}
                        rowClassName={() => "cursor-pointer hover:bg-slate-800"}
                        columns={[
                          {
                            key: "select",
                            label: "",
                            header: (() => {
                              const visibleIds = filtered.map(g => g.id)
                              const selectedVisible = visibleIds.filter(gid => addGroupSelected.has(gid)).length
                              const allChecked = visibleIds.length > 0 && selectedVisible === visibleIds.length
                              const indeterminate = selectedVisible > 0 && !allChecked
                              return (
                                <input
                                  type="checkbox"
                                  checked={allChecked}
                                  ref={(el) => { if (el) el.indeterminate = indeterminate }}
                                  onChange={() => {
                                    if (allChecked) setAddGroupSelected(new Set())
                                    else setAddGroupSelected(new Set(visibleIds))
                                  }}
                                  onClick={(e) => e.stopPropagation()}
                                  className="rounded border-slate-600"
                                  aria-label="Select all visible groups"
                                />
                              )
                            })(),
                            cell: (g) => (
                              <input
                                type="checkbox"
                                checked={addGroupSelected.has(g.id)}
                                onChange={(e) => e.stopPropagation()}
                                className="rounded border-slate-600"
                              />
                            ),
                            defaultWidth: 40,
                            resizable: false,
                            sortable: false,
                          },
                          { key: "name", label: "Name", accessor: (g) => g.name, cell: (g) => <span className="font-medium text-white">{g.name}</span>, defaultWidth: 180, filter: { type: "text" } },
                          { key: "category", label: "Category", accessor: (g) => g.category ?? "", cell: (g) => <span className="text-slate-300">{g.category ?? "\u2014"}</span>, defaultWidth: 140, filter: { type: "enum", from: "accessor" } },
                          { key: "priority", label: "Priority", accessor: (g) => g.priority, cell: (g) => <span className="text-slate-300">{g.priority}</span>, defaultWidth: 90 },
                        ]}
                      />
                    </div>
                  )
                })()}

                <div className="flex items-center justify-between pt-2">
                  <span className="text-slate-400 text-sm">
                    {addGroupSelected.size} group{addGroupSelected.size !== 1 ? "s" : ""} selected
                  </span>
                  <Button
                    disabled={addGroupSelected.size === 0 || groupMembershipMutation.isPending}
                    onClick={() => {
                      const currentIds = host?.group_ids ?? []
                      groupMembershipMutation.mutate(
                        { group_ids: [...currentIds, ...Array.from(addGroupSelected)] },
                        {
                          onSuccess: () => {
                            setAddGroupOpen(false)
                            setAddGroupSelected(new Set())
                            setAddGroupSearch("")
                          },
                        }
                      )
                    }}
                  >
                    {groupMembershipMutation.isPending ? "Adding..." : "Add to Group"}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>

          <ConfirmDialog
            open={removeGroupConfirm !== null}
            onOpenChange={(open) => { if (!open) setRemoveGroupConfirm(null) }}
            title="Remove from Group"
            description={`Remove this host from "${groups?.find(g => g.id === removeGroupConfirm)?.name ?? ""}"? The host will no longer inherit rules and configurations from this group.`}
            confirmLabel="Remove"
            variant="destructive"
            loading={groupMembershipMutation.isPending}
            onConfirm={() => {
              const currentIds = host?.group_ids ?? []
              groupMembershipMutation.mutate(
                { group_ids: currentIds.filter(gid => gid !== removeGroupConfirm) },
                { onSuccess: () => setRemoveGroupConfirm(null) }
              )
            }}
          />

          {(() => {
            const selectedNames = groups
              ?.filter(g => selectedGroupIds.has(g.id))
              .map(g => g.name) ?? []
            const nameList = selectedNames.length <= 5
              ? selectedNames.join(", ")
              : `${selectedNames.slice(0, 5).join(", ")} and ${selectedNames.length - 5} more`
            return (
              <ConfirmDialog
                open={bulkRemoveGroupConfirm}
                onOpenChange={(open) => { if (!open) setBulkRemoveGroupConfirm(false) }}
                title={`Remove ${selectedGroupIds.size} ${selectedGroupIds.size === 1 ? "group" : "groups"} from this host?`}
                description={`The host will no longer inherit rules and configurations from: ${nameList}.`}
                confirmLabel="Remove All"
                variant="destructive"
                loading={groupMembershipMutation.isPending}
                onConfirm={() => {
                  const currentIds = host?.group_ids ?? []
                  groupMembershipMutation.mutate(
                    { group_ids: currentIds.filter(gid => !selectedGroupIds.has(gid)) },
                    {
                      onSuccess: () => {
                        setBulkRemoveGroupConfirm(false)
                        setSelectedGroupIds(new Set())
                      },
                    }
                  )
                }}
              />
            )
          })()}
        </div>
      )}

      {activeTab === "rules" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Rules</h2>
              <p className="text-slate-400 text-sm mt-1">
                Combined rules applied to this host from all assigned groups, in priority order.
              </p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={openFwAddDialog}>
                Add Rule
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!host?.ssh_key_id || !!syncPreview}
                onClick={() => openModulePreview("rules")}
              >
                <ArrowUpFromLineIcon className="w-4 h-4 mr-1" />
                Sync Rules
              </Button>
            </div>
          </div>

          {effectivePolicies && (
            <div className="flex items-center gap-4 text-sm">
              <span className="text-slate-400">Chain Policies:</span>
              <span className="text-slate-300 flex items-center gap-1.5">
                INPUT:{" "}
                <span className={effectivePolicies.input === "accept" ? "text-amber-400 font-medium" : "text-slate-200 font-medium"}>
                  {effectivePolicies.input}
                </span>
                <span className="text-slate-500 text-xs">from</span>
                <Badge variant="outline" className="text-xs font-mono">
                  {effectivePolicies.input_source_group_name ?? "default"}
                </Badge>
              </span>
              <span className="text-slate-300 flex items-center gap-1.5">
                OUTPUT:{" "}
                <span className={effectivePolicies.output === "drop" ? "text-amber-400 font-medium" : "text-slate-200 font-medium"}>
                  {effectivePolicies.output}
                </span>
                <span className="text-slate-500 text-xs">from</span>
                <Badge variant="outline" className="text-xs font-mono">
                  {effectivePolicies.output_source_group_name ?? "default"}
                </Badge>
              </span>
            </div>
          )}

          {showRulesLoading && <TableSkeleton rows={3} columns={4} />}

          {rulesError && (
            <div className="text-red-400 py-6 text-center">Failed to load effective rules</div>
          )}

          {!rulesLoading && !rulesError && effectiveRules && effectiveRules.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No effective rules. Assign this host to a group with rules.
            </div>
          )}

          {!rulesLoading && !rulesError && effectiveRules && effectiveRules.length > 0 && (
            <DataTable<EffectiveFirewallRule>
              tableId="host-effective-rules"
              data={effectiveRules}
              getRowKey={(rule) => `${rule.rule_id ?? "sys"}-${rule.group_id ?? "none"}`}
              emptyMessage="No effective rules."
              // Column widths trimmed (Source/Destination/Group/Comment/Actions) so the column-default sum fits the ~770px content area at ~1080px viewports without a horizontal scrollbar; full text on hover via title.
              columns={[
                { key: "priority", label: "Priority", accessor: (r) => r.group_priority ?? r.priority, cell: (r) => <span className="font-mono text-slate-300 text-xs">{r.group_priority ?? r.priority}</span>, defaultWidth: 80 },
                { key: "action", label: "Action", accessor: (r) => r.action, cell: (r) => <ActionBadge action={r.action} />, defaultWidth: 90, filter: { type: "enum", options: [{label:"Allow",value:"allow"},{label:"Deny",value:"deny"},{label:"Reject",value:"reject"}] } },
                { key: "protocol", label: "Protocol", accessor: (r) => r.protocol, cell: (r) => <span className="text-slate-300 uppercase text-xs">{r.protocol}</span>, defaultWidth: 90, filter: { type: "enum", options: [{label:"TCP",value:"tcp"},{label:"UDP",value:"udp"},{label:"ICMP",value:"icmp"},{label:"Any",value:"any"}] } },
                { key: "direction", label: "Direction", accessor: (r) => r.direction, cell: (r) => <span className="text-slate-300 capitalize text-xs">{r.direction}</span>, defaultWidth: 100, filter: { type: "enum", options: [{label:"Input",value:"input"},{label:"Output",value:"output"}] } },
                { key: "source", label: "Source", accessor: (r) => r.source_host_name ?? r.source_cidr ?? "any", cell: (r) => r.source_host_name ? <span className="text-sky-400 text-xs inline-block max-w-[110px] truncate align-middle" title={`${r.source_host_name} (${r.source_cidr ?? ""})`}>{r.source_host_name} <span className="text-slate-500">({r.source_cidr ?? "…"})</span></span> : <span className="font-mono text-slate-300 text-xs inline-block max-w-[110px] truncate align-middle" title={r.source_cidr ?? "any"}>{r.source_cidr ?? "any"}</span>, defaultWidth: 110, filter: { type: "text" } },
                { key: "destination", label: "Destination", accessor: (r) => r.destination_host_name ?? r.destination_cidr ?? "any", cell: (r) => r.destination_host_name ? <span className="text-sky-400 text-xs inline-block max-w-[110px] truncate align-middle" title={`${r.destination_host_name} (${r.destination_cidr ?? ""})`}>{r.destination_host_name} <span className="text-slate-500">({r.destination_cidr ?? "…"})</span></span> : <span className="font-mono text-slate-300 text-xs inline-block max-w-[110px] truncate align-middle" title={r.destination_cidr ?? "any"}>{r.destination_cidr ?? "any"}</span>, defaultWidth: 110, filter: { type: "text" } },
                { key: "ports", label: "Port(s)", cell: (r) => <span className="font-mono text-slate-300 text-xs">{formatPorts(r)}</span>, defaultWidth: 90 },
                { key: "group", label: "Group", accessor: (r) => r.source === "system" ? "System" : r.source === "host" ? "Host override" : r.group_name ?? "", cell: (r) => <Badge variant="outline" className="text-xs font-mono max-w-[100px] truncate" title={r.source === "system" ? "System" : r.source === "host" ? "Host override" : r.group_name ?? ""}>{r.source === "system" ? "System" : r.source === "host" ? "Host override" : r.group_name ?? "—"}</Badge>, defaultWidth: 110, filter: { type: "enum", from: "accessor" } },
                { key: "comment", label: "Comment", accessor: (r) => r.comment ?? "", cell: (r) => <span className="text-slate-400 text-xs max-w-[90px] truncate inline-block align-middle" title={r.comment ?? ""}>{r.comment ?? "—"}</span>, defaultWidth: 90 },
                {
                  key: "actions",
                  label: "Actions",
                  cell: (rule) => (
                    rule.is_system || rule.source === "system" ? (
                      <span className="text-slate-600 text-xs">Read-only</span>
                    ) : rule.source === "group" ? (
                      <Button size="sm" variant="ghost" onClick={() => openFwEditDialog(rule)}>Edit</Button>
                    ) : rule.rule_id != null ? (
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openFwEditDialog(rule)}>Edit</Button>
                        <Button size="sm" variant="ghost" disabled={fwDeleteMutation.isPending} onClick={() => handleFwDelete(rule.rule_id!)} className="text-red-400 hover:text-red-300 hover:bg-red-950">
                          {fwDeleteMutation.isPending ? "…" : "Delete"}
                        </Button>
                      </div>
                    ) : null
                  ),
                  defaultWidth: 130,
                  resizable: false,
                  sortable: false,
                },
              ]}
            />
          )}
          {fwDeleteMutation.error && (
            <div className="text-red-400 text-sm">{fwDeleteMutation.error.message}</div>
          )}

          <Dialog open={fwDialogOpen} onOpenChange={(open) => { if (!open) closeFwDialog(); else setFwDialogOpen(true) }}>
            <DialogContent className="sm:max-w-lg">
              <DialogHeader>
                <DialogTitle>{fwEditingId != null ? "Edit Firewall Rule Override" : "Add Firewall Rule Override"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleFwSubmit} className="space-y-4 mt-2">
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="fw-action">Action</Label>
                    <select
                      id="fw-action"
                      className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white"
                      value={fwAction}
                      onChange={(e) => setFwAction(e.target.value)}
                    >
                      <option value="allow">Allow</option>
                      <option value="deny">Deny</option>
                      <option value="reject">Reject</option>
                    </select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="fw-protocol">Protocol</Label>
                    <select
                      id="fw-protocol"
                      className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white"
                      value={fwProtocol}
                      onChange={(e) => setFwProtocol(e.target.value)}
                    >
                      <option value="tcp">TCP</option>
                      <option value="udp">UDP</option>
                      <option value="icmp">ICMP</option>
                      <option value="any">Any</option>
                    </select>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="fw-direction">Direction</Label>
                  <select
                    id="fw-direction"
                    className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white"
                    value={fwDirection}
                    onChange={(e) => setFwDirection(e.target.value)}
                  >
                    <option value="input">Input</option>
                    <option value="output">Output</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center justify-between">
                      <Label>Source</Label>
                      <div className="flex gap-1 text-xs">
                        <button type="button" onClick={() => setFwSourceMode("cidr")} className={`px-2 py-0.5 rounded ${fwSourceMode === "cidr" ? "bg-slate-700 text-white" : "text-slate-400"}`}>CIDR</button>
                        <button type="button" onClick={() => setFwSourceMode("host")} className={`px-2 py-0.5 rounded ${fwSourceMode === "host" ? "bg-slate-700 text-white" : "text-slate-400"}`}>Host</button>
                      </div>
                    </div>
                    {fwSourceMode === "cidr" ? (
                      <Input type="text" placeholder="e.g. 10.0.0.0/24" value={fwSourceCidr} onChange={(e) => setFwSourceCidr(e.target.value)} />
                    ) : (
                      <HostCombobox value={fwSourceHostId} onChange={setFwSourceHostId} />
                    )}
                  </div>
                  <div className="space-y-1">
                    <div className="flex items-center justify-between">
                      <Label>Destination</Label>
                      <div className="flex gap-1 text-xs">
                        <button type="button" onClick={() => setFwDestMode("cidr")} className={`px-2 py-0.5 rounded ${fwDestMode === "cidr" ? "bg-slate-700 text-white" : "text-slate-400"}`}>CIDR</button>
                        <button type="button" onClick={() => setFwDestMode("host")} className={`px-2 py-0.5 rounded ${fwDestMode === "host" ? "bg-slate-700 text-white" : "text-slate-400"}`}>Host</button>
                      </div>
                    </div>
                    {fwDestMode === "cidr" ? (
                      <Input type="text" placeholder="e.g. 0.0.0.0/0" value={fwDestCidr} onChange={(e) => setFwDestCidr(e.target.value)} />
                    ) : (
                      <HostCombobox value={fwDestHostId} onChange={setFwDestHostId} />
                    )}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="fw-port-start">Port Start</Label>
                    <Input
                      id="fw-port-start"
                      type="number"
                      min={1}
                      max={65535}
                      placeholder="e.g. 80"
                      value={fwPortStart}
                      onChange={(e) => setFwPortStart(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="fw-port-end">Port End</Label>
                    <Input
                      id="fw-port-end"
                      type="number"
                      min={1}
                      max={65535}
                      placeholder="e.g. 443"
                      value={fwPortEnd}
                      onChange={(e) => setFwPortEnd(e.target.value)}
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="fw-priority">Priority</Label>
                  <Input
                    id="fw-priority"
                    type="number"
                    value={fwPriority}
                    onChange={(e) => setFwPriority(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="fw-comment">Comment</Label>
                  <Input
                    id="fw-comment"
                    type="text"
                    placeholder="Optional description"
                    value={fwComment}
                    onChange={(e) => setFwComment(e.target.value)}
                  />
                </div>
                {(fwCreateMutation.error || fwUpdateMutation.error) && (
                  <p className="text-sm text-red-400">{(fwCreateMutation.error ?? fwUpdateMutation.error)?.message}</p>
                )}
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeFwDialog}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={fwCreateMutation.isPending || fwUpdateMutation.isPending}>
                    {fwCreateMutation.isPending || fwUpdateMutation.isPending
                      ? "Saving..."
                      : fwEditingId != null ? "Save Changes" : "Create Override"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>

          {host?.firewall_backend === "unknown" && (
            <InstallFirewallSection hostId={id} queryClient={queryClient} />
          )}
          <CurrentStateSection moduleType="firewall" modules={currentStateQuery.data} hostId={id} />
        </div>
      )}

      {activeTab === "services" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Services</h2>
              <p className="text-slate-400 text-sm mt-1">
                Services applied to this host from groups and host-level overrides.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!host?.ssh_key_id || !!syncPreview}
                onClick={() => openModulePreview("services")}
              >
                <ArrowUpFromLineIcon className="w-4 h-4 mr-1" />
                Sync Services
              </Button>
              <Button onClick={openSvcDialog}>Add Service</Button>
            </div>
          </div>

          {svcDeleteMutation.error && (
            <div className="text-red-400 text-sm">{svcDeleteMutation.error.message}</div>
          )}

          {showServicesLoading && <TableSkeleton rows={3} columns={4} />}

          {servicesError && (
            <div className="text-red-400 py-6 text-center">Failed to load services</div>
          )}

          {!servicesLoading && !servicesError && effectiveServices && effectiveServices.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No services configured. Add a host override or assign service rules to a group.
            </div>
          )}

          {!servicesLoading && !servicesError && effectiveServices && effectiveServices.length > 0 && (
            <DataTable<EffectiveService>
              tableId="host-effective-services"
              data={effectiveServices}
              getRowKey={(svc) => `${svc.source}-${svc.source_id}-${svc.service_name}`}
              emptyMessage="No services configured."
              columns={[
                { key: "service_name", label: "Service Name", accessor: (s) => s.service_name, cell: (s) => <span className="font-mono text-white text-sm">{s.service_name}</span>, defaultWidth: 200, filter: { type: "text", placeholder: "e.g. nginx" } },
                { key: "state", label: "State", accessor: (s) => s.state, cell: (s) => <SystemdStateBadge state={s.state} titleCase />, defaultWidth: 110, filter: { type: "enum", options: [{label:"Running",value:"running"},{label:"Stopped",value:"stopped"}] } },
                { key: "enabled", label: "Enabled", accessor: (s) => s.enabled, cell: (s) => <EnabledBadge enabled={s.enabled} />, defaultWidth: 100, filter: { type: "boolean" } },
                { key: "source", label: "Source", accessor: (s) => s.source === "group" ? s.source_name : "Host override", cell: (s) => <Badge variant="outline" className="text-xs">{s.source === "group" ? s.source_name : "Host override"}</Badge>, defaultWidth: 140, filter: { type: "enum", from: "accessor" } },
                {
                  key: "actions",
                  label: "Actions",
                  cell: (svc) => (
                    svc.source === "group" ? (
                      <Button size="sm" variant="ghost" onClick={() => openSvcEditFromEffective(svc)}>Edit</Button>
                    ) : svc.source === "host" ? (
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openSvcEditFromEffective(svc)}>Edit</Button>
                        <Button size="sm" variant="ghost" disabled={svcDeleteMutation.isPending} onClick={() => handleSvcDelete(svc.service_name)} className="text-red-400 hover:text-red-300 hover:bg-red-950">
                          {svcDeleteMutation.isPending ? "…" : "Delete"}
                        </Button>
                      </div>
                    ) : (
                      <span className="text-slate-600 text-xs">Read-only</span>
                    )
                  ),
                  defaultWidth: 160,
                  resizable: false,
                  sortable: false,
                },
              ]}
            />
          )}

          <Dialog open={svcDialogOpen} onOpenChange={setSvcDialogOpen}>
            <DialogContent className="sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>
                  {svcEditorMode === "edit"
                    ? "Edit Service Override"
                    : svcOriginalAttempted
                      ? `Add Service Override${svcName ? `: ${svcName}` : ""}`
                      : "Add Service"}
                </DialogTitle>
              </DialogHeader>
              <form onSubmit={handleSvcSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="svc-name">Service Name</Label>
                  <Input
                    id="svc-name"
                    type="text"
                    placeholder="e.g. nginx, sshd, docker"
                    value={svcName}
                    onChange={(e) => setSvcName(e.target.value)}
                    disabled={svcEditorMode === "edit" || svcOriginalAttempted}
                    required
                  />
                </div>

                {svcEditorMode === "add" ? (
                  <div className="space-y-2">
                    <Label>Deploy Mode</Label>
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant={svcDeployMode === "override" ? "default" : "outline"}
                        onClick={() => setSvcDeployMode("override")}
                      >
                        Override existing
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant={svcDeployMode === "full" ? "default" : "outline"}
                        onClick={() => setSvcDeployMode("full")}
                      >
                        New Service (full file)
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-1">
                    <Label>Deploy Mode</Label>
                    <p className="text-sm text-slate-400">
                      {svcDeployMode === "full" ? "New Service (full file)" : "Override existing"}
                    </p>
                  </div>
                )}

                {svcOriginalAttempted && (
                  <div className="space-y-2">
                    <Label>Current on-disk unit file (systemctl cat)</Label>
                    {svcOriginalLoading ? (
                      <p className="text-xs text-slate-500">Fetching current unit file from host...</p>
                    ) : typeof svcOriginalUnit === "string" ? (
                      <pre className="rounded-md border border-slate-700 bg-slate-950 p-3 text-xs font-mono text-slate-300 overflow-x-auto max-h-40 overflow-y-auto whitespace-pre">
                        {svcOriginalUnit}
                      </pre>
                    ) : (
                      <div className="rounded-md border border-amber-700/50 bg-amber-950/30 p-3 text-xs text-amber-300">
                        Could not fetch the on-disk unit file from the host. Check SSH connectivity or whether the service exists on the target.
                      </div>
                    )}
                  </div>
                )}

                <div className="space-y-2">
                  <Label htmlFor="svc-unit-content">Unit file content</Label>
                  <Textarea
                    id="svc-unit-content"
                    rows={8}
                    className="font-mono text-sm resize-y"
                    placeholder={
                      svcDeployMode === "full"
                        ? "[Unit]\nDescription=My Service\n\n[Service]\nExecStart=/usr/bin/myapp\nRestart=always\n\n[Install]\nWantedBy=multi-user.target"
                        : "[Service]\nMemoryLimit=512M"
                    }
                    value={svcUnitContent}
                    onChange={(e) => setSvcUnitContent(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="svc-state">State</Label>
                  <select
                    id="svc-state"
                    value={svcState}
                    onChange={(e) => setSvcState(e.target.value as "running" | "stopped")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="running">Running</option>
                    <option value="stopped">Stopped</option>
                  </select>
                </div>

                <div className="flex items-center gap-2">
                  <input
                    id="svc-enabled"
                    type="checkbox"
                    checked={svcEnabled}
                    onChange={(e) => setSvcEnabled(e.target.checked)}
                    className="rounded border-input"
                  />
                  <Label htmlFor="svc-enabled">Enabled</Label>
                  <span className="text-xs text-slate-500">Start on boot</span>
                </div>

                {svcSaveMutation.error && (
                  <p className="text-sm text-red-400">{svcSaveMutation.error.message}</p>
                )}

                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setSvcDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={svcSaveMutation.isPending}>
                    {svcSaveMutation.isPending ? "Saving..." : "Save"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>

          {/* Live Service Inventory */}
          <hr className="border-slate-700" />

          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Service Inventory</h2>
              <p className="text-slate-400 text-sm mt-1">
                Live systemd services on this host. Fetched via SSH on demand.
              </p>
            </div>
            <Button
              onClick={loadInventory}
              disabled={inventoryLoading}
            >
              {inventoryLoading ? "Loading..." : inventoryLoaded ? "Refresh" : "Load Inventory"}
            </Button>
          </div>

          {actionResult && (
            <div
              className={`flex items-center justify-between rounded-lg border p-3 text-sm ${
                actionResult.success
                  ? "border-green-700 bg-green-950 text-green-300"
                  : "border-red-700 bg-red-950 text-red-300"
              }`}
            >
              <span>{actionResult.message}</span>
              <button
                onClick={() => setActionResult(null)}
                className="ml-4 text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>
          )}

          {inventoryError && (
            <div className="text-red-400 text-sm">{inventoryError}</div>
          )}

          {inventoryLoaded && inventory.length === 0 && !inventoryLoading && (
            <div className="text-slate-400 py-6 text-center">No services found.</div>
          )}

          {inventoryLoaded && inventory.length > 0 && (
            <>
              <div className="flex items-center gap-3">
                <Input
                  placeholder="Filter services..."
                  value={inventoryFilter}
                  onChange={(e) => setInventoryFilter(e.target.value)}
                  className="max-w-sm"
                />
                <label className="flex items-center gap-2 text-sm text-slate-400 whitespace-nowrap">
                  <input
                    type="checkbox"
                    checked={inventoryHideSystem}
                    onChange={(e) => setInventoryHideSystem(e.target.checked)}
                    className="rounded border-slate-600"
                  />
                  Hide system services
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-400 whitespace-nowrap">
                  <input
                    type="checkbox"
                    checked={inventoryHideManaged}
                    onChange={(e) => setInventoryHideManaged(e.target.checked)}
                    className="rounded border-slate-600"
                  />
                  Hide managed
                </label>
              </div>

              <DataTable<LiveService>
                tableId="host-service-inventory"
                data={filteredInventory}
                getRowKey={(svc) => svc.unit}
                emptyMessage="No services found."
                columns={[
                  {
                    key: "unit",
                    label: "Unit",
                    accessor: (s) => s.unit,
                    cell: (s) => (
                      <span className="font-mono text-white text-sm">
                        {s.unit}
                        {s.is_managed && <Badge variant="outline" className="text-xs ml-2">Managed</Badge>}
                      </span>
                    ),
                    defaultWidth: 220,
                    filter: { type: "text", placeholder: "e.g. ssh" },
                  },
                  {
                    key: "active_state",
                    label: "Active State",
                    accessor: (s) => s.active_state,
                    cell: (s) => (
                      <SystemdStateBadge state={s.active_state} />
                    ),
                    defaultWidth: 120,
                    filter: { type: "enum", from: "accessor" },
                  },
                  { key: "sub_state", label: "Sub State", accessor: (s) => s.sub_state, cell: (s) => <span className="text-slate-300 text-xs">{s.sub_state}</span>, defaultWidth: 110, filter: { type: "enum", from: "accessor" } },
                  { key: "load_state", label: "Load State", accessor: (s) => s.load_state, cell: (s) => <span className="text-slate-300 text-xs">{s.load_state}</span>, defaultWidth: 100, filter: { type: "enum", from: "accessor" } },
                  { key: "description", label: "Description", accessor: (s) => s.description, cell: (s) => <span className="text-slate-400 text-xs max-w-[200px] truncate">{s.description}</span>, defaultWidth: 200 },
                  {
                    key: "actions",
                    label: "Actions",
                    cell: (svc) => (
                      <div className="flex gap-1 flex-wrap">
                        {(["start", "stop", "restart"] as const).map((action) => (
                          <Button key={action} size="sm" variant="ghost" disabled={actionLoading && pendingAction?.service === svc.unit} onClick={() => handleActionClick(svc, action)} className="text-xs">
                            {actionLoading && pendingAction?.service === svc.unit && pendingAction?.action === action ? "..." : action.charAt(0).toUpperCase() + action.slice(1)}
                          </Button>
                        ))}
                        {!svc.is_protected && (
                          <Button size="sm" variant="ghost" onClick={() => openSvcEdit(svc)} className="text-xs text-blue-400 hover:text-blue-300">Edit</Button>
                        )}
                        {svc.is_managed && !svc.is_protected && (() => {
                          const matchingOverride = hostOverrides?.find(
                            (o) => o.service_name === svc.unit || o.service_name === svc.unit.replace(/\.service$/, "")
                          )
                          return matchingOverride ? (
                            <Button size="sm" variant="ghost" disabled={svcDeleteMutation.isPending} onClick={() => handleSvcDelete(svc.unit)} className="text-xs text-red-400 hover:text-red-300 hover:bg-red-950">
                              {svcDeleteMutation.isPending ? "..." : "Remove"}
                            </Button>
                          ) : null
                        })()}
                      </div>
                    ),
                    defaultWidth: 220,
                    resizable: false,
                    sortable: false,
                  },
                ]}
              />

              <p className="text-slate-500 text-xs">
                Showing {filteredInventory.length} of {inventory.length} services
              </p>
            </>
          )}

          {/* Protected Service Confirmation Dialog */}
          <Dialog open={protectedConfirmOpen} onOpenChange={setProtectedConfirmOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Protected Service Warning</DialogTitle>
              </DialogHeader>
              <div className="space-y-4 mt-2">
                <p className="text-amber-400 text-sm">
                  <strong>{protectedTarget?.service}</strong> is a protected system service.
                  Performing <strong>{protectedTarget?.action}</strong> on this service could
                  cause system instability or loss of access.
                </p>
                <p className="text-slate-400 text-sm">Are you sure you want to proceed?</p>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={() => setProtectedConfirmOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => {
                      setProtectedConfirmOpen(false)
                      if (protectedTarget) executeCommand(protectedTarget.service, protectedTarget.action)
                    }}
                  >
                    Confirm {protectedTarget?.action}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>

        </div>
      )}

      {activeTab === "hosts-file" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Hosts File</h2>
              <p className="text-slate-400 text-sm mt-1">
                /etc/hosts entries applied to this host from groups, overrides, and system defaults.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!host?.ssh_key_id || !!syncPreview}
                onClick={() => openModulePreview("hosts-file")}
              >
                <ArrowUpFromLineIcon className="w-4 h-4 mr-1" />
                Sync
              </Button>
              <Button
                variant="outline"
                onClick={fetchHostsPreview}
                disabled={hostsPreviewLoading}
              >
                {hostsPreviewLoading ? "Loading..." : "Preview File"}
              </Button>
              <Button onClick={openHostsDialog}>Add Override</Button>
            </div>
          </div>

          {hostsDeleteMutation.error && (
            <div className="text-red-400 text-sm">{hostsDeleteMutation.error.message}</div>
          )}

          {hostsPreviewError && (
            <div className="text-red-400 text-sm">{hostsPreviewError}</div>
          )}

          {hostsPreview !== null && (
            <div className="rounded-lg border border-slate-700 bg-slate-950 p-4">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-medium text-slate-300">/etc/hosts preview</h3>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setHostsPreview(null)}
                  className="text-slate-400 hover:text-white"
                >
                  Close
                </Button>
              </div>
              <pre className="text-xs text-slate-300 font-mono whitespace-pre overflow-x-auto">{hostsPreview}</pre>
            </div>
          )}

          {showHostsEntriesLoading && <TableSkeleton rows={3} columns={4} />}

          {hostsEntriesError && (
            <div className="text-red-400 py-6 text-center">Failed to load hosts entries</div>
          )}

          {!hostsEntriesLoading && !hostsEntriesError && (
            <DataTable<EffectiveHostsEntry>
              tableId="host-effective-hosts-entries"
              data={effectiveHosts}
              emptyMessage="No hosts entries configured. Add a host override or assign entries to a group."
              getRowKey={(entry) => `${entry.source}-${entry.source_id}-${entry.ip_address}-${entry.hostname}`}
              columns={[
                {
                  key: "ip_address",
                  label: "IP Address",
                  accessor: (entry) => entry.ip_address,
                  cell: (entry) => <span className="font-mono text-white text-sm">{entry.ip_address}</span>,
                  defaultWidth: 140,
                  filter: { type: "text", placeholder: "e.g. 10.0.1" },
                },
                {
                  key: "hostname",
                  label: "Hostname",
                  accessor: (entry) => entry.hostname,
                  cell: (entry) => <span className="font-mono text-slate-300 text-sm">{entry.hostname}</span>,
                  defaultWidth: 180,
                  filter: { type: "text", placeholder: "e.g. web-01" },
                },
                {
                  key: "aliases",
                  label: "Aliases",
                  accessor: (entry) => entry.aliases.join(", "),
                  cell: (entry) => (
                    <span className="text-slate-300 text-xs truncate">
                      {entry.aliases.length > 0 ? entry.aliases.join(", ") : "—"}
                    </span>
                  ),
                  defaultWidth: 200,
                },
                {
                  key: "source",
                  label: "Source",
                  accessor: (entry) =>
                    entry.source === "system" ? "System" : entry.source === "group" ? entry.source_name : "Host override",
                  cell: (entry) => (
                    <Badge variant="outline" className="text-xs">
                      {entry.source === "system"
                        ? "System"
                        : entry.source === "group"
                          ? entry.source_name
                          : "Host override"}
                    </Badge>
                  ),
                  defaultWidth: 140,
                  filter: { type: "enum", from: "accessor" },
                },
                {
                  key: "actions",
                  label: "Actions",
                  cell: (entry) => (
                    entry.is_system || entry.source === "system" ? (
                      <span className="text-slate-600 text-xs">Read-only</span>
                    ) : entry.source === "group" ? (
                      <Button size="sm" variant="ghost" onClick={() => openHostsEditDialog(entry)}>
                        Edit
                      </Button>
                    ) : (() => {
                      const override = hostHostsOverrides?.find(
                        (o) => o.hostname === entry.hostname && o.ip_address === entry.ip_address
                      )
                      return override ? (
                        <div className="flex gap-1">
                          <Button size="sm" variant="ghost" onClick={() => openHostsEditDialog(entry)}>
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={hostsDeleteMutation.isPending}
                            onClick={() => handleHostsEntryDelete(override)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {hostsDeleteMutation.isPending ? "…" : "Delete"}
                          </Button>
                        </div>
                      ) : null
                    })()
                  ),
                  defaultWidth: 160,
                  resizable: false,
                  sortable: false,
                },
              ]}
            />
          )}

          <Dialog open={hostsDialogOpen} onOpenChange={(open) => { if (!open) closeHostsDialog(); else setHostsDialogOpen(true) }}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>{hostsEditingId != null ? "Edit Hosts Entry Override" : "Add Hosts Entry Override"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleHostsSubmit} className="space-y-4 mt-2">
                <div className="flex gap-2 text-xs">
                  <button type="button" onClick={() => setHostsMode("literal")} className={`px-3 py-1 rounded ${hostsMode === "literal" ? "bg-slate-700 text-white" : "text-slate-400 border border-slate-700"}`}>
                    Literal IP + hostname
                  </button>
                  <button type="button" onClick={() => setHostsMode("host")} className={`px-3 py-1 rounded ${hostsMode === "host" ? "bg-slate-700 text-white" : "text-slate-400 border border-slate-700"}`}>
                    Registered host
                  </button>
                </div>

                {hostsMode === "literal" ? (
                  <>
                    <div className="space-y-2">
                      <Label htmlFor="hosts-ip">IP Address</Label>
                      <Input
                        id="hosts-ip"
                        type="text"
                        placeholder="e.g. 192.168.1.10"
                        value={hostsIp}
                        onChange={(e) => setHostsIp(e.target.value)}
                        required
                      />
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="hosts-hostname">Hostname</Label>
                      <Input
                        id="hosts-hostname"
                        type="text"
                        placeholder="e.g. myserver.local"
                        value={hostsHostname}
                        onChange={(e) => setHostsHostname(e.target.value)}
                        required
                      />
                    </div>
                  </>
                ) : (
                  <div className="space-y-2">
                    <Label>Host</Label>
                    <HostCombobox value={hostsRefId} onChange={setHostsRefId} />
                    <p className="text-xs text-slate-500">Uses the host&apos;s current IP and hostname at sync time.</p>
                  </div>
                )}

                <div className="space-y-2">
                  <Label htmlFor="hosts-aliases">Aliases (comma-separated)</Label>
                  <Input
                    id="hosts-aliases"
                    type="text"
                    placeholder="e.g. myserver, ms"
                    value={hostsAliases}
                    onChange={(e) => setHostsAliases(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="hosts-comment">Comment</Label>
                  <Input
                    id="hosts-comment"
                    type="text"
                    placeholder="Optional comment"
                    value={hostsComment}
                    onChange={(e) => setHostsComment(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="hosts-priority">Priority</Label>
                  <Input
                    id="hosts-priority"
                    type="number"
                    value={hostsPriority}
                    onChange={(e) => setHostsPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                {(hostsSaveMutation.error || hostsUpdateMutation.error) && (
                  <p className="text-sm text-red-400">{(hostsSaveMutation.error ?? hostsUpdateMutation.error)?.message}</p>
                )}

                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={closeHostsDialog}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={hostsSaveMutation.isPending || hostsUpdateMutation.isPending}>
                    {hostsSaveMutation.isPending || hostsUpdateMutation.isPending
                      ? "Saving..."
                      : hostsEditingId != null ? "Save Changes" : "Create Override"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
          <CurrentStateSection moduleType="hosts_file" modules={currentStateQuery.data} hostId={id} />
        </div>
      )}

      {activeTab === "users" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Linux Users & Groups</h2>
              <p className="text-slate-400 text-sm mt-1">
                Users and groups applied to this host from groups and host-level overrides.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!host?.ssh_key_id || !!syncPreview}
                onClick={() => openModulePreview("users")}
              >
                <ArrowUpFromLineIcon className="w-4 h-4 mr-1" />
                Sync
              </Button>
              <Button variant="outline" onClick={openLuDialog}>Add User Override</Button>
              <Button variant="outline" onClick={openLgDialog}>Add Group Override</Button>
            </div>
          </div>

          {(luDeleteMutation.error || lgDeleteMutation.error) && (
            <div className="text-red-400 text-sm">{luDeleteMutation.error?.message || lgDeleteMutation.error?.message}</div>
          )}

          {/* Users sub-section */}
          <div>
            <h3 className="text-sm font-medium text-slate-300 mb-2">
              Users ({effectiveLinuxUsers?.length ?? 0})
            </h3>
            {showLinuxUsersLoading && <TableSkeleton rows={3} columns={4} />}
            {linuxUsersError && (
              <div className="text-red-400 py-4 text-center text-sm">Failed to load users</div>
            )}
            {!linuxUsersLoading && !linuxUsersError && (
              <DataTable<EffectiveLinuxUser>
                tableId="host-effective-linux-users"
                data={effectiveLinuxUsers}
                emptyMessage="No users configured."
                getRowKey={(user) => `${user.source}-${user.source_id}-${user.username}`}
                columns={[
                  {
                    key: "username",
                    label: "Username",
                    accessor: (user) => user.username,
                    cell: (user) => <span className="font-mono text-white text-sm">{user.username}</span>,
                    defaultWidth: 160,
                    filter: { type: "text", placeholder: "e.g. deploy" },
                  },
                  {
                    key: "uid",
                    label: "UID",
                    accessor: (user) => user.uid ?? "auto",
                    cell: (user) => <span className="font-mono text-slate-300 text-xs">{user.uid ?? "auto"}</span>,
                    defaultWidth: 80,
                    filter: { type: "text" },
                  },
                  {
                    key: "shell",
                    label: "Shell",
                    accessor: (user) => user.shell,
                    cell: (user) => <span className="font-mono text-slate-300 text-xs">{user.shell}</span>,
                    defaultWidth: 140,
                    filter: { type: "text" },
                  },
                  {
                    key: "state",
                    label: "State",
                    accessor: (user) => user.state,
                    cell: (user) => (
                      <ItemStateBadge state={user.state} />
                    ),
                    defaultWidth: 110,
                    filter: { type: "enum", options: [{label:"Present",value:"present"},{label:"Absent",value:"absent"}] },
                  },
                  {
                    key: "keys",
                    label: "Keys",
                    accessor: (user) => user.authorized_keys.length,
                    cell: (user) => (
                      <Badge variant="outline" className="text-xs">
                        {user.authorized_keys.length} {user.authorized_keys.length === 1 ? "key" : "keys"}
                      </Badge>
                    ),
                    defaultWidth: 80,
                  },
                  {
                    key: "sudo",
                    label: "Sudo",
                    accessor: (user) => !!user.sudo_rule,
                    cell: (user) => user.sudo_rule ? (
                      <Badge className="bg-amber-600 text-white">Yes</Badge>
                    ) : (
                      <span className="text-slate-600 text-xs">No</span>
                    ),
                    defaultWidth: 80,
                    filter: { type: "boolean" },
                  },
                  {
                    key: "source",
                    label: "Source",
                    accessor: (user) => user.source === "group" ? user.source_name : "Host override",
                    cell: (user) => (
                      <Badge variant="outline" className="text-xs">
                        {user.source === "group" ? user.source_name : "Host override"}
                      </Badge>
                    ),
                    defaultWidth: 140,
                    filter: { type: "enum", from: "accessor" },
                  },
                  {
                    key: "actions",
                    label: "Actions",
                    cell: (user) => (
                      user.source === "host" ? (
                        <div className="flex gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleLuEdit(user.username)}
                            className="text-slate-300 hover:text-white hover:bg-slate-800"
                          >
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={luDeleteMutation.isPending}
                            onClick={() => handleLuDelete(user.username)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {luDeleteMutation.isPending ? "…" : "Delete"}
                          </Button>
                        </div>
                      ) : (
                        <span className="text-slate-600 text-xs">Read-only</span>
                      )
                    ),
                    defaultWidth: 160,
                    resizable: false,
                    sortable: false,
                  },
                ]}
              />
            )}
          </div>

          {/* Groups sub-section */}
          <div>
            <h3 className="text-sm font-medium text-slate-300 mb-2">
              Groups ({effectiveLinuxGroups?.length ?? 0})
            </h3>
            {showLinuxGroupsLoading && <TableSkeleton rows={3} columns={4} />}
            {linuxGroupsError && (
              <div className="text-red-400 py-4 text-center text-sm">Failed to load groups</div>
            )}
            {!linuxGroupsLoading && !linuxGroupsError && (
              <DataTable<EffectiveLinuxGroup>
                tableId="host-effective-linux-groups"
                data={effectiveLinuxGroups}
                emptyMessage="No groups configured."
                getRowKey={(group) => `${group.source}-${group.source_id}-${group.groupname}`}
                columns={[
                  {
                    key: "groupname",
                    label: "Group Name",
                    accessor: (group) => group.groupname,
                    cell: (group) => <span className="font-mono text-white text-sm">{group.groupname}</span>,
                    defaultWidth: 180,
                    filter: { type: "text" },
                  },
                  {
                    key: "gid",
                    label: "GID",
                    accessor: (group) => group.gid ?? "auto",
                    cell: (group) => <span className="font-mono text-slate-300 text-xs">{group.gid ?? "auto"}</span>,
                    defaultWidth: 80,
                    filter: { type: "text" },
                  },
                  {
                    key: "state",
                    label: "State",
                    accessor: (group) => group.state,
                    cell: (group) => (
                      <ItemStateBadge state={group.state} />
                    ),
                    defaultWidth: 110,
                    filter: { type: "enum", options: [{label:"Present",value:"present"},{label:"Absent",value:"absent"}] },
                  },
                  {
                    key: "source",
                    label: "Source",
                    accessor: (group) => group.source === "group" ? group.source_name : "Host override",
                    cell: (group) => (
                      <Badge variant="outline" className="text-xs">
                        {group.source === "group" ? group.source_name : "Host override"}
                      </Badge>
                    ),
                    defaultWidth: 140,
                    filter: { type: "enum", from: "accessor" },
                  },
                  {
                    key: "actions",
                    label: "Actions",
                    cell: (group) => (
                      group.source === "host" ? (
                        <div className="flex gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleLgEdit(group.groupname)}
                            className="text-slate-300 hover:text-white hover:bg-slate-800"
                          >
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={lgDeleteMutation.isPending}
                            onClick={() => handleLgDelete(group.groupname)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {lgDeleteMutation.isPending ? "…" : "Delete"}
                          </Button>
                        </div>
                      ) : (
                        <span className="text-slate-600 text-xs">Read-only</span>
                      )
                    ),
                    defaultWidth: 160,
                    resizable: false,
                    sortable: false,
                  },
                ]}
              />
            )}
          </div>

          <Dialog open={luDialogOpen} onOpenChange={setLuDialogOpen}>
            <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>{luEditingId != null ? "Edit Linux User Override" : "Add Linux User Override"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleLuSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="lu-username">Username</Label>
                  <Input
                    id="lu-username"
                    type="text"
                    placeholder="e.g. deploy, appuser"
                    value={luUsername}
                    onChange={(e) => setLuUsername(e.target.value)}
                    required
                    disabled={luEditingId != null}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-uid">UID (optional)</Label>
                  <Input
                    id="lu-uid"
                    type="number"
                    placeholder="Auto-assign if empty"
                    value={luUid}
                    onChange={(e) => setLuUid(e.target.value)}
                    min={1000}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-shell">Shell</Label>
                  <Input
                    id="lu-shell"
                    type="text"
                    value={luShell}
                    onChange={(e) => setLuShell(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-home">Home Directory (optional)</Label>
                  <Input
                    id="lu-home"
                    type="text"
                    placeholder="e.g. /home/deploy"
                    value={luHomeDir}
                    onChange={(e) => setLuHomeDir(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-state">State</Label>
                  <select
                    id="lu-state"
                    value={luState}
                    onChange={(e) => setLuState(e.target.value as "present" | "absent")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-keys">SSH Authorized Keys</Label>
                  <textarea
                    id="lu-keys"
                    placeholder="One SSH public key per line"
                    value={luAuthorizedKeys}
                    onChange={(e) => setLuAuthorizedKeys(e.target.value)}
                    rows={3}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                  />
                  <p className="text-xs text-slate-500">One SSH public key per line</p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-groups">Supplementary Groups</Label>
                  <Input
                    id="lu-groups"
                    type="text"
                    placeholder="e.g. docker, wheel, sudo"
                    value={luSupplementaryGroups}
                    onChange={(e) => setLuSupplementaryGroups(e.target.value)}
                  />
                  <p className="text-xs text-slate-500">Comma-separated group names</p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-sudo">Sudo Rule (optional)</Label>
                  <Input
                    id="lu-sudo"
                    type="text"
                    placeholder="e.g. ALL=(ALL) NOPASSWD: ALL"
                    value={luSudoRule}
                    onChange={(e) => setLuSudoRule(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-comment">Comment (optional)</Label>
                  <Input
                    id="lu-comment"
                    type="text"
                    placeholder="GECOS / description"
                    value={luComment}
                    onChange={(e) => setLuComment(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-priority">Priority</Label>
                  <Input
                    id="lu-priority"
                    type="number"
                    value={luPriority}
                    onChange={(e) => setLuPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                {(luSaveMutation.error || luUpdateMutation.error) && (
                  <p className="text-sm text-red-400">{(luSaveMutation.error || luUpdateMutation.error)?.message}</p>
                )}

                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setLuDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={luSaveMutation.isPending || luUpdateMutation.isPending}>
                    {luSaveMutation.isPending || luUpdateMutation.isPending
                      ? "Saving..."
                      : luEditingId != null
                        ? "Save Changes"
                        : "Create Override"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>

          <Dialog open={lgDialogOpen} onOpenChange={setLgDialogOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>{lgEditingId != null ? "Edit Linux Group Override" : "Add Linux Group Override"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleLgSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="lg-name">Group Name</Label>
                  <Input
                    id="lg-name"
                    type="text"
                    placeholder="e.g. docker, developers"
                    value={lgGroupname}
                    onChange={(e) => setLgGroupname(e.target.value)}
                    required
                    disabled={lgEditingId != null}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lg-gid">GID (optional)</Label>
                  <Input
                    id="lg-gid"
                    type="number"
                    placeholder="Auto-assign if empty"
                    value={lgGid}
                    onChange={(e) => setLgGid(e.target.value)}
                    min={1000}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lg-state">State</Label>
                  <select
                    id="lg-state"
                    value={lgState}
                    onChange={(e) => setLgState(e.target.value as "present" | "absent")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lg-priority">Priority</Label>
                  <Input
                    id="lg-priority"
                    type="number"
                    value={lgPriority}
                    onChange={(e) => setLgPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                {(lgSaveMutation.error || lgUpdateMutation.error) && (
                  <p className="text-sm text-red-400">{(lgSaveMutation.error || lgUpdateMutation.error)?.message}</p>
                )}

                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setLgDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={lgSaveMutation.isPending || lgUpdateMutation.isPending}>
                    {lgSaveMutation.isPending || lgUpdateMutation.isPending
                      ? "Saving..."
                      : lgEditingId != null
                        ? "Save Changes"
                        : "Create Override"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
          <CurrentStateSection
            moduleType="linux_user"
            modules={currentStateQuery.data}
            hostId={id}
            onManageUser={manageCollectedUser}
            onManageGroup={manageCollectedGroup}
            userHasOverride={(username) => !!hostLinuxUserOverrides?.find(o => o.username === username)}
            groupHasOverride={(groupname) => !!hostLinuxGroupOverrides?.find(o => o.groupname === groupname)}
          />
        </div>
      )}

      {activeTab === "cron-jobs" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Cron Jobs</h2>
              <p className="text-slate-400 text-sm mt-1">
                Cron jobs applied to this host from groups and host-level overrides.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!host?.ssh_key_id || !!syncPreview}
                onClick={() => openModulePreview("cron-jobs")}
              >
                <ArrowUpFromLineIcon className="w-4 h-4 mr-1" />
                Sync
              </Button>
              <Button onClick={openCjDialog}>Add Override</Button>
            </div>
          </div>

          {cjDeleteMutation.error && (
            <div className="text-red-400 text-sm">{cjDeleteMutation.error.message}</div>
          )}

          {showCronJobsLoading && <TableSkeleton rows={3} columns={4} />}

          {cronJobsError && (
            <div className="text-red-400 py-6 text-center">Failed to load cron jobs</div>
          )}

          {!cronJobsLoading && !cronJobsError && (
            <DataTable<EffectiveCronJob>
              tableId="host-effective-cron-jobs"
              data={effectiveCronJobs}
              emptyMessage="No cron jobs configured. Add a host override or assign cron jobs to a group."
              getRowKey={(job) => `${job.source}-${job.source_id}-${job.name}-${job.user}`}
              columns={[
                {
                  key: "name",
                  label: "Name",
                  accessor: (job) => job.name,
                  cell: (job) => <span className="font-mono text-white text-sm">{job.name}</span>,
                  defaultWidth: 180,
                  filter: { type: "text", placeholder: "e.g. backup" },
                },
                {
                  key: "user",
                  label: "User",
                  accessor: (job) => job.user,
                  cell: (job) => <span className="font-mono text-slate-300 text-xs">{job.user}</span>,
                  defaultWidth: 100,
                  filter: { type: "text", placeholder: "e.g. root" },
                },
                {
                  key: "schedule",
                  label: "Schedule",
                  accessor: (job) => job.schedule,
                  cell: (job) => (
                    <div>
                      <span className="font-mono text-slate-300 text-xs">{job.schedule}</span>
                      {cronToHuman(job.schedule) !== job.schedule && (
                        <div className="text-slate-500 text-xs mt-0.5">{cronToHuman(job.schedule)}</div>
                      )}
                    </div>
                  ),
                  defaultWidth: 160,
                  filter: { type: "text", placeholder: "e.g. */5" },
                },
                {
                  key: "command",
                  label: "Command",
                  accessor: (job) => job.command,
                  cell: (job) => (
                    <span className="font-mono text-slate-300 text-xs" title={job.command}>
                      {job.command.length > 60 ? job.command.slice(0, 60) + "..." : job.command}
                    </span>
                  ),
                  defaultWidth: 260,
                  filter: { type: "text", placeholder: "e.g. backup" },
                },
                {
                  key: "state",
                  label: "State",
                  accessor: (job) => job.state,
                  cell: (job) => <ItemStateBadge state={job.state} />,
                  defaultWidth: 110,
                  filter: { type: "enum", options: [{label:"Present",value:"present"},{label:"Absent",value:"absent"}] },
                },
                {
                  key: "source",
                  label: "Source",
                  accessor: (job) => job.source === "group" ? job.source_name : "Host override",
                  cell: (job) => (
                    <Badge variant="outline" className="text-xs">
                      {job.source === "group" ? job.source_name : "Host override"}
                    </Badge>
                  ),
                  defaultWidth: 140,
                  filter: { type: "enum", from: "accessor" },
                },
                {
                  key: "actions",
                  label: "Actions",
                  cell: (job) => (
                    job.source === "group" ? (
                      <Button size="sm" variant="ghost" onClick={() => openCjEditDialog(job)}>
                        Edit
                      </Button>
                    ) : job.source === "host" ? (
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openCjEditDialog(job)}>
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={cjDeleteMutation.isPending}
                          onClick={() => handleCjDelete(job.name, job.user)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {cjDeleteMutation.isPending ? "…" : "Delete"}
                        </Button>
                      </div>
                    ) : (
                      <span className="text-slate-600 text-xs">Read-only</span>
                    )
                  ),
                  defaultWidth: 160,
                  resizable: false,
                  sortable: false,
                },
              ]}
            />
          )}

          <Dialog open={cjDialogOpen} onOpenChange={(open) => { if (!open) closeCjDialog(); else setCjDialogOpen(true) }}>
            <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>{cjEditingId != null ? "Edit Cron Job Override" : "Add Cron Job Override"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCjSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="cj-name">Name</Label>
                  <Input
                    id="cj-name"
                    type="text"
                    placeholder="e.g. backup-db, cleanup-logs"
                    value={cjName}
                    onChange={(e) => setCjName(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-user">User</Label>
                  <Input
                    id="cj-user"
                    type="text"
                    placeholder="root"
                    value={cjUser}
                    onChange={(e) => setCjUser(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-schedule">Schedule (cron expression)</Label>
                  <Input
                    id="cj-schedule"
                    type="text"
                    placeholder="*/5 * * * *"
                    value={cjSchedule}
                    onChange={(e) => setCjSchedule(e.target.value)}
                    required
                  />
                  {cjSchedule.trim() && (
                    <p className="text-xs text-slate-400">
                      {cronToHuman(cjSchedule)}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-command">Command</Label>
                  <textarea
                    id="cj-command"
                    placeholder="e.g. /usr/local/bin/backup.sh --full"
                    value={cjCommand}
                    onChange={(e) => setCjCommand(e.target.value)}
                    required
                    rows={3}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-state">State</Label>
                  <select
                    id="cj-state"
                    value={cjState}
                    onChange={(e) => setCjState(e.target.value as "present" | "absent")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-priority">Priority</Label>
                  <Input
                    id="cj-priority"
                    type="number"
                    value={cjPriority}
                    onChange={(e) => setCjPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-comment">Comment (optional)</Label>
                  <textarea
                    id="cj-comment"
                    placeholder="Optional description"
                    value={cjComment}
                    onChange={(e) => setCjComment(e.target.value)}
                    rows={2}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                  />
                </div>

                <div className="space-y-2">
                  <Label>Environment Variables</Label>
                  <div className="space-y-2">
                    {cjEnvVars.map((v, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <Input
                          type="text"
                          placeholder="KEY"
                          value={v.key}
                          onChange={(e) => {
                            const updated = cjEnvVars.map((ev, i) => i === idx ? { ...ev, key: e.target.value } : ev)
                            setCjEnvVars(updated)
                          }}
                          className="flex-1 font-mono text-xs"
                        />
                        <span className="text-slate-500 text-xs">=</span>
                        <Input
                          type="text"
                          placeholder="value"
                          value={v.value}
                          onChange={(e) => {
                            const updated = cjEnvVars.map((ev, i) => i === idx ? { ...ev, value: e.target.value } : ev)
                            setCjEnvVars(updated)
                          }}
                          className="flex-1 font-mono text-xs"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setCjEnvVars(cjEnvVars.filter((_, i) => i !== idx))}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950 px-2"
                        >
                          &times;
                        </Button>
                      </div>
                    ))}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setCjEnvVars([...cjEnvVars, { key: "", value: "" }])}
                    >
                      + Add variable
                    </Button>
                  </div>
                </div>

                {(cjSaveMutation.error || cjUpdateMutation.error) && (
                  <p className="text-sm text-red-400">{(cjSaveMutation.error ?? cjUpdateMutation.error)?.message}</p>
                )}

                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeCjDialog}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={cjSaveMutation.isPending || cjUpdateMutation.isPending}>
                    {cjSaveMutation.isPending || cjUpdateMutation.isPending
                      ? "Saving..."
                      : cjEditingId != null ? "Save Changes" : "Create Override"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
          <CurrentStateSection moduleType="cron" modules={currentStateQuery.data} hostId={id} />
        </div>
      )}

      {activeTab === "packages" && (() => {
        // Parse per-package errors from module error_message (format: "pkg1: msg1; pkg2: msg2")
        const packageModule = currentStateQuery.data?.find(m => m.module_type === "package")
        const packageErrors: Record<string, string> = {}
        if (packageModule?.error_message) {
          for (const part of packageModule.error_message.split("; ")) {
            const colonIdx = part.indexOf(": ")
            if (colonIdx > 0) {
              packageErrors[part.slice(0, colonIdx)] = part.slice(colonIdx + 2)
            }
          }
        }
        return (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Packages</h2>
              <p className="text-slate-400 text-sm mt-1">
                Packages applied to this host from groups and host-level overrides.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!host?.ssh_key_id || !!syncPreview}
                onClick={() => openModulePreview("packages")}
              >
                <ArrowUpFromLineIcon className="w-4 h-4 mr-1" />
                Sync
              </Button>
              <Button onClick={openPpDialog}>Add Override</Button>
            </div>
          </div>

          {ppDeleteMutation.error && (
            <div className="text-red-400 text-sm">{ppDeleteMutation.error.message}</div>
          )}

          {showPackagesLoading && <TableSkeleton rows={3} columns={4} />}

          {packagesError && (
            <div className="text-red-400 py-6 text-center">Failed to load packages</div>
          )}

          {!packagesLoading && !packagesError && (
            <DataTable<EffectivePackage>
              tableId="host-effective-packages"
              data={effectivePackages}
              emptyMessage="No packages configured. Add a host override or assign packages to a group."
              getRowKey={(pkg) => `${pkg.source}-${pkg.source_id}-${pkg.package_name}`}
              columns={[
                {
                  key: "package_name",
                  label: "Package Name",
                  accessor: (pkg) => pkg.package_name,
                  cell: (pkg) => (
                    <div className="font-mono text-sm">
                      <span className={packageErrors[pkg.package_name] ? "text-red-400" : "text-white"}>{pkg.package_name}</span>
                      {packageErrors[pkg.package_name] && (
                        <div className="text-red-400 text-xs mt-1 font-sans">{packageErrors[pkg.package_name]}</div>
                      )}
                    </div>
                  ),
                  defaultWidth: 180,
                  filter: { type: "text", placeholder: "e.g. curl" },
                },
                {
                  key: "version",
                  label: "Version",
                  accessor: (pkg) => pkg.version ?? "any",
                  cell: (pkg) => <span className="font-mono text-slate-300 text-xs">{pkg.version ?? "any"}</span>,
                  defaultWidth: 100,
                  filter: { type: "text" },
                },
                {
                  key: "state",
                  label: "State",
                  accessor: (pkg) => pkg.state,
                  cell: (pkg) => <PackageStateBadge state={pkg.state} />,
                  defaultWidth: 110,
                  filter: { type: "enum", options: [{label:"Present",value:"present"},{label:"Absent",value:"absent"},{label:"Latest",value:"latest"}] },
                },
                {
                  key: "package_manager",
                  label: "Package Manager",
                  accessor: (pkg) => pkg.package_manager,
                  cell: (pkg) => <Badge variant="outline" className="text-xs font-mono">{pkg.package_manager}</Badge>,
                  defaultWidth: 140,
                  filter: { type: "enum", options: [{label:"Auto",value:"auto"},{label:"APT",value:"apt"},{label:"DNF",value:"dnf"},{label:"YUM",value:"yum"}] },
                },
                {
                  key: "hold",
                  label: "Hold",
                  accessor: (pkg) => pkg.hold,
                  cell: (pkg) => (
                    pkg.hold ? (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400">held</span>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )
                  ),
                  defaultWidth: 80,
                  filter: { type: "boolean" },
                },
                {
                  key: "source",
                  label: "Source",
                  accessor: (pkg) => pkg.source === "group" ? pkg.source_name : "Host override",
                  cell: (pkg) => (
                    <Badge variant="outline" className="text-xs">
                      {pkg.source === "group" ? pkg.source_name : "Host override"}
                    </Badge>
                  ),
                  defaultWidth: 140,
                  filter: { type: "enum", from: "accessor" },
                },
                {
                  key: "actions",
                  label: "Actions",
                  cell: (pkg) => (
                    pkg.source === "group" ? (
                      <Button size="sm" variant="ghost" onClick={() => openPpEditDialog(pkg)}>
                        Edit
                      </Button>
                    ) : pkg.source === "host" ? (
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openPpEditDialog(pkg)}>
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={ppDeleteMutation.isPending}
                          onClick={() => handlePpDelete(pkg.package_name)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {ppDeleteMutation.isPending ? "…" : "Delete"}
                        </Button>
                      </div>
                    ) : (
                      <span className="text-slate-600 text-xs">Read-only</span>
                    )
                  ),
                  defaultWidth: 160,
                  resizable: false,
                  sortable: false,
                },
              ]}
            />
          )}

          {/* Effective Repositories */}
          <div>
            <h2 className="text-lg font-semibold text-white">Effective Repositories</h2>
            <p className="text-slate-400 text-sm mt-1">
              Package repositories applied from groups. Manage these in group settings.
            </p>
          </div>

          {effectiveReposQuery.isLoading && <TableSkeleton rows={2} columns={4} />}

          {effectiveReposQuery.error && (
            <div className="text-red-400 py-6 text-center">Failed to load repositories</div>
          )}

          {!effectiveReposQuery.isLoading && !effectiveReposQuery.error && (
            <DataTable<PackageRepository>
              tableId="host-effective-package-repos"
              data={effectiveRepos}
              emptyMessage="No repositories configured. Add repositories at the group level."
              getRowKey={(repo) => repo.id}
              columns={[
                {
                  key: "name",
                  label: "Name",
                  accessor: (repo) => repo.name,
                  cell: (repo) => <span className="font-medium text-white">{repo.name}</span>,
                  defaultWidth: 160,
                  filter: { type: "text" },
                },
                {
                  key: "url",
                  label: "URL",
                  accessor: (repo) => repo.url,
                  cell: (repo) => <span className="font-mono text-slate-300 text-xs truncate">{repo.url}</span>,
                  defaultWidth: 260,
                  filter: { type: "text", placeholder: "e.g. github.com" },
                },
                {
                  key: "repo_type",
                  label: "Type",
                  accessor: (repo) => repo.repo_type,
                  cell: (repo) => <Badge variant="outline" className="text-xs font-mono">{repo.repo_type}</Badge>,
                  defaultWidth: 80,
                  filter: { type: "enum", options: [{label:"APT",value:"apt"},{label:"YUM",value:"yum"}] },
                },
                {
                  key: "distribution",
                  label: "Distribution",
                  accessor: (repo) => repo.distribution ?? "",
                  cell: (repo) => (
                    <span className="text-slate-400 text-sm">
                      {repo.distribution ?? <span className="text-slate-600">—</span>}
                    </span>
                  ),
                  defaultWidth: 140,
                  filter: { type: "text" },
                },
                {
                  key: "state",
                  label: "State",
                  accessor: (repo) => repo.state,
                  cell: (repo) => <ItemStateBadge state={repo.state} />,
                  defaultWidth: 110,
                  filter: { type: "enum", from: "accessor" },
                },
                {
                  key: "source",
                  label: "Source",
                  accessor: (repo) => groups?.find(g => g.id === repo.group_id)?.name ?? String(repo.group_id),
                  cell: (repo) => (
                    <Link
                      href={`/groups/${repo.group_id}`}
                      className="text-xs text-blue-400 hover:text-blue-300 underline"
                    >
                      Group {groups?.find(g => g.id === repo.group_id)?.name ?? repo.group_id}
                    </Link>
                  ),
                  defaultWidth: 140,
                  filter: { type: "enum", from: "accessor" },
                },
              ]}
            />
          )}

          <Dialog open={ppDialogOpen} onOpenChange={(open) => { if (!open) closePpDialog(); else setPpDialogOpen(true) }}>
            <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>{ppEditingId != null ? "Edit Package Override" : "Add Package Override"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handlePpSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="pp-name">Package Name</Label>
                  <Input
                    id="pp-name"
                    type="text"
                    placeholder="e.g. nginx, curl, htop"
                    value={ppName}
                    onChange={(e) => setPpName(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-version">Version</Label>
                  <Input
                    id="pp-version"
                    type="text"
                    placeholder="any version"
                    value={ppVersion}
                    onChange={(e) => setPpVersion(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-state">State</Label>
                  <select
                    id="pp-state"
                    value={ppState}
                    onChange={(e) => setPpState(e.target.value as "present" | "absent" | "latest")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                    <option value="latest">Latest</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-manager">Package Manager</Label>
                  <select
                    id="pp-manager"
                    value={ppManager}
                    onChange={(e) => setPpManager(e.target.value as "auto" | "apt" | "dnf" | "yum")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="auto">Auto-detect</option>
                    <option value="apt">apt</option>
                    <option value="dnf">dnf</option>
                    <option value="yum">yum</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-comment">Comment (optional)</Label>
                  <textarea
                    id="pp-comment"
                    placeholder="Optional description"
                    value={ppComment}
                    onChange={(e) => setPpComment(e.target.value)}
                    rows={2}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                  />
                </div>

                <div className="flex items-center gap-2">
                  <input
                    id="pp-hold"
                    type="checkbox"
                    checked={ppHold}
                    onChange={(e) => setPpHold(e.target.checked)}
                    className="rounded border-input"
                  />
                  <Label htmlFor="pp-hold">Hold package</Label>
                  <span className="text-xs text-slate-500">Prevent automatic upgrades</span>
                </div>

                {(ppSaveMutation.error || ppUpdateMutation.error) && (
                  <p className="text-sm text-red-400">{(ppSaveMutation.error ?? ppUpdateMutation.error)?.message}</p>
                )}

                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closePpDialog}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={ppSaveMutation.isPending || ppUpdateMutation.isPending}>
                    {ppSaveMutation.isPending || ppUpdateMutation.isPending
                      ? "Saving..."
                      : ppEditingId != null ? "Save Changes" : "Create Override"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
          <CurrentStateSection moduleType="package" modules={currentStateQuery.data} hostId={id} />
        </div>
        )
      })()}

      {activeTab === "ca-certs" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <ShieldCheckIcon className="w-5 h-5" />
                Effective CA Certs
              </h2>
              <p className="text-slate-400 text-sm mt-1">
                Trusted certificate authorities applied to this host from groups and host-level overrides.
                Deployed as a one-time action — no drift detection.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={caDeployMutation.isPending || !host?.ssh_key_id}
                onClick={() => setCaDeployConfirm(true)}
              >
                <PlayIcon className="w-4 h-4 mr-1" />
                Deploy
              </Button>
              <Button onClick={openCaDialog}>Add Override</Button>
            </div>
          </div>

          {caDeleteMutation.error && (
            <div className="text-red-400 text-sm">{caDeleteMutation.error.message}</div>
          )}
          {caDeployMutation.error && (
            <div className="text-red-400 text-sm">{caDeployMutation.error.message}</div>
          )}

          {showCACertsLoading && <TableSkeleton rows={3} columns={5} />}

          {caCertsError && (
            <div className="text-red-400 py-6 text-center">Failed to load CA certificates</div>
          )}

          {!caCertsLoading && !caCertsError && (
            <DataTable<EffectiveCACert>
              tableId="host-effective-ca-certs"
              data={effectiveCACerts}
              emptyMessage="No CA certificates configured. Add a host override or assign certificates to a group."
              getRowKey={(c) => `${c.source}-${c.source_id}-${c.fingerprint_sha256}`}
              columns={[
                {
                  key: "name",
                  label: "Name",
                  accessor: (c) => c.name,
                  cell: (c) => <span className="font-medium text-white">{c.name}</span>,
                  defaultWidth: 160,
                  filter: { type: "text" },
                },
                {
                  key: "subject",
                  label: "Subject",
                  accessor: (c) => c.subject ?? "",
                  cell: (c) => (
                    <span className="text-slate-300 text-sm truncate" title={c.subject ?? ""}>
                      {c.subject ?? "—"}
                    </span>
                  ),
                  defaultWidth: 200,
                  filter: { type: "text", placeholder: "e.g. Root CA" },
                },
                {
                  key: "not_after",
                  label: "Expires",
                  accessor: (c) => c.not_after ?? "",
                  cell: (c) => (
                    <span className="text-slate-300 text-sm">
                      {c.not_after ? new Date(c.not_after).toLocaleDateString() : "—"}
                    </span>
                  ),
                  defaultWidth: 120,
                  filter: { type: "dateRange" },
                },
                {
                  key: "fingerprint_sha256",
                  label: "Fingerprint (SHA-256)",
                  accessor: (c) => c.fingerprint_sha256,
                  cell: (c) => {
                    const parts = c.fingerprint_sha256.split(":")
                    const fpShort = parts.length <= 14
                      ? c.fingerprint_sha256
                      : `${parts.slice(0, 6).join(":")}…${parts.slice(-6).join(":")}`
                    return (
                      <span className="font-mono text-xs text-slate-400" title={c.fingerprint_sha256}>
                        {fpShort}
                      </span>
                    )
                  },
                  defaultWidth: 200,
                },
                {
                  key: "state",
                  label: "State",
                  accessor: (c) => c.state,
                  cell: (c) => <ItemStateBadge state={c.state} />,
                  defaultWidth: 110,
                  filter: { type: "enum", options: [{label:"Present",value:"present"},{label:"Absent",value:"absent"}] },
                },
                {
                  key: "source",
                  label: "Source",
                  accessor: (c) => c.source === "group" ? c.source_name : "Host override",
                  cell: (c) => (
                    <Badge variant="outline" className="text-xs">
                      {c.source === "group" ? c.source_name : "Host override"}
                    </Badge>
                  ),
                  defaultWidth: 140,
                  filter: { type: "enum", from: "accessor" },
                },
                {
                  key: "actions",
                  label: "Actions",
                  cell: (c) => (
                    c.source === "group" ? (
                      <Button size="sm" variant="ghost" onClick={() => openCaEditDialog(c)}>
                        Edit
                      </Button>
                    ) : c.source === "host" ? (
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openCaEditDialog(c)}>
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={caDeleteMutation.isPending}
                          onClick={() => handleCaDelete(c.fingerprint_sha256, c.name)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {caDeleteMutation.isPending ? "…" : "Delete"}
                        </Button>
                      </div>
                    ) : (
                      <span className="text-slate-600 text-xs">Read-only</span>
                    )
                  ),
                  defaultWidth: 160,
                  resizable: false,
                  sortable: false,
                },
              ]}
            />
          )}

          {/* Recent runs */}
          <div>
            <h2 className="text-lg font-semibold text-white">Recent Deployment Runs</h2>
            <p className="text-slate-400 text-sm mt-1">Deployment history for this host.</p>
          </div>

          <DataTable<CACertActionRun>
            tableId="host-ca-cert-runs"
            data={hostCACertRuns?.slice(0, 20)}
            emptyMessage="No deployment runs yet."
            getRowKey={(r) => r.id}
            columns={[
              {
                key: "id",
                label: "Run #",
                accessor: (r) => r.id,
                cell: (r) => <span className="font-mono text-xs text-slate-400">#{r.id}</span>,
                defaultWidth: 80,
                sortable: true,
              },
              {
                key: "status",
                label: "Status",
                accessor: (r) => r.status,
                cell: (r) => {
                  const statusClass: Record<string, string> = {
                    pending: "bg-slate-600 text-white",
                    running: "bg-blue-600 text-white",
                    success: "bg-green-600 text-white",
                    failed: "bg-red-600 text-white",
                    cancelled: "bg-slate-500 text-white",
                  }
                  return <Badge className={statusClass[r.status] ?? ""}>{r.status}</Badge>
                },
                defaultWidth: 110,
                filter: { type: "enum", options: [{label:"Pending",value:"pending"},{label:"Running",value:"running"},{label:"Success",value:"success"},{label:"Failed",value:"failed"},{label:"Cancelled",value:"cancelled"}] },
              },
              {
                key: "started_at",
                label: "Started",
                accessor: (r) => r.started_at ?? "",
                cell: (r) => {
                  const s = r.started_at
                  if (!s) return <span className="text-slate-300 text-sm">—</span>
                  try { return <span className="text-slate-300 text-sm">{new Date(s).toLocaleString()}</span> } catch { return <span className="text-slate-300 text-sm">{s}</span> }
                },
                defaultWidth: 180,
                filter: { type: "dateRange" },
              },
              {
                key: "completed_at",
                label: "Completed",
                accessor: (r) => r.completed_at ?? "",
                cell: (r) => {
                  const s = r.completed_at
                  if (!s) return <span className="text-slate-300 text-sm">—</span>
                  try { return <span className="text-slate-300 text-sm">{new Date(s).toLocaleString()}</span> } catch { return <span className="text-slate-300 text-sm">{s}</span> }
                },
                defaultWidth: 180,
                filter: { type: "dateRange" },
              },
            ]}
          />

          <Dialog open={caDialogOpen} onOpenChange={(open) => { if (!open) closeCaDialog(); else setCaDialogOpen(true) }}>
            <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>{caEditingId != null ? "Edit CA Certificate Override" : "Add CA Certificate Override"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCaSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="ca-name">Display name</Label>
                  <Input
                    id="ca-name"
                    type="text"
                    placeholder="e.g. Internal Root CA"
                    value={caName}
                    onChange={(e) => setCaName(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ca-pem">PEM content</Label>
                  <textarea
                    id="ca-pem"
                    className="w-full h-48 rounded-lg border border-input bg-transparent px-3 py-2 font-mono text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                    value={caPem}
                    onChange={(e) => setCaPem(e.target.value)}
                    placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ca-state">State</Label>
                  <select
                    id="ca-state"
                    value={caState}
                    onChange={(e) => setCaState(e.target.value as "present" | "absent")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ca-comment">Comment (optional)</Label>
                  <Input
                    id="ca-comment"
                    type="text"
                    placeholder="Why this CA is trusted"
                    value={caComment}
                    onChange={(e) => setCaComment(e.target.value)}
                  />
                </div>

                {(caSaveMutation.error || caUpdateMutation.error) && (
                  <p className="text-sm text-red-400">{(caSaveMutation.error ?? caUpdateMutation.error)?.message}</p>
                )}

                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeCaDialog}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={caSaveMutation.isPending || caUpdateMutation.isPending}>
                    {caSaveMutation.isPending || caUpdateMutation.isPending
                      ? "Saving..."
                      : caEditingId != null ? "Save Changes" : "Create Override"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>

          <ConfirmDialog
            open={caDeployConfirm}
            onOpenChange={setCaDeployConfirm}
            title="Deploy CA certificates to this host?"
            description="This runs the CA cert deployment Ansible playbook on this host. If a deploy is already in progress it will be rejected."
            confirmLabel="Deploy"
            onConfirm={() => caDeployMutation.mutate(undefined)}
            loading={caDeployMutation.isPending}
          />
        </div>
      )}

      {activeTab === "dns" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective DNS Resolver</h2>
              <p className="text-slate-400 text-sm mt-1">
                DNS resolver configuration applied to this host.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!host?.ssh_key_id || !!syncPreview}
                onClick={() => openModulePreview("dns")}
              >
                <ArrowUpFromLineIcon className="w-4 h-4 mr-1" />
                Sync DNS
              </Button>
              {hostResolverOverride && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={resolverDeleteMutation.isPending}
                  onClick={handleResolverDeleteOverride}
                  className="text-red-400 hover:text-red-300 hover:bg-red-950"
                >
                  {resolverDeleteMutation.isPending ? "Deleting..." : "Delete Override"}
                </Button>
              )}
              <Button size="sm" onClick={openResolverEditDialog}>
                {hostResolverOverride ? "Edit Override" : effectiveResolver ? "Create Override" : "Configure DNS"}
              </Button>
            </div>
          </div>

          {showResolverLoading && <CardSkeleton />}

          {resolverNotConfigured && (
            <div className="text-slate-400 py-6 text-center">
              DNS is not managed for this host. Configure DNS at the group level to get started.
            </div>
          )}

          {resolverError && (
            <div className="text-red-400 py-6 text-center">Failed to load DNS resolver</div>
          )}

          {!resolverLoading && !resolverError && effectiveResolver && (
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 space-y-4">
              <div className="flex items-center gap-4 py-2 border-b border-slate-800">
                <span className="text-slate-400 text-sm w-40 shrink-0">Source</span>
                <Badge variant="outline" className="text-xs">
                  {effectiveResolver.source === "group"
                    ? effectiveResolver.source_name
                    : "Host override"}
                </Badge>
              </div>

              <div className="flex items-center gap-4 py-2 border-b border-slate-800">
                <span className="text-slate-400 text-sm w-40 shrink-0">Resolver Type</span>
                <span className="text-white text-sm">
                  {effectiveResolver.resolver_type === "resolv_conf" && "resolv.conf"}
                  {effectiveResolver.resolver_type === "systemd_resolved" && "systemd-resolved"}
                  {effectiveResolver.resolver_type === "networkmanager" && "NetworkManager"}
                </span>
              </div>

              <div className="flex items-start gap-4 py-2 border-b border-slate-800">
                <span className="text-slate-400 text-sm w-40 shrink-0">Nameservers</span>
                <div className="space-y-1">
                  {effectiveResolver.nameservers.length > 0 ? effectiveResolver.nameservers.map((ns, idx) => (
                    <div key={idx} className="font-mono text-sm text-slate-300">{ns}</div>
                  )) : (
                    <span className="text-slate-500 text-sm">None configured</span>
                  )}
                </div>
              </div>

              <div className="flex items-start gap-4 py-2 border-b border-slate-800">
                <span className="text-slate-400 text-sm w-40 shrink-0">Search Domains</span>
                <div className="space-y-1">
                  {effectiveResolver.search_domains.length > 0 ? effectiveResolver.search_domains.map((sd, idx) => (
                    <div key={idx} className="font-mono text-sm text-slate-300">{sd}</div>
                  )) : (
                    <span className="text-slate-500 text-sm">None configured</span>
                  )}
                </div>
              </div>

              {Object.keys(effectiveResolver.options).length > 0 && (
                <div className="flex items-start gap-4 py-2 border-b border-slate-800">
                  <span className="text-slate-400 text-sm w-40 shrink-0">Options</span>
                  <div className="space-y-1">
                    {Object.entries(effectiveResolver.options).map(([key, value]) => (
                      <div key={key} className="font-mono text-sm text-slate-300">
                        {key}: {String(value)}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {effectiveResolver.resolver_type === "systemd_resolved" && (
                <div className="flex items-center gap-4 py-2 border-b border-slate-800 last:border-0">
                  <span className="text-slate-400 text-sm w-40 shrink-0">DNS-over-TLS</span>
                  <Badge className={effectiveResolver.dns_over_tls ? "bg-green-700 text-white" : "bg-slate-600 text-white"}>
                    {effectiveResolver.dns_over_tls ? "Enabled" : "Disabled"}
                  </Badge>
                </div>
              )}
            </div>
          )}

          {!resolverLoading && !resolverError && effectiveResolver && (
            <div className="text-xs text-slate-500">
              {hostResolverOverride
                ? "This host has a resolver override."
                : "Inherited from group configuration."}
            </div>
          )}

          {resolverDeleteMutation.error && (
            <div className="text-red-400 text-sm">{resolverDeleteMutation.error.message}</div>
          )}

          <Dialog open={resolverDialogOpen} onOpenChange={setResolverDialogOpen}>
            <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>{hostResolverOverride ? "Edit Host Override" : "Create Host Override"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleResolverSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="resolver-type">Resolver Type</Label>
                  <select
                    id="resolver-type"
                    value={resolverType}
                    onChange={(e) => setResolverType(e.target.value as typeof resolverType)}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="resolv_conf">resolv.conf</option>
                    <option value="systemd_resolved">systemd-resolved</option>
                    <option value="networkmanager">NetworkManager</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label>Nameservers</Label>
                  {resolverNameservers.length > 0 && (
                    <div className="space-y-1">
                      {resolverNameservers.map((ns, idx) => (
                        <div key={idx} className="flex items-center gap-2 rounded border border-slate-700 bg-slate-800 px-3 py-1.5">
                          <span className="text-sm font-mono text-slate-300 flex-1">{ns}</span>
                          <button
                            type="button"
                            onClick={() => setResolverNameservers(resolverNameservers.filter((_, i) => i !== idx))}
                            className="text-red-400 hover:text-red-300 text-sm px-1"
                          >
                            &times;
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <Input
                      type="text"
                      placeholder="e.g. 8.8.8.8 or 2001:4860:4860::8888"
                      value={resolverNsInput}
                      onChange={(e) => setResolverNsInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addResolverNameserver() } }}
                      className="flex-1"
                    />
                    <Button type="button" variant="outline" size="sm" onClick={addResolverNameserver}>Add</Button>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>Search Domains</Label>
                  {resolverSearchDomains.length > 0 && (
                    <div className="space-y-1">
                      {resolverSearchDomains.map((sd, idx) => (
                        <div key={idx} className="flex items-center gap-2 rounded border border-slate-700 bg-slate-800 px-3 py-1.5">
                          <span className="text-sm font-mono text-slate-300 flex-1">{sd}</span>
                          <button
                            type="button"
                            onClick={() => setResolverSearchDomains(resolverSearchDomains.filter((_, i) => i !== idx))}
                            className="text-red-400 hover:text-red-300 text-sm px-1"
                          >
                            &times;
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <Input
                      type="text"
                      placeholder="e.g. example.com"
                      value={resolverSdInput}
                      onChange={(e) => setResolverSdInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addResolverSearchDomain() } }}
                      className="flex-1"
                    />
                    <Button type="button" variant="outline" size="sm" onClick={addResolverSearchDomain}>Add</Button>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>Options</Label>
                  <div className="space-y-2">
                    {resolverOptions.map((opt, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <select
                          value={opt.key}
                          onChange={(e) => setResolverOptions(resolverOptions.map((o, i) => i === idx ? { ...o, key: e.target.value } : o))}
                          className="w-40 rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                        >
                          <option value="">Select option…</option>
                          <option value="ndots">ndots</option>
                          <option value="timeout">timeout</option>
                          <option value="attempts">attempts</option>
                          <option value="rotate">rotate</option>
                          <option value="edns0">edns0</option>
                        </select>
                        <Input
                          type="text"
                          placeholder="value"
                          value={opt.value}
                          onChange={(e) => setResolverOptions(resolverOptions.map((o, i) => i === idx ? { ...o, value: e.target.value } : o))}
                          className="flex-1 font-mono text-xs"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setResolverOptions(resolverOptions.filter((_, i) => i !== idx))}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950 px-2"
                        >
                          &times;
                        </Button>
                      </div>
                    ))}
                    <Button type="button" variant="outline" size="sm" onClick={() => setResolverOptions([...resolverOptions, { key: "", value: "" }])}>
                      + Add option
                    </Button>
                  </div>
                </div>

                {resolverType === "systemd_resolved" && (
                  <div className="flex items-center gap-2">
                    <input
                      id="resolver-dot"
                      type="checkbox"
                      checked={resolverDnsOverTls}
                      onChange={(e) => setResolverDnsOverTls(e.target.checked)}
                      className="rounded border-input"
                    />
                    <Label htmlFor="resolver-dot">DNS-over-TLS</Label>
                    <span className="text-xs text-slate-500">Encrypt DNS queries (systemd-resolved only)</span>
                  </div>
                )}

                {resolverSaveMutation.error && (
                  <p className="text-sm text-red-400">{resolverSaveMutation.error.message}</p>
                )}

                <DialogFooter>
                  <Button type="button" variant="outline" onClick={() => setResolverDialogOpen(false)}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={resolverSaveMutation.isPending}>
                    {resolverSaveMutation.isPending
                      ? "Saving..."
                      : hostResolverOverride ? "Save Changes" : "Create Override"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>

          <CurrentStateSection moduleType="resolver" modules={currentStateQuery.data} hostId={id} />
        </div>
      )}

      {activeTab === "actions" && (
        <ActionsTab scope="host" targetId={id} host={hostQuery.data} />
      )}

      {activeTab === "schedules" && (
        <ScheduledActionsSection scope="host" targetId={id} />
      )}

      {confirmState && (
        <ConfirmDialog
          open={confirmState.open}
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel={confirmState.confirmLabel ?? "Confirm"}
          variant={confirmState.variant ?? "destructive"}
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}

      {terminalOpen && host && (
        <div className="fixed inset-x-0 bottom-0 z-50 h-[50vh] border-t border-slate-700 bg-slate-950 flex flex-col">
          <div className="flex items-center justify-between px-4 py-2 border-b border-slate-800">
            <div className="flex items-center gap-2 text-sm text-slate-300">
              <TerminalIcon className="w-4 h-4" />
              <span>{host.hostname}</span>
            </div>
            <div className="flex items-center gap-2">
              <Link href={`/hosts/${id}/terminal`} className="text-xs text-slate-400 hover:text-white">
                Open Full Page
              </Link>
              <button onClick={() => setTerminalOpen(false)} className="text-slate-400 hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
          <div className="flex-1 min-h-0 p-1">
            <SshTerminal hostId={id} hostname={host.hostname} />
          </div>
        </div>
      )}
      {host && (
        <ConfirmDialog
          open={trustKeyOpen}
          onOpenChange={(open) => { if (!trustingKey) setTrustKeyOpen(open) }}
          title={`Trust new host key for ${host.hostname}?`}
          description={`This clears the stored SSH host key. The next connection will accept whatever key ${host.hostname} (${host.ip_address}) presents and store it. If the host was not intentionally re-keyed, an attacker may be intercepting the connection.`}
          confirmLabel={trustingKey ? "Trusting..." : "Trust new key"}
          variant="destructive"
          loading={trustingKey}
          onConfirm={async () => {
            setTrustingKey(true)
            try {
              await apiFetch(`/api/hosts/${id}/trust-host-key`, { method: "POST" })
              await queryClient.invalidateQueries({ queryKey: ["host", String(id)] })
              toast.success("Host key cleared. Next connection will re-TOFU.")
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Failed to clear host key")
            } finally {
              setTrustingKey(false)
              setTrustKeyOpen(false)
            }
          }}
        />
      )}

      {syncPreview && (
        <Dialog open onOpenChange={(o) => { if (!o) closeSyncPreview() }}>
          <DialogContent className="sm:max-w-3xl">
            <DialogHeader>
              <DialogTitle>
                {syncPreview.scope === "all"
                  ? "Sync All — Preview"
                  : `Sync ${moduleLabel(syncPreview.module ?? "")} — Preview`}
              </DialogTitle>
            </DialogHeader>

            <div className="space-y-3 max-h-[60vh] overflow-y-auto">
              {syncPreview.loading && <CardSkeleton />}

              {syncPreview.error && (
                <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
                  {syncPreview.error}
                </div>
              )}

              {syncPreview.diffs && !syncPreview.loading && (
                <>
                  {!syncPreview.diffs.some((d) => d.has_changes) && (
                    <div className="text-green-400 text-sm">Everything is already in sync.</div>
                  )}
                  {/* Changed modules first so actionable content sits at the top; unchanged cards collapse by default. */}
                  {[...syncPreview.diffs]
                    .sort((a, b) => Number(b.has_changes) - Number(a.has_changes))
                    .map((d) => (
                      <ModuleDiffView
                        key={d.module}
                        diff={d}
                        showHeader={syncPreview.scope === "all"}
                        defaultExpanded={d.has_changes}
                      />
                    ))}
                </>
              )}

              {bulkJob && (
                <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-sm text-slate-300">
                  Job status: <span className="font-medium">{bulkJob.status}</span>
                </div>
              )}

              {applyError && (
                <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
                  {applyError}
                </div>
              )}
            </div>

            <DialogFooter>
              {(() => {
                const jobDone = bulkJob?.status === "success" || bulkJob?.status === "failed"
                // Once the preview has loaded with nothing to apply, the primary
                // action is just to dismiss — a disabled "Apply Changes" reads as
                // "something is broken" rather than "nothing to do".
                const noChanges =
                  !!syncPreview.diffs &&
                  !syncPreview.loading &&
                  !syncPreview.error &&
                  !syncPreview.diffs.some((d) => d.has_changes)
                return (
                  <>
                    <Button variant="outline" onClick={closeSyncPreview} disabled={applying}>
                      {jobDone || noChanges ? "Close" : "Cancel"}
                    </Button>
                    {!jobDone && !noChanges && (
                      <Button
                        onClick={syncPreview.scope === "all" ? applyBulkSync : applyModuleSync}
                        disabled={applying || syncPreview.loading || !!syncPreview.error || !syncPreview.diffs}
                      >
                        {applying ? "Applying…" : "Apply Changes"}
                      </Button>
                    )}
                  </>
                )
              })()}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}
