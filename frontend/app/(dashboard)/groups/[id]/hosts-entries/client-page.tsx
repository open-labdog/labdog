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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { DataTable } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { hostsEntrySchema, type HostsEntryInput } from "@/lib/schemas"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import type { HostsEntry, HostGroup } from "@/lib/types"

export default function GroupHostsEntriesPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingEntry, setEditingEntry] = useState<HostsEntry | null>(null)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  const entryDefaults: HostsEntryInput = { ip_address: "", hostname: "", aliases: "", comment: "", priority: 100 }

  const form = useForm<HostsEntryInput>({
    resolver: zodResolver(hostsEntrySchema),
    defaultValues: entryDefaults,
    mode: "onSubmit",
  })

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  const { data: entries, isLoading, error } = useQuery<HostsEntry[]>({
    queryKey: ["hosts-entries", id],
    queryFn: () => apiFetch<HostsEntry[]>(`/api/groups/${id}/hosts-entries`),
    enabled: !!id,
  })
  const showLoading = useDelayedLoading(isLoading)

  const saveMutation = useApiMutation({
    mutationFn: ({ entryId, payload }: { entryId?: number; payload: Record<string, unknown> }) => {
      if (entryId) {
        return apiFetch(`/api/groups/${id}/hosts-entries/${entryId}`, { method: "PUT", body: JSON.stringify(payload) })
      }
      return apiFetch(`/api/groups/${id}/hosts-entries`, { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["hosts-entries", id]],
    onSuccess: () => setDialogOpen(false),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (entryId: number) =>
      apiFetch(`/api/groups/${id}/hosts-entries/${entryId}`, { method: "DELETE" }),
    invalidateKeys: [["hosts-entries", id]],
  })

  function openCreateDialog() {
    setEditingEntry(null)
    form.reset(entryDefaults)
    saveMutation.reset()
    setDialogOpen(true)
  }

  function openEditDialog(entry: HostsEntry) {
    setEditingEntry(entry)
    saveMutation.reset()
    setDialogOpen(true)
  }

  useEffect(() => {
    if (dialogOpen && editingEntry) {
      form.reset({
        ip_address: editingEntry.ip_address,
        hostname: editingEntry.hostname,
        aliases: editingEntry.aliases.join(", "),
        comment: editingEntry.comment ?? "",
        priority: editingEntry.priority,
      })
    }
  }, [dialogOpen, editingEntry, form])

  const onSubmit = form.handleSubmit((data) => {
    const payload = {
      ip_address: data.ip_address,
      hostname: data.hostname,
      aliases: (data.aliases ?? "").split(",").map((a: string) => a.trim()).filter(Boolean),
      comment: data.comment || null,
      priority: data.priority,
    }
    saveMutation.mutate({ entryId: editingEntry?.id, payload })
  })

  function handleDelete(entry: HostsEntry) {
    setConfirmState({
      open: true,
      title: "Delete Hosts Entry",
      description: `Delete hosts entry "${entry.ip_address} ${entry.hostname}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        try {
          await deleteMutation.mutateAsync(entry.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  return (
    <div className="space-y-6">
      {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Hosts File" }]} />}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Hosts File</h1>
        </div>
        <Button onClick={openCreateDialog}>Add Entry</Button>
      </div>

      {showLoading && <TableSkeleton rows={5} columns={4} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load hosts entries</div>
      )}

      {!isLoading && !error && (
        <DataTable<HostsEntry>
          tableId="group-hosts-entries"
          data={entries}
          emptyMessage={<>No hosts file entries yet. Click <strong>Add Entry</strong> to create one.</>}
          getRowKey={(e) => e.id}
          columns={[
            {
              key: "ip_address",
              label: "IP Address",
              accessor: (e) => e.ip_address,
              cell: (e) => <span className="font-mono text-white text-sm">{e.ip_address}</span>,
              defaultWidth: 160,
              filter: { type: "text", placeholder: "e.g. 10.0.1" },
            },
            {
              key: "hostname",
              label: "Hostname",
              accessor: (e) => e.hostname,
              cell: (e) => <span className="font-mono text-slate-300 text-sm">{e.hostname}</span>,
              defaultWidth: 200,
              filter: { type: "text", placeholder: "e.g. web-01" },
            },
            {
              key: "aliases",
              label: "Aliases",
              accessor: (e) => e.aliases.join(", "),
              cell: (e) => (
                <span className="text-slate-300 text-xs">
                  {e.aliases.length > 0 ? e.aliases.join(", ") : "—"}
                </span>
              ),
              defaultWidth: 200,
            },
            {
              key: "comment",
              label: "Comment",
              accessor: (e) => e.comment ?? "",
              cell: (e) => <span className="text-slate-400 text-xs">{e.comment ?? "—"}</span>,
              defaultWidth: 180,
            },
            {
              key: "actions",
              label: "Actions",
              cell: (entry) => (
                <div className="flex gap-1">
                  {entry.is_system ? (
                    <Badge variant="outline" className="text-xs text-slate-500">System</Badge>
                  ) : (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => openEditDialog(entry)}>
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={deleteMutation.isPending}
                        onClick={() => handleDelete(entry)}
                        className="text-red-400 hover:text-red-300 hover:bg-red-950"
                      >
                        {deleteMutation.isPending ? "…" : "Delete"}
                      </Button>
                    </>
                  )}
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
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editingEntry ? "Edit Hosts Entry" : "Add Hosts Entry"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={onSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="entry-ip">IP Address</Label>
              <Input
                id="entry-ip"
                type="text"
                placeholder="e.g. 192.168.1.10"
                {...form.register("ip_address")}
              />
              {form.formState.errors.ip_address?.message && <p className="text-sm text-red-400">{form.formState.errors.ip_address.message}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="entry-hostname">Hostname</Label>
              <Input
                id="entry-hostname"
                type="text"
                placeholder="e.g. myserver.local"
                {...form.register("hostname")}
              />
              {form.formState.errors.hostname?.message && <p className="text-sm text-red-400">{form.formState.errors.hostname.message}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="entry-aliases">Aliases (comma-separated)</Label>
              <Input
                id="entry-aliases"
                type="text"
                placeholder="e.g. myserver, ms"
                {...form.register("aliases")}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="entry-comment">Comment</Label>
              <Input
                id="entry-comment"
                type="text"
                placeholder="Optional comment"
                {...form.register("comment")}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="entry-priority">Priority</Label>
              <Input
                id="entry-priority"
                type="number"
                min={0}
                {...form.register("priority", { valueAsNumber: true })}
              />
              {form.formState.errors.priority?.message && <p className="text-sm text-red-400">{form.formState.errors.priority.message}</p>}
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
                {saveMutation.isPending ? "Saving..." : editingEntry ? "Save Changes" : "Create"}
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
