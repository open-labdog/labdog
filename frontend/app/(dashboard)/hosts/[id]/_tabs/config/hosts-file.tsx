"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch, API_BASE } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { Banner, Confirm, Field, Modal, Provenance, Seg, Table, Toolbar } from "@/components/ld"
import type { EffectiveHostsEntry, Host, HostsEntry, ModuleCurrentState } from "@/lib/types"
import { CurrentStateSection } from "./shared"

const defaults = { mode: "literal" as "literal" | "host", refId: null as number | null, ip: "", hostname: "", aliases: "", comment: "", priority: 100 }

export function HostsFileTab({
  hostId,
  host,
  currentState,
  syncBusy,
  onSync,
}: {
  hostId: number
  host: Host | undefined
  currentState: ModuleCurrentState[] | undefined
  syncBusy: boolean
  onSync: () => void
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState(defaults)
  const [deleting, setDeleting] = useState<HostsEntry | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const { data: entries, isLoading, error } = useQuery<EffectiveHostsEntry[]>({
    queryKey: ["host-effective-hosts-entries", hostId],
    queryFn: () => apiFetch<EffectiveHostsEntry[]>(`/api/hosts/${hostId}/effective-hosts-entries`),
  })
  const { data: overrides } = useQuery<HostsEntry[]>({
    queryKey: ["host-hosts-overrides", hostId],
    queryFn: () => apiFetch<HostsEntry[]>(`/api/hosts/${hostId}/hosts-entries`),
  })
  const { data: hosts = [] } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts"), enabled: dialogOpen })

  const saveMutation = useApiMutation({
    mutationFn: ({ entryId, payload }: { entryId?: number; payload: Record<string, unknown> }) =>
      entryId
        ? apiFetch(`/api/hosts/${hostId}/hosts-entries/${entryId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/hosts/${hostId}/hosts-entries`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-hosts-entries", hostId], ["host-hosts-overrides", hostId]],
    onSuccess: () => setDialogOpen(false),
  })
  const deleteMutation = useApiMutation({
    mutationFn: (entryId: number) => apiFetch(`/api/hosts/${hostId}/hosts-entries/${entryId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-hosts-entries", hostId], ["host-hosts-overrides", hostId]],
    onSuccess: () => setDeleting(null),
  })

  function openCreate() {
    setEditingId(null)
    setForm(defaults)
    saveMutation.reset()
    setDialogOpen(true)
  }

  function openEdit(entry: EffectiveHostsEntry) {
    const override = entry.source === "host" ? overrides?.find((o) => o.hostname === entry.hostname && o.ip_address === entry.ip_address) : null
    setForm({
      mode: override?.host_ref_id != null ? "host" : "literal",
      refId: override?.host_ref_id ?? null,
      ip: entry.ip_address, hostname: entry.hostname,
      aliases: entry.aliases.join(", "), comment: entry.comment ?? "",
      priority: override?.priority ?? 100,
    })
    setEditingId(override?.id ?? null)
    saveMutation.reset()
    setDialogOpen(true)
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const isRef = form.mode === "host"
    const payload: Record<string, unknown> = {
      ip_address: isRef ? null : form.ip,
      hostname: isRef ? null : form.hostname,
      host_ref_id: isRef ? form.refId : null,
      aliases: form.aliases.split(",").map((a) => a.trim()).filter(Boolean),
      comment: form.comment || null,
      priority: form.priority,
    }
    saveMutation.mutate({ entryId: editingId ?? undefined, payload })
  }

  async function fetchPreview() {
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const res = await fetch(`${API_BASE}/api/hosts/${hostId}/hosts-file-preview`, { credentials: "include" })
      if (!res.ok) throw new Error("Failed to load preview")
      setPreview(await res.text())
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Failed to load preview")
    } finally {
      setPreviewLoading(false)
    }
  }

  const rows = entries ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={!host?.ssh_key_id || syncBusy} onClick={onSync}>
              sync
            </button>
            <button type="button" className="btn btn-sm btn-ghost" disabled={previewLoading} onClick={fetchPreview}>
              {previewLoading ? "loading…" : "preview file"}
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
              add override
            </button>
          </>
        }
      >
        <span className="tt">{plural(rows.length, "entry")} effective</span>
      </Toolbar>

      {error && <Banner tone="danger" flush>Could not load /etc/hosts entries: {error.message}</Banner>}
      {deleteMutation.error && <Banner tone="danger" flush>{deleteMutation.error.message}</Banner>}
      {previewError && <Banner tone="danger" flush>{previewError}</Banner>}

      {preview !== null && (
        <div className="border-b border-line bg-bg p-2.5">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="tt">/etc/hosts preview</span>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPreview(null)}>close</button>
          </div>
          <pre className="mono max-h-64 overflow-y-auto text-[11px] text-text-2">{preview}</pre>
        </div>
      )}

      <Table<EffectiveHostsEntry>
        cols={[
          { k: "ip", label: "ip address", w: "130px", sortable: false, cell: (e) => <span className="mono font-medium text-text">{e.ip_address}</span> },
          { k: "hostname", label: "hostname", w: "minmax(150px,1fr)", sortable: false, cell: (e) => <span className="mono text-text-2">{e.hostname}</span> },
          { k: "aliases", label: "aliases", w: "minmax(140px,1.2fr)", sortable: false, cell: (e) => <span className="trunc text-text-3">{e.aliases.length ? e.aliases.join(", ") : "—"}</span> },
          { k: "source", label: "source", w: "minmax(120px,1fr)", sortable: false, cell: (e) => <Provenance origin={e.source} label={e.source === "system" ? "system" : e.source === "host" ? "this host" : e.source_name} /> },
          {
            k: "actions", label: "", w: "108px", right: true, sortable: false,
            cell: (e) => {
              if (e.is_system || e.source === "system") return <span className="text-[11px] text-text-faint">read-only</span>
              if (e.source === "group") return <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(e)}>edit</button>
              const override = overrides?.find((o) => o.hostname === e.hostname && o.ip_address === e.ip_address)
              if (!override) return null
              return (
                <span className="flex gap-0.5">
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(e)}>edit</button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => setDeleting(override)}>delete</button>
                </span>
              )
            },
          },
        ]}
        rows={rows}
        keyOf={(e) => `${e.source}-${e.source_id}-${e.ip_address}-${e.hostname}`}
        loading={isLoading}
        empty="No hosts file entries. Add a host override or assign entries to a group."
      />

      {dialogOpen && (
        <Modal
          title={editingId != null ? "Edit hosts entry override" : "Add hosts entry override"}
          w={520}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>{saveMutation.isPending ? "Saving…" : editingId != null ? "Save changes" : "Create override"}</button>
            </>
          }
        >
          <Seg options={[{ k: "literal", label: "Literal IP + hostname" }, { k: "host", label: "Registered host" }]} value={form.mode} onChange={(k) => setForm((f) => ({ ...f, mode: k as "literal" | "host" }))} />

          {form.mode === "literal" ? (
            <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
              <Field label="ip address" htmlFor="hosts-ip">
                <input id="hosts-ip" className="inp mono" placeholder="e.g. 192.168.1.10" value={form.ip} onChange={(e) => setForm((f) => ({ ...f, ip: e.target.value }))} required />
              </Field>
              <Field label="hostname" htmlFor="hosts-hostname">
                <input id="hosts-hostname" className="inp mono" placeholder="e.g. myserver.local" value={form.hostname} onChange={(e) => setForm((f) => ({ ...f, hostname: e.target.value }))} required />
              </Field>
            </div>
          ) : (
            <Field as="div" label="host" hint="uses the host's current IP and hostname at sync time">
              <select className="inp mono" value={form.refId ?? ""} onChange={(e) => setForm((f) => ({ ...f, refId: e.target.value ? Number(e.target.value) : null }))}>
                <option value="">— pick a host —</option>
                {hosts.map((h) => <option key={h.id} value={h.id}>{h.hostname} · {h.ip_address}</option>)}
              </select>
            </Field>
          )}

          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field label="aliases" htmlFor="hosts-aliases" hint="comma-separated">
              <input id="hosts-aliases" className="inp" placeholder="e.g. myserver, ms" value={form.aliases} onChange={(e) => setForm((f) => ({ ...f, aliases: e.target.value }))} />
            </Field>
            <Field label="priority" htmlFor="hosts-priority">
              <input id="hosts-priority" type="number" min={0} className="inp mono num" value={form.priority} onChange={(e) => setForm((f) => ({ ...f, priority: Number(e.target.value) }))} required />
            </Field>
          </div>
          <Field label="comment" htmlFor="hosts-comment" hint="optional">
            <input id="hosts-comment" className="inp" value={form.comment} onChange={(e) => setForm((f) => ({ ...f, comment: e.target.value }))} />
          </Field>

          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete hosts entry override"
          description={`Delete the override for "${deleting.ip_address} ${deleting.hostname}"? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleting.id)}
        />
      )}

      <CurrentStateSection moduleType="hosts_file" modules={currentState} hostId={hostId} />
    </div>
  )
}
