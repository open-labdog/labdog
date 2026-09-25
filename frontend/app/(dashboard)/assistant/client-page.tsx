"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "next/navigation"

import { ChatTranscript } from "@/components/ai/chat-transcript"
import { BudgetMeters, NewSessionOptions, SessionSummary } from "@/components/ai/session-aside"
import { SessionList } from "@/components/ai/session-list"
import { TERMINAL_STATES } from "@/components/ai/session-meta"
import { Banner, Confirm, Empty, PageHead, Tag } from "@/components/ld"
import { API_BASE, apiFetch, ApiError } from "@/lib/api"
import { plural } from "@/lib/fleet"
import { AI_SESSION_STATUS, def } from "@/lib/status"
import { formatTimestamp } from "@/lib/utils"
import type {
  AIApprovalRequest,
  AIAutonomyLevel,
  AIProvider,
  AISession,
  AISessionDetail,
  AIUsageSummary,
  Host,
} from "@/lib/types"

const CRUMBS = [{ label: "assistant" }]

export default function AssistantPage() {
  const queryClient = useQueryClient()
  /**
   * Which session the page is showing.
   *
   * Seeded from `?session=` so a link can open one directly. The alerts
   * page has linked here since alert intake shipped — `View investigation`
   * pushed `/assistant?session=<id>` — but nothing read the parameter, so
   * the button navigated to the Assistant page and selected nothing. It
   * looked like it worked, which is the worst kind of broken link: the
   * operator lands on a page full of identically-titled sessions and has
   * to guess which one they asked for.
   *
   * Read once, as the initial value, rather than synced: after arriving,
   * clicking a different session in the list is the operator changing
   * their mind, and re-asserting the URL's choice over that would fight
   * them.
   */
  const searchParams = useSearchParams()
  const [selectedId, setSelectedId] = useState<number | null>(() => {
    const requested = Number(searchParams.get("session"))
    return Number.isInteger(requested) && requested > 0 ? requested : null
  })
  const [mission, setMission] = useState("")
  const [autonomy, setAutonomy] = useState<AIAutonomyLevel>("read_only")
  const [skipSnapshots, setSkipSnapshots] = useState(false)
  // null means "let the backend pick its default provider".
  const [providerId, setProviderId] = useState<number | null>(null)
  const [targetHosts, setTargetHosts] = useState<number[]>([])
  // Keyed by session so switching sessions cannot show the previous one's
  // partial text, without needing a synchronous reset inside the effect.
  const [live, setLive] = useState<{ sessionId: number; text: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<AISession | null>(null)
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

  // The same query the Settings usage panel reads, so the two share a
  // cache entry and the budget agrees on both pages.
  const { data: usage } = useQuery<AIUsageSummary>({
    queryKey: ["ai-usage"],
    queryFn: () => apiFetch<AIUsageSummary>("/api/ai/usage?days=30"),
    refetchInterval: 30_000,
  })
  const currency = usage?.currency || "USD"

  /**
   * Host ids -> names, for showing a session's scope.
   *
   * A session stores ids, and an id tells an operator nothing about which
   * machine an investigation touched. The hosts query is already loaded
   * here for the target picker, so this needs no extra request.
   */
  const hostNames = useMemo(() => {
    const byId = new Map<number, string>()
    for (const h of hosts ?? []) byId.set(h.id, h.hostname)
    return (ids: number[] | null | undefined): string[] => (ids ?? []).map((id) => byId.get(id) ?? `host ${id}`)
  }, [hosts])

  /**
   * Stop a run that is still going.
   *
   * The endpoint has existed since the loop learned to check for
   * cancellation every turn; nothing in the UI ever called it, so the only
   * way to stop a session was to wait for a cap to end it. That is a long
   * wait on the defaults — 40 turns, or 15 minutes of wall clock — while
   * the model keeps spending.
   *
   * Cancelling is a request, not a kill: the loop notices between turns,
   * so a session mid-command finishes that command first. The button says
   * "Stopping…" until the status changes rather than pretending it is
   * already over.
   */
  const stopSession = useMutation({
    mutationFn: (id: number) => apiFetch(`/api/ai/sessions/${id}/cancel`, { method: "POST" }),
    onSuccess: () => {
      setError(null)
      queryClient.invalidateQueries({ queryKey: ["ai-session", selectedId] })
      queryClient.invalidateQueries({ queryKey: ["ai-sessions"] })
      // A cancelled session expires its pending approval, so a card left
      // on the approvals page would otherwise invite a click that does
      // nothing.
      queryClient.invalidateQueries({ queryKey: ["ai-approvals"] })
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : "Could not stop the session."),
  })

  const deleteSession = useMutation({
    mutationFn: (id: number) => apiFetch(`/api/ai/sessions/${id}`, { method: "DELETE" }),
    onSuccess: (_data, id) => {
      if (id === selectedId) setSelectedId(null)
      setDeleting(null)
      queryClient.invalidateQueries({ queryKey: ["ai-sessions"] })
      setError(null)
    },
    onError: (e: unknown) => {
      setDeleting(null)
      setError(e instanceof ApiError ? e.message : "Could not delete the session.")
    },
  })

  /**
   * Every request still waiting on a person, across all sessions.
   *
   * Polled rather than pushed: the SSE stream belongs to one session, and
   * the case this exists for is precisely the one where the operator has
   * that session closed — a scheduled run that parked overnight.
   */
  const { data: pendingApprovals } = useQuery<AIApprovalRequest[]>({
    queryKey: ["ai-approvals"],
    queryFn: () => apiFetch<AIApprovalRequest[]>("/api/ai/approvals?status=pending"),
    refetchInterval: 30_000,
  })

  const { data: session, error: sessionError } = useQuery<AISessionDetail>({
    queryKey: ["ai-session", selectedId],
    queryFn: () => apiFetch<AISessionDetail>(`/api/ai/sessions/${selectedId}`),
    enabled: selectedId !== null,
    // A deleted session (a stale `?session=` link) will not reappear, so
    // say so now rather than after three backed-off retries.
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 3,
  })
  // A session was asked for (clicked, or named by `?session=`) and has not
  // arrived: nothing typed now may start a new session by accident.
  const loadingSession = selectedId !== null && !session

  // Parked is not working. The distinction drives the whole screen: no
  // "Working…", no SSE subscription (nothing will publish), the Stop
  // button stays, and the composer says it is waiting on the operator's
  // decision rather than on the model.
  const isParked = session?.status === "waiting_approval"
  const isRunning = session ? !TERMINAL_STATES.has(session.status) && !isParked : false

  // The banner only covers what is not already on screen; the open
  // session shows its own card inline, and repeating it there would read
  // as two separate requests.
  const waitingElsewhere = (pendingApprovals ?? []).filter((a) => a.session_id !== selectedId)

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
  }, [selectedId, isRunning, queryClient])

  // Only ever render the buffer belonging to the session on screen.
  const liveText = live?.sessionId === selectedId ? live.text : ""

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

  const decideApproval = useMutation({
    mutationFn: ({ approvalId, approve, note }: { approvalId: number; approve: boolean; note: string }) =>
      apiFetch(`/api/ai/approvals/${approvalId}`, {
        method: "POST",
        json: { approve, note: note || null },
      }),
    onSuccess: () => {
      setError(null)
      // The session goes back to running and the worker takes over, so
      // both the detail and the list need re-reading. The SSE effect
      // re-subscribes off the status change.
      queryClient.invalidateQueries({ queryKey: ["ai-session", selectedId] })
      queryClient.invalidateQueries({ queryKey: ["ai-sessions"] })
      queryClient.invalidateQueries({ queryKey: ["ai-approvals"] })
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : "Could not record that decision."),
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

  const enabled = (providers ?? []).filter((p) => p.enabled)
  const hasProvider = enabled.length > 0

  // A backend that cannot run tools cannot investigate; the API refuses
  // such a session outright. Offering it here would only sell the operator
  // a guaranteed failure, so it is listed as unavailable instead of being
  // selectable.
  //
  // Read from the server's own capability flag rather than matched against
  // a provider_type: the backend decides what can run tools, and a list of
  // type names here would be a second copy of that answer to keep in step.
  const usable = enabled.filter((p) => p.supports_tools)
  const toolless = enabled.filter((p) => !p.supports_tools)
  const canInvestigate = usable.length > 0

  const composerLocked = loadingSession || isRunning || isParked || !canInvestigate
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

  const status = session ? def(AI_SESSION_STATUS, session.status) : null
  const providerName = session?.provider_id != null ? providers?.find((p) => p.id === session.provider_id)?.name : undefined

  return (
    <>
      <PageHead
        crumbs={CRUMBS}
        title={
          selectedId === null ? (
            "New session"
          ) : (
            <>
              <span className="mono">session #{selectedId}</span>
              {status && session && (
                <Tag tone={status.tone} title={session.status}>
                  {status.label}
                </Tag>
              )}
              {/*
                A run cut short is still "succeeded" — it did what it was
                asked until the budget ran out — so the status alone says a
                truncated investigation and a complete one are the same
                thing. They are not: one of them stopped with work left to
                do, and whether to raise the cap and run it again is a
                decision the operator can only make if they know.
              */}
              {session?.stopped_reason && <Tag tone="warn">stopped early · {session.stopped_reason}</Tag>}
            </>
          )
        }
        sub={
          session ? (
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className="trunc max-w-full text-text" title={session.mission}>
                {session.title ?? session.mission}
              </span>
              {/* Same reasoning as the list: an open transcript is the
                  thing an operator lines up against a Grafana panel or a
                  journal, and it could not say when it ran. */}
              <span className="mono num text-[11.5px] text-text-3" title={new Date(session.created_at).toISOString()}>
                started {formatTimestamp(session.created_at)}
              </span>
            </div>
          ) : selectedId === null ? (
            "Ask the assistant to investigate your hosts. It works through LabDog's own tools, so every command it runs is classified and audited."
          ) : undefined
        }
        actions={
          <>
            {/* Shown for `waiting_approval` too — no task is running, but
                the session is not over either, and abandoning it is a
                reasonable answer to a request you do not want to grant. */}
            {session && !TERMINAL_STATES.has(session.status) && (
              <button type="button" className="btn btn-sm btn-danger" disabled={stopSession.isPending} onClick={() => stopSession.mutate(session.id)}>
                {stopSession.isPending ? "Stopping…" : "Stop"}
              </button>
            )}
            {selectedId !== null && (
              <button type="button" className="btn btn-sm" onClick={() => setSelectedId(null)}>
                New session
              </button>
            )}
          </>
        }
      />

      {/* A scheduled run that parked overnight is otherwise invisible:
          nothing on this page is open on it, and the session list is long.
          Named and clickable, because "you have 2 pending approvals" that
          does not say which is a notification the operator has to go and
          decode. */}
      {waitingElsewhere.length > 0 && (
        <Banner tone="hold" flush pulse>
          <span className="font-medium">
            {waitingElsewhere.length === 1 ? "A session is waiting for your decision" : `${waitingElsewhere.length} sessions are waiting for your decision`}:
          </span>{" "}
          {waitingElsewhere.map((a, i) => (
            <span key={a.id}>
              {i > 0 && ", "}
              <button
                type="button"
                onClick={() => setSelectedId(a.session_id)}
                className="mono cursor-pointer border-0 bg-transparent p-0 text-inherit underline underline-offset-4"
              >
                {a.command_preview.slice(0, 60)}
              </button>
            </span>
          ))}
        </Banner>
      )}

      {!hasProvider && (
        <Banner tone="warn" flush action={<Link href="/settings?section=ai" className="btn btn-sm hover:no-underline">Settings → AI</Link>}>
          No AI provider is configured yet. Add one, and switch on <span className="mono">ai.enabled</span>, under Settings → AI.
        </Banner>
      )}

      {/* Distinct from having no provider at all: one exists, it just
          cannot run tools, so it cannot investigate. Without this the
          composer would sit there enabled and every attempt would be
          refused by the API with no explanation on screen. */}
      {hasProvider && !canInvestigate && (
        <Banner tone="warn" flush action={<Link href="/ai-providers" className="btn btn-sm hover:no-underline">AI providers →</Link>}>
          The only configured provider is a single-shot backend, which cannot run tools and so cannot investigate anything. Add an
          OpenAI-compatible or Anthropic provider to use the assistant.
        </Banner>
      )}

      {error && (
        <Banner tone="danger" flush action={<button type="button" className="btn btn-sm btn-ghost" onClick={() => setError(null)}>dismiss</button>}>
          {error}
        </Banner>
      )}

      {/* Three columns from lg up — sessions, transcript, and the aside —
          each scrolling on its own. Below that one column scrolls: a short
          session list, the transcript, the composer, then the aside — except
          for a new session, whose choices come before the composer that
          starts it. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <SessionList
          sessions={sessions}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onDelete={setDeleting}
          deleting={deleteSession.isPending}
          hostNames={hostNames}
          currency={currency}
        />

        <div className="order-2 flex min-w-0 shrink-0 flex-col lg:min-h-0 lg:flex-1 lg:shrink">
          <div className="flex flex-col gap-2.5 p-3.5 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
            {selectedId === null ? (
              <Empty
                title="New session"
                note="Say what to look into below. Pick the provider, autonomy and hosts in scope first — they cannot change once the session starts."
              />
            ) : session ? (
              <>
                <ChatTranscript
                  messages={session.messages}
                  toolCalls={session.tool_calls}
                  approvals={session.approvals}
                  hostNameFor={(id) => (id === null ? undefined : hostNames([id])[0])}
                  onDecideApproval={(approvalId, approve, note) => decideApproval.mutate({ approvalId, approve, note })}
                  decidingApproval={decideApproval.isPending}
                  liveText={liveText}
                  isRunning={isRunning}
                />
                {session.error_message && <Banner tone="danger">{session.error_message}</Banner>}
              </>
            ) : sessionError ? (
              <Banner tone="danger">
                Could not load session #{selectedId}: {sessionError instanceof Error ? sessionError.message : String(sessionError)}
              </Banner>
            ) : (
              <p className="m-0 text-xs text-text-3">Loading session #{selectedId}…</p>
            )}
          </div>

          <form
            className="flex shrink-0 flex-col gap-1.5 border-t border-line bg-surface px-3.5 py-2.5"
            onSubmit={(e) => {
              e.preventDefault()
              submit()
            }}
          >
            <textarea
              className="inp"
              rows={3}
              aria-label={selectedId === null ? "what to investigate" : "follow-up message"}
              value={mission}
              onChange={(e) => setMission(e.target.value)}
              placeholder={
                isParked
                  ? "Decide on the pending command above to continue this session."
                  : session
                    ? "Ask a follow-up…"
                    : selectedId !== null
                      ? sessionError
                        ? "This session could not be loaded — pick another, or start a new one."
                        : "Loading the session…"
                      : "e.g. Check whether any service failed to start after the last reboot on node-1."
              }
              disabled={composerLocked}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit()
              }}
            />
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-text-3">
                {targetHosts.length > 0 && selectedId === null ? `${plural(targetHosts.length, "host")} in scope · Ctrl+Enter to send` : "Ctrl+Enter to send"}
              </span>
              <button type="submit" className="btn btn-sm btn-primary ml-auto" disabled={!mission.trim() || composerLocked || busy}>
                {isRunning ? "Working…" : isParked ? "Waiting for you" : selectedId === null ? "Start session" : "Send"}
              </button>
            </div>
          </form>
        </div>

        <aside
          aria-label="session settings"
          className={`flex shrink-0 flex-col gap-3.5 border-line bg-surface p-[13px] lg:order-3 lg:w-[268px] lg:overflow-y-auto lg:border-l lg:border-t-0 lg:border-b-0 ${
            selectedId === null ? "order-1 border-b" : "order-3 border-t"
          }`}
        >
          {selectedId === null ? (
            <NewSessionOptions
              usable={usable}
              toolless={toolless}
              providerId={providerId}
              onProvider={setProviderId}
              autonomy={autonomy}
              onAutonomy={setAutonomy}
              skipSnapshots={skipSnapshots}
              onSkipSnapshots={setSkipSnapshots}
              hosts={hosts}
              targetHosts={targetHosts}
              onToggleHost={toggleHost}
            />
          ) : (
            session && <SessionSummary session={session} hostNames={hostNames} providerName={providerName} currency={currency} />
          )}
          <BudgetMeters usage={usage} />
          {/* Every shell command the assistant runs is written to the audit
              log as an `ai_command` entry against its host. */}
          <Link href="/audit" className="btn btn-sm btn-ghost mt-auto self-start hover:no-underline">
            every command it runs is in the audit log →
          </Link>
        </aside>
      </div>

      <Confirm
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete session"
        description="Delete this session and its transcript? Recorded spend is kept in the usage totals."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleteSession.isPending}
        onConfirm={() => {
          if (deleting) deleteSession.mutate(deleting.id)
        }}
      />
    </>
  )
}
