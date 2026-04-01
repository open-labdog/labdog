"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { SearchIcon, XIcon, InfoIcon } from "lucide-react"
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
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { showSuccess, showError } from "@/lib/toast"
import { sshKeySchema, type SshKeyInput } from "@/lib/schemas"
import type { SSHKey } from "@/lib/types"

export default function SSHKeysPage() {
  const [searchQuery, setSearchQuery] = useState("")
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

  const filteredKeys = sshKeys?.filter(k =>
    k.name.toLowerCase().includes(searchQuery.toLowerCase())
  ) ?? []

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

  const toggleSelectAll = () => {
    if (selected.size === filteredKeys.length && filteredKeys.length > 0) {
      setSelected(new Set())
    } else {
      setSelected(new Set(filteredKeys.map(k => k.id)))
    }
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

      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <Input
            placeholder="Search SSH keys..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 pr-8"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white"
            >
              <XIcon className="w-4 h-4" />
            </button>
          )}
        </div>
        {searchQuery && (
          <span className="text-sm text-slate-400">
            Showing {filteredKeys.length} of {sshKeys?.length ?? 0} keys
          </span>
        )}
      </div>

      {showLoading && <TableSkeleton rows={5} columns={3} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load SSH keys</div>
      )}

      {!isLoading && !error && filteredKeys.length === 0 && searchQuery && (
        <div className="text-slate-400 py-8 text-center">
          No results matching &apos;{searchQuery}&apos;
        </div>
      )}

      {!isLoading && !error && sshKeys?.length === 0 && !searchQuery && (
        <div className="text-slate-400 py-8 text-center">
          No SSH keys yet. Upload your first key to get started.
        </div>
      )}

      {!isLoading && !error && filteredKeys.length > 0 && (
        <>
          {selected.size > 0 && (
            <div className="flex items-center gap-3 px-4 py-2 bg-slate-800 rounded-lg border border-slate-700 mb-2">
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
          <div className="rounded-lg border border-slate-700 bg-slate-900">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-700">
                  <TableHead className="w-10">
                    <input
                      type="checkbox"
                      checked={selected.size === filteredKeys.length && filteredKeys.length > 0}
                      onChange={toggleSelectAll}
                      className="rounded border-slate-600"
                    />
                  </TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>SSH User</TableHead>
                  <TableHead>Default</TableHead>
                  <TableHead>Created At</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredKeys.map((key) => (
                  <TableRow key={key.id} className="border-slate-700">
                    <TableCell>
                      <input
                        type="checkbox"
                        checked={selected.has(key.id)}
                        onChange={() => toggleSelect(key.id)}
                        className="rounded border-slate-600"
                      />
                    </TableCell>
                    <TableCell className="font-medium text-white">{key.name}</TableCell>
                    <TableCell className="font-mono text-slate-300 text-sm">{key.ssh_user}</TableCell>
                    <TableCell>
                      {key.is_default ? (
                        <Badge className="bg-green-600 text-white">Default</Badge>
                      ) : (
                        <span className="text-slate-500">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-slate-400">
                      {new Date(key.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openEdit(key)}
                        >
                          Edit
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => handleDelete(key.id)}
                          disabled={deleteMutation.isPending}
                        >
                          {deleteMutation.isPending ? "..." : "Delete"}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
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
