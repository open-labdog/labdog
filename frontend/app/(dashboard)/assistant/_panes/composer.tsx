"use client"

import Link from "next/link"
import { Banner } from "@/components/ld"
import { plural } from "@/lib/fleet"
import type { AISessionDetail } from "@/lib/types"

/** The message box under the transcript: starts a session, or sends a follow-up to the open one. */
export function Composer({
  selectedId,
  session,
  sessionError,
  followUpRefused,
  mission,
  setMission,
  isParked,
  isRunning,
  composerLocked,
  busy,
  targetHostCount,
  onSubmit,
}: {
  selectedId: number | null
  session: AISessionDetail | undefined
  sessionError: Error | null
  followUpRefused: string | null
  mission: string
  setMission: (mission: string) => void
  isParked: boolean
  isRunning: boolean
  composerLocked: boolean
  busy: boolean
  targetHostCount: number
  onSubmit: () => void
}) {
  return (
    <form
      className="flex shrink-0 flex-col gap-1.5 border-t border-line bg-surface px-3.5 py-2.5"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      {followUpRefused && (
        <Banner tone="warn" action={<Link href="/settings?section=ai" className="btn btn-sm hover:no-underline">Settings → AI</Link>}>
          {followUpRefused}
        </Banner>
      )}
      <textarea
        className="inp"
        rows={3}
        aria-label={selectedId === null ? "what to investigate" : "follow-up message"}
        value={mission}
        onChange={(e) => setMission(e.target.value)}
        placeholder={
          isParked
            ? "Decide on the pending command above to continue this session."
            : followUpRefused
              ? "This session cannot take another message."
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
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSubmit()
        }}
      />
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-text-3">
          {targetHostCount > 0 && selectedId === null ? `${plural(targetHostCount, "host")} in scope · Ctrl+Enter to send` : "Ctrl+Enter to send"}
        </span>
        <button type="submit" className="btn btn-sm btn-primary ml-auto" disabled={!mission.trim() || composerLocked || busy}>
          {isRunning ? "Working…" : isParked ? "Waiting for you" : selectedId === null ? "Start session" : "Send"}
        </button>
      </div>
    </form>
  )
}
