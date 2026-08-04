"use client"

import { Badge } from "@/components/ui/badge"
import type { AIToolCall } from "@/lib/types"

/**
 * Colour carries the safety verdict, not the success/failure of the call:
 * an operator scanning a transcript needs to see at a glance whether the
 * model tried to change anything.
 */
const CLASSIFICATION_STYLE: Record<string, string> = {
  read_only: "bg-slate-600 text-slate-200",
  mutating: "bg-amber-600 text-white",
  denied: "bg-red-600 text-white",
  unknown: "bg-slate-600 text-slate-200",
}

const CLASSIFICATION_LABEL: Record<string, string> = {
  read_only: "read",
  mutating: "write",
  denied: "blocked",
  unknown: "unclassified",
}

export function ToolCallBadge({ call }: { call: AIToolCall }) {
  const command =
    typeof call.arguments?.command === "string" ? call.arguments.command : null

  return (
    <div className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-slate-300">{call.tool_name}</span>
        <Badge
          className={CLASSIFICATION_STYLE[call.classification] ?? CLASSIFICATION_STYLE.unknown}
        >
          {CLASSIFICATION_LABEL[call.classification] ?? call.classification}
        </Badge>
        {call.status !== "executed" && (
          <Badge variant="outline" className="text-xs">
            {call.status}
          </Badge>
        )}
      </div>

      {command && (
        <pre className="mt-2 overflow-x-auto rounded bg-slate-950 px-2 py-1 font-mono text-xs text-slate-300">
          {command}
        </pre>
      )}

      {call.result_summary && (
        <p className="mt-2 text-xs text-slate-400">{call.result_summary}</p>
      )}
    </div>
  )
}
