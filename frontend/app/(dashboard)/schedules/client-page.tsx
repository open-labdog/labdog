"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { CalendarClock, ShieldAlert } from "lucide-react"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { TableSkeleton } from "@/components/ui/skeleton"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import { ScheduledActionsList } from "@/components/scheduled-actions/scheduled-actions-list"
import { apiFetch } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useDelayedLoading } from "@/lib/utils"
import type { ScheduledAction } from "@/lib/types"

export default function SchedulesPage() {
  const { user } = useAuth()
  const [createOpen, setCreateOpen] = useState(false)

  const { data: rows, isLoading, error } = useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions"],
    queryFn: () =>
      apiFetch<ScheduledAction[]>(
        "/api/scheduled-actions?include_last_run=true",
      ),
    refetchInterval: (query) => {
      const data = query.state.data
      if (
        data?.some(
          (r) =>
            r.last_run?.status === "running" || r.last_run?.status === "queued",
        )
      ) {
        return 3000
      }
      return false
    },
  })
  const showLoading = useDelayedLoading(isLoading)

  // Permission gate — the API requires superuser; show an inline
  // explanation rather than letting non-superusers hit a 403 toast.
  if (user && !user.is_superuser) {
    return (
      <div className="space-y-6">
        <Breadcrumb items={[{ label: "Schedules" }]} />
        <div className="flex flex-col items-center gap-3 rounded-lg border border-slate-700 bg-slate-900 p-10 text-center">
          <ShieldAlert className="h-10 w-10 text-slate-700" />
          <p className="text-slate-300 font-medium">
            Schedules require superuser privileges
          </p>
          <p className="text-sm text-slate-500 max-w-md">
            Ask an administrator to schedule actions on your behalf, or to
            promote your account if you need ongoing access.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Schedules" }]} />

      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Schedules</h1>
          <p className="text-slate-400 text-sm mt-1">
            Cron-driven runs of any registered action across hosts, groups, or
            the entire fleet.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>+ New</Button>
      </div>

      {showLoading && <TableSkeleton rows={3} columns={6} />}
      {error && (
        <div className="text-red-400 py-8 text-center">
          Failed to load schedules
        </div>
      )}

      {!isLoading && !error && (
        <ScheduledActionsList
          rows={rows ?? []}
          tableId="schedules-page-v1"
          emptyState={
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <CalendarClock className="h-10 w-10 text-slate-700" />
              <div>
                <p className="text-slate-300 font-medium">No schedules yet</p>
                <p className="text-sm text-slate-500 mt-1 max-w-md">
                  Create one here, or hit <strong>Schedule…</strong> on any
                  action card from a host or group page.
                </p>
              </div>
              <Button onClick={() => setCreateOpen(true)} className="mt-2">
                Create a schedule
              </Button>
            </div>
          }
        />
      )}

      {createOpen && (
        <ScheduleActionDialog open={createOpen} onOpenChange={setCreateOpen} />
      )}
    </div>
  )
}
