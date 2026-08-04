"use client"

import { useEffect, useRef } from "react"

import { ToolCallBadge } from "@/components/ai/tool-call-badge"
import type { AIMessage, AIToolCall } from "@/lib/types"

interface Props {
  messages: AIMessage[]
  toolCalls: AIToolCall[]
  /** Text streaming in for the turn that has not been persisted yet. */
  liveText?: string
  isRunning?: boolean
}

/**
 * Renders the conversation.
 *
 * Tool results are shown as the badge of the call that produced them
 * rather than as their own turn: the raw result is often thousands of
 * lines of log output, and the operator wants to see what was run and
 * what it concluded, not re-read the log the model already read.
 */
export function ChatTranscript({ messages, toolCalls, liveText, isRunning }: Props) {
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
              {message.content && (
                <div className="text-sm whitespace-pre-wrap text-slate-300">
                  {message.content}
                </div>
              )}
              {calls.map((call) => (
                <ToolCallBadge key={call.id} call={call} />
              ))}
            </div>
          )
        })}

      {liveText && (
        <div className="text-sm whitespace-pre-wrap text-slate-300">{liveText}</div>
      )}

      {isRunning && !liveText && (
        <div className="text-sm text-slate-400">Working…</div>
      )}

      <div ref={endRef} />
    </div>
  )
}
