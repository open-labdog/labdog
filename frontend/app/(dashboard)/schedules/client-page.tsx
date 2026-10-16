"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { Banner, Empty, Toolbar } from "@/components/ld"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import { ScheduledActionsList } from "@/components/scheduled-actions/scheduled-actions-list"
import type { ScheduledAction } from "@/lib/types"

/** Embedded in the Actions page's Schedules tab — a schedule is an
 *  action with a cron, so it has no page of its own any more. */
export default function SchedulesPage() {
  const [createOpen, setCreateOpen] = useState(false)

  const { data: rows, isLoading, error } = useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions"],
    queryFn: () => apiFetch<ScheduledAction[]>("/api/scheduled-actions?include_last_run=true"),
    refetchInterval: (query) => {
      const data = query.state.data
      return data?.some((r) => r.last_run?.status === "running" || r.last_run?.status === "queued") ? 3000 : false
    },
  })

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar actions={<button type="button" className="btn btn-sm btn-primary" onClick={() => setCreateOpen(true)}>+ New</button>}>
        <span className="tt">cron-driven runs of any registered action across hosts, groups, or the entire fleet</span>
      </Toolbar>

      {error && <Banner tone="danger" flush>Could not load schedules: {error.message}</Banner>}

      {!isLoading && !error && (rows ?? []).length === 0 ? (
        <Empty title="No schedules yet" note={<>Create one here, or hit <strong>Schedule…</strong> on any action from a host or group page.</>} action={<button type="button" className="btn btn-sm btn-primary" onClick={() => setCreateOpen(true)}>Create a schedule</button>} />
      ) : (
        <ScheduledActionsList rows={rows ?? []} loading={isLoading} />
      )}

      {createOpen && <ScheduleActionDialog open={createOpen} onOpenChange={setCreateOpen} />}
    </div>
  )
}
