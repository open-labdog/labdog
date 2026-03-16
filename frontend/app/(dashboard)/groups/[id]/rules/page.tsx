"use client"

import { useState } from "react"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Lock, GitBranch } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { RuleDialog } from "@/components/rule-dialog"
import { apiFetch } from "@/lib/api"
import type { FirewallRule, HostGroup } from "@/lib/types"

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

export default function GroupRulesPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<FirewallRule | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const { data: group, isLoading: groupLoading } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  const { data: rules, isLoading, error } = useQuery<FirewallRule[]>({
    queryKey: ["rules", id],
    queryFn: () => apiFetch<FirewallRule[]>(`/api/groups/${id}/rules`),
    enabled: !!id,
  })

  const handleAdd = () => {
    setEditingRule(null)
    setDialogOpen(true)
  }

  const handleEdit = (rule: FirewallRule) => {
    setEditingRule(rule)
    setDialogOpen(true)
  }

  const handleDelete = async (rule: FirewallRule) => {
    if (!confirm(`Delete rule #${rule.priority} (${rule.action} ${rule.protocol})?`)) return
    setDeletingId(rule.id)
    setDeleteError(null)
    try {
      await apiFetch(`/api/groups/${id}/rules/${rule.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["rules", id] })
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Firewall Rules</h1>
          <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
        </div>
        {!group?.gitops_enabled && <Button onClick={handleAdd}>Add Rule</Button>}
      </div>

      {group?.gitops_enabled && (
        <div className="flex items-start gap-3 p-4 rounded-lg bg-blue-950 border border-blue-800">
          <GitBranch className="h-5 w-5 text-blue-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-blue-200 font-medium">GitOps Enabled</p>
            <p className="text-blue-300 text-sm mt-1">Rules are managed via GitOps. Changes must be pushed to Git.</p>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="text-slate-400 py-8 text-center">Loading rules…</div>
      )}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load rules</div>
      )}

      {deleteError && (
        <div className="text-red-400 text-sm">{deleteError}</div>
      )}

      {!isLoading && !error && rules && rules.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No rules yet. Click <strong>Add Rule</strong> to create one.
        </div>
      )}

      {!isLoading && !error && rules && rules.length > 0 && (
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
                <TableHead>Comment</TableHead>
                <TableHead className="w-32">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.map((rule) => (
                <TableRow key={rule.id} className="border-slate-700">
                  <TableCell className="font-mono text-slate-300">
                    <div className="flex items-center gap-1">
                      {rule.is_system && (
                        <Lock className="h-3 w-3 text-slate-500" aria-label="System rule" />
                      )}
                      {rule.priority}
                    </div>
                  </TableCell>
                  <TableCell>
                    <ActionBadge action={rule.action} />
                  </TableCell>
                  <TableCell className="text-slate-300 uppercase text-xs">{rule.protocol}</TableCell>
                  <TableCell className="text-slate-300 capitalize text-xs">{rule.direction}</TableCell>
                  <TableCell className="font-mono text-slate-300 text-xs">{rule.source_cidr ?? "any"}</TableCell>
                  <TableCell className="font-mono text-slate-300 text-xs">{rule.destination_cidr ?? "any"}</TableCell>
                  <TableCell className="font-mono text-slate-300 text-xs">{formatPorts(rule)}</TableCell>
                  <TableCell className="text-slate-400 text-xs max-w-[160px] truncate">{rule.comment ?? "—"}</TableCell>
                   <TableCell>
                     <div className="flex gap-1">
                       <Button
                         size="sm"
                         variant="ghost"
                         disabled={rule.is_system || group?.gitops_enabled}
                         onClick={() => handleEdit(rule)}
                         title={
                           group?.gitops_enabled
                             ? "Rules are managed via GitOps"
                             : rule.is_system
                               ? "System rules cannot be edited"
                               : "Edit rule"
                         }
                       >
                         Edit
                       </Button>
                       <Button
                         size="sm"
                         variant="ghost"
                         disabled={rule.is_system || deletingId === rule.id || group?.gitops_enabled}
                         onClick={() => handleDelete(rule)}
                         title={
                           group?.gitops_enabled
                             ? "Rules are managed via GitOps"
                             : rule.is_system
                               ? "System rules cannot be deleted"
                               : "Delete rule"
                         }
                         className="text-red-400 hover:text-red-300 hover:bg-red-950"
                       >
                         {deletingId === rule.id ? "…" : "Delete"}
                       </Button>
                     </div>
                   </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <RuleDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        groupId={id}
        rule={editingRule}
      />
    </div>
  )
}
