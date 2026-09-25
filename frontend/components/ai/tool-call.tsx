"use client"

import { CodeBlock, Tag } from "@/components/ld"
import { AI_CLASSIFICATION, def } from "@/lib/status"
import type { AIToolCall } from "@/lib/types"

/**
 * One tool call, as the design draws a command: the verdict as a chip,
 * the command (or the tool's name when it is not a shell command) as
 * the headline, the host on the right, and what came back underneath.
 *
 * The chip carries the safety verdict, not the call's success or
 * failure: an operator scanning a transcript needs to see at a glance
 * whether the model tried to change anything. A call that did not run
 * says so beside the host.
 */
export function ToolCall({ call, hostName }: { call: AIToolCall; hostName?: string }) {
  const command = typeof call.arguments?.command === "string" ? call.arguments.command : null
  const cls = def(AI_CLASSIFICATION, call.classification)

  return (
    <CodeBlock
      tag={<Tag tone={cls.tone}>{cls.label}</Tag>}
      title={<span title={call.tool_name}>{command ?? call.tool_name}</span>}
      actions={
        <>
          {call.status !== "executed" && <Tag>{call.status}</Tag>}
          {hostName && <span className="tt">{hostName}</span>}
        </>
      }
      maxH={240}
    >
      {call.result_summary}
    </CodeBlock>
  )
}
