"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Confirm } from "@/components/ld"
import { ApiError, apiFetch } from "@/lib/api"
import type { ActionHostRun, ActionRun, AIProvider } from "@/lib/types"

/** An earlier investigation in one of these is not reopened; the button
 *  starts another. Mirrors `_ABANDONED` in app/ai/run_investigation.py. */
const ABANDONED = new Set(["failed", "cancelled"])

function sameIds(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false
  const sorted = [...b].sort((x, y) => x - y)
  return [...a].sort((x, y) => x - y).every((id, i) => id === sorted[i])
}

/**
 * "Investigate with AI" on a finished run: a read-only assistant session
 * about the whole run, or about `hostRun` when one host is picked in the
 * log. Once there is one for the same hosts, the button opens it instead.
 *
 * A run from the run list has no host rows or investigations, so a list
 * passes the host it is about (`first_failure`). The API then reopens an
 * earlier session for that host itself.
 *
 * Not shown at all where no AI provider is set up, so an install without
 * AI gets no button it cannot use.
 */
export function InvestigateRunButton({
  run,
  hostRun,
  ghost,
}: {
  run: ActionRun
  hostRun: Pick<ActionHostRun, "id" | "host_id" | "hostname"> | null
  ghost?: boolean
}) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const { data: providers } = useQuery<AIProvider[]>({
    queryKey: ["ai-providers"],
    queryFn: () => apiFetch<AIProvider[]>("/api/ai/providers"),
    retry: false,
    staleTime: 60_000,
  })

  const start = useMutation({
    mutationFn: () =>
      apiFetch<{ session_id: number; created: boolean }>(`/api/actions/runs/${run.id}/investigate`, {
        method: "POST",
        json: hostRun ? { host_run_id: hostRun.id } : {},
      }),
    onSuccess: ({ session_id }) => {
      setConfirming(false)
      queryClient.invalidateQueries({ queryKey: ["action-run", run.id] })
      router.push(`/assistant?session=${session_id}`)
    },
    onError: (err: unknown) => {
      setConfirming(false)
      toast.error(err instanceof ApiError || err instanceof Error ? err.message : "Could not start an investigation")
    },
  })

  const scopeIds = hostRun
    ? hostRun.host_id !== null ? [hostRun.host_id] : []
    : run.host_runs.flatMap((hr) => (hr.host_id !== null ? [hr.host_id] : []))
  const earlier = (run.investigations ?? []).find((i) => !ABANDONED.has(i.status) && sameIds(i.target_host_ids, scopeIds))
  // A deleted host leaves nothing to connect to; the API refuses it too.
  if (scopeIds.length === 0) return null

  if (earlier) {
    return (
      <button type="button" className={`btn btn-sm ${ghost ? "btn-ghost" : ""}`} onClick={() => router.push(`/assistant?session=${earlier.session_id}`)}>
        View investigation →
      </button>
    )
  }

  const enabled = (providers ?? []).filter((p) => p.enabled)
  if (enabled.length === 0) return null
  // The API uses the default provider, so that is the one to name.
  const provider = enabled.find((p) => p.is_default)
  // The scope (one host or the whole run) is in the confirm dialog, not the label.
  const label = "Investigate with AI"
  if (!provider || !provider.supports_tools) {
    return (
      <button
        type="button"
        className={`btn btn-sm ${ghost ? "btn-ghost" : ""}`}
        disabled
        title={provider ? `The default AI provider, ${provider.name}, cannot run tools, so it cannot investigate.` : "No AI provider is marked as the default."}
      >
        {label}
      </button>
    )
  }

  const scope = hostRun ? (hostRun.hostname ?? "this host") : scopeIds.length === 1 ? "this run's host" : `this run's ${scopeIds.length} hosts`
  const where = provider.sends_data_offsite ? `${provider.name}, off your network` : provider.name
  return (
    <>
      <button type="button" className={`btn btn-sm ${ghost ? "btn-ghost" : ""}`} data-testid="investigate-run" onClick={() => setConfirming(true)}>
        {label}
      </button>
      <Confirm
        open={confirming}
        onOpenChange={setConfirming}
        title={hostRun ? `Investigate ${scope} with AI?` : "Investigate this run with AI?"}
        description={`Starts a read-only assistant session on ${scope}, with the run's details and the failing parts of its log. The log goes to ${where}. The assistant can look at ${scopeIds.length === 1 ? "the host but not change it" : "the hosts but not change them"}.`}
        confirmLabel="Start investigation"
        loading={start.isPending}
        onConfirm={() => start.mutate()}
      />
    </>
  )
}
