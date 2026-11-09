"use client"

import type { Dispatch, SetStateAction } from "react"
import { useRouter } from "next/navigation"
import { syncModuleLabel } from "@/lib/modules"
import type { Host } from "@/lib/types"
import { Dot, Empty, Panel, Tag, toneVar, type Tone } from "@/components/ld"
import { OP, moduleErrors, realChanges, type HostPlan, type Phase, type PlanBreakdown } from "../model"

/** Step 2: every host's dry-run diff, with the run's selection on the left edge. */
export function DiffPanel({
  phase,
  plans,
  plan,
  open,
  setOpen,
  sel,
  setSel,
  toggle,
}: {
  phase: Phase
  plans: HostPlan[]
  plan: PlanBreakdown
  open: Record<number, boolean>
  setOpen: Dispatch<SetStateAction<Record<number, boolean>>>
  sel: Set<number>
  setSel: Dispatch<SetStateAction<Set<number>>>
  toggle: (hostId: number) => void
}) {
  const router = useRouter()
  const { changing, targets, excluded, changeCount } = plan
  return (
    <Panel
      title="diff"
      meta={`${changeCount} change${changeCount === 1 ? "" : "s"} across ${changing.length} host${changing.length === 1 ? "" : "s"} · dry run`}
      actions={
        phase === "review" ? (
          <>
            <span className="tt" style={{ color: excluded ? "var(--warn)" : "var(--text-3)" }}>
              {targets.length} of {changing.length} selected to run
            </span>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSel(new Set(changing.map((p) => p.host.id)))}>
              all
            </button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSel(new Set())}>
              none
            </button>
            <span className="h-[13px] w-px bg-line" />
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOpen(Object.fromEntries(plans.map((p) => [p.host.id, true])))}>
              expand all
            </button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOpen({})}>
              collapse
            </button>
          </>
        ) : undefined
      }
    >
      {plans.length === 0 && <Empty title="Nothing in scope" note="The scope has no hosts." />}
      {plans.map((p) => {
        const o = !!open[p.host.id]
        const changes = realChanges(p.diffs)
        const modErrors = moduleErrors(p.diffs)
        const selectable = phase === "review" && !p.skipped && !p.error && changes.length > 0
        const on = sel.has(p.host.id) && changes.length > 0
        return (
          <div key={p.host.id} className="border-b border-line-faint" style={{ opacity: !p.skipped && !p.error && changes.length > 0 && !on ? 0.52 : 1 }}>
            <div className="row-hover flex items-center gap-[9px] px-[11px] py-[5px]" style={{ background: o ? "var(--surface-2)" : "transparent" }}>
              <input
                type="checkbox"
                checked={on}
                disabled={!selectable}
                onChange={() => toggle(p.host.id)}
                aria-label={`include ${p.host.hostname}`}
                style={{ accentColor: "var(--accent)", flexShrink: 0 }}
                title={selectable ? "include this host in the run" : p.skipped ? "cannot be reached — skipped, not failed" : p.error ? "preview failed" : "nothing to apply on this host"}
              />
              <button type="button" onClick={() => setOpen((s) => ({ ...s, [p.host.id]: !s[p.host.id] }))} className="flex min-w-0 flex-1 items-center gap-2.5 border-0 bg-transparent py-[3px] text-left">
                <span className="w-2 text-[9px] text-text-faint">{o ? "▼" : "▶"}</span>
                <span className="mono w-[130px] shrink-0 truncate text-[12.5px] font-medium text-text">{p.host.hostname}</span>
                {p.skipped ? (
                  <Tag tone="idle">skipped · {p.skipped}</Tag>
                ) : p.error ? (
                  <Tag tone="danger" title={p.error}>
                    preview failed
                  </Tag>
                ) : changes.length === 0 && modErrors.length > 0 ? (
                  <Tag tone="danger" title={modErrors.map((d) => `${d.module}: ${d.error}`).join("\n")}>
                    {modErrors.length} module error{modErrors.length === 1 ? "" : "s"}
                  </Tag>
                ) : changes.length === 0 ? (
                  <Tag tone="ok">no change</Tag>
                ) : (
                  <span className="flex gap-[5px]">
                    {(["add", "remove", "update"] as const).map((op) => {
                      const n = changes.filter((c) => c.op === op).length
                      return n ? (
                        <Tag key={op} mono tone={OP[op].tone}>
                          {OP[op].s}
                          {n}
                        </Tag>
                      ) : null
                    })}
                    {modErrors.length > 0 && (
                      <Tag tone="danger" title={modErrors.map((d) => `${d.module}: ${d.error}`).join("\n")}>
                        {modErrors.length} module error{modErrors.length === 1 ? "" : "s"}
                      </Tag>
                    )}
                  </span>
                )}
                <span className="tt ml-auto">
                  {selectable && !on ? (
                    <span className="text-warn">excluded</span>
                  ) : p.error ? (
                    p.error
                  ) : changes.length === 0 && modErrors.length > 0 ? (
                    <span className="text-danger">{`${modErrors[0].module}: ${modErrors[0].error}`}</span>
                  ) : (
                    `${changes.length} change${changes.length === 1 ? "" : "s"}`
                  )}
                </span>
              </button>
              <button type="button" className="btn btn-sm btn-ghost shrink-0 text-ld-accent" onClick={() => router.push(`/hosts/${p.host.id}`)}>
                open →
              </button>
            </div>
            {o && !p.skipped && (
              <div className="mono flex flex-col gap-0.5 py-1 pl-[30px] pr-[11px] pb-2.5 text-[11.5px]">
                {p.error && <div className="text-danger">{p.error}</div>}
                {(p.diffs ?? []).map((d) => (
                  <div key={d.module} className="flex flex-col gap-0.5">
                    <div className="mt-1 flex items-center gap-2">
                      <span className="tt">{syncModuleLabel(d.module)}</span>
                      {d.error && <span className="text-[11px] text-danger">{d.error}</span>}
                      {!d.error && !d.has_changes && <span className="text-[10.5px] text-text-faint">unchanged</span>}
                    </div>
                    {d.changes
                      .filter((c) => c.op !== "unchanged")
                      .map((c, i) => (
                        <div key={i} className="flex items-start gap-[9px] rounded-[3px] px-[7px] py-[3px]" style={{ background: OP[c.op].bg }}>
                          <span className="w-[9px] font-bold" style={{ color: toneVar(OP[c.op].tone) }}>
                            {OP[c.op].s}
                          </span>
                          <div className="min-w-0 flex-1 whitespace-pre-wrap break-words text-text">{c.summary}</div>
                        </div>
                      ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
      {excluded > 0 && phase === "review" && (
        <div className="flex items-center gap-[9px] bg-warn-soft px-[11px] py-2">
          <Dot tone="warn" />
          <span className="text-[11.5px] text-text">
            {excluded} host{excluded === 1 ? "" : "s"} excluded — they stay on their current state and keep this change pending. Apply to the rest first, then come back.
          </span>
        </div>
      )}
    </Panel>
  )
}

export interface Ack {
  k: string
  label: string
}

/**
 * The review's side panel: the selected run's blast radius, the pre-flight
 * checks, and (while reviewing) the acknowledgements that arm Apply.
 */
export function PlanSidebar({
  phase,
  plan,
  modules,
  scopeHosts,
  selSsh,
  pveInScope,
  unknownBackend,
  acks,
  ack,
  setAck,
  typed,
  setTyped,
  allAck,
  onApply,
}: {
  phase: Phase
  plan: PlanBreakdown
  modules: string[]
  scopeHosts: Host[]
  selSsh: boolean
  pveInScope: number
  unknownBackend: Host[]
  acks: Ack[]
  ack: Record<string, boolean>
  setAck: Dispatch<SetStateAction<Record<string, boolean>>>
  typed: string
  setTyped: (typed: string) => void
  allAck: boolean
  onApply: () => void
}) {
  const { changing, unchanged, skipped, errored, targets, excluded, selChanges } = plan
  return (
    <aside className="scroll flex w-[288px] shrink-0 flex-col gap-[13px] border-l border-line bg-surface p-[13px]">
      <div>
        <span className="tt">
          blast radius {excluded > 0 && <span className="text-warn">· this run only</span>}
        </span>
        <div className="mt-2 grid grid-cols-2 gap-[9px]">
          {(
            [
              ["hosts in this run", targets.length, "accent"],
              ["excluded", excluded, excluded ? "warn" : "idle"],
              ["changes", selChanges.length, "hold"],
              ["ssh-affecting", selSsh ? "yes" : "no", selSsh ? "warn" : "idle"],
            ] as [string, string | number, Tone][]
          ).map(([l, v, t]) => (
            <div key={l} className="flex flex-col gap-[3px] rounded-r border border-line bg-surface-2 p-[9px]">
              <span className="tt text-[8.5px]">{l}</span>
              <span className="mono num text-[17px] font-semibold" style={{ color: toneVar(t) }}>
                {v}
              </span>
            </div>
          ))}
        </div>
        <div className="mt-[7px] text-[11px] leading-[1.5] text-text-3">
          Plan covers {scopeHosts.length} host{scopeHosts.length === 1 ? "" : "s"}: {changing.length} changing, {unchanged.length} unchanged
          {skipped.length ? `, ${skipped.length} skipped (no SSH key)` : ""}
          {errored.length ? `, ${errored.length} with a preview error` : ""}.
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="tt">pre-flight checks</span>
        {(
          [
            modules.includes("firewall")
              ? { label: "SSH lockout prevention", state: "pass", note: "LabDog injects its control-plane SSH rule into every firewall apply; it cannot be removed by a group or a host." }
              : null,
            skipped.length || errored.length
              ? {
                  label: "Reachability",
                  state: "warn",
                  note: [
                    skipped.length ? `${skipped.map((p) => p.host.hostname).join(", ")} ${skipped.length === 1 ? "has" : "have"} no SSH key — skipped, not failed` : "",
                    errored.length ? `${errored.map((p) => p.host.hostname).join(", ")} could not be fully previewed — see the diff for the error` : "",
                  ]
                    .filter(Boolean)
                    .join("; "),
                }
              : { label: "Reachability", state: "pass", note: `all ${scopeHosts.length} host${scopeHosts.length === 1 ? "" : "s"} answered the dry run` },
            unknownBackend.length
              ? { label: "Firewall backend", state: "warn", note: `${unknownBackend.map((h) => h.hostname).join(", ")}: backend not yet detected — it is picked on first apply` }
              : modules.includes("firewall")
                ? { label: "Firewall backend", state: "pass", note: "every host in scope has a known backend" }
                : null,
            { label: "Plan required before apply", state: "pass", note: "this screen is the plan; Apply is armed only after the acknowledgements" },
            {
              label: "Rollback",
              state: "off",
              note: pveInScope
                ? `syncs do not snapshot — ${pveInScope} of ${scopeHosts.length} host${scopeHosts.length === 1 ? " is a" : "s are"} Proxmox guest${pveInScope === 1 ? "" : "s"}, so a manual snapshot first is possible`
                : "syncs do not snapshot, and no host in scope is a Proxmox guest",
            },
          ].filter((c): c is { label: string; state: string; note: string } => c !== null) as { label: string; state: string; note: string }[]
        ).map((c) => (
          <div key={c.label} className="flex items-start gap-2">
            <div className="mt-1">
              <Dot tone={c.state === "pass" ? "ok" : c.state === "warn" ? "warn" : "idle"} />
            </div>
            <div className="min-w-0">
              <div className="text-xs font-medium text-text">
                {c.label}{" "}
                <span className="tt" style={{ color: c.state === "warn" ? "var(--warn)" : c.state === "off" ? "var(--text-faint)" : "var(--ok)" }}>
                  {c.state}
                </span>
              </div>
              <div className="text-[11px] leading-[1.45] text-text-3">{c.note}</div>
            </div>
          </div>
        ))}
      </div>

      {phase === "review" && (
        <div className="mt-auto flex flex-col gap-[9px] rounded-r border border-line-strong bg-surface-2 p-[11px]">
          <span className="tt text-text-2">to apply, confirm you have read</span>
          {acks.map((a) => (
            <label key={a.k} className="flex cursor-pointer items-start gap-2 text-[11.5px]">
              <input type="checkbox" checked={!!ack[a.k]} onChange={(e) => setAck((s) => ({ ...s, [a.k]: e.target.checked }))} style={{ accentColor: "var(--accent)", marginTop: 2 }} />
              <span style={{ color: ack[a.k] ? "var(--text-2)" : "var(--text)" }}>{a.label}</span>
            </label>
          ))}
          <label className="flex flex-col gap-1">
            <span className="text-[11.5px]">Type the host count to arm Apply</span>
            <input className="inp mono" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={String(targets.length)} style={{ width: 90 }} aria-label="host count" />
          </label>
          <button type="button" className="btn btn-primary justify-center" disabled={!allAck} onClick={onApply}>
            {targets.length ? `Apply to ${targets.length} host${targets.length === 1 ? "" : "s"}` : "Select at least one host"}
          </button>
          <span className="text-[10.5px] leading-[1.45] text-text-3">Apply runs one coalesced playbook per host. Hosts already busy queue behind the running operation.</span>
        </div>
      )}
    </aside>
  )
}
