"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
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
import { sshKeySchema, type SshKeyInput } from "@/lib/schemas"
import type { SSHKey } from "@/lib/types"

export default function SSHKeysPage() {
  const [searchQuery, setSearchQuery] = useState("")
  const [dialogOpen, setDialogOpen] = useState(false)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  const form = useForm<SshKeyInput>({
    resolver: zodResolver(sshKeySchema),
    defaultValues: { name: "", private_key: "", is_default: false },
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
          is_default: data.is_default ?? false,
        }),
      }),
    invalidateKeys: [["ssh-keys"]],
    onSuccess: () => {
      setDialogOpen(false)
      form.reset()
    },
  })

  const deleteMutation = useApiMutation({
    mutationFn: (keyId: number) =>
      apiFetch(`/api/ssh-keys/${keyId}`, { method: "DELETE" }),
    invalidateKeys: [["ssh-keys"]],
    successMessage: "SSH key deleted",
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
          <DialogTrigger>
            <Button>Upload Key</Button>
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

              <div className="flex gap-3 pt-2">
                <Button type="submit" disabled={uploadMutation.isPending}>
                  {uploadMutation.isPending ? "Uploading..." : "Upload Key"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => { setDialogOpen(false); form.reset(); uploadMutation.reset() }}
                >
                  Cancel
                </Button>
              </div>
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
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Name</TableHead>
                <TableHead>Default</TableHead>
                <TableHead>Created At</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredKeys.map((key) => (
                <TableRow key={key.id} className="border-slate-700">
                  <TableCell className="font-medium text-white">{key.name}</TableCell>
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
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => handleDelete(key.id)}
                      disabled={deleteMutation.isPending}
                    >
                      {deleteMutation.isPending ? "Deleting..." : "Delete"}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
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
    </div>
  )
}
