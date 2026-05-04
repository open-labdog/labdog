"use client"

import { useState, useEffect } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { GitBranch } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { SystemdStateBadge, EnabledBadge } from "@/components/status-badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { DataTable } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { serviceSchema, type ServiceInput } from "@/lib/schemas"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import type { ServiceRule, HostGroup } from "@/lib/types"


export default function GroupServicesPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingService, setEditingService] = useState<ServiceRule | null>(null)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  const serviceDefaults: ServiceInput = { service_name: "", state: "running", enabled: true, unit_content: "", deploy_mode: "override", priority: 100, comment: "" }

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

  const gitopsEnabled = !!group?.gitops_enabled

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
        unit_content: editingService.unit_content ?? "",
        deploy_mode: (editingService.deploy_mode as "full" | "override") ?? "override",
        priority: editingService.priority,
        comment: editingService.comment ?? "",
      })
    }
  }, [dialogOpen, editingService, form])

  const onSubmit = form.handleSubmit((data) => {
    const payload = {
      ...data,
      comment: data.comment || null,
      unit_content: data.unit_content || null,
    }
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
      {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Services" }]} />}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Service Rules</h1>
        </div>
        {!gitopsEnabled && <Button onClick={openCreateDialog}>Add Service</Button>}
      </div>

      {gitopsEnabled && (
        <div className="flex items-start gap-3 p-4 rounded-lg bg-blue-950 border border-blue-800">
          <GitBranch className="h-5 w-5 text-blue-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-blue-200 font-medium">GitOps Enabled</p>
            <p className="text-blue-300 text-sm mt-1">Services are managed via GitOps. Changes must be pushed to Git.</p>
          </div>
        </div>
      )}

      {showLoading && <TableSkeleton rows={5} columns={4} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load services</div>
      )}

      {!isLoading && !error && (
        <DataTable<ServiceRule>
          tableId="group-services"
          data={services}
          emptyMessage={<>No service rules yet. Click <strong>Add Service</strong> to create one.</>}
          getRowKey={(s) => s.id}
          columns={[
            {
              key: "service_name",
              label: "Service Name",
              accessor: (s) => s.service_name,
              cell: (s) => <span className="font-mono text-white text-sm">{s.service_name}</span>,
              defaultWidth: 200,
              filter: { type: "text", placeholder: "e.g. nginx" },
            },
            {
              key: "state",
              label: "State",
              accessor: (s) => s.state,
              cell: (s) => <SystemdStateBadge state={s.state} titleCase />,
              defaultWidth: 120,
              filter: { type: "enum", options: [{label:"Running",value:"running"},{label:"Stopped",value:"stopped"}] },
            },
            {
              key: "enabled",
              label: "Enabled",
              accessor: (s) => s.enabled,
              cell: (s) => <EnabledBadge enabled={s.enabled} />,
              defaultWidth: 110,
              filter: { type: "boolean" },
            },
            {
              key: "priority",
              label: "Priority",
              accessor: (s) => s.priority,
              cell: (s) => <span className="font-mono text-slate-300 text-xs">{s.priority}</span>,
              defaultWidth: 90,
            },
            {
              key: "comment",
              label: "Comment",
              accessor: (s) => s.comment ?? "",
              cell: (s) => <span className="text-slate-400 text-xs">{s.comment ?? "—"}</span>,
              defaultWidth: 200,
            },
            {
              key: "actions",
              label: "Actions",
              cell: (service) => (
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={gitopsEnabled}
                    onClick={() => openEditDialog(service)}
                    title={gitopsEnabled ? "Managed via GitOps" : undefined}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={deleteMutation.isPending || gitopsEnabled}
                    onClick={() => handleDelete(service)}
                    title={gitopsEnabled ? "Managed via GitOps" : undefined}
                    className="text-red-400 hover:text-red-300 hover:bg-red-950"
                  >
                    {deleteMutation.isPending ? "…" : "Delete"}
                  </Button>
                </div>
              ),
              defaultWidth: 160,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
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
              <Label>Deploy Mode</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant={form.watch("deploy_mode") === "override" ? "default" : "outline"}
                  onClick={() => form.setValue("deploy_mode", "override", { shouldDirty: true })}
                >
                  Override existing
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={form.watch("deploy_mode") === "full" ? "default" : "outline"}
                  onClick={() => form.setValue("deploy_mode", "full", { shouldDirty: true })}
                >
                  New Service (full file)
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="service-unit-content">Unit file content</Label>
              <textarea
                id="service-unit-content"
                rows={8}
                placeholder={
                  form.watch("deploy_mode") === "full"
                    ? "[Unit]\nDescription=My Service\n\n[Service]\nExecStart=/usr/bin/myapp\nRestart=always\n\n[Install]\nWantedBy=multi-user.target"
                    : "[Service]\nMemoryLimit=512M"
                }
                {...form.register("unit_content")}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
              />
              <p className="text-xs text-slate-500">
                {form.watch("deploy_mode") === "full"
                  ? "Full unit file — will be deployed to /etc/systemd/system/<name>.service on every host in the group."
                  : "Drop-in override — applied only on hosts where the service already exists. Skipped silently if missing."}
              </p>
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

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving..." : editingService ? "Save Changes" : "Create"}
              </Button>
            </DialogFooter>
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
