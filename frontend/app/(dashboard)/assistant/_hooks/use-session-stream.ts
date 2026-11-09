"use client"

import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { API_BASE } from "@/lib/api"

/**
 * The open session's live text, streamed over SSE while it runs.
 *
 * One SSE subscription per running session. Live text is buffered here and
 * discarded when the turn lands in the transcript, so a reconnect or a
 * missed event can never leave a duplicate on screen — the database is
 * the source of truth, the stream is only for immediacy. Warnings and
 * errors the stream reports go to `setError`, the page's one banner.
 */
export function useSessionStream(selectedId: number | null, isRunning: boolean, setError: (error: string | null) => void): string {
  const queryClient = useQueryClient()
  // Keyed by session so switching sessions cannot show the previous one's
  // partial text, without needing a synchronous reset inside the effect.
  const [live, setLive] = useState<{ sessionId: number; text: string } | null>(null)
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    sourceRef.current?.close()
    sourceRef.current = null

    if (selectedId === null || !isRunning) return

    const sessionId = selectedId
    const source = new EventSource(`${API_BASE}/api/ai/sessions/${sessionId}/stream`, {
      withCredentials: true,
    })
    sourceRef.current = source

    source.addEventListener("text", (event) => {
      // Guarded like the budget_warning and error handlers below — a
      // truncated frame would otherwise throw inside the listener and lose
      // the rest of the streamed turn.
      let data: { text?: string }
      try {
        data = JSON.parse((event as MessageEvent).data)
      } catch {
        return
      }
      setLive((prev) =>
        prev?.sessionId === sessionId
          ? { sessionId, text: prev.text + (data.text ?? "") }
          : { sessionId, text: data.text ?? "" }
      )
    })

    // A tool call means the assistant turn just landed in the database, so
    // drop the buffer and let the query be the source of truth.
    const refresh = () => {
      setLive(null)
      queryClient.invalidateQueries({ queryKey: ["ai-session", sessionId] })
    }
    source.addEventListener("tool_call", refresh)
    source.addEventListener("tool_result", refresh)
    // The session has just parked. Re-read it so the card appears without
    // waiting for the status event that follows.
    source.addEventListener("approval_required", () => {
      refresh()
      queryClient.invalidateQueries({ queryKey: ["ai-approvals"] })
    })

    // Both warnings are advisory: the run is still going. They share the
    // error banner because there is one place on this page to say
    // something is wrong, and "you are about to be cut off" belongs in it.
    const warn = (fallback: string) => (event: Event) => {
      let data: { message?: string }
      try {
        data = JSON.parse((event as MessageEvent).data)
      } catch {
        // A truncated frame would otherwise throw inside the listener and
        // take the rest of the stream with it — the same guard the text
        // handler above carries, which this one only claimed to.
        setError(fallback)
        return
      }
      setError(data.message ?? fallback)
    }

    source.addEventListener("budget_warning", warn("AI spend is approaching its budget."))
    // Subscription-billed providers stop on the plan's quota, not on a
    // money budget, so this is the only advance notice they get.
    source.addEventListener("rate_limit_warning", warn("The Claude plan's usage quota is nearly used up."))

    source.addEventListener("error", (event) => {
      const raw = (event as MessageEvent).data
      if (raw) {
        try {
          setError(JSON.parse(raw).message ?? "The assistant hit an error.")
        } catch {
          setError("The assistant hit an error.")
        }
      }
    })

    source.addEventListener("status", () => {
      setLive(null)
      queryClient.invalidateQueries({ queryKey: ["ai-session", sessionId] })
      queryClient.invalidateQueries({ queryKey: ["ai-sessions"] })
      queryClient.invalidateQueries({ queryKey: ["ai-usage"] })
      queryClient.invalidateQueries({ queryKey: ["ai-approvals"] })
      source.close()
    })

    return () => source.close()
  }, [selectedId, isRunning, queryClient, setError])

  // Only ever render the buffer belonging to the session on screen.
  return live?.sessionId === selectedId ? live.text : ""
}
