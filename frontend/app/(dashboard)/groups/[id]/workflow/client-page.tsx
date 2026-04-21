"use client"

import { useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { DataTable } from "@/components/ui/data-table"
import { TableSkeleton } from "@/components/ui/skeleton"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { showSuccess, showError } from "@/lib/toast"
import { RunStatusBadge } from "@/components/status-badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { ActionDefinition, HostGroup, UpdateWorkflow, WorkflowRun } from "@/lib/types"

const textareaClass =
  "w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"

function formatDateTime(iso: string | null): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString()
}

const GROUP_ACTION_KEYS = ["linux-upgrade", "linux-os-upgrade"]

interface WorkflowFormState {
  batch_size: number
  schedule_cron: string
  pre_update_snapshot: boolean
  auto_rollback: boolean
  auto_reboot: boolean
  verification_prompt: string
  enabled: boolean
  action_key: string
  action_parameters: Record<string, string>
}

function workflowToForm(wf: UpdateWorkflow): WorkflowFormState {
  return {
    batch_size: wf.batch_size,
    schedule_cron: wf.schedule_cron ?? "",
    pre_update_snapshot: wf.pre_update_snapshot,
    auto_rollback: wf.auto_rollback,
    auto_reboot: wf.auto_reboot,
    verification_prompt: wf.verification_prompt ?? "",
    enabled: wf.enabled,
    action_key: wf.action_key ?? "linux-upgrade",
    action_parameters: wf.action_parameters ?? {},
  }
}

const defaultForm: WorkflowFormState = {
  batch_size: 1,
  schedule_cron: "",
  pre_update_snapshot: true,
  auto_rollback: true,
  auto_reboot: true,
  verification_prompt: "",
  enabled: false,
  action_key: "linux-upgrade",
  action_parameters: {},
}

export default function WorkflowConfigPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)
  const router = useRouter()
  const queryClient = useQueryClient()

  const [form, setForm] = useState<WorkflowFormState>(defaultForm)
  const [formInitialized, setFormInitialized] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [runningNow, setRunningNow] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deletePending, setDeletePending] = useState(false)

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  const {
    data: workflow,
    isLoading: workflowLoading,
    error: workflowError,
  } = useQuery<UpdateWorkflow | null>({
    queryKey: ["group-workflow", id],
    queryFn: async () => {
      try {
        return await apiFetch<UpdateWorkflow>(`/api/groups/${id}/workflow`)
      } catch (err: unknown) {
        const apiErr = err as { status?: number }
        if (apiErr?.status === 404) return null
        throw err
      }
    },
    enabled: !!id,
  })

  // Initialize form when workflow first loads
  if (workflow && !formInitialized) {
    setForm(workflowToForm(workflow))
    setFormInitialized(true)
  }
  if (workflow === null && !formInitialized) {
    setForm(defaultForm)
    setFormInitialized(true)
  }

  const { data: actions } = useQuery({
    queryKey: ["actions"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
  })

  const groupActions = (actions ?? []).filter((a) => GROUP_ACTION_KEYS.includes(a.key))

  const {
    data: runs = [],
    isLoading: runsLoading,
  } = useQuery<WorkflowRun[]>({
    queryKey: ["group-workflow-runs", id],
    queryFn: () => apiFetch<WorkflowRun[]>(`/api/groups/${id}/workflow/runs?limit=20`),
    enabled: !!workflow,
    refetchInterval: (query) => {
      const data = query.state.data as WorkflowRun[] | undefined
      if (!data) return false
      const hasActive = data.some((r) => r.status === "pending" || r.status === "running")
      return hasActive ? 3000 : false
    },
  })
  const showRunsLoading = useDelayedLoading(runsLoading)
  const showWorkflowLoading = useDelayedLoading(workflowLoading)

  const deleteMutation = useApiMutation({
    mutationFn: () => apiFetch(`/api/groups/${id}/workflow`, { method: "DELETE" }),
    invalidateKeys: [["group-workflow", id], ["group-workflow-runs", id]],
    successMessage: "Workflow deleted",
    onSuccess: () => {
      setFormInitialized(false)
    },
  })

  async function handleSave() {
    setFormSaving(true)
    setFormError(null)
    try {
      const payload: Record<string, unknown> = {
        batch_size: form.batch_size,
        schedule_cron: form.schedule_cron || null,
        pre_update_snapshot: form.pre_update_snapshot,
        auto_rollback: form.auto_rollback,
        auto_reboot: form.auto_reboot,
        verification_prompt: form.verification_prompt || null,
        enabled: form.enabled,
        action_key: form.action_key,
        action_parameters: form.action_parameters,
      }
      await apiFetch(`/api/groups/${id}/workflow`, { method: "PUT", json: payload })
      await queryClient.invalidateQueries({ queryKey: ["group-workflow", id] })
      setFormInitialized(false)
      showSuccess("Workflow saved")
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save workflow")
    } finally {
      setFormSaving(false)
    }
  }

  async function handleRunNow() {
    setRunningNow(true)
    try {
      await apiFetch(`/api/groups/${id}/workflow/run`, { method: "POST" })
      await queryClient.invalidateQueries({ queryKey: ["group-workflow-runs", id] })
      showSuccess("Workflow run started")
    } catch (err) {
      showError(err instanceof Error ? err.message : "Failed to trigger run")
    } finally {
      setRunningNow(false)
    }
  }

  async function handleDelete() {
    setDeletePending(true)
    try {
      await deleteMutation.mutateAsync(undefined as never)
    } finally {
      setDeletePending(false)
      setDeleteConfirmOpen(false)
    }
  }

  const hasActiveRun = runs.some((r) => r.status === "pending" || r.status === "running")
  const runNowDisabled = !workflow?.enabled || hasActiveRun || runningNow

  return (
    <div className="space-y-8">
      {!embedded && (
        <Breadcrumb
          items={[
            { label: "Groups", href: "/groups" },
            { label: group?.name ?? "Group", href: `/groups/${id}` },
            { label: "Workflow" },
          ]}
        />
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Update Workflow</h1>
          <p className="text-slate-400 text-sm mt-1">
            Automated update orchestration for this group&apos;s hosts
          </p>
        </div>
        {workflow && (
          <Button
            onClick={handleRunNow}
            disabled={runNowDisabled}
            title={
              !workflow.enabled
                ? "Workflow is disabled"
                : hasActiveRun
                ? "A run is already active"
                : "Trigger a workflow run now"
            }
          >
            {runningNow ? "Starting..." : "Run Now"}
          </Button>
        )}
      </div>

      {showWorkflowLoading && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
          <div className="h-4 bg-slate-800 animate-pulse rounded w-1/3 mb-4" />
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-8 bg-slate-800 animate-pulse rounded" />
            ))}
          </div>
        </div>
      )}

      {workflowError && (
        <div className="text-red-400 py-8 text-center">Failed to load workflow configuration</div>
      )}

      {/* No workflow state */}
      {!workflowLoading && !workflowError && workflow === null && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-8 text-center space-y-4">
          <p className="text-slate-400">No update workflow configured for this group.</p>
          <Button onClick={handleSave} disabled={formSaving}>
            {formSaving ? "Creating..." : "Configure Workflow"}
          </Button>
        </div>
      )}

      {/* Workflow config form */}
      {!workflowLoading && !workflowError && workflow !== null && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 space-y-6">
          <h2 className="text-lg font-semibold text-white">Configuration</h2>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            {/* Batch size */}
            <div className="space-y-2">
              <Label htmlFor="batch-size">Batch Size</Label>
              <Input
                id="batch-size"
                type="number"
                min={1}
                value={form.batch_size}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, batch_size: Number(e.target.value) }))
                }
              />
              <p className="text-xs text-slate-500">Number of hosts to update simultaneously</p>
            </div>

            {/* Schedule cron */}
            <div className="space-y-2">
              <Label htmlFor="schedule-cron">Schedule (Cron)</Label>
              <Input
                id="schedule-cron"
                type="text"
                placeholder="0 3 * * 0"
                value={form.schedule_cron}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, schedule_cron: e.target.value }))
                }
              />
              <p className="text-xs text-slate-500">Cron expression for scheduled runs (leave blank to disable)</p>
            </div>
          </div>

          {/* Toggle options */}
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <input
                id="pre-update-snapshot"
                type="checkbox"
                checked={form.pre_update_snapshot}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, pre_update_snapshot: e.target.checked }))
                }
                className="rounded border-input"
              />
              <div>
                <Label htmlFor="pre-update-snapshot">Pre-update Snapshot</Label>
                <p className="text-xs text-slate-500">Take a Proxmox snapshot before updating each host</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <input
                id="auto-rollback"
                type="checkbox"
                checked={form.auto_rollback}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, auto_rollback: e.target.checked }))
                }
                className="rounded border-input"
              />
              <div>
                <Label htmlFor="auto-rollback">Auto Rollback</Label>
                <p className="text-xs text-slate-500">Automatically roll back to snapshot on update failure</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <input
                id="auto-reboot"
                type="checkbox"
                checked={form.auto_reboot}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, auto_reboot: e.target.checked }))
                }
                className="rounded border-input"
              />
              <div>
                <Label htmlFor="auto-reboot">Auto Reboot</Label>
                <p className="text-xs text-slate-500">Reboot hosts after applying updates</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <input
                id="enabled"
                type="checkbox"
                checked={form.enabled}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, enabled: e.target.checked }))
                }
                className="rounded border-input"
              />
              <div>
                <Label htmlFor="enabled">Enabled</Label>
                <p className="text-xs text-slate-500">Allow this workflow to run (scheduled and manual)</p>
              </div>
            </div>
          </div>

          {/* Action type selector */}
          <div className="space-y-2">
            <Label htmlFor="action-key">Action Type</Label>
            <Select
              value={form.action_key}
              onValueChange={(value) =>
                setForm((prev) => ({
                  ...prev,
                  action_key: value,
                  action_parameters: {},
                }))
              }
            >
              <SelectTrigger id="action-key">
                <SelectValue placeholder="Select action type" />
              </SelectTrigger>
              <SelectContent>
                {groupActions.length > 0
                  ? groupActions.map((a) => (
                      <SelectItem key={a.key} value={a.key}>
                        {a.name}
                      </SelectItem>
                    ))
                  : GROUP_ACTION_KEYS.map((key) => (
                      <SelectItem key={key} value={key}>
                        {key}
                      </SelectItem>
                    ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-slate-500">The update action to run on each host in this workflow</p>
          </div>

          {/* Conditional codename fields for linux-os-upgrade */}
          {form.action_key === "linux-os-upgrade" && (
            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="current-codename">Current Codename</Label>
                <Input
                  id="current-codename"
                  type="text"
                  required
                  placeholder="e.g. bookworm"
                  value={form.action_parameters.current_version ?? ""}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      action_parameters: {
                        ...prev.action_parameters,
                        current_version: e.target.value,
                      },
                    }))
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="target-codename">Target Codename</Label>
                <Input
                  id="target-codename"
                  type="text"
                  required
                  placeholder="e.g. trixie"
                  value={form.action_parameters.next_version ?? ""}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      action_parameters: {
                        ...prev.action_parameters,
                        next_version: e.target.value,
                      },
                    }))
                  }
                />
              </div>
            </div>
          )}

          {/* Verification prompt */}
          <div className="space-y-2">
            <Label htmlFor="verification-prompt">Verification Prompt (optional)</Label>
            <textarea
              id="verification-prompt"
              placeholder="Additional verification instructions for AI..."
              value={form.verification_prompt}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, verification_prompt: e.target.value }))
              }
              rows={3}
              className={textareaClass}
            />
          </div>

          {formError && (
            <p className="text-sm text-red-400">{formError}</p>
          )}

          <div className="flex gap-3 pt-2">
            <Button
              variant="outline"
              className="text-red-400 hover:text-red-300 hover:bg-red-950 border-red-900"
              onClick={() => setDeleteConfirmOpen(true)}
              disabled={deletePending}
            >
              Delete Workflow
            </Button>
            <Button onClick={handleSave} disabled={formSaving}>
              {formSaving ? "Saving..." : "Save"}
            </Button>
          </div>
        </div>
      )}

      {/* Recent Runs section — only shown when workflow exists */}
      {!workflowLoading && !workflowError && workflow !== null && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-white">Recent Runs</h2>

          {showRunsLoading && <TableSkeleton rows={5} columns={5} />}

          {!runsLoading && (
            <DataTable<WorkflowRun>
              tableId="group-workflow-runs"
              data={runs}
              emptyMessage={<>No runs yet. Click <strong>Run Now</strong> to trigger the first run.</>}
              getRowKey={(r) => r.id}
              onRowClick={(run) => router.push(`/groups/${id}/workflow/runs/${run.id}`)}
              rowClassName={() => "cursor-pointer"}
              columns={[
                {
                  key: "id",
                  label: "Run ID",
                  accessor: (r) => r.id,
                  cell: (r) => <span className="font-mono text-white text-sm">#{r.id}</span>,
                  defaultWidth: 100,
                },
                {
                  key: "status",
                  label: "Status",
                  accessor: (r) => r.status,
                  cell: (r) => <RunStatusBadge status={r.status} />,
                  defaultWidth: 120,
                  filter: { type: "enum", options: [{label:"Pending",value:"pending"},{label:"Running",value:"running"},{label:"Completed",value:"completed"},{label:"Failed",value:"failed"},{label:"Partial",value:"partial"}] },
                },
                {
                  key: "started_at",
                  label: "Started",
                  accessor: (r) => r.started_at,
                  cell: (r) => <span className="text-slate-300 text-sm">{formatDateTime(r.started_at)}</span>,
                  defaultWidth: 180,
                  filter: { type: "dateRange" },
                },
                {
                  key: "completed_at",
                  label: "Completed",
                  accessor: (r) => r.completed_at,
                  cell: (r) => <span className="text-slate-300 text-sm">{formatDateTime(r.completed_at)}</span>,
                  defaultWidth: 180,
                  filter: { type: "dateRange" },
                },
                {
                  key: "triggered_by",
                  label: "Triggered By",
                  accessor: (r) => r.triggered_by ? "Manual" : "Scheduled",
                  cell: (r) => <span className="text-slate-400 text-sm">{r.triggered_by ? "Manual" : "Scheduled"}</span>,
                  defaultWidth: 140,
                  filter: { type: "enum", options: [{label:"Manual",value:"Manual"},{label:"Scheduled",value:"Scheduled"}] },
                },
              ]}
            />
          )}
        </div>
      )}

      <ConfirmDialog
        open={deleteConfirmOpen}
        onOpenChange={(open) => !open && setDeleteConfirmOpen(false)}
        title="Delete Workflow"
        description="Are you sure you want to delete this workflow configuration? All run history will also be deleted. This action cannot be undone."
        confirmLabel="Delete"
        variant="destructive"
        loading={deletePending}
        onConfirm={handleDelete}
      />
    </div>
  )
}
