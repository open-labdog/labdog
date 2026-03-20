"use client"

import { useParams } from "next/navigation"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { ArrowLeftIcon, TerminalIcon } from "lucide-react"
import { apiFetch } from "@/lib/api"
import { SshTerminal } from "@/components/ssh-terminal"
import type { Host } from "@/lib/types"

export default function TerminalPage() {
  const params = useParams()
  const id = Number(params.id)

  const { data: host } = useQuery<Host>({
    queryKey: ["host", id],
    queryFn: () => apiFetch<Host>(`/api/hosts/${id}`),
  })

  return (
    <div className="flex flex-col h-[calc(100vh-2rem)]">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
        <Link href={`/hosts/${id}`} className="text-slate-400 hover:text-white">
          <ArrowLeftIcon className="w-4 h-4" />
        </Link>
        <TerminalIcon className="w-4 h-4 text-slate-400" />
        <span className="text-sm font-medium text-white">{host?.hostname ?? "Loading..."}</span>
      </div>
      <div className="flex-1 min-h-0 p-1">
        <SshTerminal hostId={id} hostname={host?.hostname ?? ""} />
      </div>
    </div>
  )
}
