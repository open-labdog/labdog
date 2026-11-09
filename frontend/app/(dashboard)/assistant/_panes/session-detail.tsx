"use client"

import { ChatTranscript } from "@/components/ai/chat-transcript"
import { Banner, Empty } from "@/components/ld"
import type { AISessionDetail } from "@/lib/types"

/** The middle pane's scrolling part: the open session's transcript, or what stands in for it. */
export function SessionDetail({
  selectedId,
  session,
  sessionError,
  hostNames,
  liveText,
  isRunning,
  onDecideApproval,
  decidingApproval,
}: {
  selectedId: number | null
  session: AISessionDetail | undefined
  sessionError: Error | null
  hostNames: (ids: number[] | null | undefined) => string[]
  liveText: string
  isRunning: boolean
  onDecideApproval: (approvalId: number, approve: boolean, note: string) => void
  decidingApproval: boolean
}) {
  return (
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
            onDecideApproval={onDecideApproval}
            decidingApproval={decidingApproval}
            liveText={liveText}
            isRunning={isRunning}
          />
          {session.error_message && <Banner tone="danger">{session.error_message}</Banner>}
          {/* Under the reply, because that is what it qualifies: a run
              stopped at a limit ends in a summary of what it had, which
              reads like an answer to whatever was last asked. */}
          {session.stopped_reason && !isRunning && (
            <Banner tone="warn">
              The assistant stopped early: {session.stopped_reason}. Its last message is what it had by then, and may not
              answer what you asked.
            </Banner>
          )}
        </>
      ) : sessionError ? (
        <Banner tone="danger">
          Could not load session #{selectedId}: {sessionError instanceof Error ? sessionError.message : String(sessionError)}
        </Banner>
      ) : (
        <p className="m-0 text-xs text-text-3">Loading session #{selectedId}…</p>
      )}
    </div>
  )
}
