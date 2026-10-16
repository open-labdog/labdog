"use client"

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { ITEM_STATE, def } from "@/lib/status"
import { cronToHuman } from "@/lib/cron"
import { cronJobSchema, type CronJobInput } from "@/lib/schemas"
import type { CronJob } from "@/lib/types"
import { Banner, Confirm, Field, Modal, Table, Tag, Toolbar } from "@/components/ld"
import { GROUP_KEYS, GitOpsBanner, stop, useEditorGroup } from "./shared"

interface EnvVar {
  key: string
  value: string
}

/** KEY=value rows for a job's environment; blank keys are dropped on save. */
function EnvEditor({ vars, onChange }: { vars: EnvVar[]; onChange: (v: EnvVar[]) => void }) {
  const update = (idx: number, field: "key" | "value", val: string) => onChange(vars.map((v, i) => (i === idx ? { ...v, [field]: val } : v)))
  return (
    <div className="flex flex-col gap-1.5">
      {vars.map((v, idx) => (
        <div key={idx} className="flex items-center gap-1.5">
          <input className="inp mono" style={{ flex: 1 }} placeholder="KEY" value={v.key} onChange={(e) => update(idx, "key", e.target.value)} aria-label={`variable ${idx + 1} name`} />
          <span className="mono text-text-faint">=</span>
          <input className="inp mono" style={{ flex: 2 }} placeholder="value" value={v.value} onChange={(e) => update(idx, "value", e.target.value)} aria-label={`variable ${idx + 1} value`} />
          <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => onChange(vars.filter((_, i) => i !== idx))} aria-label="remove variable">
            ×
          </button>
        </div>
      ))}
      <div>
        <button type="button" className="btn btn-sm" onClick={() => onChange([...vars, { key: "", value: "" }])}>
          + add variable
        </button>
      </div>
    </div>
  )
}

const envRecordToVars = (env: Record<string, string>): EnvVar[] => Object.entries(env).map(([key, value]) => ({ key, value }))
function envVarsToRecord(vars: EnvVar[]): Record<string, string> {
  const record: Record<string, string> = {}
  for (const v of vars) {
    const k = v.key.trim()
    if (k) record[k] = v.value
  }
  return record
}

function parseSchedule(schedule: string) {
  const parts = schedule.trim().split(/\s+/)
  return { minute: parts[0] ?? "*", hour: parts[1] ?? "*", day: parts[2] ?? "*", month: parts[3] ?? "*", weekday: parts[4] ?? "*" }
}

const defaults: CronJobInput = { name: "", user: "root", minute: "*", hour: "*", day: "*", month: "*", weekday: "*", command: "", state: "present", priority: 100, comment: "" }

/**
 * Cron jobs a group declares — a schedule, a user, a command and its
 * environment. Embedded in the group page's Config tab.
 */
export function CronEditor({ groupId }: { groupId: number }) {
  const { group, gitops } = useEditorGroup(groupId)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<CronJob | null>(null)
  const [deleting, setDeleting] = useState<CronJob | null>(null)
  const [envVars, setEnvVars] = useState<EnvVar[]>([])

  const form = useForm<CronJobInput>({ resolver: zodResolver(cronJobSchema), defaultValues: defaults, mode: "onSubmit" })
  const [minute, hour, day, month, weekday] = [form.watch("minute"), form.watch("hour"), form.watch("day"), form.watch("month"), form.watch("weekday")]
  const preview = `${minute} ${hour} ${day} ${month} ${weekday}`

  const { data: cronJobs, isLoading, error } = useQuery<CronJob[]>({
    queryKey: ["cron-jobs", groupId],
    queryFn: () => apiFetch<CronJob[]>(`/api/groups/${groupId}/cron-jobs`),
    enabled: !!groupId,
  })

  const saveMutation = useApiMutation({
    mutationFn: ({ jobId, payload }: { jobId?: number; payload: Record<string, unknown> }) =>
      jobId
        ? apiFetch(`/api/groups/${groupId}/cron-jobs/${jobId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/groups/${groupId}/cron-jobs`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["cron-jobs", groupId], ...GROUP_KEYS],
    onSuccess: () => setDialogOpen(false),
  })
  const deleteMutation = useApiMutation({
    mutationFn: (jobId: number) => apiFetch(`/api/groups/${groupId}/cron-jobs/${jobId}`, { method: "DELETE" }),
    invalidateKeys: [["cron-jobs", groupId], ...GROUP_KEYS],
    onSuccess: () => setDeleting(null),
  })

  function openCreate() {
    setEditing(null)
    form.reset(defaults)
    setEnvVars([])
    saveMutation.reset()
    setDialogOpen(true)
  }

  function openEdit(job: CronJob) {
    setEditing(job)
    setEnvVars(envRecordToVars(job.environment ?? {}))
    saveMutation.reset()
    setDialogOpen(true)
  }

  useEffect(() => {
    if (dialogOpen && editing) {
      form.reset({ name: editing.name, user: editing.user, ...parseSchedule(editing.schedule), command: editing.command, state: editing.state, priority: editing.priority, comment: editing.comment ?? "" })
    }
  }, [dialogOpen, editing, form])

  const onSubmit = form.handleSubmit((data) => {
    saveMutation.mutate({
      jobId: editing?.id,
      payload: {
        name: data.name,
        user: data.user,
        schedule: `${data.minute} ${data.hour} ${data.day} ${data.month} ${data.weekday}`,
        command: data.command,
        state: data.state,
        priority: data.priority,
        comment: data.comment || null,
        environment: envVarsToRecord(envVars),
      },
    })
  })

  const rows = cronJobs ?? []
  const errs = form.formState.errors
  const scheduleError = errs.minute?.message ?? errs.hour?.message ?? errs.day?.message ?? errs.month?.message ?? errs.weekday?.message

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          !gitops && (
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
              Add cron job
            </button>
          )
        }
      >
        <span className="tt">{plural(rows.length, "job")} declared here</span>
      </Toolbar>
      {gitops && <GitOpsBanner group={group} what="cron jobs are" />}
      {error && (
        <Banner tone="danger" flush>
          Could not load cron jobs: {error.message}
        </Banner>
      )}

      <Table<CronJob>
        cols={[
          { k: "name", label: "job", w: "minmax(140px,1fr)", sortable: false, cell: (j) => <span className="mono font-medium text-text">{j.name}</span> },
          { k: "user", label: "user", w: "90px", sortable: false, cell: (j) => <span className="mono text-[11px]">{j.user}</span> },
          {
            k: "schedule",
            label: "schedule",
            w: "minmax(150px,1fr)",
            sortable: false,
            cell: (j) => (
              <span className="flex min-w-0 flex-col">
                <span className="mono text-[11px] text-text">{j.schedule}</span>
                {cronToHuman(j.schedule) !== j.schedule && <span className="trunc text-[10.5px] text-text-faint">{cronToHuman(j.schedule)}</span>}
              </span>
            ),
          },
          { k: "command", label: "command", w: "minmax(200px,1.8fr)", sortable: false, cell: (j) => <span className="mono text-[11px]" title={j.command}>{j.command}</span> },
          { k: "state", label: "state", w: "90px", sortable: false, cell: (j) => <Tag tone={def(ITEM_STATE, j.state).tone}>{j.state}</Tag> },
          { k: "priority", label: "priority", w: "70px", right: true, sortable: false, cell: (j) => <span className="mono num">{j.priority}</span> },
          {
            k: "actions",
            label: "",
            w: "118px",
            right: true,
            sortable: false,
            cell: (j) => (
              <span className="flex gap-0.5" onClick={stop}>
                <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} onClick={() => openEdit(j)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={gitops || deleteMutation.isPending} onClick={() => setDeleting(j)}>
                  delete
                </button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(j) => j.id}
        onRowClick={gitops ? undefined : openEdit}
        loading={isLoading}
        empty="Nothing declared. Add cron job declares this module for the group; the crontab is written on the next plan."
      />

      {dialogOpen && (
        <Modal
          title={editing ? "Edit cron job" : "Add cron job"}
          meta={group ? `group: ${group.name}` : undefined}
          w={620}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <span className="tt mr-auto">desired state only — nothing applies until a plan runs</span>
              <button type="button" className="btn" onClick={() => setDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving…" : editing ? "Save changes" : "Add cron job"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_120px_110px_90px]">
            <Field label="name" htmlFor="cron-name" error={errs.name?.message}>
              <input id="cron-name" className="inp mono" placeholder="backup-nightly" {...form.register("name")} />
            </Field>
            <Field label="user" htmlFor="cron-user" error={errs.user?.message}>
              <input id="cron-user" className="inp mono" {...form.register("user")} />
            </Field>
            <Field label="state" htmlFor="cron-state">
              <select id="cron-state" className="inp" {...form.register("state")}>
                <option value="present">present</option>
                <option value="absent">absent</option>
              </select>
            </Field>
            <Field label="priority" htmlFor="cron-priority" error={errs.priority?.message}>
              <input id="cron-priority" type="number" min={0} className="inp mono num" {...form.register("priority", { valueAsNumber: true })} />
            </Field>
          </div>
          <Field as="div" label="schedule" hint={cronToHuman(preview) !== preview ? cronToHuman(preview) : "minute · hour · day · month · weekday"} error={scheduleError}>
            <div className="grid gap-1.5" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
              {(["minute", "hour", "day", "month", "weekday"] as const).map((f) => (
                <input key={f} className="inp mono num text-center" placeholder={f} aria-label={f} {...form.register(f)} />
              ))}
            </div>
          </Field>
          <Field label="command" htmlFor="cron-command" error={errs.command?.message}>
            <textarea id="cron-command" className="inp mono" rows={3} spellCheck={false} placeholder="/usr/local/bin/backup.sh --nightly" {...form.register("command")} />
          </Field>
          <Field as="div" label="environment" hint="KEY=value pairs written above the job">
            <EnvEditor vars={envVars} onChange={setEnvVars} />
          </Field>
          <Field label="comment" htmlFor="cron-comment" hint="optional">
            <input id="cron-comment" className="inp" {...form.register("comment")} />
          </Field>
          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete cron job"
          description={`${deleting.name} is no longer declared by this group; the crontab line stays until a plan runs with it absent. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleting.id)}
        />
      )}
    </div>
  )
}
