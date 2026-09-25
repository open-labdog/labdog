"use client"

import { useParams } from "next/navigation"
import { ActionRunDetail } from "@/components/action-run-detail"

export default function GroupActionRunPage() {
  const params = useParams()
  const runId = Number(params.runId)
  return <ActionRunDetail runId={runId} />
}
