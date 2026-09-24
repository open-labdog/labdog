"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { cronToHuman } from "@/lib/cron"
import { def, ITEM_STATE } from "@/lib/status"
import { Banner, Confirm, Field, Modal, Provenance, Table, Tag, Toolbar } from "@/components/ld"
import type { CronJob, EffectiveCronJob, ModuleCurrentState } from "@/lib/types"
import { CurrentStateSection } from "./shared"

const defaults = { name: "", user: "root", schedule: "", command: "", state: "present" as "present" | "absent", priority: 100, comment: "", env: [] as { key: string; value: string }[] }

export function CronTab({
  hostId,
  currentState,
  syncBusy,
  onSync,
}: {
  hostId: number
  currentState: ModuleCurrentState[] | undefined
  syncBusy: boolean
  onSync: () => void
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState(defaults)
  const [deleting, setDeleting] = useState<{ name: string; user: string } | null>(null)

  const { data: jobs, isLoading, error } = useQuery<EffectiveCronJob[]>({
    queryKey: ["host-effective-cron-jobs", hostId],
    queryFn: () => apiFetch<EffectiveCronJob[]>(`/api/hosts/${hostId}/effective-cron-jobs`),
  })
  const { data: overrides } = useQuery<CronJob[]>({
    queryKey: ["host-cron-overrides", hostId],
    queryFn: () => apiFetch<CronJob[]>(`/api/hosts/${hostId}/cron-jobs`),
  })

  const saveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      editingId != null
        ? apiFetch(`/api/hosts/${hostId}/cron-jobs/${editingId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/hosts/${hostId}/cron-jobs`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-cron-jobs", hostId], ["host-cron-overrides", hostId]],
    onSuccess: () => setDialogOpen(false),
  })
  const deleteMutation = useApiMutation({
    mutationFn: (overrideId: number) => apiFetch(`/api/hosts/${hostId}/cron-jobs/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-cron-jobs", hostId], ["host-cron-overrides", hostId]],
    onSuccess: () => setDeleting(null),
  })

  function openCreate() {
    setEditingId(null)
    setForm(defaults)
    saveMutation.reset()
    setDialogOpen(true)
  }
  function openEdit(job: EffectiveCronJob) {
    const override = job.source === "host" ? overrides?.find((o) => o.name === job.name && o.user === job.user) : null
    setForm({ name: job.name, user: job.user, schedule: job.schedule, command: job.command, state: job.state, priority: job.priority, comment: job.comment ?? "", env: Object.entries(job.environment).map(([key, value]) => ({ key, value })) })
    setEditingId(override?.id ?? null)
    saveMutation.reset()
    setDialogOpen(true)
  }
  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const env: Record<string, string> = {}
    for (const v of form.env) { const k = v.key.trim(); if (k) env[k] = v.value }
    saveMutation.mutate({ name: form.name, user: form.user, schedule: form.schedule, command: form.command, state: form.state, priority: form.priority, comment: form.comment || null, environment: env })
  }
  function handleDelete(job: { name: string; user: string }) {
    const o = overrides?.find((x) => x.name === job.name && x.user === job.user)
    if (o) deleteMutation.mutate(o.id)
  }

  const rows = jobs ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={syncBusy} onClick={onSync}>sync</button>
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>add override</button>
          </>
        }
      >
        <span className="tt">{plural(rows.length, "job")} effective</span>
      </Toolbar>

      {error && <Banner tone="danger" flush>Could not load cron jobs: {error.message}</Banner>}
      {deleteMutation.error && <Banner tone="danger" flush>{deleteMutation.error.message}</Banner>}

      <Table<EffectiveCronJob>
        cols={[
          { k: "name", label: "name", w: "minmax(150px,1fr)", sortable: false, cell: (j) => <span className="mono font-medium text-text">{j.name}</span> },
          { k: "user", label: "user", w: "90px", sortable: false, cell: (j) => <span className="mono text-[11px] text-text-3">{j.user}</span> },
          { k: "schedule", label: "schedule", w: "minmax(120px,0.9fr)", sortable: false, cell: (j) => <span className="mono text-[11px] text-text-3" title={cronToHuman(j.schedule)}>{j.schedule}</span> },
          { k: "command", label: "command", w: "minmax(160px,1.4fr)", sortable: false, cell: (j) => <span className="mono trunc text-[11px] text-text-3" title={j.command}>{j.command}</span> },
          { k: "state", label: "state", w: "80px", sortable: false, cell: (j) => <Tag tone={def(ITEM_STATE, j.state).tone}>{j.state}</Tag> },
          { k: "source", label: "source", w: "minmax(110px,1fr)", sortable: false, cell: (j) => <Provenance origin={j.source} label={j.source === "host" ? "this host" : j.source_name} /> },
          {
            k: "actions", label: "", w: "108px", right: true, sortable: false,
            cell: (j) =>
              j.source === "group" ? (
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(j)}>edit</button>
              ) : (
                <span className="flex gap-0.5">
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(j)}>edit</button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => setDeleting({ name: j.name, user: j.user })}>delete</button>
                </span>
              ),
          },
        ]}
        rows={rows}
        keyOf={(j) => `${j.source}-${j.source_id}-${j.name}-${j.user}`}
        loading={isLoading}
        empty="No cron jobs configured. Add a host override or assign cron jobs to a group."
      />

      {dialogOpen && (
        <Modal
          title={editingId != null ? "Edit cron job override" : "Add cron job override"}
          w={560}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>{saveMutation.isPending ? "Saving…" : editingId != null ? "Save changes" : "Create override"}</button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field label="name" htmlFor="cj-name">
              <input id="cj-name" className="inp" placeholder="e.g. backup-db, cleanup-logs" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
            </Field>
            <Field label="user" htmlFor="cj-user">
              <input id="cj-user" className="inp mono" placeholder="root" value={form.user} onChange={(e) => setForm((f) => ({ ...f, user: e.target.value }))} required />
            </Field>
          </div>
          <Field label="schedule" htmlFor="cj-schedule" hint={form.schedule.trim() ? cronToHuman(form.schedule) : "cron expression"}>
            <input id="cj-schedule" className="inp mono" placeholder="*/5 * * * *" value={form.schedule} onChange={(e) => setForm((f) => ({ ...f, schedule: e.target.value }))} required />
          </Field>
          <Field label="command" htmlFor="cj-command">
            <textarea id="cj-command" className="inp mono" rows={3} placeholder="e.g. /usr/local/bin/backup.sh --full" value={form.command} onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))} required />
          </Field>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field as="div" label="state">
              <select className="inp" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value as "present" | "absent" }))}>
                <option value="present">present</option>
                <option value="absent">absent</option>
              </select>
            </Field>
            <Field label="priority" htmlFor="cj-priority">
              <input id="cj-priority" type="number" min={0} className="inp mono num" value={form.priority} onChange={(e) => setForm((f) => ({ ...f, priority: Number(e.target.value) }))} required />
            </Field>
          </div>
          <Field label="comment" htmlFor="cj-comment" hint="optional">
            <input id="cj-comment" className="inp" value={form.comment} onChange={(e) => setForm((f) => ({ ...f, comment: e.target.value }))} />
          </Field>

          <Field as="div" label="environment variables">
            <div className="flex flex-col gap-1.5">
              {form.env.map((v, idx) => (
                <div key={idx} className="flex items-center gap-1.5">
                  <input className="inp mono" style={{ flex: 1 }} placeholder="KEY" value={v.key} onChange={(e) => setForm((f) => ({ ...f, env: f.env.map((ev, i) => (i === idx ? { ...ev, key: e.target.value } : ev)) }))} />
                  <span className="text-text-faint">=</span>
                  <input className="inp mono" style={{ flex: 1 }} placeholder="value" value={v.value} onChange={(e) => setForm((f) => ({ ...f, env: f.env.map((ev, i) => (i === idx ? { ...ev, value: e.target.value } : ev)) }))} />
                  <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setForm((f) => ({ ...f, env: f.env.filter((_, i) => i !== idx) }))}>&times;</button>
                </div>
              ))}
              <button type="button" className="btn btn-sm" onClick={() => setForm((f) => ({ ...f, env: [...f.env, { key: "", value: "" }] }))}>+ add variable</button>
            </div>
          </Field>

          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete cron job override"
          description={`Delete the override for "${deleting.name}" (user: ${deleting.user})? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => handleDelete(deleting)}
        />
      )}

      <CurrentStateSection moduleType="cron" modules={currentState} hostId={hostId} />
    </div>
  )
}
