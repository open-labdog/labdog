"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Confirm } from "@/components/ld"
import { ApiError, apiFetch } from "@/lib/api"
import { shortAgo } from "@/lib/fleet"
import type { AIProvider, InvestigationRef, InvestigationSubject } from "@/lib/types"

/** The key an earlier session is found by. Mirrors `subject_key` in
 *  app/ai/subject_investigation.py. */
export function subjectKey(s: InvestigationSubject): string {
  switch (s.kind) {
    case "resource":
      return `resource:${s.host_id}:${s.metric}`
    case "sync":
      return `sync:${s.sync_job_id}`
    case "stuck":
      return `stuck:${s.op}:${s.id}`
    case "drift":
      return `drift:${s.host_id}:${s.module || "*"}`
    case "host":
      return `host:${s.host_id}`
  }
}

/** How a resource subject's metric reads in a sentence. */
export const METRIC_WORD = { cpu: "CPU", memory: "memory", disk: "root disk" } as const

/** The four-point sparkle every AI entry point wears. */
export function AISpark({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" className="shrink-0">
      <path fill="currentColor" d="M8 .8 9.6 6.4 15.2 8 9.6 9.6 8 15.2 6.4 9.6.8 8 6.4 6.4Z" />
    </svg>
  )
}

/** Shared by every button on a page: one request answers which subjects
 *  already have a session to reopen. */
function useInvestigations() {
  return useQuery<InvestigationRef[]>({
    queryKey: ["ai-investigations"],
    queryFn: () => apiFetch<InvestigationRef[]>("/api/ai/investigations"),
    retry: false,
    staleTime: 30_000,
  })
}

/**
 * "Investigate with AI" for anything that is not an action run (those
 * keep `InvestigateRunButton`): a resource hot spot, a failed sync, a
 * stuck operation, drift, a whole host. It sends the subject's ids only;
 * the server writes the mission and starts a read-only assistant session.
 * Where one was already started about the same subject, the button opens
 * that one instead.
 *
 * Not shown at all where no AI provider is set up, so an install without
 * AI gets no button it cannot use.
 *
 * `icon` is the compact form for dense rows (hot spots, findings); the
 * full label goes in its tooltip and aria-label.
 */
export function InvestigateButton({
  subject,
  about,
  sends,
  label = "Investigate with AI",
  icon,
  ghost,
}: {
  subject: InvestigationSubject
  /** What it is about, for the dialog: "gitlab's root disk". */
  about: string
  /** What goes to the provider, for the dialog: "the disk's usage". */
  sends: string
  label?: string
  icon?: boolean
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
  const { data: investigations } = useInvestigations()
  const key = subjectKey(subject)
  const earlier = (investigations ?? []).find((i) => i.subject_key === key)

  const start = useMutation({
    mutationFn: () => apiFetch<{ session_id: number; created: boolean }>("/api/ai/investigations", { method: "POST", json: { subject } }),
    onSuccess: ({ session_id }) => {
      setConfirming(false)
      queryClient.invalidateQueries({ queryKey: ["ai-investigations"] })
      router.push(`/assistant?session=${session_id}`)
    },
    onError: (err: unknown) => {
      setConfirming(false)
      toast.error(err instanceof ApiError || err instanceof Error ? err.message : "Could not start an investigation")
    },
  })

  const cls = `btn btn-sm ${ghost ? "btn-ghost" : ""}`
  const pad = icon ? { padding: "3px 6px" } : {}
  const tint = { ...pad, color: "var(--hold-ink)" }

  if (earlier) {
    const title = `View the investigation started ${shortAgo(earlier.created_at)} ago`
    return (
      <button
        type="button"
        className={cls}
        // Filled, so an icon-only button shows there is already a session.
        style={{ ...tint, background: "var(--hold-soft)" }}
        title={title}
        aria-label={title}
        data-testid="investigate-open"
        onClick={(e) => {
          e.stopPropagation()
          router.push(`/assistant?session=${earlier.session_id}`)
        }}
      >
        <AISpark />
        {!icon && "View investigation →"}
      </button>
    )
  }

  const enabled = (providers ?? []).filter((p) => p.enabled)
  if (enabled.length === 0) return null
  // The API uses the default provider, so that is the one to name.
  const provider = enabled.find((p) => p.is_default)
  if (!provider || !provider.supports_tools) {
    return (
      <button
        type="button"
        className={cls}
        style={pad}
        disabled
        aria-label={label}
        title={provider ? `The default AI provider, ${provider.name}, cannot run tools, so it cannot investigate.` : "No AI provider is marked as the default."}
      >
        <AISpark />
        {!icon && label}
      </button>
    )
  }

  const where = provider.sends_data_offsite ? `${provider.name}, off your network` : provider.name
  return (
    <>
      <button
        type="button"
        className={cls}
        style={tint}
        title={label}
        aria-label={`${label}: ${about}`}
        data-testid="investigate"
        onClick={(e) => {
          e.stopPropagation()
          setConfirming(true)
        }}
      >
        <AISpark />
        {!icon && label}
      </button>
      <Confirm
        open={confirming}
        onOpenChange={setConfirming}
        title={`Investigate ${about} with AI?`}
        description={`Starts a read-only assistant session with ${sends}. It goes to ${where}, along with whatever the assistant reads on the host. The assistant can look but not change anything.`}
        confirmLabel="Start investigation"
        loading={start.isPending}
        onConfirm={() => start.mutate()}
      />
    </>
  )
}
