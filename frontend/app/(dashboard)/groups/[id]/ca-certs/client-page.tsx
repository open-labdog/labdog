"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Loader2Icon, PlayIcon, ShieldCheckIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { TableSkeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import type { CACertActionRun, CACertRule } from "@/lib/types"
import { useDelayedLoading } from "@/lib/utils"


function StateBadge({ state }: { state: "present" | "absent" }) {
  return (
    <Badge className={state === "present" ? "bg-green-600 text-white" : "bg-red-600 text-white"}>
      {state === "present" ? "Present" : "Absent"}
    </Badge>
  )
}

function StatusBadge({ status }: { status: CACertActionRun["status"] }) {
  const map: Record<string, string> = {
    pending: "bg-slate-600 text-white",
    running: "bg-blue-600 text-white",
    success: "bg-green-600 text-white",
    failed: "bg-red-600 text-white",
    cancelled: "bg-slate-500 text-white",
  }
  return <Badge className={map[status] ?? ""}>{status}</Badge>
}

function shortFingerprint(fp: string) {
  // Show first 6 and last 6 hex pairs for compactness
  const parts = fp.split(":")
  if (parts.length <= 14) return fp
  return `${parts.slice(0, 6).join(":")}…${parts.slice(-6).join(":")}`
}

function formatDateTime(s: string | null) {
  if (!s) return "—"
  try {
    return new Date(s).toLocaleString()
  } catch {
    return s
  }
}

export default function GroupCACertsPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)

  const [addOpen, setAddOpen] = useState(false)
  const [name, setName] = useState("")
  const [pem, setPem] = useState("")
  const [comment, setComment] = useState("")

  const [editTarget, setEditTarget] = useState<CACertRule | null>(null)
  const [editName, setEditName] = useState("")
  const [editState, setEditState] = useState<"present" | "absent">("present")
  const [editComment, setEditComment] = useState("")

  const [deleteTarget, setDeleteTarget] = useState<CACertRule | null>(null)
  const [deployConfirm, setDeployConfirm] = useState(false)

  const { data: certs = [], isLoading: loading } = useQuery<CACertRule[]>({
    queryKey: ["group-ca-certs", id],
    queryFn: () => apiFetch<CACertRule[]>(`/api/groups/${id}/ca-certs`),
    enabled: !!id,
  })
  const showLoading = useDelayedLoading(loading)

  const { data: runs = [] } = useQuery<CACertActionRun[]>({
    queryKey: ["group-ca-cert-runs", id],
    queryFn: () => apiFetch<CACertActionRun[]>(`/api/ca-certs/groups/${id}/runs`),
    enabled: !!id,
    refetchInterval: 5000,
  })

  const createMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/groups/${id}/ca-certs`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    invalidateKeys: [["group-ca-certs", id]],
    onSuccess: () => {
      setAddOpen(false)
      setName("")
      setPem("")
      setComment("")
    },
  })

  const updateMutation = useApiMutation({
    mutationFn: ({ ruleId, payload }: { ruleId: number; payload: Record<string, unknown> }) =>
      apiFetch(`/api/groups/${id}/ca-certs/${ruleId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    invalidateKeys: [["group-ca-certs", id]],
    onSuccess: () => setEditTarget(null),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (ruleId: number) =>
      apiFetch(`/api/groups/${id}/ca-certs/${ruleId}`, { method: "DELETE" }),
    invalidateKeys: [["group-ca-certs", id]],
    onSuccess: () => setDeleteTarget(null),
  })

  const deployMutation = useApiMutation({
    mutationFn: () =>
      apiFetch(`/api/ca-certs/groups/${id}/deploy`, { method: "POST" }),
    invalidateKeys: [["group-ca-cert-runs", id]],
    onSuccess: () => setDeployConfirm(false),
  })

  const handleCreate = (e: FormEvent) => {
    e.preventDefault()
    createMutation.mutate({
      name,
      pem_content: pem,
      state: "present",
      comment: comment || null,
    })
  }

  const openEdit = (rule: CACertRule) => {
    setEditTarget(rule)
    setEditName(rule.name)
    setEditState(rule.state)
    setEditComment(rule.comment ?? "")
  }

  const handleUpdate = (e: FormEvent) => {
    e.preventDefault()
    if (!editTarget) return
    updateMutation.mutate({
      ruleId: editTarget.id,
      payload: {
        name: editName,
        state: editState,
        comment: editComment || null,
      },
    })
  }

  return (
    <div className="space-y-6">
      {!embedded && (
        <div>
          <h1 className="text-2xl font-semibold text-white">CA Certs</h1>
          <p className="text-slate-400 text-sm mt-1">
            Trusted certificate authorities deployed to hosts in this group.
          </p>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <ShieldCheckIcon className="w-5 h-5" />
            Certificates ({certs.length})
          </h2>
          <p className="text-slate-400 text-sm mt-1">
            Deployed as a one-time action — no drift detection. Newly added hosts auto-deploy.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={certs.length === 0 || deployMutation.isPending}
            onClick={() => setDeployConfirm(true)}
          >
            <PlayIcon className="w-4 h-4 mr-1" />
            Deploy to All Hosts
          </Button>
          <Button onClick={() => setAddOpen(true)}>Add Certificate</Button>
        </div>
      </div>

      {showLoading ? (
        <TableSkeleton />
      ) : certs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center">
          <p className="text-slate-400">No CA certificates defined for this group.</p>
          <Button className="mt-4" onClick={() => setAddOpen(true)}>
            Add the first certificate
          </Button>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Subject</TableHead>
              <TableHead>Expires</TableHead>
              <TableHead>Fingerprint (SHA-256)</TableHead>
              <TableHead>State</TableHead>
              <TableHead className="w-[140px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {certs.map((c) => (
              <TableRow key={c.id}>
                <TableCell className="font-medium text-white">{c.name}</TableCell>
                <TableCell className="text-slate-300 text-sm max-w-xs truncate" title={c.subject ?? ""}>
                  {c.subject ?? "—"}
                </TableCell>
                <TableCell className="text-slate-300 text-sm">
                  {c.not_after ? new Date(c.not_after).toLocaleDateString() : "—"}
                </TableCell>
                <TableCell className="font-mono text-xs text-slate-400" title={c.fingerprint_sha256}>
                  {shortFingerprint(c.fingerprint_sha256)}
                </TableCell>
                <TableCell><StateBadge state={c.state} /></TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(c)}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-red-400 hover:text-red-300"
                      onClick={() => setDeleteTarget(c)}
                    >
                      Delete
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* Recent runs */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold text-white">Recent Deployment Runs</h2>
        {runs.length === 0 ? (
          <p className="text-slate-400 text-sm">No deployment runs yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Run #</TableHead>
                <TableHead>Host</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Started</TableHead>
                <TableHead>Completed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.slice(0, 20).map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-mono text-xs text-slate-400">#{r.id}</TableCell>
                  <TableCell className="text-slate-300">host {r.host_id}</TableCell>
                  <TableCell><StatusBadge status={r.status} /></TableCell>
                  <TableCell className="text-slate-300 text-sm">{formatDateTime(r.started_at)}</TableCell>
                  <TableCell className="text-slate-300 text-sm">{formatDateTime(r.completed_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Add dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-2xl">
          <form onSubmit={handleCreate}>
            <DialogHeader>
              <DialogTitle>Add CA Certificate</DialogTitle>
              <DialogDescription>
                Paste the PEM-encoded CA certificate. Metadata is extracted automatically.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div>
                <Label htmlFor="ca-name">Display name</Label>
                <Input
                  id="ca-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Internal Root CA"
                  required
                />
              </div>
              <div>
                <Label htmlFor="ca-pem">PEM content</Label>
                <textarea
                  id="ca-pem"
                  className="w-full h-48 rounded border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-200"
                  value={pem}
                  onChange={(e) => setPem(e.target.value)}
                  placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                  required
                />
              </div>
              <div>
                <Label htmlFor="ca-comment">Comment (optional)</Label>
                <Input
                  id="ca-comment"
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="Why this CA is trusted"
                />
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setAddOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending && (
                  <Loader2Icon className="w-4 h-4 mr-1 animate-spin" />
                )}
                Add Certificate
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={!!editTarget} onOpenChange={(open) => !open && setEditTarget(null)}>
        <DialogContent>
          <form onSubmit={handleUpdate}>
            <DialogHeader>
              <DialogTitle>Edit CA Certificate</DialogTitle>
              <DialogDescription>
                Certificate content is immutable. Only name, state, and comment can be changed.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div>
                <Label htmlFor="edit-name">Display name</Label>
                <Input
                  id="edit-name"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  required
                />
              </div>
              <div>
                <Label htmlFor="edit-state">State</Label>
                <select
                  id="edit-state"
                  className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"
                  value={editState}
                  onChange={(e) => setEditState(e.target.value as "present" | "absent")}
                >
                  <option value="present">Present</option>
                  <option value="absent">Absent</option>
                </select>
              </div>
              <div>
                <Label htmlFor="edit-comment">Comment</Label>
                <Input
                  id="edit-comment"
                  value={editComment}
                  onChange={(e) => setEditComment(e.target.value)}
                />
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setEditTarget(null)}>
                Cancel
              </Button>
              <Button type="submit" disabled={updateMutation.isPending}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      {deleteTarget && (
        <ConfirmDialog
          open={!!deleteTarget}
          onOpenChange={(open) => !open && setDeleteTarget(null)}
          title="Delete CA certificate?"
          description={`This removes "${deleteTarget.name}" from the group. The certificate will be removed from hosts on the next deploy run.`}
          confirmLabel="Delete"
          variant="destructive"
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          loading={deleteMutation.isPending}
        />
      )}

      {/* Deploy confirmation */}
      <ConfirmDialog
        open={deployConfirm}
        onOpenChange={setDeployConfirm}
        title="Deploy CA certificates to all hosts?"
        description="This runs the CA cert deployment Ansible playbook on every host in this group. Hosts already running a deploy will be skipped."
        confirmLabel="Deploy"
        onConfirm={() => deployMutation.mutate(undefined)}
        loading={deployMutation.isPending}
      />
    </div>
  )
}
