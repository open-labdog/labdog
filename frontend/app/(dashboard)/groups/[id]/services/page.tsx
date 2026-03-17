"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiFetch } from "@/lib/api"
import type { ServiceRule } from "@/lib/types"

function StateBadge({ state }: { state: string }) {
  return (
    <Badge className={state === "running" ? "bg-green-600 text-white" : "bg-slate-600 text-white"}>
      {state.charAt(0).toUpperCase() + state.slice(1)}
    </Badge>
  )
}

function EnabledBadge({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <Badge className="bg-green-700 text-white">Enabled</Badge>
  ) : (
    <Badge variant="outline">Disabled</Badge>
  )
}

export default function GroupServicesPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingService, setEditingService] = useState<ServiceRule | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)

  // Form fields
  const [serviceName, setServiceName] = useState("")
  const [state, setState] = useState<"running" | "stopped">("running")
  const [enabled, setEnabled] = useState(true)
  const [priority, setPriority] = useState(100)
  const [comment, setComment] = useState("")

  const { data: services, isLoading, error } = useQuery<ServiceRule[]>({
    queryKey: ["services", id],
    queryFn: () => apiFetch<ServiceRule[]>(`/api/groups/${id}/services`),
    enabled: !!id,
  })

  function openCreateDialog() {
    setEditingService(null)
    setServiceName("")
    setState("running")
    setEnabled(true)
    setPriority(100)
    setComment("")
    setFormError(null)
    setDialogOpen(true)
  }

  function openEditDialog(service: ServiceRule) {
    setEditingService(service)
    setServiceName(service.service_name)
    setState(service.state)
    setEnabled(service.enabled)
    setPriority(service.priority)
    setComment(service.comment ?? "")
    setFormError(null)
    setDialogOpen(true)
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setFormError(null)
    setFormLoading(true)

    const payload = {
      service_name: serviceName,
      state,
      enabled,
      priority,
      comment: comment || null,
    }

    try {
      if (editingService) {
        await apiFetch(`/api/groups/${id}/services/${editingService.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      } else {
        await apiFetch(`/api/groups/${id}/services`, {
          method: "POST",
          body: JSON.stringify(payload),
        })
      }
      await queryClient.invalidateQueries({ queryKey: ["services", id] })
      setDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save service")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleDelete(service: ServiceRule) {
    if (!confirm(`Delete service rule "${service.service_name}"?`)) return
    setDeletingId(service.id)
    setDeleteError(null)
    try {
      await apiFetch(`/api/groups/${id}/services/${service.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["services", id] })
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
          <h1 className="text-2xl font-bold text-white">Service Rules</h1>
          <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
        </div>
        <Button onClick={openCreateDialog}>Add Service</Button>
      </div>

      {isLoading && (
        <div className="text-slate-400 py-8 text-center">Loading services…</div>
      )}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load services</div>
      )}

      {deleteError && (
        <div className="text-red-400 text-sm">{deleteError}</div>
      )}

      {!isLoading && !error && services && services.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No service rules yet. Click <strong>Add Service</strong> to create one.
        </div>
      )}

      {!isLoading && !error && services && services.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Service Name</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Enabled</TableHead>
                <TableHead className="w-16">Priority</TableHead>
                <TableHead>Comment</TableHead>
                <TableHead className="w-40">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {services.map((service) => (
                <TableRow key={service.id} className="border-slate-700">
                  <TableCell className="font-mono text-white text-sm">{service.service_name}</TableCell>
                  <TableCell>
                    <StateBadge state={service.state} />
                  </TableCell>
                  <TableCell>
                    <EnabledBadge enabled={service.enabled} />
                  </TableCell>
                  <TableCell className="font-mono text-slate-300 text-xs">{service.priority}</TableCell>
                  <TableCell className="text-slate-400 text-xs max-w-[160px] truncate">{service.comment ?? "—"}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => openEditDialog(service)}
                      >
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={deletingId === service.id}
                        onClick={() => handleDelete(service)}
                        className="text-red-400 hover:text-red-300 hover:bg-red-950"
                      >
                        {deletingId === service.id ? "…" : "Delete"}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editingService ? "Edit Service Rule" : "Add Service Rule"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="service-name">Service Name</Label>
              <Input
                id="service-name"
                type="text"
                placeholder="e.g. nginx, sshd, docker"
                value={serviceName}
                onChange={(e) => setServiceName(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="service-state">State</Label>
              <select
                id="service-state"
                value={state}
                onChange={(e) => setState(e.target.value as "running" | "stopped")}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
              >
                <option value="running">Running</option>
                <option value="stopped">Stopped</option>
              </select>
            </div>

            <div className="flex items-center gap-2">
              <input
                id="service-enabled"
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="rounded border-input"
              />
              <Label htmlFor="service-enabled">Enabled</Label>
            </div>

            <div className="space-y-2">
              <Label htmlFor="service-priority">Priority</Label>
              <Input
                id="service-priority"
                type="number"
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value))}
                required
                min={0}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="service-comment">Comment</Label>
              <Input
                id="service-comment"
                type="text"
                placeholder="Optional comment"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
            </div>

            {formError && (
              <p className="text-sm text-red-400">{formError}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={formLoading}>
                {formLoading ? "Saving..." : editingService ? "Save Changes" : "Create"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
              >
                Cancel
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
