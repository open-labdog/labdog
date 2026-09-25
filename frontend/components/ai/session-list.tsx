"use client"

import { Tag } from "@/components/ld"
import { money } from "@/components/ai/usage-panel"
import { MODE_LABEL, TERMINAL_STATES, describeScope } from "@/components/ai/session-meta"
import { shortAgo } from "@/lib/fleet"
import { AI_SESSION_STATUS, def } from "@/lib/status"
import { formatTimestamp } from "@/lib/utils"
import type { AISession } from "@/lib/types"

interface Props {
  sessions: AISession[] | undefined
  selectedId: number | null
  onSelect: (id: number) => void
  onDelete: (session: AISession) => void
  deleting: boolean
  hostNames: (ids: number[] | null | undefined) => string[]
  currency: string
}

/**
 * The assistant's left column: every session, newest first. Beside the
 * transcript on a wide screen; a short scrolling list above it on a
 * narrow one.
 */
export function SessionList({ sessions, selectedId, onSelect, onDelete, deleting, hostNames, currency }: Props) {
  const rows = sessions ?? []
  return (
    <section
      aria-label="sessions"
      className="flex shrink-0 flex-col border-b border-line bg-surface lg:min-h-0 lg:w-[260px] lg:border-b-0 lg:border-r"
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-line bg-surface-2 px-3 py-2">
        <span className="tt text-text-2">sessions</span>
        <span className="mono num text-[10.5px] text-text-faint">{rows.length}</span>
      </div>
      <div className="scroll max-h-[220px] lg:max-h-none lg:flex-1">
        {sessions === undefined && <p className="m-0 px-3 py-4 text-center text-xs text-text-3">Loading sessions…</p>}
        {sessions !== undefined && rows.length === 0 && <p className="m-0 px-3 py-4 text-center text-xs text-text-3">No sessions yet.</p>}
        {rows.map((s) => {
          const on = s.id === selectedId
          const running = !TERMINAL_STATES.has(s.status)
          const st = def(AI_SESSION_STATUS, s.status)
          const name = s.title ?? s.mission
          return (
            <div
              key={s.id}
              className="group flex items-start border-b border-line-faint"
              style={on ? { background: "var(--accent-soft)", boxShadow: "inset 2px 0 0 var(--accent)" } : undefined}
            >
              <button
                type="button"
                onClick={() => onSelect(s.id)}
                aria-current={on ? "true" : undefined}
                className={`flex min-w-0 flex-1 flex-col gap-1 border-0 bg-transparent px-3 py-2 text-left ${on ? "" : "row-hover"}`}
              >
                <span className={`line-clamp-2 text-[12px] text-text ${on ? "font-semibold" : "font-medium"}`}>{name}</span>
                {/*
                  When a session is titled after what started it, several
                  runs of the same thing are titled identically —
                  alert investigations especially, where the title is the
                  alert name. Four rows reading "Alert: X, succeeded,
                  alert, jellyfin" are one row as far as the reader is
                  concerned. The time is what tells them apart.

                  Which is why the absolute time leads and the relative
                  one trails it. This first shipped the other way round,
                  with "23h ago" as the label and the real time hidden in
                  a tooltip — and three sessions from the same evening
                  still read identically, which was the whole complaint.
                  "How long ago" is a coarse answer that stops
                  distinguishing rows within an hour of each other;
                  "19:03" never does, and is also what a Grafana panel or
                  a log line can be lined up against.
                */}
                <span className="mono num text-[10.5px] text-text-3" title={new Date(s.created_at).toISOString()}>
                  {formatTimestamp(s.created_at)} <span className="whitespace-nowrap text-text-faint">· {shortAgo(s.created_at)} ago</span>
                </span>
                <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                  <Tag tone={st.tone}>{st.label}</Tag>
                  {/* Marked in the list too, not only once opened: the
                      point of knowing is deciding which one to open. */}
                  {s.stopped_reason && (
                    <Tag tone="warn" title={`Stopped early: ${s.stopped_reason}`}>
                      cut short
                    </Tag>
                  )}
                  {MODE_LABEL[s.mode] && <Tag>{MODE_LABEL[s.mode]}</Tag>}
                  <span className="trunc min-w-0 text-[11px] text-text-3">{describeScope(hostNames(s.target_host_ids))}</span>
                  {s.cost > 0 && <span className="mono num text-[11px] text-text-3">{money(s.cost, currency, 3)}</span>}
                </span>
              </button>
              {/* Out of the way until hover where there is a hover — the
                  list is for picking a session, not for managing one — and
                  always there on a touch screen, where there is not. */}
              <button
                type="button"
                aria-label={`Delete session: ${name}`}
                title={running ? "Cancel this session before deleting it" : "Delete this session and its transcript"}
                disabled={running || deleting}
                onClick={() => onDelete(s)}
                className="btn btn-sm btn-ghost mr-1 mt-1.5 text-text-faint hover:text-danger focus-visible:opacity-100 group-hover:opacity-100 group-hover:disabled:opacity-45 [@media(hover:hover)]:opacity-0"
              >
                delete
              </button>
            </div>
          )
        })}
      </div>
    </section>
  )
}
