"use client"

import Link from "next/link"
import { Dot, Panel, Tag } from "@/components/ld"
import { toneVar } from "@/components/ld/tone"
import { InvestigateRunButton } from "@/components/investigate-run-button"
import { cronToHuman } from "@/lib/cron"
import { plural, shortAgo, untilLabel } from "@/lib/fleet"
import { durationLabel, durationMs, isOrphaned, scheduleName } from "@/lib/overview"
import { hostRunReason, hostRunTone, hostRunWord, type HostSchedule } from "@/lib/host-overview"
import type { Tone } from "@/lib/fleet"
import type { HostRunEntry } from "@/lib/types"

const runHref = (hostId: number, r: Pick<HostRunEntry, "action_run_id">) => `/hosts/${hostId}/actions/runs/${r.action_run_id}`

function RunButtons({ hostId, hostname, r }: { hostId: number; hostname: string; r: HostRunEntry }) {
  return (
    <span className="ml-auto inline-flex shrink-0 items-center gap-1.5">
      <Link href={runHref(hostId, r)} className="btn btn-sm hover:no-underline">
        Open run
      </Link>
      <InvestigateRunButton run={{ id: r.action_run_id }} hostRun={{ id: r.host_run_id, host_id: hostId, hostname }} />
    </span>
  )
}

/**
 * Only when this host's own result in a schedule's newest run was a
 * failure: the schedule, how many in a row, the first error line.
 */
export function FailedRunBanner({ failing, hostId, hostname }: { failing: HostSchedule[]; hostId: number; hostname: string }) {
  if (failing.length === 0) return null
  return (
    <div className="flex flex-col gap-1.5" data-testid="host-failed-runs">
      {failing.map(({ s, last, streak }) =>
        last ? (
          <div
            key={s.id}
            role="alert"
            className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 rounded-r px-[11px] py-2 text-xs text-text"
            style={{
              background: "var(--danger-soft)",
              boxShadow: `inset 2px 0 0 ${toneVar("danger")}`,
            }}
          >
            <Dot tone="danger" />
            <span className="min-w-0 flex-1">
              <b className="font-semibold">
                {scheduleName(s)} {last.rolled_back ? "failed and was rolled back" : "failed"} on this host
                {streak > 1 ? ` in its last ${streak} scheduled runs` : " in its last scheduled run"}.
              </b>{" "}
              <span className="text-text-2">
                {shortAgo(last.created_at)} ago: <span className="mono">{hostRunReason(last)}</span>
              </span>
            </span>
            <RunButtons hostId={hostId} hostname={hostname} r={last} />
          </div>
        ) : null,
      )}
    </div>
  )
}

/** This host's own result in a schedule's last `n` runs, oldest left. */
function Squares({ history, n = 10 }: { history: HostRunEntry[]; n?: number }) {
  const shown = history.slice(0, n).reverse()
  return (
    <span className="inline-flex shrink-0 gap-[3px]" aria-label={`last ${shown.length} runs on this host`}>
      {Array.from({ length: n - shown.length }, (_, i) => (
        <i key={`p${i}`} className="block h-[11px] w-[9px] rounded-[2px] border border-line-faint" />
      ))}
      {shown.map((r) => (
        <i
          key={r.host_run_id}
          className="block h-[11px] w-[9px] rounded-[2px]"
          style={{ background: toneVar(hostRunTone(r)) }}
          title={`${hostRunWord(r)} · ${shortAgo(r.created_at)} ago${durationMs(r) != null ? ` · took ${durationLabel(durationMs(r))}` : ""}`}
        />
      ))}
    </span>
  )
}

const COLS = "minmax(150px,1.3fr) minmax(130px,1.1fr) minmax(100px,0.9fr) 136px minmax(130px,1fr) 70px"
const LEGEND: [Tone, string][] = [
  ["ok", "succeeded here"],
  ["danger", "failed here"],
  ["hold", "rolled back to snapshot"],
  ["sync", "running"],
  ["idle", "cancelled"],
]

/**
 * Every schedule that reaches this host, directly or through a group, with
 * this host's own result in its last ten runs. A group run that was
 * "partial" reads green here when this host was fine.
 */
export function ScheduledRunsPanel({ schedules, hostId, hostname, loading, onAll }: { schedules: HostSchedule[]; hostId: number; hostname: string; loading: boolean; onAll: () => void }) {
  const failing = schedules.filter((h) => h.streak > 0).length
  return (
    <Panel
      title="scheduled runs"
      meta={schedules.length ? `${plural(schedules.length, "schedule")} ${schedules.length === 1 ? "reaches" : "reach"} this host${failing ? ` · ${failing} failing` : ""}` : undefined}
      actions={
        <button type="button" className="btn btn-sm btn-ghost" onClick={onAll}>
          all schedules →
        </button>
      }
    >
      {schedules.length === 0 ? (
        <div className="px-[11px] py-2.5 text-[11.5px] text-text-3">
          {loading ? "Loading…" : "No schedule reaches this host, directly or through its groups. Schedules are actions with a cron, created from the Activity tab or a group."}
        </div>
      ) : (
        <div className="overflow-x-auto" data-testid="host-schedules">
          <div className="min-w-[760px]">
            <div className="tt grid gap-2.5 border-b border-line px-[11px] py-1.5 text-[8.5px]" style={{ gridTemplateColumns: COLS }}>
              <span>schedule</span>
              <span>reaches it via</span>
              <span>when</span>
              <span>last 10, this host</span>
              <span>last run here</span>
              <span className="text-right">next</span>
            </div>
            {schedules.map((h) => (
              <ScheduleRow key={h.s.id} h={h} hostId={hostId} hostname={hostname} />
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-line-faint px-[11px] py-2 text-[10.5px] text-text-3">
        {LEGEND.map(([t, l]) => (
          <span key={l} className="inline-flex items-center gap-1.5">
            <i className="block h-[9px] w-[8px] rounded-[2px]" style={{ background: toneVar(t) }} />
            {l}
          </span>
        ))}
        <span className="text-text-faint">oldest left · each square is this host&apos;s own result</span>
      </div>
    </Panel>
  )
}

function ScheduleRow({ h, hostId, hostname }: { h: HostSchedule; hostId: number; hostname: string }) {
  const { s, last, streak, via, viaGroupId } = h
  const orphan = isOrphaned(s)
  const bad = streak > 0
  const tone = last ? hostRunTone(last) : "idle"
  return (
    <div
      className="border-b border-line-faint"
      style={{
        boxShadow: bad ? `inset 2px 0 0 ${toneVar("danger")}` : undefined,
        opacity: s.enabled ? 1 : 0.62,
      }}
    >
      <div className="grid items-center gap-2.5 px-[11px] py-[7px] text-[11.5px]" style={{ gridTemplateColumns: COLS }}>
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="trunc font-medium text-text">{scheduleName(s)}</span>
          {s.destructive && s.snapshot_enabled && (
            <Tag tone="ok" title="Proxmox snapshot before running">
              snapshot
            </Tag>
          )}
          {!s.enabled && <Tag>paused</Tag>}
          {orphan && (
            <Tag tone="danger" title="No enabled pack provides this action, so the scheduler skips it">
              action missing
            </Tag>
          )}
        </span>
        <span className="trunc text-text-3">
          {viaGroupId != null ? (
            <>
              group{" "}
              <Link href={`/groups/${viaGroupId}`} className="mono text-ld-accent hover:no-underline">
                {via.replace(/^group /, "")}
              </Link>
            </>
          ) : (
            via
          )}
        </span>
        <span className="trunc text-text-2" title={s.schedule_cron ?? undefined}>
          {s.schedule_cron ? cronToHuman(s.schedule_cron) : "manual"}
        </span>
        <Squares history={h.history} />
        {last ? (
          <Link href={runHref(hostId, last)} className="inline-flex min-w-0 items-center gap-1.5 hover:no-underline" style={{ color: toneVar(tone) }}>
            <Dot tone={tone} />
            <span className="trunc">
              {hostRunWord(last)} {shortAgo(last.created_at)} ago
              {durationMs(last) != null && <span className="text-text-faint"> · {durationLabel(durationMs(last))}</span>}
            </span>
          </Link>
        ) : (
          <span className="text-text-faint">{h.history.length ? "running" : "never ran here"}</span>
        )}
        {orphan && s.enabled ? (
          <span className="text-right text-[11px] text-danger-ink" title="The scheduler skips a schedule whose action no pack provides">
            skipped
          </span>
        ) : (
          <span className="mono num text-right text-[11px] text-text-2">{s.enabled && s.next_run_at ? untilLabel(s.next_run_at) : "—"}</span>
        )}
      </div>
      {bad && last && (
        <div className="mx-[11px] mb-2 flex flex-wrap items-center gap-2.5 rounded-r border border-line bg-surface-2 px-[9px] py-1.5 text-[11px] text-text-2">
          <span className="mono min-w-0 flex-1" style={{ overflowWrap: "anywhere" }}>
            {hostRunReason(last)}
          </span>
          <RunButtons hostId={hostId} hostname={hostname} r={last} />
        </div>
      )}
    </div>
  )
}
