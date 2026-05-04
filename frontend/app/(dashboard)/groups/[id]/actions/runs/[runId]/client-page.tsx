"use client"

import { useParams } from "next/navigation"
import { ActionRunDetail } from "@/components/action-run-detail"

export default function GroupActionRunPage() {
  const params = useParams()
  const groupId = Number(params.id)
  const runId = Number(params.runId)
  return (
    <div className="p-6">
      <ActionRunDetail
        runId={runId}
        backHref={`/groups/${groupId}?tab=actions`}
        backLabel="Back to Actions"
      />
    </div>
  )
}
