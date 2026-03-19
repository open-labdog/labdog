"use client"

import { useState, useEffect } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
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
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { serviceSchema, type ServiceInput } from "@/lib/schemas"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import type { ServiceRule, HostGroup } from "@/lib/types"

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

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingService, setEditingService] = useState<ServiceRule | null>(null)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  const serviceDefaults: ServiceInput = { service_name: "", state: "running", enabled: true, priority: 100, comment: "" }

  const form = useForm<ServiceInput>({
    resolver: zodResolver(serviceSchema),
    defaultValues: serviceDefaults,
    mode: "onSubmit",
  })

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  const { data: services, isLoading, error } = useQuery<ServiceRule[]>({
    queryKey: ["services", id],
    queryFn: () => apiFetch<ServiceRule[]>(`/api/groups/${id}/services`),
    enabled: !!id,
  })
  const showLoading = useDelayedLoading(isLoading)

  const saveMutation = useApiMutation({
    mutationFn: ({ serviceId, payload }: { serviceId?: number; payload: Record<string, unknown> }) => {
      if (serviceId) {
        return apiFetch(`/api/groups/${id}/services/${serviceId}`, { method: "PUT", body: JSON.stringify(payload) })
      }
      return apiFetch(`/api/groups/${id}/services`, { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["services", id]],
    onSuccess: () => setDialogOpen(false),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (serviceId: number) =>
      apiFetch(`/api/groups/${id}/services/${serviceId}`, { method: "DELETE" }),
    invalidateKeys: [["services", id]],
  })

  function openCreateDialog() {
    setEditingService(null)
    form.reset(serviceDefaults)
    saveMutation.reset()
    setDialogOpen(true)
  }

  function openEditDialog(service: ServiceRule) {
    setEditingService(service)
    saveMutation.reset()
    setDialogOpen(true)
  }

  useEffect(() => {
    if (dialogOpen && editingService) {
      form.reset({
        service_name: editingService.service_name,
        state: editingService.state,
        enabled: editingService.enabled,
        priority: editingService.priority,
        comment: editingService.comment ?? "",
      })
    }
  }, [dialogOpen, editingService, form])

  const onSubmit = form.handleSubmit((data) => {
    const payload = { ...data, comment: data.comment || null }
    saveMutation.mutate({ serviceId: editingService?.id, payload })
  })

  function handleDelete(service: ServiceRule) {
    setConfirmState({
      open: true,
      title: "Delete Service Rule",
      description: `Delete service rule "${service.service_name}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        try {
          await deleteMutation.mutateAsync(service.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Services" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Service Rules</h1>
          <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
        </div>
        <Button onClick={openCreateDialog}>Add Service</Button>
      </div>

      {showLoading && <TableSkeleton rows={5} columns={4} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load services</div>
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
                        disabled={deleteMutation.isPending}
                        onClick={() => handleDelete(service)}
                        className="text-red-400 hover:text-red-300 hover:bg-red-950"
                      >
                        {deleteMutation.isPending ? "…" : "Delete"}
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
          <form onSubmit={onSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="service-name">Service Name</Label>
              <Input
                id="service-name"
                type="text"
                placeholder="e.g. nginx, sshd, docker"
                {...form.register("service_name")}
              />
              {form.formState.errors.service_name?.message && <p className="text-sm text-red-400">{form.formState.errors.service_name.message}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="service-state">State</Label>
              <select
                id="service-state"
                {...form.register("state")}
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
                {...form.register("enabled")}
                className="rounded border-input"
              />
              <Label htmlFor="service-enabled">Enabled</Label>
            </div>

            <div className="space-y-2">
              <Label htmlFor="service-priority">Priority</Label>
              <Input
                id="service-priority"
                type="number"
                min={0}
                {...form.register("priority", { valueAsNumber: true })}
              />
              {form.formState.errors.priority?.message && <p className="text-sm text-red-400">{form.formState.errors.priority.message}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="service-comment">Comment</Label>
              <Input
                id="service-comment"
                type="text"
                placeholder="Optional comment"
                {...form.register("comment")}
              />
            </div>

            {saveMutation.error && (
              <p className="text-sm text-red-400">{saveMutation.error.message}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving..." : editingService ? "Save Changes" : "Create"}
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

      {confirmState && (
        <ConfirmDialog
          open={confirmState.open}
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel="Delete"
          variant="destructive"
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}
    </div>
  )
}
