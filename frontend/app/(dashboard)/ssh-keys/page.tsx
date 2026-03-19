"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
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
import { showError } from "@/lib/toast"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import type { SSHKey } from "@/lib/types"

export default function SSHKeysPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [keyName, setKeyName] = useState("")
  const [privateKey, setPrivateKey] = useState("")
  const [isDefault, setIsDefault] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  const { data: sshKeys, isLoading, error } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })
  const showLoading = useDelayedLoading(isLoading)

  async function handleUpload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setFormError(null)
    setFormLoading(true)

    try {
      await apiFetch("/api/ssh-keys", {
        method: "POST",
        body: JSON.stringify({
          name: keyName,
          private_key: privateKey,
          is_default: isDefault,
        }),
      })
      await queryClient.invalidateQueries({ queryKey: ["ssh-keys"] })
      setDialogOpen(false)
      setKeyName("")
      setPrivateKey("")
      setIsDefault(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to upload key")
    } finally {
      setFormLoading(false)
    }
  }

  function handleDelete(keyId: number) {
    setConfirmState({
      open: true,
      title: "Delete SSH Key",
      description: "Are you sure you want to delete this SSH key? This action cannot be undone.",
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        setDeletingId(keyId)
        try {
          await apiFetch(`/api/ssh-keys/${keyId}`, { method: "DELETE" })
          await queryClient.invalidateQueries({ queryKey: ["ssh-keys"] })
          setConfirmState(null)
        } catch {
          showError("Failed to delete SSH key")
          setConfirmState(null)
        } finally {
          setDeletingId(null)
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
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger>
            <Button>Upload Key</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Upload SSH Key</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleUpload} className="space-y-4 mt-2">
              <div className="space-y-2">
                <Label htmlFor="key-name">Name</Label>
                <Input
                  id="key-name"
                  type="text"
                  placeholder="e.g. production-key"
                  value={keyName}
                  onChange={(e) => setKeyName(e.target.value)}
                  required
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="private-key">Private Key</Label>
                <textarea
                  id="private-key"
                  placeholder="Paste your private key here..."
                  value={privateKey}
                  onChange={(e) => setPrivateKey(e.target.value)}
                  rows={6}
                  required
                  className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm font-mono text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring resize-none dark:bg-input/30"
                />
              </div>

              <div className="flex items-center gap-2">
                <input
                  id="is-default"
                  type="checkbox"
                  checked={isDefault}
                  onChange={(e) => setIsDefault(e.target.checked)}
                  className="rounded border-input"
                />
                <Label htmlFor="is-default">Set as default key</Label>
              </div>

              {formError && (
                <p className="text-sm text-red-400">{formError}</p>
              )}

              <div className="flex gap-3 pt-2">
                <Button type="submit" disabled={formLoading}>
                  {formLoading ? "Uploading..." : "Upload Key"}
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

      {showLoading && <TableSkeleton rows={5} columns={3} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load SSH keys</div>
      )}

      {!isLoading && !error && sshKeys && sshKeys.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No SSH keys yet. Upload your first key to get started.
        </div>
      )}

      {!isLoading && !error && sshKeys && sshKeys.length > 0 && (
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
              {sshKeys.map((key) => (
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
                      disabled={deletingId === key.id}
                    >
                      {deletingId === key.id ? "Deleting..." : "Delete"}
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
