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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
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
      <Breadcrumb items={[{ label: "Schedules" }]} />

      <div>
        <h1 className="text-2xl font-bold text-white">Schedules</h1>
        <p className="text-slate-400 text-sm mt-1">
          Overview of automated update workflows configured across your groups.
        </p>
      </div>

      {showLoading && <TableSkeleton rows={4} columns={6} />}

      {!isLoading && (!workflows || workflows.length === 0) && (
        <div className="text-slate-400 py-8 text-center">
          No update workflows configured yet. Configure one from a group&apos;s{" "}
          <strong>Updates</strong> tab.
        </div>
      )}

      {!isLoading && workflows && workflows.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Group</TableHead>
                <TableHead>Schedule</TableHead>
                <TableHead>Enabled</TableHead>
                <TableHead>Snapshots</TableHead>
                <TableHead>Auto Rollback</TableHead>
                <TableHead className="w-40">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {workflows.map(({ group, workflow }) => (
                <TableRow key={workflow.id} className="border-slate-700">
                  <TableCell>
                    <Link
                      href={`/groups/${group.id}`}
                      className="text-white font-medium hover:underline"
                    >
                      {group.name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    {workflow.schedule_cron ? (
                      <div>
                        <span className="font-mono text-slate-300 text-xs">
                          {workflow.schedule_cron}
                        </span>
                        {cronToHuman(workflow.schedule_cron) !==
                          workflow.schedule_cron && (
                          <div className="text-slate-500 text-xs mt-0.5">
                            {cronToHuman(workflow.schedule_cron)}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="text-slate-500 text-sm">Manual only</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge
                      className={
                        workflow.enabled
                          ? "bg-green-600 text-white"
                          : "bg-slate-600 text-white"
                      }
                    >
                      {workflow.enabled ? "Enabled" : "Disabled"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge
                      className={
                        workflow.pre_update_snapshot
                          ? "bg-green-600 text-white"
                          : "bg-slate-600 text-white"
                      }
                    >
                      {workflow.pre_update_snapshot ? "On" : "Off"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge
                      className={
                        workflow.auto_rollback
                          ? "bg-green-600 text-white"
                          : "bg-slate-600 text-white"
                      }
                    >
                      {workflow.auto_rollback ? "On" : "Off"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Link href={`/groups/${group.id}?tab=workflow`}>
                      <Button size="sm" variant="ghost">
                        Configure
                      </Button>
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}
