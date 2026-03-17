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
import type { Host, FirewallRule, SSHKey, HostGroup } from "@/lib/types"

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

      {/* Effective Rules */}
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
    </div>
  )
}
