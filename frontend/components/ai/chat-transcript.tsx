"use client"

import { useEffect, useRef, type ReactNode } from "react"

import { ApprovalCard } from "@/components/ai/approval-card"
import { Markdown } from "@/components/ai/markdown"
import { ToolCall } from "@/components/ai/tool-call"
import { Dot } from "@/components/ld"
import type { AIApprovalRequest, AIMessage, AIToolCall } from "@/lib/types"

interface Props {
  messages: AIMessage[]
  toolCalls: AIToolCall[]
  approvals?: AIApprovalRequest[]
  hostNameFor?: (hostId: number | null) => string | undefined
  onDecideApproval?: (approvalId: number, approve: boolean, note: string) => void
  decidingApproval?: boolean
  /** Text streaming in for the turn that has not been persisted yet. */
  liveText?: string
  isRunning?: boolean
}

/** The design's assistant turn: a small mono `ai` tile beside the text. */
function AssistantTurn({ children }: { children: ReactNode }) {
  return (
    <div className="flex max-w-[86%] gap-[9px]">
      <div className="mono grid h-5 w-5 shrink-0 place-items-center rounded-[5px] border border-line bg-surface-3 text-[9px] text-text-2">ai</div>
      <div className="min-w-0 flex-1 pt-0.5">{children}</div>
    </div>
  )
}

/**
 * Renders the conversation.
 *
 * Assistant turns are markdown — reports arrive with headings, bold and
 * tables — so they go through the markdown renderer. User turns stay
 * plain text: an operator typing asterisks means asterisks, and there is
 * no reason to give their own input a second interpretation pass.
 *
 * Tool results are shown as the block of the call that produced them
 * rather than as their own turn: the raw result is often thousands of
 * lines of log output, and the operator wants to see what was run and
 * what it concluded, not re-read the log the model already read.
 */
export function ChatTranscript({
  messages,
  toolCalls,
  approvals = [],
  hostNameFor,
  onDecideApproval,
  decidingApproval,
  liveText,
  isRunning,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null)

  // `nearest`, so only the transcript's own scroll box moves — never the
  // shell around it.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }, [messages.length, liveText])

  const callsByName = new Map<string, AIToolCall[]>()
  for (const call of toolCalls) {
    const list = callsByName.get(call.tool_name) ?? []
    list.push(call)
    callsByName.set(call.tool_name, list)
  }
  // Tool calls are consumed in order as we walk the assistant turns.
  const consumed = new Set<number>()

  const approvalById = new Map(approvals.map((a) => [a.id, a]))
  // Anything the walk below does not place gets appended at the end, so a
  // pending request can never be invisible because of a matching quirk —
  // an approval the operator cannot see is one they cannot act on, and the
  // session stays parked forever.
  const placed = new Set<number>()

  const renderApproval = (approval: AIApprovalRequest) => (
    <ApprovalCard
      key={`approval-${approval.id}`}
      approval={approval}
      hostName={hostNameFor?.(approval.target_host_id)}
      pending={decidingApproval}
      onDecide={(approve, note) => onDecideApproval?.(approval.id, approve, note)}
    />
  )

  const renderCall = (call: AIToolCall) => {
    const approval = call.approval_id ? approvalById.get(call.approval_id) : undefined
    if (!approval) return <ToolCall key={call.id} call={call} hostName={hostNameFor?.(call.target_host_id)} />
    placed.add(approval.id)
    return renderApproval(approval)
  }

  return (
    <div className="flex flex-col gap-2.5">
      {messages
        .filter((m) => m.role !== "tool")
        .map((message) => {
          if (message.role === "user") {
            return (
              <div
                key={message.id}
                className="max-w-[76%] self-end whitespace-pre-wrap break-words rounded-r-lg border border-ld-accent-line bg-ld-accent-soft px-3 py-[9px] text-[12.5px] text-text"
              >
                {message.content}
              </div>
            )
          }

          // Each request takes the first recorded call of its name not yet
          // taken, and marks it taken before the next request looks. (This
          // used to find all of a turn's calls first and mark them after,
          // so two `run_ssh_command`s in one turn both found the first —
          // shown twice — and the second was never shown at all.)
          const calls: AIToolCall[] = []
          for (const requested of message.tool_calls ?? []) {
            const call = (callsByName.get(requested.name) ?? []).find((c) => !consumed.has(c.id))
            if (!call) continue
            consumed.add(call.id)
            calls.push(call)
          }

          return (
            <div key={message.id} className="flex flex-col gap-2.5">
              {message.content && (
                <AssistantTurn>
                  <Markdown>{message.content}</Markdown>
                </AssistantTurn>
              )}
              {calls.map(renderCall)}
            </div>
          )
        })}

      {approvals.filter((a) => !placed.has(a.id)).map(renderApproval)}

      {liveText && (
        <AssistantTurn>
          <Markdown>{liveText}</Markdown>
        </AssistantTurn>
      )}

      {isRunning && !liveText && (
        <div className="flex items-center gap-2 text-[11.5px] text-text-3">
          <Dot tone="sync" pulse />
          Working…
        </div>
      )}

      <div ref={endRef} />
    </div>
  )
}
