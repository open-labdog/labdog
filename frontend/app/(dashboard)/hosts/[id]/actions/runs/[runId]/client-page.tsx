"use client"

import { useParams } from "next/navigation"
import { ActionRunDetail } from "@/components/action-run-detail"

export default function HostActionRunPage() {
  const params = useParams()
  const hostId = Number(params.id)
  const runId = Number(params.runId)
  return (
    <div className="p-6">
      <ActionRunDetail
        runId={runId}
        backHref={`/hosts/${hostId}?tab=actions`}
        backLabel="Back to Actions"
      />
    </div>
  )
}
