"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useDelayedLoading } from "@/lib/utils"
import { cronToHuman } from "@/lib/cron"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { TableSkeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { DataTable } from "@/components/ui/data-table"
import type { HostGroup, UpdateWorkflow } from "@/lib/types"

interface WorkflowWithGroup {
  group: HostGroup
  workflow: UpdateWorkflow
}

export default function SchedulesPage() {
  const { data: groups, isLoading: groupsLoading } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  const { data: workflows, isLoading: workflowsLoading } = useQuery<WorkflowWithGroup[]>({
    queryKey: ["all-workflows", groups?.map((g) => g.id)],
    queryFn: async () => {
      if (!groups) return []
      const results = await Promise.allSettled(
        groups.map(async (g) => {
          try {
            const wf = await apiFetch<UpdateWorkflow>(`/api/groups/${g.id}/workflow`)
            return { group: g, workflow: wf } as WorkflowWithGroup
          } catch {
            return null
          }
        })
      )
      return results
        .filter(
          (r): r is PromiseFulfilledResult<WorkflowWithGroup | null> =>
            r.status === "fulfilled"
        )
        .map((r) => r.value)
        .filter((v): v is WorkflowWithGroup => v !== null)
    },
    enabled: !!groups && groups.length > 0,
  })

  const isLoading = groupsLoading || workflowsLoading
  const showLoading = useDelayedLoading(isLoading)

  return (
    <div className="space-y-8">
      <Breadcrumb items={[{ label: "Update Workflows" }]} />

      <div>
        <h1 className="text-2xl font-bold text-white">Update Workflows</h1>
        <p className="text-slate-400 text-sm mt-1">
          Automated update workflows configured across your groups.
        </p>
      </div>

      {showLoading && <TableSkeleton rows={4} columns={6} />}

      {!isLoading && (
        <DataTable<WorkflowWithGroup>
          tableId="schedules"
          data={workflows}
          emptyMessage={
            <>
              No update workflows configured yet. Configure one from a group&apos;s{" "}
              <strong>Workflow</strong> tab.
            </>
          }
          getRowKey={(row) => row.workflow.id}
          columns={[
            {
              key: "group",
              label: "Group",
              accessor: (row) => row.group.name,
              cell: (row) => (
                <Link href={`/groups/${row.group.id}`} className="text-white font-medium hover:underline">
                  {row.group.name}
                </Link>
              ),
              defaultWidth: 180,
              filter: { type: "text" },
            },
            {
              key: "schedule",
              label: "Schedule",
              accessor: (row) => row.workflow.schedule_cron ?? "",
              cell: (row) => row.workflow.schedule_cron ? (
                <div>
                  <span className="font-mono text-white text-sm">
                    {row.workflow.schedule_cron}
                  </span>
                  {cronToHuman(row.workflow.schedule_cron) !== row.workflow.schedule_cron && (
                    <div className="text-slate-400 text-xs mt-0.5">
                      {cronToHuman(row.workflow.schedule_cron)}
                    </div>
                  )}
                </div>
              ) : (
                <span className="text-slate-500 text-sm">Manual only</span>
              ),
              defaultWidth: 240,
              filter: { type: "text", placeholder: "e.g. */5" },
            },
            {
              key: "enabled",
              label: "Enabled",
              accessor: (row) => row.workflow.enabled,
              cell: (row) => (
                <Badge className={row.workflow.enabled ? "bg-green-600 text-white" : "bg-slate-600 text-white"}>
                  {row.workflow.enabled ? "Enabled" : "Disabled"}
                </Badge>
              ),
              defaultWidth: 110,
              filter: { type: "boolean" },
            },
            {
              key: "snapshots",
              label: "Snapshots",
              accessor: (row) => row.workflow.pre_update_snapshot,
              cell: (row) => (
                <Badge className={row.workflow.pre_update_snapshot ? "bg-green-600 text-white" : "bg-slate-600 text-white"}>
                  {row.workflow.pre_update_snapshot ? "On" : "Off"}
                </Badge>
              ),
              defaultWidth: 110,
              filter: { type: "boolean" },
            },
            {
              key: "rollback",
              label: "Rollback",
              accessor: (row) => row.workflow.auto_rollback,
              cell: (row) => (
                <Badge className={row.workflow.auto_rollback ? "bg-green-600 text-white" : "bg-slate-600 text-white"}>
                  {row.workflow.auto_rollback ? "On" : "Off"}
                </Badge>
              ),
              defaultWidth: 110,
              filter: { type: "boolean" },
            },
            {
              key: "actions",
              label: "Actions",
              cell: (row) => (
                <Link href={`/groups/${row.group.id}?tab=workflow`}>
                  <Button size="sm" variant="ghost">Configure</Button>
                </Link>
              ),
              defaultWidth: 160,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}
    </div>
  )
}
