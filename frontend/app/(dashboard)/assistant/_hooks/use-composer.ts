"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { apiFetch, ApiError } from "@/lib/api"
import type { AIAutonomyLevel, AISession, AISessionDetail } from "@/lib/types"

interface Options {
  selectedId: number | null
  setSelectedId: (id: number | null) => void
  setError: (error: string | null) => void
  /** The open session, when there is one: the composer then sends a follow-up. */
  session: AISessionDetail | undefined
  /** True while nothing typed may be sent (see the page for why). */
  composerLocked: boolean
}

/**
 * What the composer sends: a new session, with the options chosen beside
 * it, or a follow-up to the open session.
 */
export function useComposer({ selectedId, setSelectedId, setError, session, composerLocked }: Options) {
  const queryClient = useQueryClient()
  const [mission, setMission] = useState("")
  const [autonomy, setAutonomy] = useState<AIAutonomyLevel>("read_only")
  const [skipSnapshots, setSkipSnapshots] = useState(false)
  // null means "let the backend pick its default provider".
  const [providerId, setProviderId] = useState<number | null>(null)
  const [targetHosts, setTargetHosts] = useState<number[]>([])

  const startSession = useMutation({
    mutationFn: (body: {
      mission: string
      autonomy_level: AIAutonomyLevel
      target_host_ids: number[]
      provider_id: number | null
      skip_snapshots: boolean
    }) => apiFetch<AISession>("/api/ai/sessions", { method: "POST", json: body }),
    onSuccess: (created) => {
      setMission("")
      setError(null)
      setSelectedId(created.id)
      queryClient.invalidateQueries({ queryKey: ["ai-sessions"] })
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not start the session.")
    },
  })

  const sendFollowUp = useMutation({
    mutationFn: (message: string) =>
      apiFetch<AISession>(`/api/ai/sessions/${selectedId}/messages`, {
        method: "POST",
        json: { message },
      }),
    onSuccess: () => {
      setMission("")
      setError(null)
      queryClient.invalidateQueries({ queryKey: ["ai-session", selectedId] })
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not send the message.")
    },
  })

  const busy = startSession.isPending || sendFollowUp.isPending

  function submit() {
    if (!mission.trim() || composerLocked || busy) return
    if (session) {
      sendFollowUp.mutate(mission)
    } else {
      startSession.mutate({
        mission,
        autonomy_level: autonomy,
        target_host_ids: targetHosts,
        provider_id: providerId,
        // Meaningless on a read-only session, and sending it would store a
        // flag the operator never actually chose.
        skip_snapshots: autonomy === "read_only" ? false : skipSnapshots,
      })
    }
  }

  function toggleHost(id: number) {
    setTargetHosts((prev) => (prev.includes(id) ? prev.filter((h) => h !== id) : [...prev, id]))
  }

  return { mission, setMission, autonomy, setAutonomy, skipSnapshots, setSkipSnapshots, providerId, setProviderId, targetHosts, toggleHost, busy, submit }
}
