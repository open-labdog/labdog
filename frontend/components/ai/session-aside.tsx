"use client"

import { useState, type ReactNode } from "react"

import { Dot, Facts, Field, Meter, Seg, Tag, toneVar, type Tone } from "@/components/ld"
import { money } from "@/components/ai/usage-panel"
import { AUTONOMY_HELP, describeScope } from "@/components/ai/session-meta"
import { AI_AUTONOMY, AI_CLASSIFICATION, def } from "@/lib/status"
import { formatTimestamp } from "@/lib/utils"
import type { AIAutonomyLevel, AIProvider, AISessionDetail, AIUsageSummary, Host } from "@/lib/types"

/**
 * The assistant's right column, as the design draws it: a stack of small
 * captioned sections. Before a session starts it holds the choices that
 * shape it; once one is open it holds what the session is and what it
 * has done. The budget sits under both.
 */

function Section({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="tt">{label}</span>
      {children}
    </div>
  )
}

const note = "text-[11px] leading-[1.5] text-text-3"

const AUTONOMY_OPTIONS: { k: AIAutonomyLevel; label: string; title: string }[] = [
  { k: "read_only", label: "Read-only", title: "read-only" },
  { k: "approval", label: "Approval", title: "approval required" },
  { k: "full_auto", label: "Full auto", title: "full auto" },
]

interface NewSessionProps {
  /** Enabled providers that can run tools — the only ones worth offering. */
  usable: AIProvider[]
  /** Enabled providers that cannot, named so their absence is explained. */
  toolless: AIProvider[]
  providerId: number | null
  onProvider: (id: number | null) => void
  autonomy: AIAutonomyLevel
  onAutonomy: (level: AIAutonomyLevel) => void
  skipSnapshots: boolean
  onSkipSnapshots: (skip: boolean) => void
  hosts: Host[] | undefined
  targetHosts: number[]
  onToggleHost: (id: number) => void
}

/** Everything a session is started with. None of it can change later. */
export function NewSessionOptions({
  usable,
  toolless,
  providerId,
  onProvider,
  autonomy,
  onAutonomy,
  skipSnapshots,
  onSkipSnapshots,
  hosts,
  targetHosts,
  onToggleHost,
}: NewSessionProps) {
  const [q, setQ] = useState("")
  const ql = q.trim().toLowerCase()
  const all = hosts ?? []
  const shown = ql ? all.filter((h) => h.hostname.toLowerCase().includes(ql) || h.ip_address.includes(ql)) : all
  const defaultProvider = usable.find((p) => p.is_default)

  return (
    <>
      <Field label="provider" htmlFor="ai-provider">
        {/* The empty value is "no explicit choice — let the backend pick
            its default", which a native select can spell directly. */}
        <select
          id="ai-provider"
          className="inp"
          value={providerId === null ? "" : String(providerId)}
          onChange={(e) => onProvider(e.target.value === "" ? null : Number(e.target.value))}
        >
          <option value="">{defaultProvider ? `Default (${defaultProvider.name})` : "Default"}</option>
          {usable.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </Field>
      {/* A backend with no tools cannot look anything up, so a session on
          one is refused rather than answered from imagination. Saying so
          here beats letting the operator discover it as a failed session. */}
      {toolless.length > 0 && (
        <p className={`m-0 -mt-2 ${note}`}>
          {toolless.map((p) => p.name).join(", ")} {toolless.length === 1 ? "is" : "are"} not listed — a single-shot backend
          cannot run tools, so it cannot investigate anything.
        </p>
      )}

      <Section label="autonomy">
        <Seg sm value={autonomy} onChange={(k) => onAutonomy(k as AIAutonomyLevel)} options={AUTONOMY_OPTIONS} />
        <span className={note}>{AUTONOMY_HELP[autonomy]}</span>
      </Section>

      {/* Only shown above read-only, where there is a change to snapshot.
          Offering it on a session that cannot change anything would be a
          control with no effect. */}
      {autonomy !== "read_only" && (
        <Section label="snapshots">
          <label className="flex items-center gap-2 text-xs text-text">
            <input type="checkbox" checked={skipSnapshots} onChange={(e) => onSkipSnapshots(e.target.checked)} />
            skip snapshots
          </label>
          <span className={note}>
            {skipSnapshots
              ? "Changes will be made with no rollback point. Faster, and undoing anything is then your problem."
              : "A Proxmox snapshot is taken before each change, on hosts that map to a VM, so it can be rolled back."}
          </span>
        </Section>
      )}

      <Field as="div" label="hosts in scope" hint={`${targetHosts.length} selected`}>
        <div className="rounded-r border border-line bg-surface-2">
          {all.length > 6 && (
            <div className="border-b border-line-faint p-[7px]">
              <input className="inp" aria-label="filter hosts" placeholder="filter hosts…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          )}
          <div className="scroll max-h-[200px] p-1">
            {all.length === 0 && <div className="px-[5px] py-1.5 text-[11.5px] text-text-3">No hosts registered.</div>}
            {all.length > 0 && shown.length === 0 && <div className="px-[5px] py-1.5 text-[11.5px] text-text-3">No hosts match.</div>}
            {shown.map((h) => (
              <label key={h.id} className="flex cursor-pointer items-center gap-2 rounded px-[5px] py-[3px]">
                <input type="checkbox" checked={targetHosts.includes(h.id)} onChange={() => onToggleHost(h.id)} />
                <span className="mono trunc flex-1 text-[11.5px] text-text">{h.hostname}</span>
                <span className="mono text-[10.5px] text-text-faint">{h.ip_address}</span>
              </label>
            ))}
          </div>
        </div>
        <span className={note}>The assistant cannot touch anything outside this list.</span>
      </Field>
    </>
  )
}

/** What the open session is, and what it has done so far. */
export function SessionSummary({
  session,
  hostNames,
  providerName,
  currency,
}: {
  session: AISessionDetail
  hostNames: (ids: number[] | null | undefined) => string[]
  providerName?: string
  currency: string
}) {
  const level = def(AI_AUTONOMY, session.autonomy_level)
  const scope = hostNames(session.target_host_ids)

  // By verdict, the way the transcript paints them — "did it try to
  // change anything?" answered without scrolling.
  const byClass = new Map<string, number>()
  for (const c of session.tool_calls) byClass.set(c.classification, (byClass.get(c.classification) ?? 0) + 1)
  const classes = ["read_only", "mutating", "denied", ...(byClass.has("unknown") ? ["unknown"] : [])]

  const snapshots = session.tool_calls.filter((c) => c.snapshot_name)

  return (
    <>
      <Section label="autonomy">
        <span>
          <Tag tone={level.tone}>{level.label}</Tag>
        </span>
        <span className={note}>{AUTONOMY_HELP[session.autonomy_level]}</span>
      </Section>

      <Section label="hosts in scope">
        <span className="mono text-[11.5px] text-text-2" title={scope.join(", ") || undefined}>
          {describeScope(scope)}
        </span>
      </Section>

      <Facts
        min={100}
        items={[
          { k: "turns", v: session.iterations, mono: true },
          { k: "commands", v: session.command_count, mono: true },
          { k: "tokens", v: (session.prompt_tokens + session.completion_tokens).toLocaleString(), mono: true },
          { k: "cost", v: session.cost_unknown ? "not reported" : money(session.cost, currency, 4), mono: true },
          ...(providerName ? [{ k: "provider", v: providerName, span: 2 }] : []),
          ...(session.finished_at ? [{ k: "finished", v: formatTimestamp(session.finished_at), span: 2, title: session.finished_at }] : []),
        ]}
      />

      <Section label="tool calls by verdict">
        {classes.map((k) => {
          const d = def(AI_CLASSIFICATION, k)
          const n = byClass.get(k) ?? 0
          return (
            <div key={k} className="flex items-center gap-2">
              <Dot tone={d.tone} />
              <span className="flex-1 text-[11.5px] text-text-2">{d.label}</span>
              <span className="mono num text-[11.5px]" style={{ color: n > 0 ? toneVar(d.tone) : "var(--text-faint)" }}>
                {n}
              </span>
            </div>
          )
        })}
      </Section>

      {session.autonomy_level !== "read_only" && (
        <div className="flex flex-col gap-[7px] rounded-r border border-line bg-surface-2 p-2.5">
          <span className="tt">safety net</span>
          {session.skip_snapshots ? (
            <span className="flex items-center gap-2 text-[11.5px] text-text">
              <Dot tone="warn" /> snapshots skipped for this session
            </span>
          ) : snapshots.length === 0 ? (
            <span className={note}>No snapshot taken yet. One is taken before each change, on hosts that map to a Proxmox VM.</span>
          ) : (
            snapshots.map((c) => (
              <span key={c.id} className="flex items-center gap-2 text-[11.5px] text-text" title={c.snapshot_pruned_at ? `removed by retention ${c.snapshot_pruned_at}` : undefined}>
                <Dot tone={c.snapshot_pruned_at ? "idle" : "ok"} />
                <span className="mono trunc">{c.snapshot_name}</span>
                {c.snapshot_pruned_at && <Tag>pruned</Tag>}
              </span>
            ))
          )}
        </div>
      )}
    </>
  )
}

function Budget({ label, spend, limit, warnPct, currency }: { label: string; spend: number; limit: number; warnPct: number; currency: string }) {
  // A zero limit means unlimited — an empty bar would imply a cap that
  // does not exist.
  if (!limit) {
    return (
      <div className="flex justify-between gap-2">
        <span className="tt">{label}</span>
        <span className="mono num text-[11px] text-text">{money(spend, currency)} · no limit</span>
      </div>
    )
  }
  const pct = Math.min(spend / limit, 1) * 100
  const tone: Tone = pct >= 100 ? "danger" : pct >= warnPct ? "warn" : "ok"
  return <Meter label={label} pct={pct} tone={tone} value={`${money(spend, currency)} / ${money(limit, currency)}`} h={6} />
}

/** Spend against the fleet-wide budgets — the design's burn-down. */
export function BudgetMeters({ usage }: { usage: AIUsageSummary | undefined }) {
  if (!usage) return null
  const currency = usage.currency || "USD"
  return (
    <Section label="budget">
      <Budget label="spend today" spend={usage.day_spend} limit={usage.day_limit} warnPct={usage.warn_pct} currency={currency} />
      <Budget label="this month" spend={usage.month_spend} limit={usage.month_limit} warnPct={usage.warn_pct} currency={currency} />
      {usage.exceeded && <span className="text-[11px] leading-[1.5] text-danger">{usage.reason} New sessions are refused until spend falls below the limit.</span>}
      <span className="text-[10.5px] leading-[1.45] text-text-3">
        Budgets are fleet-wide. Each session also stops at the turn, command, token and time caps under Settings → AI.
      </span>
    </Section>
  )
}
