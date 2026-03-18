"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
import type { CronJob } from "@/lib/types"

function cronToHuman(schedule: string): string {
  const s = schedule.trim()
  if (s === "* * * * *") return "Every minute"
  if (s === "0 * * * *") return "Every hour"
  if (s === "0 0 * * *") return "Every day at midnight"

  // 0 N * * *  => Every day at N:00
  const dailyMatch = s.match(/^0\s+(\d+)\s+\*\s+\*\s+\*$/)
  if (dailyMatch) return `Every day at ${dailyMatch[1]}:00`

  // */N * * * *  => Every N minutes
  const everyNMin = s.match(/^\*\/(\d+)\s+\*\s+\*\s+\*\s+\*$/)
  if (everyNMin) return `Every ${everyNMin[1]} minutes`

  return s
}

function StateBadge({ state }: { state: string }) {
  return (
    <Badge className={state === "present" ? "bg-green-600 text-white" : "bg-red-600 text-white"}>
      {state.charAt(0).toUpperCase() + state.slice(1)}
    </Badge>
  )
}

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

export default function GroupCronJobsPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<CronJob | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)

  const [name, setName] = useState("")
  const [user, setUser] = useState("root")
  const [schedule, setSchedule] = useState("")
  const [command, setCommand] = useState("")
  const [state, setState] = useState<"present" | "absent">("present")
  const [priority, setPriority] = useState(100)
  const [comment, setComment] = useState("")
  const [envVars, setEnvVars] = useState<EnvVar[]>([])

  const { data: cronJobs, isLoading, error } = useQuery<CronJob[]>({
    queryKey: ["cron-jobs", id],
    queryFn: () => apiFetch<CronJob[]>(`/api/groups/${id}/cron-jobs`),
    enabled: !!id,
  })

  function openCreateDialog() {
    setEditing(null)
    setName("")
    setUser("root")
    setSchedule("")
    setCommand("")
    setState("present")
    setPriority(100)
    setComment("")
    setEnvVars([])
    setFormError(null)
    setDialogOpen(true)
  }

  function openEditDialog(job: CronJob) {
    setEditing(job)
    setName(job.name)
    setUser(job.user)
    setSchedule(job.schedule)
    setCommand(job.command)
    setState(job.state)
    setPriority(job.priority)
    setComment(job.comment ?? "")
    setEnvVars(envRecordToVars(job.environment ?? {}))
    setFormError(null)
    setDialogOpen(true)
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setFormError(null)
    setFormLoading(true)

    const payload = {
      name,
      user,
      schedule,
      command,
      state,
      priority,
      comment: comment || null,
      environment: envVarsToRecord(envVars),
    }

    try {
      if (editing) {
        await apiFetch(`/api/groups/${id}/cron-jobs/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      } else {
        await apiFetch(`/api/groups/${id}/cron-jobs`, {
          method: "POST",
          body: JSON.stringify(payload),
        })
      }
      await queryClient.invalidateQueries({ queryKey: ["cron-jobs", id] })
      setDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save cron job")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleDelete(job: CronJob) {
    if (!confirm(`Delete cron job "${job.name}"?`)) return
    setDeletingId(job.id)
    setDeleteError(null)
    try {
      await apiFetch(`/api/groups/${id}/cron-jobs/${job.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["cron-jobs", id] })
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setDeletingId(null)
    }
  }

  function truncateCommand(cmd: string, max = 60): string {
    return cmd.length > max ? cmd.slice(0, max) + "..." : cmd
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Cron Jobs</h1>
          <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
        </div>
        <Button onClick={openCreateDialog}>Add Cron Job</Button>
      </div>

      {isLoading && (
        <div className="text-slate-400 py-8 text-center">Loading cron jobs...</div>
      )}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load cron jobs</div>
      )}

      {deleteError && (
        <div className="text-red-400 text-sm">{deleteError}</div>
      )}

      {!isLoading && !error && cronJobs && cronJobs.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No cron jobs yet. Click <strong>Add Cron Job</strong> to create one.
        </div>
      )}

      {!isLoading && !error && cronJobs && cronJobs.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Name</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Schedule</TableHead>
                <TableHead>Command</TableHead>
                <TableHead>State</TableHead>
                <TableHead className="w-40">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cronJobs.map((job) => (
                <TableRow key={job.id} className="border-slate-700">
                  <TableCell className="font-mono text-white text-sm">{job.name}</TableCell>
                  <TableCell className="font-mono text-slate-300 text-xs">{job.user}</TableCell>
                  <TableCell>
                    <div>
                      <span className="font-mono text-slate-300 text-xs">{job.schedule}</span>
                      {cronToHuman(job.schedule) !== job.schedule && (
                        <div className="text-slate-500 text-xs mt-0.5">{cronToHuman(job.schedule)}</div>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-slate-300 text-xs max-w-[240px]">
                    <span title={job.command}>{truncateCommand(job.command)}</span>
                  </TableCell>
                  <TableCell>
                    <StateBadge state={job.state} />
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => openEditDialog(job)}
                      >
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={deletingId === job.id}
                        onClick={() => handleDelete(job)}
                        className="text-red-400 hover:text-red-300 hover:bg-red-950"
                      >
                        {deletingId === job.id ? "..." : "Delete"}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Cron Job" : "Add Cron Job"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="cj-name">Name</Label>
              <Input
                id="cj-name"
                type="text"
                placeholder="e.g. backup-db, cleanup-logs"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-user">User</Label>
              <Input
                id="cj-user"
                type="text"
                placeholder="root"
                value={user}
                onChange={(e) => setUser(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-schedule">Schedule (cron expression)</Label>
              <Input
                id="cj-schedule"
                type="text"
                placeholder="*/5 * * * *"
                value={schedule}
                onChange={(e) => setSchedule(e.target.value)}
                required
              />
              {schedule.trim() && (
                <p className="text-xs text-slate-400">
                  {cronToHuman(schedule)}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-command">Command</Label>
              <textarea
                id="cj-command"
                placeholder="e.g. /usr/local/bin/backup.sh --full"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                required
                rows={3}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-state">State</Label>
              <select
                id="cj-state"
                value={state}
                onChange={(e) => setState(e.target.value as "present" | "absent")}
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
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value))}
                required
                min={0}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="cj-comment">Comment (optional)</Label>
              <textarea
                id="cj-comment"
                placeholder="Optional description"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                rows={2}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
              />
            </div>

            <div className="space-y-2">
              <Label>Environment Variables</Label>
              <EnvEditor vars={envVars} onChange={setEnvVars} />
            </div>

            {formError && (
              <p className="text-sm text-red-400">{formError}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={formLoading}>
                {formLoading ? "Saving..." : editing ? "Save Changes" : "Create"}
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
