"use client"

import { useParams } from "next/navigation"
import { ActionsTab } from "@/components/actions-tab"

export default function GroupActionsPage({ embedded: _embedded = false }: { embedded?: boolean }) {  // eslint-disable-line @typescript-eslint/no-unused-vars
  const params = useParams()
  const id = Number(params.id)
  return <ActionsTab scope="group" targetId={id} />
}
