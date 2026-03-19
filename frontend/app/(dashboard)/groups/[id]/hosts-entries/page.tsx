"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
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
import { apiFetch } from "@/lib/api"
import type { HostsEntry, HostGroup } from "@/lib/types"

export default function GroupHostsEntriesPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingEntry, setEditingEntry] = useState<HostsEntry | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)

  // Form fields
  const [ipAddress, setIpAddress] = useState("")
  const [hostname, setHostname] = useState("")
  const [aliases, setAliases] = useState("")
  const [comment, setComment] = useState("")
  const [priority, setPriority] = useState(100)

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

  function openCreateDialog() {
    setEditingEntry(null)
    setIpAddress("")
    setHostname("")
    setAliases("")
    setComment("")
    setPriority(100)
    setFormError(null)
    setDialogOpen(true)
  }

  function openEditDialog(entry: HostsEntry) {
    setEditingEntry(entry)
    setIpAddress(entry.ip_address)
    setHostname(entry.hostname)
    setAliases(entry.aliases.join(", "))
    setComment(entry.comment ?? "")
    setPriority(entry.priority)
    setFormError(null)
    setDialogOpen(true)
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setFormError(null)
    setFormLoading(true)

    const payload = {
      ip_address: ipAddress,
      hostname,
      aliases: aliases
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean),
      comment: comment || null,
      priority,
    }

    try {
      if (editingEntry) {
        await apiFetch(`/api/groups/${id}/hosts-entries/${editingEntry.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      } else {
        await apiFetch(`/api/groups/${id}/hosts-entries`, {
          method: "POST",
          body: JSON.stringify(payload),
        })
      }
      await queryClient.invalidateQueries({ queryKey: ["hosts-entries", id] })
      setDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save hosts entry")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleDelete(entry: HostsEntry) {
    if (!confirm(`Delete hosts entry "${entry.ip_address} ${entry.hostname}"?`)) return
    setDeletingId(entry.id)
    setDeleteError(null)
    try {
      await apiFetch(`/api/groups/${id}/hosts-entries/${entry.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["hosts-entries", id] })
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Hosts Entries" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Hosts File Entries</h1>
          <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
        </div>
        <Button onClick={openCreateDialog}>Add Entry</Button>
      </div>

      {isLoading && (
        <div className="text-slate-400 py-8 text-center">Loading hosts entries…</div>
      )}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load hosts entries</div>
      )}

      {deleteError && (
        <div className="text-red-400 text-sm">{deleteError}</div>
      )}

      {!isLoading && !error && entries && entries.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No hosts file entries yet. Click <strong>Add Entry</strong> to create one.
        </div>
      )}

      {!isLoading && !error && entries && entries.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>IP Address</TableHead>
                <TableHead>Hostname</TableHead>
                <TableHead>Aliases</TableHead>
                <TableHead>Comment</TableHead>
                <TableHead className="w-40">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => (
                <TableRow key={entry.id} className="border-slate-700">
                  <TableCell className="font-mono text-white text-sm">{entry.ip_address}</TableCell>
                  <TableCell className="font-mono text-slate-300 text-sm">{entry.hostname}</TableCell>
                  <TableCell className="text-slate-300 text-xs max-w-[200px] truncate">
                    {entry.aliases.length > 0 ? entry.aliases.join(", ") : "—"}
                  </TableCell>
                  <TableCell className="text-slate-400 text-xs max-w-[160px] truncate">{entry.comment ?? "—"}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      {entry.is_system ? (
                        <Badge variant="outline" className="text-xs text-slate-500">System</Badge>
                      ) : (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => openEditDialog(entry)}
                          >
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={deletingId === entry.id}
                            onClick={() => handleDelete(entry)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {deletingId === entry.id ? "…" : "Delete"}
                          </Button>
                        </>
                      )}
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
            <DialogTitle>{editingEntry ? "Edit Hosts Entry" : "Add Hosts Entry"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="entry-ip">IP Address</Label>
              <Input
                id="entry-ip"
                type="text"
                placeholder="e.g. 192.168.1.10"
                value={ipAddress}
                onChange={(e) => setIpAddress(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="entry-hostname">Hostname</Label>
              <Input
                id="entry-hostname"
                type="text"
                placeholder="e.g. myserver.local"
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="entry-aliases">Aliases (comma-separated)</Label>
              <Input
                id="entry-aliases"
                type="text"
                placeholder="e.g. myserver, ms"
                value={aliases}
                onChange={(e) => setAliases(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="entry-comment">Comment</Label>
              <Input
                id="entry-comment"
                type="text"
                placeholder="Optional comment"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="entry-priority">Priority</Label>
              <Input
                id="entry-priority"
                type="number"
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value))}
                required
                min={0}
              />
            </div>

            {formError && (
              <p className="text-sm text-red-400">{formError}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={formLoading}>
                {formLoading ? "Saving..." : editingEntry ? "Save Changes" : "Create"}
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
