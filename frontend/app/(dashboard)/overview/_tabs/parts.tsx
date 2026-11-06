"use client"

import { useEffect, useRef, useState, type ReactNode } from "react"
import { InvestigateRunButton } from "@/components/investigate-run-button"
import { Confirm, Dot, Tag, type Tone } from "@/components/ld"
import { toneVar } from "@/components/ld/tone"
import type { FeedItem } from "@/lib/activity"
import { apiFetch } from "@/lib/api"
import { shortAgo } from "@/lib/fleet"
import { useApiMutation } from "@/lib/mutations"
import { clock, durationLabel, durationMs, outcomeTone, type Bucket, type Fire } from "@/lib/overview"
import type { PendingItem } from "@/lib/pending"
import type { ScheduledActionRunSummary } from "@/lib/types"

/* ── small pieces shared by the Overview tabs ───────────────────── */

export const muted = "px-[11px] py-2 text-[11.5px] text-text-3"

/** One number with its reason under it. A button: every tile opens the
 *  tab that explains it. */
export function Tile({ label, value, tone, sub, onClick, children }: { label: string; value: ReactNode; tone?: Tone; sub?: ReactNode; onClick?: () => void; children?: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="row-hover flex min-w-0 flex-col gap-[3px] rounded-r border border-line bg-surface px-[11px] py-[9px] text-left"
    >
      <span className="tt text-[8.5px]">{label}</span>
      <span className="mono num text-[19px] font-semibold leading-tight" style={{ color: tone ? toneVar(tone) : "var(--text)" }}>
        {value}
      </span>
      {children}
      {sub && <span className="trunc w-full text-[10.5px] text-text-3">{sub}</span>}
    </button>
  )
}

/** A schedule's last runs as squares, oldest left, newest right. */
export function History({ runs, n = 8 }: { runs: ScheduledActionRunSummary[] | undefined; n?: number }) {
  const shown = [...(runs ?? []).slice(0, n)].reverse()
  const pad = n - shown.length
  return (
    <span className="inline-flex shrink-0 gap-[2px]" aria-label={`last ${shown.length} runs`}>
      {Array.from({ length: pad }, (_, i) => (
        <i key={`p${i}`} className="block h-[11px] w-[7px] rounded-[1.5px] border border-line-faint" />
      ))}
      {shown.map((r) => (
        <i
          key={r.id}
          className="block h-[11px] w-[7px] rounded-[1.5px]"
          style={{ background: toneVar(outcomeTone(r.status)) }}
          title={`${r.status} · ${shortAgo(r.created_at)} ago${durationMs(r) != null ? ` · took ${durationLabel(durationMs(r))}` : ""}`}
        />
      ))}
    </span>
  )
}

export function HistoryLegend() {
  const items: [Tone, string][] = [
    ["ok", "ok on every host"],
    ["warn", "partial"],
    ["danger", "failed"],
    ["sync", "running"],
    ["idle", "cancelled"],
  ]
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 px-[11px] py-2 text-[10.5px] text-text-3">
      {items.map(([t, l]) => (
        <span key={l} className="inline-flex items-center gap-1.5">
          <i className="block h-[9px] w-[6px] rounded-[1.5px]" style={{ background: toneVar(t) }} />
          {l}
        </span>
      ))}
    </div>
  )
}

/** Stacked bars per time bucket: ok, failed, running. */
export function BucketStrip({
  data,
  h = 38,
  active,
  onPick,
  label,
}: {
  data: Bucket[]
  h?: number
  active?: number | null
  onPick?: (start: number | null) => void
  label: (b: Bucket) => string
}) {
  const max = Math.max(1, ...data.map((b) => b.ok + b.failed + b.running))
  return (
    <div className="grid items-end gap-[2px]" style={{ gridTemplateColumns: `repeat(${data.length}, minmax(0, 1fr))`, height: h }}>
      {data.map((b) => {
        const total = b.ok + b.failed + b.running
        const on = active === b.start
        const dim = active != null && !on
        const seg = (n: number, tone: Tone) => (n ? <i className="block rounded-[1px]" style={{ height: `${(n / max) * 100}%`, background: toneVar(tone) }} /> : null)
        return (
          <button
            key={b.start}
            type="button"
            disabled={!onPick}
            onClick={() => onPick?.(on ? null : b.start)}
            title={`${label(b)} · ${b.ok} ok${b.failed ? ` · ${b.failed} failed` : ""}${b.running ? ` · ${b.running} running` : ""}`}
            aria-label={`${label(b)}: ${total} runs, ${b.failed} failed`}
            aria-pressed={onPick ? on : undefined}
            className="flex h-full flex-col-reverse gap-px border-0 bg-transparent p-0"
            style={{ opacity: dim ? 0.35 : 1, cursor: onPick ? "pointer" : "default" }}
          >
            {total === 0 ? <i className="block h-[2px] bg-line-strong" /> : null}
            {seg(b.ok, "ok")}
            {seg(b.failed, "danger")}
            {seg(b.running, "sync")}
          </button>
        )
      })}
    </div>
  )
}

export function Axis({ labels }: { labels: string[] }) {
  return (
    <div className="mono mt-1 flex justify-between text-[9.5px] text-text-faint">
      {labels.map((l, i) => (
        <span key={i}>{l}</span>
      ))}
    </div>
  )
}

/** A horizontal bar split into tone segments. */
export function StackBar({ parts, total, h = 7 }: { parts: { n: number; tone: Tone; label: string }[]; total?: number; h?: number }) {
  const sum = total ?? parts.reduce((a, p) => a + p.n, 0)
  return (
    <span className="flex min-w-0 flex-1 overflow-hidden bg-surface-3" style={{ height: h, borderRadius: 3 }} title={parts.filter((p) => p.n).map((p) => `${p.n} ${p.label}`).join(" · ")}>
      {sum > 0 &&
        parts.map((p) =>
          p.n ? <i key={p.label} className="block h-full" style={{ width: `${(p.n / sum) * 100}%`, background: toneVar(p.tone) }} /> : null,
        )}
    </span>
  )
}

/** "12 / 18" over a bar, and who is left out. */
export function Fraction({ label, n, total, note, href, go }: { label: string; n: number; total: number; note: string; href?: string; go: (href: string) => void }) {
  const pct = total ? (n / total) * 100 : 0
  const tone: Tone = total === 0 ? "idle" : n === total ? "ok" : pct >= 50 ? "warn" : "danger"
  return (
    <button
      type="button"
      onClick={() => href && go(href)}
      disabled={!href}
      className="row-hover flex min-w-0 flex-col gap-1 rounded-r border border-line bg-surface px-[11px] py-[9px] text-left disabled:cursor-default"
    >
      <span className="tt text-[8.5px]">{label}</span>
      <span className="mono num text-[15px] font-semibold leading-none" style={{ color: toneVar(tone) }}>
        {n}
        <span className="text-[10.5px] font-normal text-text-faint"> / {total}</span>
      </span>
      <span className="block h-[5px] w-full overflow-hidden rounded-[3px] bg-surface-3">
        <i className="block h-full" style={{ width: `${pct}%`, background: toneVar(tone) }} />
      </span>
      <span className="trunc w-full text-[10.5px] text-text-3" title={note}>
        {note}
      </span>
    </button>
  )
}

/** The next 24 hours: one mark per run, taller when it reaches more hosts.
 *  Each schedule is named once, at its first run, on the lowest of three
 *  label rows where the name fits. */
export function Next24({ fires, fleet, now }: { fires: Fire[]; fleet: number; now: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(400)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const span = 24 * 3_600_000
  const rowsEnd = [-Infinity, -Infinity, -Infinity]
  const labels = new Map<string, { row: number; x: number }>()
  const named = new Set<number>()
  for (const f of fires) {
    if (named.has(f.schedule.id)) continue
    named.add(f.schedule.id)
    const text = f.schedule.action_name ?? f.schedule.action_key
    const w = text.length * 6.1 + 10
    const x = Math.min(Math.max(((f.at - now) / span) * width, w / 2), width - w / 2)
    const row = rowsEnd.findIndex((end) => end < x - w / 2)
    if (row < 0) continue
    rowsEnd[row] = x + w / 2
    labels.set(`${f.schedule.id}:${f.at}`, { row, x })
  }
  return (
    <div className="px-[11px] pb-2 pt-12">
      <div ref={ref} className="relative h-[46px] border-b border-line">
        {fires.map((f) => {
          const key = `${f.schedule.id}:${f.at}`
          const left = ((f.at - now) / span) * 100
          const ht = Math.max(6, (f.hosts / Math.max(fleet, 1)) * 40)
          const big = fleet > 1 && f.hosts > fleet / 2
          const label = labels.get(key)
          return (
            <span key={key}>
              <span
                className="absolute bottom-0 w-[3px] rounded-t-[1px]"
                style={{ left: `${left}%`, marginLeft: -1.5, height: ht, background: big ? "var(--accent)" : "var(--text-2)" }}
                title={`${clock(f.at)} · ${f.schedule.action_name ?? f.schedule.action_key} · ${f.hosts} host${f.hosts === 1 ? "" : "s"}`}
              />
              {label && (
                <span
                  className="absolute whitespace-nowrap text-[10.5px] text-text-2"
                  style={{ left: label.x, transform: "translateX(-50%)", bottom: `calc(100% + ${4 + label.row * 13}px)` }}
                >
                  {f.schedule.action_name ?? f.schedule.action_key}
                </span>
              )}
            </span>
          )
        })}
      </div>
      <Axis labels={["now", "+6h", "+12h", "+18h", "+24h"]} />
    </div>
  )
}

/* ── rows ───────────────────────────────────────────────────────── */

const SEVERITY_TONE: Record<PendingItem["severity"], Tone> = { fail: "danger", block: "hold", warn: "warn", info: "idle" }
const KIND_TONE: Partial<Record<PendingItem["lane"], Tone>> = { approvals: "hold", failed: "danger", stuck: "warn" }

function CancelRun({ id, label }: { id: number; label: string }) {
  const [open, setOpen] = useState(false)
  const cancel = useApiMutation({
    mutationFn: () => apiFetch(`/api/actions/runs/${id}/cancel`, { method: "POST" }),
    invalidateKeys: [["dashboard", "stuck-operations"], ["action-runs"]],
    successMessage: "Cancel requested",
    onSuccess: () => setOpen(false),
  })
  return (
    <>
      <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOpen(true)}>
        Cancel
      </button>
      <Confirm
        open={open}
        onOpenChange={setOpen}
        title={`Cancel ${label}?`}
        description="Hosts it has not started are cancelled now. A host that is running finishes its playbook first, so the run can take a while to end."
        confirmLabel="Cancel run"
        cancelLabel="Keep running"
        variant="destructive"
        loading={cancel.isPending}
        onConfirm={() => cancel.mutate()}
      />
    </>
  )
}

export function PendRow({ p, go, onDismiss }: { p: PendingItem; go: (href: string) => void; onDismiss: (id: string) => void }) {
  const ff = p.run?.first_failure
  return (
    <div
      className="row-hover flex items-start gap-[11px] border-b border-line-faint px-[11px] py-[9px]"
      style={{ boxShadow: p.severity === "fail" ? `inset 2px 0 0 ${toneVar("danger")}` : undefined }}
    >
      <div className="mt-1">
        <Dot tone={SEVERITY_TONE[p.severity]} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="tt" style={{ color: KIND_TONE[p.lane] ? toneVar(KIND_TONE[p.lane]) : "var(--text-3)" }}>
            {p.kind}
          </span>
          <span className="mono num text-[10px]" style={{ color: p.expires === "firing" ? "var(--danger)" : "var(--text-faint)" }}>
            {p.expires}
          </span>
        </div>
        <div className="trunc text-[12.5px] font-medium text-text" title={p.title}>
          {p.title}
        </div>
        <div className="mono trunc text-[10.5px]" style={{ color: p.severity === "fail" ? "var(--danger-ink)" : "var(--text-3)" }} title={p.detail}>
          {p.detail}
        </div>
        {p.left !== undefined && (
          <span className="mt-0.5 block h-[3px] w-40 max-w-full overflow-hidden rounded-[2px] bg-surface-3" title={`${Math.round(p.left * 100)}% of its time left`}>
            <i className="block h-full" style={{ width: `${p.left * 100}%`, background: toneVar(p.left < 0.25 ? "danger" : p.left < 0.5 ? "warn" : "hold") }} />
          </span>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
        <button type="button" className="btn btn-sm" onClick={() => go(p.href)}>
          {p.open}
        </button>
        {p.run && ff && <InvestigateRunButton run={p.run} hostRun={{ id: ff.host_run_id, host_id: ff.host_id, hostname: ff.hostname }} ghost />}
        {p.stuck?.kind === "action" && <CancelRun id={p.stuck.id} label={p.stuck.label} />}
        {p.dismissible && (
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => onDismiss(p.id)} title="Hide from this browser's queue — it stays in history">
            Hide
          </button>
        )}
      </div>
    </div>
  )
}

const ACT_TONE: Record<FeedItem["status"], Tone | undefined> = {
  failed: "danger",
  running: "sync",
  queued: "idle",
  cancelled: "idle",
  ok: undefined,
}

const WHO: Record<FeedItem["who"], string> = { schedule: "schedule", alert: "alert", user: "user", system: "system" }

export function FeedRow({ a, go, who }: { a: FeedItem; go: (href: string) => void; who?: string }) {
  const ff = a.run?.first_failure
  const failed = a.status === "failed"
  return (
    <div className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]" style={{ boxShadow: failed ? "inset 2px 0 0 var(--danger)" : undefined }}>
      <span className="mono num w-8 shrink-0 text-[10.5px] text-text-3" title={new Date(a.at).toLocaleString()}>
        {shortAgo(a.at)}
      </span>
      <Tag tone={a.kind === "alert" && a.status === "running" ? "warn" : ACT_TONE[a.status]}>{a.kind === "ai" ? "AI" : a.kind}</Tag>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="trunc text-xs text-text">{a.title}</span>
          {a.tally && (
            <span className="mono num shrink-0 text-[10.5px]" style={{ color: a.tally.includes("failed") ? "var(--danger-ink)" : "var(--text-3)" }}>
              {a.tally}
            </span>
          )}
        </div>
        {a.detail && (
          <div className="mono trunc text-[10.5px]" style={{ color: failed ? "var(--danger-ink)" : "var(--text-3)" }} title={a.detail}>
            {a.detail}
          </div>
        )}
      </div>
      {a.durationMs != null && (
        <span className="mono num hidden shrink-0 text-[10.5px] text-text-3 sm:inline" title="how long it took">
          {durationLabel(a.durationMs)}
        </span>
      )}
      <span className="tt shrink-0 text-[9px]">{who ?? WHO[a.who]}</span>
      {a.run && ff && failed && <InvestigateRunButton run={a.run} hostRun={{ id: ff.host_run_id, host_id: ff.host_id, hostname: ff.hostname }} ghost />}
      {a.href ? (
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => go(a.href!)}>
          open →
        </button>
      ) : a.hostId != null ? (
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => go(`/hosts/${a.hostId}`)}>
          host →
        </button>
      ) : null}
    </div>
  )
}
