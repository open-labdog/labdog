"use client"

import Link from "next/link"
import { TERMINAL_STATES } from "@/components/ai/session-meta"
import { Banner, PageHead, Tag, type Tone } from "@/components/ld"
import type { AIApprovalRequest, AISessionDetail } from "@/lib/types"
import { formatTimestamp } from "@/lib/utils"

const CRUMBS = [{ label: "assistant" }]

/** The screen's header: which session is open, its state, Stop and New session. */
export function SessionHead({
  selectedId,
  setSelectedId,
  session,
  status,
  stopping,
  onStop,
}: {
  selectedId: number | null
  setSelectedId: (id: number | null) => void
  session: AISessionDetail | undefined
  status: { label: string; tone: Tone } | null
  stopping: boolean
  onStop: (id: number) => void
}) {
  return (
    <PageHead
      crumbs={CRUMBS}
      title={
        selectedId === null ? (
          "New session"
        ) : (
          <>
            <span>session #{selectedId}</span>
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
          "Ask the assistant to investigate your hosts. It works through unbork's own tools, so every command it runs is classified and audited."
        ) : undefined
      }
      actions={
        <>
          {/* Shown for `waiting_approval` too — no task is running, but
              the session is not over either, and abandoning it is a
              reasonable answer to a request you do not want to grant. */}
          {session && !TERMINAL_STATES.has(session.status) && (
            <button type="button" className="btn btn-sm btn-danger" disabled={stopping} onClick={() => onStop(session.id)}>
              {stopping ? "Stopping…" : "Stop"}
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
  )
}

/** The banners under the header: approvals waiting elsewhere, provider problems, and the page's error. */
export function PageBanners({
  waitingElsewhere,
  setSelectedId,
  hasProvider,
  canInvestigate,
  error,
  setError,
}: {
  waitingElsewhere: AIApprovalRequest[]
  setSelectedId: (id: number | null) => void
  hasProvider: boolean
  canInvestigate: boolean
  error: string | null
  setError: (error: string | null) => void
}) {
  return (
    <>
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
    </>
  )
}
