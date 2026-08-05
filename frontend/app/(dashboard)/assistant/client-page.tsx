"use client"

import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ChatTranscript } from "@/components/ai/chat-transcript"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { API_BASE, apiFetch, ApiError } from "@/lib/api"
import type {
  AIAutonomyLevel,
  AIProvider,
  AISession,
  AISessionDetail,
  Host,
} from "@/lib/types"

const AUTONOMY_HELP: Record<AIAutonomyLevel, string> = {
  read_only:
    "The assistant may only run commands that read state. Anything that would change a host is refused.",
  approval:
    "Reads run immediately; changes wait for your approval. Approvals arrive in a later release — for now this behaves like read-only.",
  full_auto:
    "The assistant may change hosts on its own. A denylist of destructive commands still applies.",
}

const STATUS_STYLE: Record<string, string> = {
  queued: "bg-blue-600 text-white",
  running: "bg-blue-600 text-white",
  succeeded: "bg-green-600 text-white",
  failed: "bg-red-600 text-white",
  cancelled: "bg-slate-600 text-slate-300",
  waiting_approval: "bg-amber-600 text-white",
}

const TERMINAL = new Set(["succeeded", "failed", "cancelled"])

export default function AssistantPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [mission, setMission] = useState("")
  const [autonomy, setAutonomy] = useState<AIAutonomyLevel>("read_only")
  const [targetHosts, setTargetHosts] = useState<number[]>([])
  // Keyed by session so switching sessions cannot show the previous one's
  // partial text, without needing a synchronous reset inside the effect.
  const [live, setLive] = useState<{ sessionId: number; text: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const sourceRef = useRef<EventSource | null>(null)

  const { data: providers } = useQuery<AIProvider[]>({
    queryKey: ["ai-providers"],
    queryFn: () => apiFetch<AIProvider[]>("/api/ai/providers"),
  })

  const { data: hosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })

  const { data: sessions } = useQuery<AISession[]>({
    queryKey: ["ai-sessions"],
    queryFn: () => apiFetch<AISession[]>("/api/ai/sessions"),
    refetchInterval: 10_000,
  })

  const { data: session } = useQuery<AISessionDetail>({
    queryKey: ["ai-session", selectedId],
    queryFn: () => apiFetch<AISessionDetail>(`/api/ai/sessions/${selectedId}`),
    enabled: selectedId !== null,
  })

  const isRunning = session ? !TERMINAL.has(session.status) : false

  // One SSE subscription per running session. Live text is buffered here and
  // discarded when the turn lands in the transcript, so a reconnect or a
  // missed event can never leave a duplicate on screen — the database is
  // the source of truth, the stream is only for immediacy.
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
      const data = JSON.parse((event as MessageEvent).data)
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

    source.addEventListener("budget_warning", (event) => {
      const data = JSON.parse((event as MessageEvent).data)
      setError(data.message ?? "AI spend is approaching its budget.")
    })

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
      source.close()
    })

    return () => source.close()
  }, [selectedId, isRunning, queryClient])

  // Only ever render the buffer belonging to the session on screen.
  const liveText = live?.sessionId === selectedId ? live.text : ""

  const startSession = useMutation({
    mutationFn: (body: {
      mission: string
      autonomy_level: AIAutonomyLevel
      target_host_ids: number[]
    }) => apiFetch<AISession>("/api/ai/sessions", { method: "POST", json: body }),
    onSuccess: (created) => {
      setMission("")
      setError(null)
      setSelectedId(created.id)
      queryClient.invalidateQueries({ queryKey: ["ai-sessions"] })
    },
    onError: (err: unknown) => {
      setError(
        err instanceof ApiError ? err.message : "Could not start the session."
      )
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

  const hasProvider = (providers ?? []).some((p) => p.enabled)

  function submit() {
    if (!mission.trim()) return
    if (session && !isRunning) {
      sendFollowUp.mutate(mission)
    } else {
      startSession.mutate({
        mission,
        autonomy_level: autonomy,
        target_host_ids: targetHosts,
      })
    }
  }

  function toggleHost(id: number) {
    setTargetHosts((prev) =>
      prev.includes(id) ? prev.filter((h) => h !== id) : [...prev, id]
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Assistant</h1>
          <p className="mt-1 text-sm text-slate-400">
            Ask the assistant to investigate your hosts. It works through
            LabDog&apos;s own tools, so every command it runs is classified and
            audited.
          </p>
        </div>
      </div>

      {!hasProvider && (
        <div className="rounded-lg border border-amber-700 bg-amber-950/40 px-4 py-3 text-sm text-amber-200">
          No AI provider is configured yet. Add one under{" "}
          <a href="/ai-providers" className="underline underline-offset-4">
            Integrations → AI Providers
          </a>{" "}
          and enable <span className="font-mono">ai.enabled</span> in Settings.
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-700 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[260px_1fr]">
        {/* Session list */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-slate-400">Sessions</Label>
            <Button size="sm" variant="ghost" onClick={() => setSelectedId(null)}>
              New
            </Button>
          </div>
          <div className="max-h-[70vh] space-y-1 overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 p-2">
            {(sessions ?? []).length === 0 && (
              <p className="px-2 py-4 text-center text-xs text-slate-400">
                No sessions yet.
              </p>
            )}
            {(sessions ?? []).map((s) => (
              <button
                key={s.id}
                onClick={() => setSelectedId(s.id)}
                className={`w-full rounded-md px-2 py-2 text-left text-xs transition-colors ${
                  s.id === selectedId
                    ? "bg-slate-800 text-white"
                    : "text-slate-300 hover:bg-slate-800"
                }`}
              >
                <span className="line-clamp-2">{s.title ?? s.mission}</span>
                <span className="mt-1 flex items-center gap-2">
                  <Badge className={STATUS_STYLE[s.status] ?? STATUS_STYLE.queued}>
                    {s.status}
                  </Badge>
                  {s.cost_usd > 0 && (
                    <span className="text-slate-400">${s.cost_usd.toFixed(3)}</span>
                  )}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Conversation */}
        <div className="space-y-4">
          {session ? (
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
              <div className="mb-4 flex flex-wrap items-center gap-2 border-b border-slate-700 pb-3">
                <Badge className={STATUS_STYLE[session.status] ?? STATUS_STYLE.queued}>
                  {session.status}
                </Badge>
                <Badge variant="outline">{session.autonomy_level}</Badge>
                <span className="text-xs text-slate-400">
                  {session.iterations} turns · {session.command_count} commands ·{" "}
                  {session.cost_unknown ? "cost not reported" : `$${session.cost_usd.toFixed(4)}`}
                </span>
              </div>

              <ChatTranscript
                messages={session.messages}
                toolCalls={session.tool_calls}
                liveText={liveText}
                isRunning={isRunning}
              />

              {session.error_message && (
                <p className="mt-4 text-sm text-red-400">{session.error_message}</p>
              )}
            </div>
          ) : (
            <div className="space-y-4 rounded-lg border border-slate-700 bg-slate-900 p-4">
              <div>
                <Label className="text-slate-400">Autonomy</Label>
                <Select
                  value={autonomy}
                  onValueChange={(v) => setAutonomy(v as AIAutonomyLevel)}
                >
                  <SelectTrigger className="mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="read_only">Read-only</SelectItem>
                    <SelectItem value="approval">Approval required</SelectItem>
                    <SelectItem value="full_auto">Full auto</SelectItem>
                  </SelectContent>
                </Select>
                <p className="mt-1 text-xs text-slate-400">{AUTONOMY_HELP[autonomy]}</p>
              </div>

              <div>
                <Label className="text-slate-400">
                  Hosts in scope
                  <span className="ml-1 text-xs">
                    (the assistant cannot touch anything outside this list)
                  </span>
                </Label>
                <div className="mt-1 max-h-40 space-y-1 overflow-y-auto rounded-md border border-slate-700 p-2">
                  {(hosts ?? []).map((h) => (
                    <label
                      key={h.id}
                      className="flex cursor-pointer items-center gap-2 text-xs text-slate-300"
                    >
                      <input
                        type="checkbox"
                        checked={targetHosts.includes(h.id)}
                        onChange={() => toggleHost(h.id)}
                      />
                      <span className="text-white">{h.hostname}</span>
                      <span className="font-mono text-slate-400">{h.ip_address}</span>
                    </label>
                  ))}
                  {(hosts ?? []).length === 0 && (
                    <p className="text-xs text-slate-400">No hosts registered.</p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Composer */}
          <div className="space-y-2">
            <Textarea
              value={mission}
              onChange={(e) => setMission(e.target.value)}
              rows={3}
              placeholder={
                session
                  ? "Ask a follow-up…"
                  : "e.g. Check whether any service failed to start after the last reboot on node-1."
              }
              disabled={isRunning || !hasProvider}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit()
              }}
            />
            <div className="flex items-center justify-between">
              <p className="text-xs text-slate-400">
                {targetHosts.length > 0 && !session
                  ? `${targetHosts.length} host(s) in scope`
                  : "Ctrl+Enter to send"}
              </p>
              <Button
                onClick={submit}
                disabled={
                  !mission.trim() ||
                  isRunning ||
                  !hasProvider ||
                  startSession.isPending ||
                  sendFollowUp.isPending
                }
              >
                {isRunning ? "Working…" : session ? "Send" : "Start session"}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
