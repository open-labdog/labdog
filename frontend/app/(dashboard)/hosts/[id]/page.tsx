"use client"

import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
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
import type { Host, FirewallRule } from "@/lib/types"

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

  return (
    <div className="space-y-8">
      {/* Host Info */}
      <div>
        <h1 className="text-2xl font-bold text-white mb-1">
          {hostLoading ? "Loading…" : host?.hostname ?? `Host #${id}`}
        </h1>
        <p className="text-slate-400 text-sm">Host details and effective firewall rules</p>
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
