"use client"

import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { PageHead } from "@/components/ld"
import type { Host } from "@/lib/types"
import { TerminalTab } from "../_tabs/terminal"

export default function TerminalPage() {
  const params = useParams()
  const id = Number(params.id)

  const { data: host } = useQuery<Host>({
    queryKey: ["host", id],
    queryFn: () => apiFetch<Host>(`/api/hosts/${id}`),
  })

  return (
    <>
      <PageHead crumbs={[{ label: "fleet", href: "/hosts" }, { label: "hosts", href: "/hosts" }, { label: host?.hostname ?? `host #${id}`, href: `/hosts/${id}` }]} title="Terminal" sub={host ? <span className="mono">{host.hostname}</span> : "Loading…"} />
      <TerminalTab hostId={id} hostname={host?.hostname ?? ""} />
    </>
  )
}
