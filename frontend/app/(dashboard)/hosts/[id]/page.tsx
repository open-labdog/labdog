"use client"

import { useState, useEffect, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { SyncStatusBadge, FirewallBadge } from "@/components/status-badge"
import { apiFetch } from "@/lib/api"
import type { Host, FirewallRule, SSHKey, HostGroup, EffectiveService, ServiceRule, EffectiveHostsEntry, HostsEntry } from "@/lib/types"

interface EffectiveRule extends FirewallRule {
  group_id: number
}

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

function formatPorts(rule: FirewallRule): string {
  if (rule.port_start == null) return "—"
  if (rule.port_end != null && rule.port_end !== rule.port_start) {
    return `${rule.port_start}–${rule.port_end}`
  }
  return String(rule.port_start)
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 py-2 border-b border-slate-800 last:border-0">
      <span className="text-slate-400 text-sm w-40 shrink-0">{label}</span>
      <span className="text-white text-sm">{children}</span>
    </div>
  )
}

export default function HostDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<"overview" | "services" | "hosts-file">("overview")
  const [editOpen, setEditOpen] = useState(false)
  const [editHostname, setEditHostname] = useState("")
  const [editIp, setEditIp] = useState("")
  const [editSshPort, setEditSshPort] = useState(22)
  const [editSshKeyId, setEditSshKeyId] = useState<number | null>(null)
  const [editGroups, setEditGroups] = useState<number[]>([])
  const [editError, setEditError] = useState<string | null>(null)
  const [editLoading, setEditLoading] = useState(false)

  const { data: host, isLoading: hostLoading, error: hostError } = useQuery<Host>({
    queryKey: ["host", id],
    queryFn: () => apiFetch<Host>(`/api/hosts/${id}`),
    enabled: !!id,
  })

  const { data: effectiveRules, isLoading: rulesLoading, error: rulesError } = useQuery<EffectiveRule[]>({
    queryKey: ["host-effective-rules", id],
    queryFn: () => apiFetch<EffectiveRule[]>(`/api/hosts/${id}/effective-rules`),
    enabled: !!id,
  })

  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  const { data: effectiveServices, isLoading: servicesLoading, error: servicesError } = useQuery<EffectiveService[]>({
    queryKey: ["host-effective-services", id],
    queryFn: () => apiFetch<EffectiveService[]>(`/api/hosts/${id}/effective-services`),
    enabled: !!id && activeTab === "services",
  })

  const { data: hostOverrides } = useQuery<ServiceRule[]>({
    queryKey: ["host-service-overrides", id],
    queryFn: () => apiFetch<ServiceRule[]>(`/api/hosts/${id}/services`),
    enabled: !!id && activeTab === "services",
  })

  const { data: effectiveHosts, isLoading: hostsEntriesLoading, error: hostsEntriesError } = useQuery<EffectiveHostsEntry[]>({
    queryKey: ["host-effective-hosts-entries", id],
    queryFn: () => apiFetch<EffectiveHostsEntry[]>(`/api/hosts/${id}/effective-hosts-entries`),
    enabled: !!id && activeTab === "hosts-file",
  })

  const { data: hostHostsOverrides } = useQuery<HostsEntry[]>({
    queryKey: ["host-hosts-overrides", id],
    queryFn: () => apiFetch<HostsEntry[]>(`/api/hosts/${id}/hosts-entries`),
    enabled: !!id && activeTab === "hosts-file",
  })

  const [hostsPreview, setHostsPreview] = useState<string | null>(null)
  const [hostsPreviewLoading, setHostsPreviewLoading] = useState(false)
  const [hostsPreviewError, setHostsPreviewError] = useState<string | null>(null)

  const [hostsDialogOpen, setHostsDialogOpen] = useState(false)
  const [hostsIp, setHostsIp] = useState("")
  const [hostsHostname, setHostsHostname] = useState("")
  const [hostsAliases, setHostsAliases] = useState("")
  const [hostsComment, setHostsComment] = useState("")
  const [hostsPriority, setHostsPriority] = useState(100)
  const [hostsFormError, setHostsFormError] = useState<string | null>(null)
  const [hostsFormLoading, setHostsFormLoading] = useState(false)
  const [hostsDeletingId, setHostsDeletingId] = useState<number | null>(null)
  const [hostsDeleteError, setHostsDeleteError] = useState<string | null>(null)

  function openHostsDialog() {
    setHostsIp("")
    setHostsHostname("")
    setHostsAliases("")
    setHostsComment("")
    setHostsPriority(100)
    setHostsFormError(null)
    setHostsDialogOpen(true)
  }

  async function handleHostsSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setHostsFormError(null)
    setHostsFormLoading(true)
    try {
      await apiFetch(`/api/hosts/${id}/hosts-entries`, {
        method: "POST",
        body: JSON.stringify({
          ip_address: hostsIp,
          hostname: hostsHostname,
          aliases: hostsAliases.split(",").map((a) => a.trim()).filter(Boolean),
          comment: hostsComment || null,
          priority: hostsPriority,
        }),
      })
      await queryClient.invalidateQueries({ queryKey: ["host-effective-hosts-entries", id] })
      await queryClient.invalidateQueries({ queryKey: ["host-hosts-overrides", id] })
      setHostsDialogOpen(false)
    } catch (err) {
      setHostsFormError(err instanceof Error ? err.message : "Failed to create entry")
    } finally {
      setHostsFormLoading(false)
    }
  }

  async function handleHostsEntryDelete(entry: HostsEntry) {
    if (!confirm(`Delete hosts entry "${entry.ip_address} ${entry.hostname}"?`)) return
    setHostsDeletingId(entry.id)
    setHostsDeleteError(null)
    try {
      await apiFetch(`/api/hosts/${id}/hosts-entries/${entry.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["host-effective-hosts-entries", id] })
      await queryClient.invalidateQueries({ queryKey: ["host-hosts-overrides", id] })
    } catch (err) {
      setHostsDeleteError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setHostsDeletingId(null)
    }
  }

  async function fetchHostsPreview() {
    setHostsPreviewLoading(true)
    setHostsPreviewError(null)
    try {
      const text = await apiFetch<string>(`/api/hosts/${id}/hosts-file-preview`)
      setHostsPreview(text)
    } catch (err) {
      setHostsPreviewError(err instanceof Error ? err.message : "Failed to load preview")
    } finally {
      setHostsPreviewLoading(false)
    }
  }

  const [svcDialogOpen, setSvcDialogOpen] = useState(false)
  const [svcName, setSvcName] = useState("")
  const [svcState, setSvcState] = useState<"running" | "stopped">("running")
  const [svcEnabled, setSvcEnabled] = useState(true)
  const [svcPriority, setSvcPriority] = useState(100)
  const [svcComment, setSvcComment] = useState("")
  const [svcFormError, setSvcFormError] = useState<string | null>(null)
  const [svcFormLoading, setSvcFormLoading] = useState(false)
  const [svcDeletingName, setSvcDeletingName] = useState<string | null>(null)
  const [svcDeleteError, setSvcDeleteError] = useState<string | null>(null)

  function openSvcDialog() {
    setSvcName("")
    setSvcState("running")
    setSvcEnabled(true)
    setSvcPriority(100)
    setSvcComment("")
    setSvcFormError(null)
    setSvcDialogOpen(true)
  }

  async function handleSvcSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setSvcFormError(null)
    setSvcFormLoading(true)
    try {
      await apiFetch(`/api/hosts/${id}/services`, {
        method: "POST",
        body: JSON.stringify({
          service_name: svcName,
          state: svcState,
          enabled: svcEnabled,
          priority: svcPriority,
          comment: svcComment || null,
        }),
      })
      await queryClient.invalidateQueries({ queryKey: ["host-effective-services", id] })
      await queryClient.invalidateQueries({ queryKey: ["host-service-overrides", id] })
      setSvcDialogOpen(false)
    } catch (err) {
      setSvcFormError(err instanceof Error ? err.message : "Failed to create override")
    } finally {
      setSvcFormLoading(false)
    }
  }

  async function handleSvcDelete(serviceName: string) {
    if (!confirm(`Delete host override for "${serviceName}"?`)) return
    const override = hostOverrides?.find(o => o.service_name === serviceName)
    if (!override) {
      setSvcDeleteError("Override not found")
      return
    }
    setSvcDeletingName(serviceName)
    setSvcDeleteError(null)
    try {
      await apiFetch(`/api/hosts/${id}/services/${override.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["host-effective-services", id] })
      await queryClient.invalidateQueries({ queryKey: ["host-service-overrides", id] })
    } catch (err) {
      setSvcDeleteError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setSvcDeletingName(null)
    }
  }

  useEffect(() => {
    if (editOpen && host) {
      setEditHostname(host.hostname)
      setEditIp(host.ip_address)
      setEditSshPort(host.ssh_port)
      setEditSshKeyId(host.ssh_key_id)
      setEditGroups(host.group_ids ?? [])
      setEditError(null)
    }
  }, [editOpen, host])

  function toggleEditGroup(groupId: number) {
    setEditGroups((prev) =>
      prev.includes(groupId) ? prev.filter((g) => g !== groupId) : [...prev, groupId]
    )
  }

  async function handleEditSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setEditError(null)
    setEditLoading(true)

    try {
      await apiFetch(`/api/hosts/${id}`, {
        method: "PUT",
        body: JSON.stringify({
          hostname: editHostname,
          ip_address: editIp,
          ssh_port: editSshPort,
          ssh_key_id: editSshKeyId,
          group_ids: editGroups,
        }),
      })
      await queryClient.invalidateQueries({ queryKey: ["host", id] })
      await queryClient.invalidateQueries({ queryKey: ["host-effective-rules", id] })
      setEditOpen(false)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed to update host")
    } finally {
      setEditLoading(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Host Info */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">
            {hostLoading ? "Loading…" : host?.hostname ?? `Host #${id}`}
          </h1>
          <p className="text-slate-400 text-sm">Host details and effective firewall rules</p>
        </div>
        {host && (
          <Dialog open={editOpen} onOpenChange={setEditOpen}>
            <DialogTrigger>
              <Button variant="outline" size="sm">Edit</Button>
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
                  <div className="space-y-2">
                    <Label>Groups</Label>
                    <div className="space-y-2 rounded-lg border border-input p-3 dark:bg-input/10">
                      {groups.map((group) => (
                        <label key={group.id} className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={editGroups.includes(group.id)}
                            onChange={() => toggleEditGroup(group.id)}
                            className="rounded border-input"
                          />
                          <span className="text-sm text-foreground">{group.name}</span>
                          {group.description && (
                            <span className="text-xs text-muted-foreground">— {group.description}</span>
                          )}
                        </label>
                      ))}
                    </div>
                  </div>
                )}

                {editError && (
                  <p className="text-sm text-red-400">{editError}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={editLoading}>
                    {editLoading ? "Saving..." : "Save Changes"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setEditOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        )}
      </div>

      <div className="flex gap-1 border-b border-slate-700">
        <button
          onClick={() => setActiveTab("overview")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "overview"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Overview
        </button>
        <button
          onClick={() => setActiveTab("services")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "services"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Services
        </button>
        <button
          onClick={() => setActiveTab("hosts-file")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "hosts-file"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Hosts File
        </button>
      </div>

      {activeTab === "overview" && (
        <>
          {hostError && (
            <div className="text-red-400">Failed to load host details</div>
          )}

          {host && (
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-0">
              <InfoRow label="Hostname">{host.hostname}</InfoRow>
              <InfoRow label="IP Address">
                <span className="font-mono">{host.ip_address}</span>
              </InfoRow>
              <InfoRow label="SSH Port">
                <span className="font-mono">{host.ssh_port}</span>
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
              <InfoRow label="Drift Check">
                {host.drift_check_enabled ? (
                  <Badge className="bg-green-700 text-white">Enabled</Badge>
                ) : (
                  <Badge variant="outline">Disabled</Badge>
                )}
              </InfoRow>
            </div>
          )}

          <div>
            <h2 className="text-lg font-semibold text-white mb-3">Effective Rules</h2>
            <p className="text-slate-400 text-sm mb-4">
              Combined rules applied to this host from all assigned groups, in priority order.
            </p>

            {rulesLoading && (
              <div className="text-slate-400 py-6 text-center">Loading effective rules…</div>
            )}

            {rulesError && (
              <div className="text-red-400 py-6 text-center">Failed to load effective rules</div>
            )}

            {!rulesLoading && !rulesError && effectiveRules && effectiveRules.length === 0 && (
              <div className="text-slate-400 py-6 text-center">
                No effective rules. Assign this host to a group with rules.
              </div>
            )}

            {!rulesLoading && !rulesError && effectiveRules && effectiveRules.length > 0 && (
              <div className="rounded-lg border border-slate-700 bg-slate-900">
                <Table>
                  <TableHeader>
                    <TableRow className="border-slate-700">
                      <TableHead className="w-16">Priority</TableHead>
                      <TableHead>Action</TableHead>
                      <TableHead>Protocol</TableHead>
                      <TableHead>Direction</TableHead>
                      <TableHead>Source</TableHead>
                      <TableHead>Dest</TableHead>
                      <TableHead>Port(s)</TableHead>
                      <TableHead>Group</TableHead>
                      <TableHead>Comment</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {effectiveRules.map((rule) => (
                      <TableRow key={rule.id} className="border-slate-700">
                        <TableCell className="font-mono text-slate-300 text-xs">{rule.priority}</TableCell>
                        <TableCell>
                          <ActionBadge action={rule.action} />
                        </TableCell>
                        <TableCell className="text-slate-300 uppercase text-xs">{rule.protocol}</TableCell>
                        <TableCell className="text-slate-300 capitalize text-xs">{rule.direction}</TableCell>
                        <TableCell className="font-mono text-slate-300 text-xs">{rule.source_cidr ?? "any"}</TableCell>
                        <TableCell className="font-mono text-slate-300 text-xs">{rule.destination_cidr ?? "any"}</TableCell>
                        <TableCell className="font-mono text-slate-300 text-xs">{formatPorts(rule)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs font-mono">
                            #{rule.group_id}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-slate-400 text-xs max-w-[140px] truncate">{rule.comment ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        </>
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
            <Button onClick={openSvcDialog}>Add Override</Button>
          </div>

          {svcDeleteError && (
            <div className="text-red-400 text-sm">{svcDeleteError}</div>
          )}

          {servicesLoading && (
            <div className="text-slate-400 py-6 text-center">Loading services…</div>
          )}

          {servicesError && (
            <div className="text-red-400 py-6 text-center">Failed to load services</div>
          )}

          {!servicesLoading && !servicesError && effectiveServices && effectiveServices.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No services configured. Add a host override or assign service rules to a group.
            </div>
          )}

          {!servicesLoading && !servicesError && effectiveServices && effectiveServices.length > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead>Service Name</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Enabled</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-32">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effectiveServices.map((svc) => (
                    <TableRow key={`${svc.source}-${svc.source_id}-${svc.service_name}`} className="border-slate-700">
                      <TableCell className="font-mono text-white text-sm">{svc.service_name}</TableCell>
                      <TableCell>
                        <Badge className={svc.state === "running" ? "bg-green-600 text-white" : "bg-slate-600 text-white"}>
                          {svc.state.charAt(0).toUpperCase() + svc.state.slice(1)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {svc.enabled ? (
                          <Badge className="bg-green-700 text-white">Enabled</Badge>
                        ) : (
                          <Badge variant="outline">Disabled</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {svc.source === "group" ? `Group: ${svc.source_name}` : "Host override"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {svc.source === "host" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={svcDeletingName === svc.service_name}
                            onClick={() => handleSvcDelete(svc.service_name)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {svcDeletingName === svc.service_name ? "…" : "Delete"}
                          </Button>
                        ) : (
                          <span className="text-slate-600 text-xs">Read-only</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <Dialog open={svcDialogOpen} onOpenChange={setSvcDialogOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Add Service Override</DialogTitle>
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
                    required
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
                </div>

                <div className="space-y-2">
                  <Label htmlFor="svc-priority">Priority</Label>
                  <Input
                    id="svc-priority"
                    type="number"
                    value={svcPriority}
                    onChange={(e) => setSvcPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="svc-comment">Comment</Label>
                  <Input
                    id="svc-comment"
                    type="text"
                    placeholder="Optional comment"
                    value={svcComment}
                    onChange={(e) => setSvcComment(e.target.value)}
                  />
                </div>

                {svcFormError && (
                  <p className="text-sm text-red-400">{svcFormError}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={svcFormLoading}>
                    {svcFormLoading ? "Saving..." : "Create Override"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setSvcDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
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
                onClick={fetchHostsPreview}
                disabled={hostsPreviewLoading}
              >
                {hostsPreviewLoading ? "Loading..." : "Preview File"}
              </Button>
              <Button onClick={openHostsDialog}>Add Override</Button>
            </div>
          </div>

          {hostsDeleteError && (
            <div className="text-red-400 text-sm">{hostsDeleteError}</div>
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

          {hostsEntriesLoading && (
            <div className="text-slate-400 py-6 text-center">Loading hosts entries…</div>
          )}

          {hostsEntriesError && (
            <div className="text-red-400 py-6 text-center">Failed to load hosts entries</div>
          )}

          {!hostsEntriesLoading && !hostsEntriesError && effectiveHosts && effectiveHosts.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No hosts entries configured. Add a host override or assign entries to a group.
            </div>
          )}

          {!hostsEntriesLoading && !hostsEntriesError && effectiveHosts && effectiveHosts.length > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead>IP Address</TableHead>
                    <TableHead>Hostname</TableHead>
                    <TableHead>Aliases</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-32">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effectiveHosts.map((entry) => (
                    <TableRow key={`${entry.source}-${entry.source_id}-${entry.hostname}`} className="border-slate-700">
                      <TableCell className="font-mono text-white text-sm">{entry.ip_address}</TableCell>
                      <TableCell className="font-mono text-slate-300 text-sm">{entry.hostname}</TableCell>
                      <TableCell className="text-slate-300 text-xs max-w-[200px] truncate">
                        {entry.aliases.length > 0 ? entry.aliases.join(", ") : "—"}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {entry.source === "system"
                            ? "System"
                            : entry.source === "group"
                              ? `Group: ${entry.source_name}`
                              : "Host override"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {entry.source === "host" && !entry.is_system ? (
                          (() => {
                            const override = hostHostsOverrides?.find(
                              (o) => o.hostname === entry.hostname && o.ip_address === entry.ip_address
                            )
                            return override ? (
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={hostsDeletingId === override.id}
                                onClick={() => handleHostsEntryDelete(override)}
                                className="text-red-400 hover:text-red-300 hover:bg-red-950"
                              >
                                {hostsDeletingId === override.id ? "…" : "Delete"}
                              </Button>
                            ) : null
                          })()
                        ) : (
                          <span className="text-slate-600 text-xs">Read-only</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <Dialog open={hostsDialogOpen} onOpenChange={setHostsDialogOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Add Hosts Entry Override</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleHostsSubmit} className="space-y-4 mt-2">
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

                {hostsFormError && (
                  <p className="text-sm text-red-400">{hostsFormError}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={hostsFormLoading}>
                    {hostsFormLoading ? "Saving..." : "Create Override"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setHostsDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      )}
    </div>
  )
}
