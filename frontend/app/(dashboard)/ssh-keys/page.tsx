"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { InfoIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Tooltip } from "@/components/ui/tooltip"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { DataTable } from "@/components/ui/data-table"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { showSuccess, showError } from "@/lib/toast"
import { sshKeySchema, type SshKeyInput } from "@/lib/schemas"
import type { SSHKey } from "@/lib/types"

export default function SSHKeysPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false)
  const [editingKey, setEditingKey] = useState<SSHKey | null>(null)
  const [editName, setEditName] = useState("")
  const [editSshUser, setEditSshUser] = useState("")
  const [editIsDefault, setEditIsDefault] = useState(false)
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const form = useForm<SshKeyInput>({
    resolver: zodResolver(sshKeySchema),
    defaultValues: { name: "", private_key: "", ssh_user: "root", is_default: false },
    mode: "onSubmit",
  })

  const { data: sshKeys, isLoading, error } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const uploadMutation = useApiMutation({
    mutationFn: (data: SshKeyInput) =>
      apiFetch("/api/ssh-keys", {
        method: "POST",
        body: JSON.stringify({
          name: data.name,
          private_key: data.private_key,
          ssh_user: data.ssh_user,
          is_default: data.is_default ?? false,
        }),
      }),
    invalidateKeys: [["ssh-keys"]],
    onSuccess: () => {
      setDialogOpen(false)
      form.reset()
    },
  })

  const deleteMutation = useApiMutation<unknown, number, SSHKey>({
    mutationFn: (keyId) =>
      apiFetch(`/api/ssh-keys/${keyId}`, { method: "DELETE" }),
    invalidateKeys: [["ssh-keys"]],
    successMessage: "SSH key deleted",
    optimisticUpdate: {
      queryKey: ["ssh-keys"],
      updater: (old, keyId) => old.filter((k) => k.id !== keyId),
    },
  })

  const onUpload = form.handleSubmit((data) => {
    uploadMutation.mutate(data)
  })

  function handleDelete(keyId: number) {
    setConfirmState({
      open: true,
      title: "Delete SSH Key",
      description: "Are you sure you want to delete this SSH key? This action cannot be undone.",
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        try {
          await deleteMutation.mutateAsync(keyId)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function handleBulkDelete() {
    const ids = Array.from(selected)
    setBulkDeleting(true)
    setBulkProgress({ done: 0, total: ids.length })
    let success = 0, failed = 0
    for (const id of ids) {
      try {
        await apiFetch(`/api/ssh-keys/${id}`, { method: "DELETE" })
        success++
      } catch {
        failed++
      }
      setBulkProgress({ done: success + failed, total: ids.length })
    }
    setBulkDeleting(false)
    setBulkProgress(null)
    setSelected(new Set())
    await queryClient.invalidateQueries({ queryKey: ["ssh-keys"] })
    if (failed === 0) {
      showSuccess(`Deleted ${success} SSH key${success !== 1 ? "s" : ""}`)
    } else {
      showError(`Deleted ${success} of ${ids.length}. ${failed} failed.`)
    }
    setBulkConfirmOpen(false)
  }

  function openEdit(key: SSHKey) {
    setEditingKey(key)
    setEditName(key.name)
    setEditSshUser(key.ssh_user)
    setEditIsDefault(key.is_default)
    setEditError(null)
  }

  async function handleEditSave() {
    if (!editingKey) return
    setEditSaving(true)
    setEditError(null)
    try {
      await apiFetch(`/api/ssh-keys/${editingKey.id}`, {
        method: "PUT",
        body: JSON.stringify({
          name: editName !== editingKey.name ? editName : undefined,
          ssh_user: editSshUser !== editingKey.ssh_user ? editSshUser : undefined,
          is_default: editIsDefault !== editingKey.is_default ? editIsDefault : undefined,
        }),
      })
      await queryClient.invalidateQueries({ queryKey: ["ssh-keys"] })
      showSuccess("SSH key updated")
      setEditingKey(null)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed to update")
    } finally {
      setEditSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "SSH Keys" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">SSH Keys</h1>
          <p className="text-slate-400 text-sm mt-1">Manage SSH keys for host access</p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) { form.reset(); uploadMutation.reset() }
        }}>
          <DialogTrigger render={<Button />}>
            Upload Key
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Upload SSH Key</DialogTitle>
            </DialogHeader>
            <form onSubmit={onUpload} noValidate className="space-y-4 mt-2">
              <div className="space-y-2">
                <Label htmlFor="key-name">Name</Label>
                <Input
                  id="key-name"
                  type="text"
                  placeholder="e.g. production-key"
                  {...form.register("name")}
                />
                {form.formState.errors.name && (
                  <p className="text-sm text-red-400">{form.formState.errors.name.message}</p>
                )}
              </div>

               <div className="space-y-2">
                 <div className="flex items-center gap-1.5">
                   <Label htmlFor="private-key">Private Key</Label>
                   <Tooltip content="Your private key is encrypted at rest with AES-256-GCM before storage.">
                     <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
                   </Tooltip>
                 </div>
                 <textarea
                   id="private-key"
                   placeholder="Paste your private key here..."
                   {...form.register("private_key")}
                   rows={6}
                   className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm font-mono text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring resize-none dark:bg-input/30"
                 />
                 {form.formState.errors.private_key && (
                   <p className="text-sm text-red-400">{form.formState.errors.private_key.message}</p>
                 )}
               </div>

              <div className="space-y-2">
                <Label htmlFor="ssh-user">SSH User</Label>
                <Input
                  id="ssh-user"
                  type="text"
                  placeholder="root"
                  {...form.register("ssh_user")}
                  className="font-mono"
                />
                {form.formState.errors.ssh_user && (
                  <p className="text-sm text-red-400">{form.formState.errors.ssh_user.message}</p>
                )}
              </div>

              <div className="flex items-center gap-2">
                <input
                  id="is-default"
                  type="checkbox"
                  {...form.register("is_default")}
                  className="rounded border-input"
                />
                <Label htmlFor="is-default">Set as default key</Label>
              </div>

              {uploadMutation.error && (
                <p className="text-sm text-red-400">{uploadMutation.error.message}</p>
              )}

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => { setDialogOpen(false); form.reset(); uploadMutation.reset() }}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={uploadMutation.isPending}>
                  {uploadMutation.isPending ? "Uploading..." : "Upload Key"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {selected.size > 0 && (
        <div className="flex items-center gap-3 px-4 py-2 bg-slate-800 rounded-lg border border-slate-700">
          <span className="text-sm text-slate-300">{selected.size} selected</span>
          {bulkProgress ? (
            <span className="text-sm text-slate-400">Deleting {bulkProgress.done}/{bulkProgress.total}...</span>
          ) : (
            <Button
              size="sm"
              variant="destructive"
              onClick={() => setBulkConfirmOpen(true)}
              disabled={bulkDeleting}
            >
              Delete Selected
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </div>
      )}

      {showLoading && <TableSkeleton rows={5} columns={3} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load SSH keys</div>
      )}

      {!isLoading && !error && (
        <DataTable<SSHKey>
          tableId="ssh-keys"
          data={sshKeys}
          emptyMessage="No SSH keys yet. Upload your first key to get started."
          getRowKey={(k) => k.id}
          columns={[
            {
              key: "select",
              label: "",
              cell: (k) => (
                <input
                  type="checkbox"
                  checked={selected.has(k.id)}
                  onChange={() => toggleSelect(k.id)}
                  className="rounded border-slate-600"
                />
              ),
              defaultWidth: 40,
              resizable: false,
              sortable: false,
            },
            {
              key: "name",
              label: "Name",
              accessor: (k) => k.name,
              cell: (k) => <span className="font-medium text-white">{k.name}</span>,
              defaultWidth: 200,
              filter: { type: "text" },
            },
            {
              key: "ssh_user",
              label: "SSH User",
              accessor: (k) => k.ssh_user,
              cell: (k) => <span className="font-mono text-slate-300 text-sm">{k.ssh_user}</span>,
              defaultWidth: 130,
              filter: { type: "enum", from: "accessor" },
            },
            {
              key: "is_default",
              label: "Default",
              accessor: (k) => k.is_default,
              cell: (k) => k.is_default
                ? <Badge className="bg-green-600 text-white">Default</Badge>
                : <span className="text-slate-500">—</span>,
              defaultWidth: 100,
              filter: { type: "boolean", trueLabel: "Yes", falseLabel: "No" },
            },
            {
              key: "created_at",
              label: "Created At",
              accessor: (k) => k.created_at,
              cell: (k) => <span className="text-slate-400">{new Date(k.created_at).toLocaleDateString()}</span>,
              defaultWidth: 120,
              filter: { type: "dateRange" },
            },
            {
              key: "actions",
              label: "Actions",
              cell: (k) => (
                <div className="flex gap-1">
                  <Button variant="ghost" size="sm" onClick={() => openEdit(k)}>Edit</Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => handleDelete(k.id)}
                    disabled={deleteMutation.isPending}
                  >
                    {deleteMutation.isPending ? "..." : "Delete"}
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

      <ConfirmDialog
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        title={`Delete ${selected.size} ${selected.size === 1 ? "key" : "keys"}?`}
        description="This action cannot be undone."
        confirmLabel="Delete All"
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={handleBulkDelete}
      />

      <Dialog open={!!editingKey} onOpenChange={(open) => { if (!open) setEditingKey(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit SSH Key</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="edit-name">Name</Label>
              <Input id="edit-name" value={editName} onChange={(e) => setEditName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-ssh-user">SSH User</Label>
              <Input id="edit-ssh-user" value={editSshUser} onChange={(e) => setEditSshUser(e.target.value)} className="font-mono" />
            </div>
            <div className="flex items-center gap-2">
              <input id="edit-default" type="checkbox" checked={editIsDefault} onChange={(e) => setEditIsDefault(e.target.checked)} className="rounded border-input" />
              <Label htmlFor="edit-default">Set as default key</Label>
            </div>
            {editError && <p className="text-sm text-red-400">{editError}</p>}
            <DialogFooter>
              <Button variant="outline" onClick={() => setEditingKey(null)}>Cancel</Button>
              <Button onClick={handleEditSave} disabled={editSaving}>
                {editSaving ? "Saving..." : "Save"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
