"use client"

import { useParams } from "next/navigation"
import { ActionRunDetail } from "@/components/action-run-detail"

/**
 * Generic action-run detail route. Used for fleet runs that don't
 * have a host or group target — the existing per-host and per-group
 * routes still take ad-hoc and group-targeted runs to keep their
 * "Back to Actions" link contextual.
 */
export default function GenericActionRunPage() {
  const params = useParams()
  const runId = Number(params.runId)
  return (
    <div className="p-6">
      <ActionRunDetail
        runId={runId}
        backHref="/schedules"
        backLabel="Back to Schedules"
      />
    </div>
  )
}
