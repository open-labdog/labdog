"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { showSuccess, showError } from "@/lib/toast"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { TableSkeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
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
import type { ProxmoxNode } from "@/lib/types"

interface NodeFormState {
  name: string
  api_url: string
  token_id: string
  token_secret: string
  verify_ssl: boolean
}

const emptyForm: NodeFormState = {
  name: "",
  api_url: "",
  token_id: "",
  token_secret: "",
  verify_ssl: true,
}

export default function ProxmoxSettingsPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingNode, setEditingNode] = useState<ProxmoxNode | null>(null)
  const [form, setForm] = useState<NodeFormState>(emptyForm)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [cleaningUp, setCleaningUp] = useState(false)
  const [confirmState, setConfirmState] = useState<{
    open: boolean
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
  } | null>(null)

  const queryClient = useQueryClient()

  const { data: nodes, isLoading, error } = useQuery<ProxmoxNode[]>({
    queryKey: ["proxmox-nodes"],
    queryFn: () => apiFetch<ProxmoxNode[]>("/api/proxmox/nodes"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const deleteMutation = useApiMutation<unknown, number, ProxmoxNode>({
    mutationFn: (nodeId) =>
      apiFetch(`/api/proxmox/nodes/${nodeId}`, { method: "DELETE" }),
    invalidateKeys: [["proxmox-nodes"]],
    successMessage: "Proxmox node deleted",
    optimisticUpdate: {
      queryKey: ["proxmox-nodes"],
      updater: (old, nodeId) => old.filter((n) => n.id !== nodeId),
    },
  })

  function openCreate() {
    setEditingNode(null)
    setForm(emptyForm)
    setFormError(null)
    setDialogOpen(true)
  }

  function openEdit(node: ProxmoxNode) {
    setEditingNode(node)
    setForm({
      name: node.name,
      api_url: node.api_url,
      token_id: node.token_id,
      token_secret: "",
      verify_ssl: node.verify_ssl,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  async function handleSave() {
    setFormSaving(true)
    setFormError(null)
    try {
      if (editingNode) {
        const payload: Record<string, unknown> = {
          name: form.name || undefined,
          api_url: form.api_url || undefined,
          token_id: form.token_id || undefined,
          verify_ssl: form.verify_ssl,
        }
        if (form.token_secret) {
          payload.token_secret = form.token_secret
        }
        await apiFetch(`/api/proxmox/nodes/${editingNode.id}`, {
          method: "PUT",
          json: payload,
        })
        showSuccess("Proxmox node updated")
      } else {
        await apiFetch("/api/proxmox/nodes", {
          method: "POST",
          json: {
            name: form.name,
            api_url: form.api_url,
            token_id: form.token_id,
            token_secret: form.token_secret,
            verify_ssl: form.verify_ssl,
          },
        })
        showSuccess("Proxmox node created")
      }
      await queryClient.invalidateQueries({ queryKey: ["proxmox-nodes"] })
      setDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save")
    } finally {
      setFormSaving(false)
    }
  }

  function handleDelete(node: ProxmoxNode) {
    setConfirmState({
      open: true,
      title: "Delete Proxmox Node",
      description: `Are you sure you want to delete "${node.name}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => (prev ? { ...prev, loading: true } : null))
        try {
          await deleteMutation.mutateAsync(node.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  async function handleCleanupSnapshots() {
    setCleaningUp(true)
    try {
      const result = await apiFetch<{ deleted: number; errors: string[] }>(
        "/proxmox/nodes/cleanup-snapshots",
        { method: "POST" }
      )
      showSuccess(`Cleaned up ${result.deleted} orphaned snapshot(s)`)
    } catch (err) {
      showError(err instanceof Error ? err.message : "Cleanup failed")
    } finally {
      setCleaningUp(false)
    }
  }

  async function handleTestConnection(node: ProxmoxNode) {
    setTestingId(node.id)
    try {
      const result = await apiFetch<{ success: boolean; message: string; version: string | null }>(
        `/api/proxmox/nodes/${node.id}/test`,
        { method: "POST" }
      )
      if (result.success) {
        showSuccess(result.version ? `Connected — Proxmox ${result.version}` : "Connection successful")
      } else {
        showError(`Connection failed: ${result.message}`)
      }
    } catch (err) {
      showError(err instanceof Error ? err.message : "Test failed")
    } finally {
      setTestingId(null)
    }
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Settings", href: "/settings" }, { label: "Proxmox" }]} />

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Proxmox Nodes</h1>
          <p className="text-slate-400 text-sm mt-1">
            Configure Proxmox VE API connections for VM management.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={handleCleanupSnapshots}
            disabled={cleaningUp}
          >
            {cleaningUp ? "Cleaning..." : "Cleanup Orphaned Snapshots"}
          </Button>
          <Button onClick={openCreate}>Add Node</Button>
        </div>
      </div>

      {showLoading && <TableSkeleton rows={3} columns={5} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load Proxmox nodes</div>
      )}

      {!isLoading && !error && nodes?.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No Proxmox nodes configured. Click <strong>Add Node</strong> to get started.
        </div>
      )}

      {!isLoading && !error && nodes && nodes.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Name</TableHead>
                <TableHead>API URL</TableHead>
                <TableHead>Token ID</TableHead>
                <TableHead>SSL Verify</TableHead>
                <TableHead className="w-48">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {nodes.map((node) => (
                <TableRow key={node.id} className="border-slate-700">
                  <TableCell className="font-medium text-white">{node.name}</TableCell>
                  <TableCell className="font-mono text-slate-300 text-sm">{node.api_url}</TableCell>
                  <TableCell className="font-mono text-slate-300 text-sm">{node.token_id}</TableCell>
                  <TableCell>
                    {node.verify_ssl ? (
                      <span className="text-green-400 text-sm">Yes</span>
                    ) : (
                      <span className="text-yellow-400 text-sm">No</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={testingId === node.id}
                        onClick={() => handleTestConnection(node)}
                      >
                        {testingId === node.id ? "Testing..." : "Test"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => openEdit(node)}
                      >
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        onClick={() => handleDelete(node)}
                        disabled={deleteMutation.isPending}
                      >
                        Delete
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDialogOpen(false)
            setFormError(null)
          }
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingNode ? "Edit Proxmox Node" : "Add Proxmox Node"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="node-name">Name</Label>
              <Input
                id="node-name"
                placeholder="e.g. pve-01"
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="node-api-url">API URL</Label>
              <Input
                id="node-api-url"
                placeholder="https://pve.example.com:8006"
                value={form.api_url}
                onChange={(e) => setForm((prev) => ({ ...prev, api_url: e.target.value }))}
                className="font-mono"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="node-token-id">Token ID</Label>
              <Input
                id="node-token-id"
                placeholder="user@realm!tokenname"
                value={form.token_id}
                onChange={(e) => setForm((prev) => ({ ...prev, token_id: e.target.value }))}
                className="font-mono"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="node-token-secret">
                Token Secret{editingNode && " (leave blank to keep current)"}
              </Label>
              <Input
                id="node-token-secret"
                type="password"
                placeholder={editingNode ? "Leave blank to keep current" : "API token secret UUID"}
                value={form.token_secret}
                onChange={(e) => setForm((prev) => ({ ...prev, token_secret: e.target.value }))}
                className="font-mono"
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                id="node-verify-ssl"
                type="checkbox"
                checked={form.verify_ssl}
                onChange={(e) => setForm((prev) => ({ ...prev, verify_ssl: e.target.checked }))}
                className="rounded border-input"
              />
              <Label htmlFor="node-verify-ssl">Verify SSL certificate</Label>
            </div>

            {formError && <p className="text-sm text-red-400">{formError}</p>}

            <div className="flex gap-3 pt-2">
              <Button onClick={handleSave} disabled={formSaving}>
                {formSaving ? "Saving..." : editingNode ? "Save Changes" : "Add Node"}
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setDialogOpen(false)
                  setFormError(null)
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
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
