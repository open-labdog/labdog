"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { Toolbar } from "@/components/ld"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import { ScheduledActionsList } from "@/components/scheduled-actions/scheduled-actions-list"
import type { ScheduledAction } from "@/lib/types"

interface ScheduledActionsSectionProps {
  scope: "host" | "group"
  targetId: number
}

export function ScheduledActionsSection({ scope, targetId }: ScheduledActionsSectionProps) {
  const [createOpen, setCreateOpen] = useState(false)

  const { data: rows, isLoading } = useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions-by-target", scope, targetId],
    queryFn: () => apiFetch<ScheduledAction[]>(`/api/scheduled-actions?target_kind=${scope}&target_id=${targetId}&include_last_run=true`),
  })

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar actions={<button type="button" className="btn btn-sm btn-primary" onClick={() => setCreateOpen(true)}>Schedule action</button>}>
        <span className="tt">cron-driven runs scoped to this {scope}</span>
      </Toolbar>

      <ScheduledActionsList rows={rows ?? []} hideColumns={["target"]} loading={isLoading} />

      {createOpen && <ScheduleActionDialog open={createOpen} onOpenChange={setCreateOpen} preselected={{ target: { kind: scope, id: targetId } }} />}
    </div>
  )
}
