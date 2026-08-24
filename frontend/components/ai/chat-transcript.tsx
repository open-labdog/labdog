"use client"

import { useEffect, useRef } from "react"

import { ApprovalCard } from "@/components/ai/approval-card"
import { ToolCallBadge } from "@/components/ai/tool-call-badge"
import { Markdown } from "@/components/ui/markdown"
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

/**
 * Renders the conversation.
 *
 * Assistant turns are markdown — reports arrive with headings, bold and
 * tables — so they go through the markdown renderer. User turns stay
 * plain text: an operator typing asterisks means asterisks, and there is
 * no reason to give their own input a second interpretation pass.
 *
 * Tool results are shown as the badge of the call that produced them
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

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
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

  const renderCall = (call: AIToolCall) => {
    const approval = call.approval_id ? approvalById.get(call.approval_id) : undefined
    if (!approval) {
      return <ToolCallBadge key={call.id} call={call} />
    }
    placed.add(approval.id)
    return (
      <ApprovalCard
        key={`approval-${approval.id}`}
        approval={approval}
        hostName={hostNameFor?.(approval.target_host_id)}
        pending={decidingApproval}
        onDecide={(approve, note) => onDecideApproval?.(approval.id, approve, note)}
      />
    )
  }

  return (
    <div className="space-y-4">
      {messages
        .filter((m) => m.role !== "tool")
        .map((message) => {
          if (message.role === "user") {
            return (
              <div key={message.id} className="flex justify-end">
                <div className="max-w-[80%] rounded-lg bg-slate-800 px-4 py-2 text-sm text-white">
                  {message.content}
                </div>
              </div>
            )
          }

          const calls = (message.tool_calls ?? [])
            .map((requested) => {
              const candidates = callsByName.get(requested.name) ?? []
              return candidates.find((c) => !consumed.has(c.id))
            })
            .filter((c): c is AIToolCall => {
              if (!c) return false
              consumed.add(c.id)
              return true
            })

          return (
            <div key={message.id} className="space-y-2">
              {message.content && <Markdown>{message.content}</Markdown>}
              {calls.map(renderCall)}
            </div>
          )
        })}

      {approvals
        .filter((a) => !placed.has(a.id))
        .map((approval) => (
          <ApprovalCard
            key={`approval-${approval.id}`}
            approval={approval}
            hostName={hostNameFor?.(approval.target_host_id)}
            pending={decidingApproval}
            onDecide={(approve, note) => onDecideApproval?.(approval.id, approve, note)}
          />
        ))}

      {liveText && <Markdown>{liveText}</Markdown>}

      {isRunning && !liveText && (
        <div className="text-sm text-slate-400">Working…</div>
      )}

      <div ref={endRef} />
    </div>
  )
}
