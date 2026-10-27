"use client"

import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { hostsEntrySchema, type HostsEntryInput } from "@/lib/schemas"
import type { Host, HostsEntry } from "@/lib/types"
import { Banner, Confirm, Field, Modal, Seg, Table, Tag, Toolbar } from "@/components/ld"
import { GROUP_KEYS, GitOpsBanner, stop, useEditorGroup } from "./shared"

const defaults: HostsEntryInput = { mode: "literal", ip_address: "", hostname: "", host_ref_id: null, aliases: "", comment: "", priority: 100 }

/**
 * /etc/hosts entries a group declares — a literal address and name, or
 * a reference to a managed host whose current address is used at sync
 * time. Embedded in the group page's Config tab.
 */
export function HostsFileEditor({ groupId }: { groupId: number }) {
  const { group, gitops } = useEditorGroup(groupId)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<HostsEntry | null>(null)
  const [deleting, setDeleting] = useState<HostsEntry | null>(null)

  const form = useForm<HostsEntryInput>({ resolver: zodResolver(hostsEntrySchema), defaultValues: defaults, mode: "onSubmit" })

  const { data: entries, isLoading, error } = useQuery<HostsEntry[]>({
    queryKey: ["hosts-entries", groupId],
    queryFn: () => apiFetch<HostsEntry[]>(`/api/groups/${groupId}/hosts-entries`),
    enabled: !!groupId,
  })
  const { data: allHosts = [] } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })
  const hostById = useMemo(() => new Map(allHosts.map((h) => [h.id, h])), [allHosts])
  const ipOf = (e: HostsEntry) => (e.host_ref_id != null ? (hostById.get(e.host_ref_id)?.ip_address ?? "…") : (e.ip_address ?? ""))
  const nameOf = (e: HostsEntry) => (e.host_ref_id != null ? (hostById.get(e.host_ref_id)?.hostname ?? "…") : (e.hostname ?? ""))

  const saveMutation = useApiMutation({
    mutationFn: ({ entryId, payload }: { entryId?: number; payload: Record<string, unknown> }) =>
      entryId
        ? apiFetch(`/api/groups/${groupId}/hosts-entries/${entryId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/groups/${groupId}/hosts-entries`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["hosts-entries", groupId], ...GROUP_KEYS],
    onSuccess: () => setDialogOpen(false),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (entryId: number) => apiFetch(`/api/groups/${groupId}/hosts-entries/${entryId}`, { method: "DELETE" }),
    invalidateKeys: [["hosts-entries", groupId], ...GROUP_KEYS],
    onSuccess: () => setDeleting(null),
  })

  function openCreate() {
    setEditing(null)
    form.reset(defaults)
    saveMutation.reset()
    setDialogOpen(true)
  }

  function openEdit(entry: HostsEntry) {
    setEditing(entry)
    saveMutation.reset()
    setDialogOpen(true)
  }

  useEffect(() => {
    if (dialogOpen && editing) {
      form.reset({
        mode: editing.host_ref_id != null ? "host" : "literal",
        ip_address: editing.ip_address ?? "",
        hostname: editing.hostname ?? "",
        host_ref_id: editing.host_ref_id ?? null,
        aliases: editing.aliases.join(", "),
        comment: editing.comment ?? "",
        priority: editing.priority,
      })
    }
  }, [dialogOpen, editing, form])

  const onSubmit = form.handleSubmit((data) => {
    const isRef = data.mode === "host"
    saveMutation.mutate({
      entryId: editing?.id,
      payload: {
        ip_address: isRef ? null : data.ip_address,
        hostname: isRef ? null : data.hostname,
        host_ref_id: isRef ? data.host_ref_id : null,
        aliases: (data.aliases ?? "")
          .split(",")
          .map((a: string) => a.trim())
          .filter(Boolean),
        comment: data.comment || null,
        priority: data.priority,
      },
    })
  })

  const rows = entries ?? []
  const mode = form.watch("mode")

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          !gitops && (
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
              Add entry
            </button>
          )
        }
      >
        <span className="tt">{rows.length} {rows.length === 1 ? "entry" : "entries"} declared here</span>
      </Toolbar>
      {gitops && <GitOpsBanner group={group} what="hosts entries are" />}
      {error && (
        <Banner tone="danger" flush>
          Could not load hosts entries: {error.message}
        </Banner>
      )}

      <Table<HostsEntry>
        cols={[
          {
            k: "ip",
            label: "address",
            w: "150px",
            sortable: false,
            cell: (e) => (
              <span className="flex items-center gap-1.5">
                <span className="mono text-text">{ipOf(e)}</span>
                {e.host_ref_id != null && <Tag tone="accent" title="follows the managed host's current address">host</Tag>}
              </span>
            ),
          },
          { k: "hostname", label: "hostname", w: "minmax(160px,1fr)", sortable: false, cell: (e) => <span className="mono font-medium text-text">{nameOf(e)}</span> },
          { k: "aliases", label: "aliases", w: "minmax(140px,1fr)", sortable: false, cell: (e) => <span className="mono text-[11px]">{e.aliases.length > 0 ? e.aliases.join(", ") : <span className="text-text-faint">—</span>}</span> },
          { k: "priority", label: "priority", w: "70px", right: true, sortable: false, cell: (e) => <span className="mono num">{e.priority}</span> },
          { k: "comment", label: "comment", w: "minmax(120px,1fr)", sortable: false, cell: (e) => <span className="text-[11.5px] text-text-3">{e.comment ?? ""}</span> },
          {
            k: "actions",
            label: "",
            w: "118px",
            right: true,
            sortable: false,
            cell: (e) => (
              <span className="flex gap-0.5" onClick={stop}>
                <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} onClick={() => openEdit(e)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={gitops || deleteMutation.isPending} onClick={() => setDeleting(e)}>
                  delete
                </button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(e) => e.id}
        onRowClick={gitops ? undefined : openEdit}
        loading={isLoading}
        empty="Nothing declared. Add entry declares this module for the group; system entries on the hosts are protected."
      />

      {dialogOpen && (
        <Modal
          title={editing ? "Edit hosts entry" : "Add hosts entry"}
          meta={group ? `group: ${group.name}` : undefined}
          w={520}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <span className="tt mr-auto">desired state only — nothing applies until a plan runs</span>
              <button type="button" className="btn" onClick={() => setDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving…" : editing ? "Save changes" : "Add entry"}
              </button>
            </>
          }
        >
          <Seg
            sm
            options={[
              { k: "literal", label: "Literal address + name" },
              { k: "host", label: "Registered host" },
            ]}
            value={mode}
            onChange={(k) => form.setValue("mode", k as "literal" | "host")}
          />
          {mode === "literal" ? (
            <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[160px_1fr]">
              <Field label="ip address" htmlFor="entry-ip" error={form.formState.errors.ip_address?.message}>
                <input id="entry-ip" className="inp mono" placeholder="192.168.1.10" {...form.register("ip_address")} />
              </Field>
              <Field label="hostname" htmlFor="entry-hostname" error={form.formState.errors.hostname?.message}>
                <input id="entry-hostname" className="inp mono" placeholder="myserver.local" {...form.register("hostname")} />
              </Field>
            </div>
          ) : (
            <Field label="host" htmlFor="entry-host" hint="uses the host's current address and name at sync time" error={form.formState.errors.host_ref_id?.message}>
              <select id="entry-host" className="inp mono" value={form.watch("host_ref_id") ?? ""} onChange={(e) => form.setValue("host_ref_id", e.target.value ? Number(e.target.value) : null)}>
                <option value="">— pick a host —</option>
                {allHosts.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.hostname} · {h.ip_address}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field label="aliases" htmlFor="entry-aliases" hint="comma-separated">
              <input id="entry-aliases" className="inp mono" placeholder="myserver, ms" {...form.register("aliases")} />
            </Field>
            <Field label="priority" htmlFor="entry-priority" error={form.formState.errors.priority?.message}>
              <input id="entry-priority" type="number" min={0} className="inp mono num" {...form.register("priority", { valueAsNumber: true })} />
            </Field>
          </div>
          <Field label="comment" htmlFor="entry-comment" hint="optional">
            <input id="entry-comment" className="inp" {...form.register("comment")} />
          </Field>
          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete hosts entry"
          description={`${ipOf(deleting)} ${nameOf(deleting)} is no longer declared by this group; hosts keep the line until a plan runs. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleting.id)}
        />
      )}
    </div>
  )
}
