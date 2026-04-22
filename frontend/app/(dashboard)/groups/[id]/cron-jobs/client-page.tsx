"use client"

import { useState, useEffect } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { InfoIcon, GitBranch } from "lucide-react"
import { Button } from "@/components/ui/button"
import { ItemStateBadge } from "@/components/status-badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Tooltip } from "@/components/ui/tooltip"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { DataTable } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { cronJobSchema, type CronJobInput } from "@/lib/schemas"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import type { CronJob, HostGroup } from "@/lib/types"

import { cronToHuman } from "@/lib/cron"


interface EnvVar {
  key: string
  value: string
}

function EnvEditor({ vars, onChange }: { vars: EnvVar[]; onChange: (v: EnvVar[]) => void }) {
  function addVar() {
    onChange([...vars, { key: "", value: "" }])
  }
  function removeVar(idx: number) {
    onChange(vars.filter((_, i) => i !== idx))
  }
  function updateVar(idx: number, field: "key" | "value", val: string) {
    const updated = vars.map((v, i) => (i === idx ? { ...v, [field]: val } : v))
    onChange(updated)
  }
  return (
    <div className="space-y-2">
      {vars.map((v, idx) => (
        <div key={idx} className="flex items-center gap-2">
          <Input
            type="text"
            placeholder="KEY"
            value={v.key}
            onChange={(e) => updateVar(idx, "key", e.target.value)}
            className="flex-1 font-mono text-xs"
          />
          <span className="text-slate-500 text-xs">=</span>
          <Input
            type="text"
            placeholder="value"
            value={v.value}
            onChange={(e) => updateVar(idx, "value", e.target.value)}
            className="flex-1 font-mono text-xs"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => removeVar(idx)}
            className="text-red-400 hover:text-red-300 hover:bg-red-950 px-2"
          >
            &times;
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={addVar}>
        + Add variable
      </Button>
    </div>
  )
}

function envRecordToVars(env: Record<string, string>): EnvVar[] {
  return Object.entries(env).map(([key, value]) => ({ key, value }))
}

function envVarsToRecord(vars: EnvVar[]): Record<string, string> {
  const record: Record<string, string> = {}
  for (const v of vars) {
    const k = v.key.trim()
    if (k) record[k] = v.value
  }
  return record
}

export default function GroupCronJobsPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<CronJob | null>(null)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)
  const [envVars, setEnvVars] = useState<EnvVar[]>([])

  const cronDefaults: CronJobInput = {
    name: "", user: "root", minute: "*", hour: "*", day: "*", month: "*", weekday: "*",
    command: "", state: "present", priority: 100, comment: "",
  }

  const form = useForm<CronJobInput>({
    resolver: zodResolver(cronJobSchema),
    defaultValues: cronDefaults,
    mode: "onSubmit",
  })

  const minute = form.watch("minute")
  const hour = form.watch("hour")
  const day = form.watch("day")
  const month = form.watch("month")
  const weekday = form.watch("weekday")
  const schedulePreview = `${minute} ${hour} ${day} ${month} ${weekday}`

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  const gitopsEnabled = !!group?.gitops_enabled

  const { data: cronJobs, isLoading, error } = useQuery<CronJob[]>({
    queryKey: ["cron-jobs", id],
    queryFn: () => apiFetch<CronJob[]>(`/api/groups/${id}/cron-jobs`),
    enabled: !!id,
  })
  const showLoading = useDelayedLoading(isLoading)

  const saveMutation = useApiMutation({
    mutationFn: ({ jobId, payload }: { jobId?: number; payload: Record<string, unknown> }) => {
      if (jobId) {
        return apiFetch(`/api/groups/${id}/cron-jobs/${jobId}`, { method: "PUT", body: JSON.stringify(payload) })
      }
      return apiFetch(`/api/groups/${id}/cron-jobs`, { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["cron-jobs", id]],
    onSuccess: () => setDialogOpen(false),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (jobId: number) =>
      apiFetch(`/api/groups/${id}/cron-jobs/${jobId}`, { method: "DELETE" }),
    invalidateKeys: [["cron-jobs", id]],
  })

  function parseSchedule(schedule: string) {
    const parts = schedule.trim().split(/\s+/)
    return {
      minute: parts[0] ?? "*",
      hour: parts[1] ?? "*",
      day: parts[2] ?? "*",
      month: parts[3] ?? "*",
      weekday: parts[4] ?? "*",
    }
  }

  function openCreateDialog() {
    setEditing(null)
    form.reset(cronDefaults)
    setEnvVars([])
    saveMutation.reset()
    setDialogOpen(true)
  }

  function openEditDialog(job: CronJob) {
    setEditing(job)
    setEnvVars(envRecordToVars(job.environment ?? {}))
    saveMutation.reset()
    setDialogOpen(true)
  }

  useEffect(() => {
    if (dialogOpen && editing) {
      const sched = parseSchedule(editing.schedule)
      form.reset({
        name: editing.name,
        user: editing.user,
        ...sched,
        command: editing.command,
        state: editing.state,
        priority: editing.priority,
        comment: editing.comment ?? "",
      })
    }
  }, [dialogOpen, editing, form])

  const onSubmit = form.handleSubmit((data) => {
    const payload = {
      name: data.name,
      user: data.user,
      schedule: `${data.minute} ${data.hour} ${data.day} ${data.month} ${data.weekday}`,
      command: data.command,
      state: data.state,
      priority: data.priority,
      comment: data.comment || null,
      environment: envVarsToRecord(envVars),
    }
    saveMutation.mutate({ jobId: editing?.id, payload })
  })

  function handleDelete(job: CronJob) {
    setConfirmState({
      open: true,
      title: "Delete Cron Job",
      description: `Delete cron job "${job.name}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        try {
          await deleteMutation.mutateAsync(job.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  function truncateCommand(cmd: string, max = 60): string {
    return cmd.length > max ? cmd.slice(0, max) + "..." : cmd
  }

  return (
    <div className="space-y-4">
      {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Cron Jobs" }]} />}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Cron Jobs</h1>
        </div>
        {!gitopsEnabled && <Button onClick={openCreateDialog}>Add Cron Job</Button>}
      </div>

      {gitopsEnabled && (
        <div className="flex items-start gap-3 p-4 rounded-lg bg-blue-950 border border-blue-800">
          <GitBranch className="h-5 w-5 text-blue-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-blue-200 font-medium">GitOps Enabled</p>
            <p className="text-blue-300 text-sm mt-1">Cron jobs are managed via GitOps. Changes must be pushed to Git.</p>
          </div>
        </div>
      )}

      {showLoading && <TableSkeleton rows={5} columns={4} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load cron jobs</div>
      )}

      {!isLoading && !error && (
        <DataTable<CronJob>
          tableId="group-cron-jobs"
          data={cronJobs}
          emptyMessage={<>No cron jobs yet. Click <strong>Add Cron Job</strong> to create one.</>}
          getRowKey={(j) => j.id}
          columns={[
            {
              key: "name",
              label: "Name",
              accessor: (j) => j.name,
              cell: (j) => <span className="font-mono text-white text-sm">{j.name}</span>,
              defaultWidth: 180,
              filter: { type: "text", placeholder: "e.g. backup" },
            },
            {
              key: "user",
              label: "User",
              accessor: (j) => j.user,
              cell: (j) => <span className="font-mono text-slate-300 text-xs">{j.user}</span>,
              defaultWidth: 100,
              filter: { type: "text", placeholder: "e.g. root" },
            },
            {
              key: "schedule",
              label: "Schedule",
              accessor: (j) => j.schedule,
              cell: (j) => (
                <div>
                  <span className="font-mono text-slate-300 text-xs">{j.schedule}</span>
                  {cronToHuman(j.schedule) !== j.schedule && (
                    <div className="text-slate-500 text-xs mt-0.5">{cronToHuman(j.schedule)}</div>
                  )}
                </div>
              ),
              defaultWidth: 160,
              filter: { type: "text", placeholder: "e.g. */5" },
            },
            {
              key: "command",
              label: "Command",
              accessor: (j) => j.command,
              cell: (j) => (
                <span className="font-mono text-slate-300 text-xs" title={j.command}>
                  {truncateCommand(j.command)}
                </span>
              ),
              defaultWidth: 260,
              filter: { type: "text", placeholder: "e.g. backup" },
            },
            {
              key: "state",
              label: "State",
              accessor: (j) => j.state,
              cell: (j) => <ItemStateBadge state={j.state} />,
              defaultWidth: 110,
              filter: { type: "enum", options: [{label:"Present",value:"present"},{label:"Absent",value:"absent"}] },
            },
            {
              key: "actions",
              label: "Actions",
              cell: (job) => (
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={gitopsEnabled}
                    onClick={() => openEditDialog(job)}
                    title={gitopsEnabled ? "Managed via GitOps" : undefined}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={deleteMutation.isPending || gitopsEnabled}
                    onClick={() => handleDelete(job)}
                    title={gitopsEnabled ? "Managed via GitOps" : undefined}
                    className="text-red-400 hover:text-red-300 hover:bg-red-950"
                  >
                    {deleteMutation.isPending ? "..." : "Delete"}
                  </Button>
                </div>
              ),
              defaultWidth: 160,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Cron Job" : "Add Cron Job"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={onSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="cj-name">Name</Label>
              <Input
                id="cj-name"
                type="text"
                placeholder="e.g. backup-db, cleanup-logs"
                {...form.register("name")}
              />
              {form.formState.errors.name?.message && <p className="text-sm text-red-400">{form.formState.errors.name.message}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-user">User</Label>
              <Input
                id="cj-user"
                type="text"
                placeholder="root"
                {...form.register("user")}
              />
            </div>

             <div className="space-y-2">
               <div className="flex items-center gap-1.5">
                 <Label>Schedule (cron expression)</Label>
                 <Tooltip content="Standard 5-field cron: minute hour day-of-month month day-of-week. E.g., '0 2 * * *' = 2am daily.">
                   <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
                 </Tooltip>
               </div>
               <div className="grid grid-cols-5 gap-2">
                 <div>
                   <Input id="cj-minute" type="text" placeholder="*" {...form.register("minute")} className="font-mono text-center" />
                   <span className="text-[10px] text-slate-500 block text-center mt-0.5">min</span>
                   {form.formState.errors.minute?.message && <p className="text-xs text-red-400">{form.formState.errors.minute.message}</p>}
                 </div>
                 <div>
                   <Input id="cj-hour" type="text" placeholder="*" {...form.register("hour")} className="font-mono text-center" />
                   <span className="text-[10px] text-slate-500 block text-center mt-0.5">hour</span>
                   {form.formState.errors.hour?.message && <p className="text-xs text-red-400">{form.formState.errors.hour.message}</p>}
                 </div>
                 <div>
                   <Input id="cj-day" type="text" placeholder="*" {...form.register("day")} className="font-mono text-center" />
                   <span className="text-[10px] text-slate-500 block text-center mt-0.5">day</span>
                   {form.formState.errors.day?.message && <p className="text-xs text-red-400">{form.formState.errors.day.message}</p>}
                 </div>
                 <div>
                   <Input id="cj-month" type="text" placeholder="*" {...form.register("month")} className="font-mono text-center" />
                   <span className="text-[10px] text-slate-500 block text-center mt-0.5">month</span>
                   {form.formState.errors.month?.message && <p className="text-xs text-red-400">{form.formState.errors.month.message}</p>}
                 </div>
                 <div>
                   <Input id="cj-weekday" type="text" placeholder="*" {...form.register("weekday")} className="font-mono text-center" />
                   <span className="text-[10px] text-slate-500 block text-center mt-0.5">wday</span>
                   {form.formState.errors.weekday?.message && <p className="text-xs text-red-400">{form.formState.errors.weekday.message}</p>}
                 </div>
               </div>
               {schedulePreview.trim() && (
                 <p className="text-xs text-slate-400">
                   {cronToHuman(schedulePreview)}
                 </p>
               )}
             </div>

            <div className="space-y-2">
              <Label htmlFor="cj-command">Command</Label>
              <textarea
                id="cj-command"
                placeholder="e.g. /usr/local/bin/backup.sh --full"
                {...form.register("command")}
                rows={3}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
              />
              {form.formState.errors.command?.message && <p className="text-sm text-red-400">{form.formState.errors.command.message}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-state">State</Label>
              <select
                id="cj-state"
                {...form.register("state")}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
              >
                <option value="present">Present</option>
                <option value="absent">Absent</option>
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-priority">Priority</Label>
              <Input
                id="cj-priority"
                type="number"
                min={0}
                {...form.register("priority", { valueAsNumber: true })}
              />
              {form.formState.errors.priority?.message && <p className="text-sm text-red-400">{form.formState.errors.priority.message}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-comment">Comment (optional)</Label>
              <textarea
                id="cj-comment"
                placeholder="Optional description"
                {...form.register("comment")}
                rows={2}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
              />
            </div>

            <div className="space-y-2">
              <Label>Environment Variables</Label>
              <EnvEditor vars={envVars} onChange={setEnvVars} />
            </div>

            {saveMutation.error && (
              <p className="text-sm text-red-400">{saveMutation.error.message}</p>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving..." : editing ? "Save Changes" : "Create"}
              </Button>
            </DialogFooter>
          </form>
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
