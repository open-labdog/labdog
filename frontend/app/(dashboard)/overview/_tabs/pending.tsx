"use client"

import { Dot, Empty, Panel, Tag, type Tone } from "@/components/ld"
import { toneVar } from "@/components/ld/tone"
import { shortAgo } from "@/lib/fleet"
import { LANES, useSettled, type PendingItem, type PendingLane, type usePendingQueue } from "@/lib/pending"
import { muted, PendRow } from "./parts"

const LANE: Record<PendingLane, { label: string; tone: Tone }> = {
  approvals: { label: "approvals", tone: "hold" },
  failed: { label: "failed runs", tone: "danger" },
  stuck: { label: "stuck", tone: "warn" },
  alerts: { label: "alerts", tone: "danger" },
  drift: { label: "drift", tone: "idle" },
}

/** One line under each lane's count: what is in it. */
function laneNote(lane: PendingLane, items: PendingItem[]): string {
  const xs = items.filter((p) => p.lane === lane)
  if (xs.length === 0) return lane === "failed" ? "every schedule's last run was ok" : lane === "stuck" ? "nothing running too long" : "none"
  switch (lane) {
    case "approvals": {
      const gates = xs.filter((p) => p.kind === "Assistant paused").length
      const scans = xs.length - gates
      return [gates && `${gates} assistant command${gates === 1 ? "" : "s"}`, scans && `${scans} discovery scan${scans === 1 ? "" : "s"}`].filter(Boolean).join(" · ")
    }
    case "alerts": {
      const crit = xs.filter((p) => p.severity === "block").length
      return `${crit} critical · ${xs.length - crit} other`
    }
    default:
      return [...new Set(xs.flatMap((p) => p.hosts))].slice(0, 3).join(", ") || xs[0].title
  }
}

export function PendingTab({
  pending,
  lane,
  setLane,
  wide,
  go,
  setView,
}: {
  pending: ReturnType<typeof usePendingQueue>
  lane: PendingLane | "all"
  setLane: (l: PendingLane | "all") => void
  wide: boolean
  go: (href: string) => void
  setView: (v: string) => void
}) {
  const settled = useSettled(24)
  const list = pending.live.filter((p) => lane === "all" || p.lane === lane)

  // The same items by host: several problems on one host usually share a cause.
  const byHost = new Map<string, Set<PendingLane>>()
  for (const p of pending.live) for (const h of p.hosts) byHost.set(h, (byHost.get(h) ?? new Set()).add(p.lane))
  const hostRows = [...byHost.entries()].sort((a, b) => b[1].size - a[1].size || a[0].localeCompare(b[0]))

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }} role="group" aria-label="lanes">
        {LANES.map((k) => {
          const n = pending.laneCounts[k]
          const on = lane === k
          return (
            <button
              key={k}
              type="button"
              aria-pressed={on}
              onClick={() => setLane(on ? "all" : k)}
              className="flex min-w-0 flex-col gap-[3px] rounded-r border border-line px-[11px] py-2 text-left"
              style={{
                borderTop: `2px solid ${n ? toneVar(LANE[k].tone) : "var(--border-strong)"}`,
                background: on ? "var(--surface-2)" : "var(--surface)",
                outline: on ? "1px solid var(--accent-line)" : undefined,
              }}
            >
              <span className="tt text-[8.5px]">{LANE[k].label}</span>
              <span className="mono num text-[19px] font-semibold leading-tight" style={{ color: n ? toneVar(LANE[k].tone) : "var(--text-faint)" }}>
                {n}
              </span>
              <span className="trunc w-full text-[10.5px] text-text-3">{laneNote(k, pending.live)}</span>
            </button>
          )
        })}
      </div>

      <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1.6fr) minmax(0,1fr)" : "minmax(0,1fr)", alignItems: "start" }}>
        <Panel
          title={lane === "all" ? "all lanes" : LANE[lane].label}
          meta={`${list.length} of ${pending.laneCounts.all} · soonest to expire`}
          actions={
            lane !== "all" ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setLane("all")}>
                all lanes
              </button>
            ) : undefined
          }
        >
          {list.length === 0 ? (
            <Empty
              title="Nothing waiting on you"
              note="Approval gates, failed scheduled runs, stuck operations, discovered hosts, firing alerts and drift all land here. Hidden items stay in history."
              action={
                <button type="button" className="btn btn-sm" onClick={() => setView("summary")}>
                  Back to summary
                </button>
              }
            />
          ) : (
            list.map((p) => <PendRow key={p.id} p={p} go={go} onDismiss={pending.dismiss} />)
          )}
        </Panel>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="by host" meta="hosts with something waiting">
            {hostRows.length === 0 ? (
              <div className={muted}>No host has anything waiting.</div>
            ) : (
              hostRows.map(([h, lanes]) => (
                <div key={h} className="flex items-center gap-2 border-b border-line-faint px-[11px] py-[7px]">
                  <span className="mono trunc flex-1 text-xs text-text">{h}</span>
                  {LANES.filter((l) => lanes.has(l)).map((l) => (
                    <Tag key={l} tone={LANE[l].tone === "idle" ? undefined : LANE[l].tone} onClick={() => setLane(l)}>
                      {LANE[l].label}
                    </Tag>
                  ))}
                </div>
              ))
            )}
          </Panel>

          <Panel title="settled · last 24h" meta="what left the queue">
            {settled.isLoading ? (
              <div className={muted}>Loading…</div>
            ) : settled.items.length === 0 ? (
              <div className={muted}>Nothing was decided, expired or resolved in the last 24 hours.</div>
            ) : (
              settled.items.slice(0, 12).map((s) => {
                const tone: Tone = s.outcome === "expired" ? "warn" : s.outcome === "rejected" ? "idle" : "ok"
                return (
                  <div key={s.id} className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]">
                    <span className="mono num w-8 shrink-0 text-[10.5px] text-text-3">{shortAgo(s.at)}</span>
                    <span className="trunc flex-1 text-xs text-text-2" title={s.what}>
                      {s.what}
                    </span>
                    <span className="inline-flex shrink-0 items-center gap-1.5 text-[11px]" style={{ color: toneVar(tone) }}>
                      <Dot tone={tone} />
                      {s.outcome}
                    </span>
                  </div>
                )
              })
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}
