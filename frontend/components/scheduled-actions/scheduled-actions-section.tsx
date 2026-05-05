"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { CalendarClock } from "lucide-react"
import { Button } from "@/components/ui/button"
import { TableSkeleton } from "@/components/ui/skeleton"
import { ScheduleActionDialog } from "@/components/scheduled-actions/schedule-action-dialog"
import { ScheduledActionsList } from "@/components/scheduled-actions/scheduled-actions-list"
import { apiFetch } from "@/lib/api"
import type { ScheduledAction } from "@/lib/types"

interface ScheduledActionsSectionProps {
  scope: "host" | "group"
  targetId: number
}

export function ScheduledActionsSection({
  scope,
  targetId,
}: ScheduledActionsSectionProps) {
  const [createOpen, setCreateOpen] = useState(false)

  const { data: rows, isLoading } = useQuery<ScheduledAction[]>({
    queryKey: ["scheduled-actions-by-target", scope, targetId],
    queryFn: () =>
      apiFetch<ScheduledAction[]>(
        `/api/scheduled-actions?target_kind=${scope}&target_id=${targetId}&include_last_run=true`,
      ),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-white">Scheduled actions</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Cron-driven runs scoped to this {scope}.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => setCreateOpen(true)}
          className="gap-1.5"
          data-testid="schedule-action-section-button"
        >
          <CalendarClock className="h-3.5 w-3.5" />
          Schedule action
        </Button>
      </div>

      {isLoading ? (
        <TableSkeleton rows={2} columns={6} />
      ) : (
        <ScheduledActionsList rows={rows ?? []} />
      )}

      {createOpen && (
        <ScheduleActionDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
          preselected={{ target: { kind: scope, id: targetId } }}
        />
      )}
    </div>
  )
}
