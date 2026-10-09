"use client"

import { useParams } from "next/navigation"
import { ActionRunDetail } from "@/components/action-run-detail"

/**
 * Generic action-run detail route, for fleet runs that have no host or
 * group target. The per-host and per-group routes reach the same
 * component with the same id — `ActionRunDetail` derives its crumb trail
 * from the run's own target, not from which route loaded it.
 */
export default function GenericActionRunPage() {
  const params = useParams()
  const runId = Number(params.runId)
  return <ActionRunDetail runId={runId} />
}
