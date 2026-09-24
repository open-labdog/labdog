"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { def, ITEM_STATE, JOB_STATUS } from "@/lib/status"
import { Banner, Confirm, Field, Modal, Provenance, Table, Tag, Toolbar } from "@/components/ld"
import type { CACertActionRun, CACertRule, EffectiveCACert, Host } from "@/lib/types"

const defaults = { name: "", pem: "", comment: "", state: "present" as "present" | "absent" }

export function CaCertsTab({ hostId, host }: { hostId: number; host: Host | undefined }) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState(defaults)
  const [deleting, setDeleting] = useState<{ fingerprint: string; name: string } | null>(null)
  const [deployConfirm, setDeployConfirm] = useState(false)

  const { data: certs, isLoading, error } = useQuery<EffectiveCACert[]>({
    queryKey: ["host-effective-ca-certs", hostId],
    queryFn: () => apiFetch<EffectiveCACert[]>(`/api/hosts/${hostId}/effective-ca-certs`),
  })
  const { data: overrides } = useQuery<CACertRule[]>({
    queryKey: ["host-ca-cert-overrides", hostId],
    queryFn: () => apiFetch<CACertRule[]>(`/api/hosts/${hostId}/ca-certs`),
  })
  const { data: runs } = useQuery<CACertActionRun[]>({
    queryKey: ["host-ca-cert-runs", hostId],
    queryFn: () => apiFetch<CACertActionRun[]>(`/api/ca-certs/hosts/${hostId}/runs`),
    refetchInterval: (query) => {
      const data = query.state.data as CACertActionRun[] | undefined
      return data?.some((r) => r.status === "pending" || r.status === "running") ? 5000 : false
    },
  })

  const saveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      editingId != null
        ? apiFetch(`/api/hosts/${hostId}/ca-certs/${editingId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/hosts/${hostId}/ca-certs`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-ca-certs", hostId], ["host-ca-cert-overrides", hostId]],
    onSuccess: () => setDialogOpen(false),
  })
  const deleteMutation = useApiMutation({
    mutationFn: (overrideId: number) => apiFetch(`/api/hosts/${hostId}/ca-certs/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-ca-certs", hostId], ["host-ca-cert-overrides", hostId]],
    onSuccess: () => setDeleting(null),
  })
  const deployMutation = useApiMutation({
    mutationFn: () => apiFetch(`/api/ca-certs/hosts/${hostId}/deploy`, { method: "POST" }),
    invalidateKeys: [["host-ca-cert-runs", hostId]],
    onSuccess: () => setDeployConfirm(false),
  })

  function openCreate() {
    setEditingId(null)
    setForm(defaults)
    saveMutation.reset()
    setDialogOpen(true)
  }
  function openEdit(cert: EffectiveCACert) {
    const override = cert.source === "host" ? overrides?.find((o) => o.fingerprint_sha256 === cert.fingerprint_sha256) : null
    setForm({ name: cert.name, pem: cert.pem_content, comment: override?.comment ?? "", state: cert.state })
    setEditingId(override?.id ?? null)
    saveMutation.reset()
    setDialogOpen(true)
  }
  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    saveMutation.mutate({ name: form.name, pem_content: form.pem, state: form.state, comment: form.comment || null })
  }
  function handleDelete(target: { fingerprint: string; name: string }) {
    const o = overrides?.find((x) => x.fingerprint_sha256 === target.fingerprint)
    if (o) deleteMutation.mutate(o.id)
  }

  const rows = certs ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={deployMutation.isPending || !host?.ssh_key_id} onClick={() => setDeployConfirm(true)}>
              deploy
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>add override</button>
          </>
        }
      >
        <span className="tt">{plural(rows.length, "certificate")} effective · deployed as a one-time action, no drift detection</span>
      </Toolbar>

      {error && <Banner tone="danger" flush>Could not load CA certificates: {error.message}</Banner>}
      {deleteMutation.error && <Banner tone="danger" flush>{deleteMutation.error.message}</Banner>}
      {deployMutation.error && <Banner tone="danger" flush>{deployMutation.error.message}</Banner>}

      <Table<EffectiveCACert>
        cols={[
          { k: "name", label: "name", w: "minmax(130px,1fr)", sortable: false, cell: (c) => <span className="font-medium text-text">{c.name}</span> },
          { k: "subject", label: "subject", w: "minmax(140px,1.2fr)", sortable: false, cell: (c) => <span className="trunc text-text-3" title={c.subject ?? ""}>{c.subject ?? "—"}</span> },
          { k: "expires", label: "expires", w: "100px", sortable: false, cell: (c) => <span className="text-text-3">{c.not_after ? new Date(c.not_after).toLocaleDateString() : "—"}</span> },
          { k: "fingerprint", label: "fingerprint", w: "minmax(140px,1.2fr)", sortable: false, cell: (c) => <span className="mono trunc text-[10.5px] text-text-faint" title={c.fingerprint_sha256}>{c.fingerprint_sha256}</span> },
          { k: "state", label: "state", w: "80px", sortable: false, cell: (c) => <Tag tone={def(ITEM_STATE, c.state).tone}>{c.state}</Tag> },
          { k: "source", label: "source", w: "minmax(110px,1fr)", sortable: false, cell: (c) => <Provenance origin={c.source} label={c.source === "host" ? "this host" : c.source_name} /> },
          {
            k: "actions", label: "", w: "108px", right: true, sortable: false,
            cell: (c) =>
              c.source === "group" ? (
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(c)}>edit</button>
              ) : (
                <span className="flex gap-0.5">
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(c)}>edit</button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => setDeleting({ fingerprint: c.fingerprint_sha256, name: c.name })}>delete</button>
                </span>
              ),
          },
        ]}
        rows={rows}
        keyOf={(c) => `${c.source}-${c.source_id}-${c.fingerprint_sha256}`}
        loading={isLoading}
        empty="No CA certificates configured. Add a host override or assign certificates to a group."
      />

      <div className="tt border-t border-line bg-surface-2 px-[13px] py-1.5">recent deployment runs</div>
      <Table<CACertActionRun>
        cols={[
          { k: "id", label: "run #", w: "70px", sortable: false, cell: (r) => <span className="mono text-[11px] text-text-3">#{r.id}</span> },
          { k: "status", label: "status", w: "90px", sortable: false, cell: (r) => <Tag tone={def(JOB_STATUS, r.status).tone}>{r.status}</Tag> },
          { k: "started", label: "started", w: "minmax(140px,1fr)", sortable: false, cell: (r) => <span className="text-text-3">{r.started_at ? new Date(r.started_at).toLocaleString() : "—"}</span> },
          { k: "completed", label: "completed", w: "minmax(140px,1fr)", sortable: false, cell: (r) => <span className="text-text-3">{r.completed_at ? new Date(r.completed_at).toLocaleString() : "—"}</span> },
        ]}
        rows={(runs ?? []).slice(0, 20)}
        keyOf={(r) => r.id}
        empty="No deployment runs yet."
      />

      {dialogOpen && (
        <Modal
          title={editingId != null ? "Edit CA certificate override" : "Add CA certificate override"}
          w={620}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>{saveMutation.isPending ? "Saving…" : editingId != null ? "Save changes" : "Create override"}</button>
            </>
          }
        >
          <Field label="display name" htmlFor="ca-name">
            <input id="ca-name" className="inp" placeholder="e.g. Internal Root CA" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
          </Field>
          <Field label="PEM content" htmlFor="ca-pem">
            <textarea id="ca-pem" className="inp mono" rows={9} placeholder={"-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"} value={form.pem} onChange={(e) => setForm((f) => ({ ...f, pem: e.target.value }))} required />
          </Field>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[120px_1fr]">
            <Field as="div" label="state">
              <select className="inp" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value as "present" | "absent" }))}>
                <option value="present">present</option>
                <option value="absent">absent</option>
              </select>
            </Field>
            <Field label="comment" htmlFor="ca-comment" hint="why this CA is trusted">
              <input id="ca-comment" className="inp" value={form.comment} onChange={(e) => setForm((f) => ({ ...f, comment: e.target.value }))} />
            </Field>
          </div>

          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete CA certificate override"
          description={`Delete the override "${deleting.name}"? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => handleDelete(deleting)}
        />
      )}

      <Confirm
        open={deployConfirm}
        onOpenChange={setDeployConfirm}
        title="Deploy CA certificates to this host?"
        description="This runs the CA certificate deployment playbook on this host now. If a deploy is already in progress it will be rejected."
        confirmLabel="Deploy"
        loading={deployMutation.isPending}
        onConfirm={() => deployMutation.mutate(undefined)}
      />
    </div>
  )
}
