"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { InvestigateButton } from "@/components/investigate-button"
import { Dot, Panel } from "@/components/ld"
import { toneVar } from "@/components/ld/tone"
import { plural, shortAgo, type Tone } from "@/lib/fleet"
import { MODULES, type ModuleDef } from "@/lib/modules"
import type { FeedItem, ActivityStatus } from "@/lib/activity"
import type { AlertEvent, GroupSummary, HostGroup, ModuleCounts } from "@/lib/types"

const STATUS_TONE: Record<ActivityStatus, Tone> = {
  ok: "ok",
  failed: "danger",
  running: "sync",
  queued: "idle",
  cancelled: "idle",
}
const STATUS_WORD: Record<ActivityStatus, string> = {
  ok: "ok",
  failed: "failed",
  running: "running",
  queued: "queued",
  cancelled: "cancelled",
}
const KIND_WORD: Record<string, string> = {
  apply: "sync",
  action: "manual",
  schedule: "schedule",
  collect: "collect",
  drift: "drift",
  alert: "alert",
  ai: "AI",
}

/** Runs, applies, alerts and assistant sessions on this host, newest first. */
export function HostActivityPanel({ items, loading, onAll }: { items: FeedItem[]; loading: boolean; onAll: () => void }) {
  const shown = items.slice(0, 6)
  return (
    <Panel
      title="recent activity"
      meta="runs, syncs, alerts and AI"
      actions={
        <button type="button" className="btn btn-sm btn-ghost" onClick={onAll}>
          all activity →
        </button>
      }
      footer={<span className="text-[10.5px] text-text-faint">Routine drift checks and collections that worked stay out of this list.</span>}
    >
      {shown.length === 0 ? (
        <div className="px-[11px] py-2.5 text-[11.5px] text-text-3">{loading ? "Loading…" : "Nothing has run on this host yet."}</div>
      ) : (
        shown.map((a) => {
          const alertFiring = a.kind === "alert" && a.status === "running"
          const tone: Tone = alertFiring ? "danger" : STATUS_TONE[a.status]
          const word = a.kind === "alert" ? (alertFiring ? "firing" : "resolved") : a.kind === "ai" && a.status === "ok" ? "done" : STATUS_WORD[a.status]
          const row = (
            <>
              <span className="mono num w-8 shrink-0 text-[10.5px] text-text-faint" title={new Date(a.at).toLocaleString()}>
                {shortAgo(a.at)}
              </span>
              <span className="tt w-[58px] shrink-0 text-[8.5px]">{KIND_WORD[a.kind] ?? a.kind}</span>
              <span className="min-w-0 flex-1">
                <span className="trunc block text-xs text-text">{a.title}</span>
                {a.detail && a.status === "failed" && (
                  <span className="mono trunc block text-[10.5px] text-danger-ink" title={a.detail}>
                    {a.detail}
                  </span>
                )}
              </span>
              <span className="inline-flex shrink-0 items-center gap-1.5 text-[11px]" style={{ color: toneVar(tone) }}>
                <Dot tone={tone} />
                {word}
              </span>
            </>
          )
          const cls = "flex items-center gap-2.5 border-b border-line-faint px-[11px] py-1.5"
          // A failed sync has no page of its own; the assistant can look into it.
          if (a.kind === "apply" && a.status === "failed" && a.syncJobId != null) {
            return (
              <div key={a.id} className={cls} style={{ boxShadow: "inset 2px 0 0 var(--danger)" }}>
                {row}
                <InvestigateButton
                  icon
                  ghost
                  label="Investigate this sync with AI"
                  subject={{ kind: "sync", sync_job_id: a.syncJobId }}
                  about="this failed sync"
                  sends="the sync's modules, its error and the failing part of its Ansible log"
                />
              </div>
            )
          }
          return a.href ? (
            <Link
              key={a.id}
              href={a.href}
              className={`row-hover text-text hover:no-underline ${cls}`}
              style={{
                boxShadow: a.status === "failed" ? "inset 2px 0 0 var(--danger)" : undefined,
              }}
            >
              {row}
            </Link>
          ) : (
            <div key={a.id} className={cls}>
              {row}
            </div>
          )
        })
      )}
    </Panel>
  )
}

/** Firing alerts on this host, then the last one that resolved. Hidden
 *  when no alert has ever been tied to it. */
export function HostAlertsPanel({ alerts }: { alerts: AlertEvent[] | undefined }) {
  if (!alerts || alerts.length === 0) return null
  const firing = alerts.filter((a) => a.status === "firing")
  const resolved = alerts.find((a) => a.status === "resolved")
  return (
    <Panel
      title="alerts"
      meta={firing.length ? `${firing.length} firing` : "none firing"}
      actions={
        <Link href="/alerts" className="btn btn-sm btn-ghost hover:no-underline">
          alerts →
        </Link>
      }
    >
      {firing.map((a) => (
        <Link
          key={a.id}
          href={a.investigation_session_id ? `/assistant?session=${a.investigation_session_id}` : "/alerts"}
          className="row-hover flex items-center gap-2.5 border-b border-line-faint px-[11px] py-1.5 text-text hover:no-underline"
        >
          <span className="trunc flex-1 text-xs">{a.alertname}</span>
          {a.severity && <span className="text-[10.5px] text-text-3">{a.severity}</span>}
          <span className="inline-flex shrink-0 items-center gap-1.5 text-[11px] text-danger-ink">
            <Dot tone="danger" />
            firing {shortAgo(a.starts_at)}
          </span>
        </Link>
      ))}
      {resolved && (
        <div className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-1.5 text-text-3">
          <span className="trunc flex-1 text-xs">{resolved.alertname}</span>
          <span className="shrink-0 text-[11px]">resolved {shortAgo(resolved.ends_at ?? resolved.starts_at)} ago</span>
        </div>
      )}
    </Panel>
  )
}

interface Layer {
  key: string
  label: React.ReactNode
  prio: string
  counts: ModuleCounts
  group?: HostGroup
}

const chipLabel = (m: ModuleDef) => m.label.toLowerCase().replace(" & ssh", "").replace("ca certificates", "ca certs").replace("dns resolver", "resolver")

/**
 * Where this host's config comes from, in merge order: its own overrides,
 * then each group from the highest priority down, each with the modules
 * it sets. A dashed chip is a module a higher layer also sets, which wins
 * any conflict there.
 */
export function ConfigLayersPanel({
  overrides,
  members,
  summaries,
  available,
  busy,
  onAdd,
  onRemove,
}: {
  overrides: ModuleCounts | undefined
  members: HostGroup[]
  summaries: GroupSummary[] | undefined
  available: HostGroup[]
  busy: boolean
  onAdd: (ids: number[]) => void
  onRemove: (id: number) => void
}) {
  const [picking, setPicking] = useState(false)
  const [q, setQ] = useState("")
  const ql = q.trim().toLowerCase()
  const filtered = ql ? available.filter((g) => g.name.toLowerCase().includes(ql) || (g.category ?? "").toLowerCase().includes(ql)) : available

  const layers = useMemo<Layer[]>(() => {
    const counts = new Map((summaries ?? []).map((s) => [s.id, s.module_counts]))
    const empty = {
      firewall: 0,
      hosts_file: 0,
      services: 0,
      users: 0,
      cron: 0,
      packages: 0,
      resolver: 0,
      ca_certs: 0,
    }
    return [
      {
        key: "host",
        label: "host overrides",
        prio: "—",
        counts: overrides ?? empty,
      },
      ...[...members]
        .sort((a, b) => b.priority - a.priority)
        .map((g) => ({
          key: `g${g.id}`,
          label: (
            <Link href={`/groups/${g.id}`} className="mono trunc text-ld-accent hover:no-underline">
              {g.name}
            </Link>
          ),
          prio: `p ${g.priority}`,
          counts: counts.get(g.id) ?? empty,
          group: g,
        })),
    ]
  }, [overrides, members, summaries])

  return (
    <Panel
      title="where its config comes from"
      meta="highest wins"
      actions={
        !picking &&
        available.length > 0 && (
          <button type="button" className="btn btn-sm" onClick={() => setPicking(true)}>
            + add to group
          </button>
        )
      }
    >
      {picking && (
        <div className="border-b border-line bg-surface-2">
          <div className="flex items-center gap-1.5 border-b border-line-faint p-[7px]">
            <input className="inp" autoFocus value={q} placeholder="filter groups…" onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} aria-label="filter groups to add" />
            <button type="button" className="btn btn-sm" disabled={!filtered.length || busy} onClick={() => onAdd(filtered.map((g) => g.id))}>
              add {filtered.length}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => {
                setPicking(false)
                setQ("")
              }}
            >
              done
            </button>
          </div>
          <div className="scroll max-h-[168px] p-1">
            {filtered.length === 0 && <div className="px-[5px] py-1.5 text-[11.5px] text-text-3">{available.length ? "No match." : "Already a member of every group."}</div>}
            {filtered.map((g) => (
              <label key={g.id} className="flex cursor-pointer items-center gap-2 rounded px-[5px] py-[3px]">
                <input type="checkbox" checked={false} disabled={busy} onChange={() => onAdd([g.id])} style={{ accentColor: "var(--accent)" }} aria-label={`add ${g.name}`} />
                <span className="flex-1 text-[11.5px]">{g.name}</span>
                <span className="text-[10.5px] text-text-faint">{g.category ?? ""}</span>
              </label>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-col gap-[5px] p-[9px]" data-testid="config-layers">
        {layers.map((l, i) => {
          const above = layers.slice(0, i)
          const mods = MODULES.filter((m) => l.counts[m.countKey] > 0)
          return (
            <div
              key={l.key}
              className="grid items-center gap-2 rounded-r border bg-surface-2 px-2 py-1.5 text-[11.5px]"
              style={{
                gridTemplateColumns: "minmax(64px,130px) 36px minmax(0,1fr) auto",
                borderColor: i === 0 && mods.length ? "var(--accent-line)" : "var(--border)",
              }}
            >
              <span className="trunc text-text-2">{l.label}</span>
              <span className="mono text-[10.5px] text-text-faint">{l.prio}</span>
              <span className="flex flex-wrap gap-1">
                {mods.length === 0 && <span className="text-[10.5px] text-text-faint">{l.group ? "sets nothing yet" : "none"}</span>}
                {mods.map((m) => {
                  const beaten = above.find((a) => a.counts[m.countKey] > 0)
                  return (
                    <span
                      key={m.id}
                      className="mono whitespace-nowrap rounded-[3px] px-[5px] text-[10.5px]"
                      style={
                        beaten
                          ? {
                              color: "var(--text-faint)",
                              border: "1px dashed var(--border-strong)",
                            }
                          : {
                              background: "var(--surface-3)",
                              color: "var(--text-2)",
                            }
                      }
                      title={
                        beaten ? `${beaten.key === "host" ? "a host override" : beaten.group?.name} also sets ${m.label.toLowerCase()} and wins any conflict` : `${l.counts[m.countKey]} ${m.unit}`
                      }
                    >
                      {chipLabel(m)} {l.counts[m.countKey]}
                    </span>
                  )
                })}
              </span>
              {l.group ? (
                <button
                  type="button"
                  className="btn btn-sm btn-ghost text-danger"
                  disabled={busy}
                  onClick={() => onRemove(l.group!.id)}
                  aria-label={`remove from ${l.group.name}`}
                  title={`Remove this host from ${l.group.name}`}
                >
                  remove
                </button>
              ) : (
                <span />
              )}
            </div>
          )
        })}
        {members.length === 0 && <span className="px-0.5 text-[11px] text-text-3">This host is not a member of any groups.</span>}
        <span className="px-0.5 text-[10.5px] text-text-faint">Dashed: a higher layer also sets this module and wins any conflict. {plural(members.length, "group")}.</span>
      </div>
    </Panel>
  )
}
