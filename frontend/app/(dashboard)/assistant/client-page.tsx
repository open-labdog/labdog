"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "next/navigation"

import { BudgetMeters, NewSessionOptions, SessionSummary } from "@/components/ai/session-aside"
import { SessionList } from "@/components/ai/session-list"
import { TERMINAL_STATES } from "@/components/ai/session-meta"
import { BulkBar, Confirm } from "@/components/ld"
import { apiFetch, ApiError } from "@/lib/api"
import { plural } from "@/lib/fleet"
import { AI_SESSION_STATUS, def } from "@/lib/status"
import type { AIApprovalRequest, AIProvider, AISessionDetail, AIUsageSummary, Host } from "@/lib/types"
import { ArchiveOldDialog, daysAgo } from "./_dialogs/archive-old"
import { useComposer } from "./_hooks/use-composer"
import { useSessionList } from "./_hooks/use-session-list"
import { useSessionStream } from "./_hooks/use-session-stream"
import { Composer } from "./_panes/composer"
import { SessionDetail } from "./_panes/session-detail"
import { PageBanners, SessionHead } from "./_panes/session-head"

/**
 * The assistant: the session list, the open session (or a new one) with
 * its composer, and the session's settings beside it.
 *
 * The list and its bulk actions are `useSessionList`, the live stream is
 * `useSessionStream`, and what the composer sends is `useComposer`; the
 * header, the banners and the middle pane's parts are in `_panes/`. This
 * file owns which session is open, the error banner's message, and the
 * open session's own actions.
 */
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
  const [error, setError] = useState<string | null>(null)

  const { data: providers } = useQuery<AIProvider[]>({
    queryKey: ["ai-providers"],
    queryFn: () => apiFetch<AIProvider[]>("/api/ai/providers"),
  })

  const { data: hosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })

  const list = useSessionList({ selectedId, setSelectedId, setError })
  const { picked, pickedIds, bulk, view } = list

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
    // A rollback runs on a worker for minutes and publishes nothing, so
    // the page looks until it has finished.
    refetchInterval: (query) => (query.state.data?.rollbacks?.some((r) => r.status === "running") ? 5_000 : false),
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

  const liveText = useSessionStream(selectedId, isRunning, setError)

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

  // The server's own sentence, so the page and the API's refusal cannot
  // disagree about what is spent.
  const followUpRefused = session?.follow_up_refused ?? null
  const composerLocked = loadingSession || isRunning || isParked || !canInvestigate || followUpRefused !== null
  const composer = useComposer({ selectedId, setSelectedId, setError, session, composerLocked })

  const status = session ? def(AI_SESSION_STATUS, session.status) : null
  const providerName = session?.provider_id != null ? providers?.find((p) => p.id === session.provider_id)?.name : undefined

  return (
    <>
      <SessionHead
        selectedId={selectedId}
        setSelectedId={setSelectedId}
        session={session}
        status={status}
        stopping={stopSession.isPending}
        onStop={(id) => stopSession.mutate(id)}
      />
      <PageBanners
        waitingElsewhere={waitingElsewhere}
        setSelectedId={setSelectedId}
        hasProvider={hasProvider}
        canInvestigate={canInvestigate}
        error={error}
        setError={setError}
      />

      {/* Three columns from lg up — sessions, transcript, and the aside —
          each scrolling on its own. Below that one column scrolls: a short
          session list, the transcript, the composer, then the aside — except
          for a new session, whose choices come before the composer that
          starts it. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <SessionList
          sessions={list.sessions}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onDelete={list.setDeleting}
          deleting={list.deleteSession.isPending}
          hostNames={hostNames}
          currency={currency}
          view={view}
          onView={list.changeView}
          selecting={list.selecting}
          onSelecting={list.changeSelecting}
          picked={picked}
          onPick={list.togglePicked}
          hasMore={!!list.sessionPages.hasNextPage}
          loadingMore={list.sessionPages.isFetchingNextPage}
          onMore={() => void list.sessionPages.fetchNextPage()}
          onArchiveOld={() => list.setArchiveOld({ before: daysAgo(30), alertsOnly: false })}
          bulkBar={
            <BulkBar n={picked.size} onClear={() => list.setPicked(new Set())}>
              {view === "active" ? (
                <button type="button" className="btn btn-sm" disabled={bulk.isPending} onClick={() => bulk.mutate({ path: "archive", body: { session_ids: pickedIds }, verb: "archived" })}>
                  archive
                </button>
              ) : (
                <>
                  <button type="button" className="btn btn-sm" disabled={bulk.isPending} onClick={() => bulk.mutate({ path: "unarchive", body: { session_ids: pickedIds }, verb: "back in the list" })}>
                    unarchive
                  </button>
                  <button type="button" className="btn btn-sm btn-danger" disabled={bulk.isPending} onClick={() => list.setConfirmBulkDelete(true)}>
                    delete
                  </button>
                </>
              )}
            </BulkBar>
          }
        />

        <div className="order-2 flex min-w-0 shrink-0 flex-col lg:min-h-0 lg:flex-1 lg:shrink">
          <SessionDetail
            selectedId={selectedId}
            session={session}
            sessionError={sessionError}
            hostNames={hostNames}
            liveText={liveText}
            isRunning={isRunning}
            onDecideApproval={(approvalId, approve, note) => decideApproval.mutate({ approvalId, approve, note })}
            decidingApproval={decideApproval.isPending}
          />
          <Composer
            selectedId={selectedId}
            session={session}
            sessionError={sessionError}
            followUpRefused={followUpRefused}
            mission={composer.mission}
            setMission={composer.setMission}
            isParked={isParked}
            isRunning={isRunning}
            composerLocked={composerLocked}
            busy={composer.busy}
            targetHostCount={composer.targetHosts.length}
            onSubmit={composer.submit}
          />
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
              providerId={composer.providerId}
              onProvider={composer.setProviderId}
              autonomy={composer.autonomy}
              onAutonomy={composer.setAutonomy}
              skipSnapshots={composer.skipSnapshots}
              onSkipSnapshots={composer.setSkipSnapshots}
              hosts={hosts}
              targetHosts={composer.targetHosts}
              onToggleHost={composer.toggleHost}
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
        open={list.confirmBulkDelete}
        onOpenChange={list.setConfirmBulkDelete}
        title={`Delete ${plural(picked.size, "session")}`}
        description="Delete these sessions and their transcripts? Recorded spend is kept in the usage totals. Any that LabDog still needs are kept and named: one still running, a full-auto alert fix the remediation safeguards still count, or one whose snapshot is still on the hypervisor."
        confirmLabel="Delete"
        variant="destructive"
        loading={bulk.isPending}
        onConfirm={() => bulk.mutate({ path: "delete", body: { session_ids: pickedIds }, verb: "deleted" })}
      />

      {list.archiveOld && (
        <ArchiveOldDialog
          archiveOld={list.archiveOld}
          setArchiveOld={list.setArchiveOld}
          pending={bulk.isPending}
          onCancel={() => list.setArchiveOld(null)}
          onArchive={(body) => bulk.mutate({ path: "archive", body, verb: "archived" })}
        />
      )}

      <Confirm
        open={list.deleting !== null}
        onOpenChange={(o) => !o && list.setDeleting(null)}
        title="Delete session"
        description="Delete this session and its transcript? Recorded spend is kept in the usage totals."
        confirmLabel="Delete"
        variant="destructive"
        loading={list.deleteSession.isPending}
        onConfirm={() => {
          if (list.deleting) list.deleteSession.mutate(list.deleting.id)
        }}
      />
    </>
  )
}
