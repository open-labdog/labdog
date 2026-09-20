"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch, ApiError } from "@/lib/api"
import { moduleByAnyName, SYNCABLE_MODULES, syncModuleLabel } from "@/lib/modules"
import { useSyncTray, type SyncJob } from "@/lib/sync-tray"
import { showError } from "@/lib/toast"
import type { DiffChange, Host, HostGroup, ModuleDiff, VMMapping } from "@/lib/types"
import { Dot, Empty, Modal, PageHead, Panel, Status, Tag, toneVar, type Tone } from "@/components/ld"

/**
 * Plan → apply, as a screen with a URL rather than a dialog.
 *
 * A plan is a scope (a group, or a hand-picked set of hosts) and a set of
 * modules. Computing it runs the dry-run preview on every host in scope
 * and lays the diffs side by side; applying it runs one coalesced
 * playbook per *selected* host. Everything downstream — the blast radius,
 * the acknowledgements, the run itself — is derived from the selection,
 * so a partial run can never be described with the full plan's numbers.
 *
 * The URL carries the definition (`?scope=group:3&modules=firewall`), so a
 * second pair of eyes can open the same plan before anyone clicks Apply.
 */

type Phase = "define" | "computing" | "review" | "applying" | "done"

interface HostPlan {
  host: Host
  diffs: ModuleDiff[] | null
  error: string | null
  /** Hosts without an SSH key cannot be reached; they are skipped, not failed. */
  skipped: string | null
}

const OP: Record<DiffChange["op"], { s: string; tone: Tone; bg: string }> = {
  add: { s: "+", tone: "add", bg: "var(--add-bg)" },
  remove: { s: "−", tone: "del", bg: "var(--del-bg)" },
  update: { s: "~", tone: "warn", bg: "var(--warn-soft)" },
  unchanged: { s: "·", tone: "idle", bg: "transparent" },
}

const TERMINAL = new Set(["success", "failed", "cancelled"])
const SSH_RE = /\b(dport\s+22|port\s+22|:22\b|\b22\/tcp|ssh)\b/i

function realChanges(diffs: ModuleDiff[] | null): DiffChange[] {
  return (diffs ?? []).flatMap((d) => d.changes.filter((c) => c.op !== "unchanged"))
}

export default function PlanPage() {
  const router = useRouter()
  const search = useSearchParams()
  const queryClient = useQueryClient()
  const { registerSync } = useSyncTray()

  const scopeParam = search.get("scope")
  const hostsParam = search.get("hosts")
  const modulesParam = search.get("modules")

  const { data: hosts } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const { data: vmMappings } = useQuery<VMMapping[]>({
    queryKey: ["vm-mappings"],
    queryFn: () => apiFetch<VMMapping[]>("/api/proxmox/vm-mappings"),
    retry: false,
  })

  /* definition — from the URL when it carries one, otherwise from the form */
  const [groupId, setGroupId] = useState<number | null>(() => (scopeParam?.startsWith("group:") ? Number(scopeParam.slice(6)) : null))
  const [hostIds, setHostIds] = useState<number[]>(() => (hostsParam ? hostsParam.split(",").map(Number).filter((n) => !Number.isNaN(n)) : []))
  const [modules, setModules] = useState<string[]>(() =>
    modulesParam
      ? modulesParam
          .split(",")
          .map((m) => moduleByAnyName(m)?.syncModule)
          .filter((m): m is string => !!m)
      : SYNCABLE_MODULES.map((m) => m.syncModule!),
  )
  const defined = groupId !== null || hostIds.length > 0

  const [phase, setPhase] = useState<Phase>("define")
  const [plans, setPlans] = useState<HostPlan[]>([])
  const [computedAt, setComputedAt] = useState<Date | null>(null)
  const [open, setOpen] = useState<Record<number, boolean>>({})
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [ack, setAck] = useState<Record<string, boolean>>({})
  const [typed, setTyped] = useState("")
  const [confirm, setConfirm] = useState(false)
  const [jobs, setJobs] = useState<Record<number, SyncJob | { status: "queued" } | { status: "rejected"; error: string }>>({})
  const [jobIds, setJobIds] = useState<Record<number, number>>({})
  const computeSeq = useRef(0)

  const group = groups?.find((g) => g.id === groupId) ?? null
  const scopeHosts = useMemo<Host[]>(() => {
    const all = hosts ?? []
    if (groupId !== null) return all.filter((h) => h.group_ids.includes(groupId)).sort((a, b) => a.hostname.localeCompare(b.hostname))
    return all.filter((h) => hostIds.includes(h.id)).sort((a, b) => a.hostname.localeCompare(b.hostname))
  }, [hosts, groupId, hostIds])
  const scopeLabel = group ? `group: ${group.name}` : hostIds.length ? `${hostIds.length} selected host${hostIds.length === 1 ? "" : "s"}` : "no scope"
  const allModules = modules.length === SYNCABLE_MODULES.length
  const moduleFilter = allModules ? null : modules
  const modulesLabel = allModules ? "all modules" : modules.map(syncModuleLabel).join(", ")

  const compute = useCallback(async () => {
    const seq = ++computeSeq.current
    setPhase("computing")
    setPlans([])
    setJobs({})
    setJobIds({})
    setAck({})
    setTyped("")
    const results = await Promise.all(
      scopeHosts.map(async (host): Promise<HostPlan> => {
        if (!host.ssh_key_id) return { host, diffs: null, error: null, skipped: "no SSH key — cannot be reached" }
        try {
          const diffs = await apiFetch<ModuleDiff[]>(`/api/sync/hosts/${host.id}/preview`, { method: "POST", json: { module_filter: moduleFilter } })
          return { host, diffs, error: null, skipped: null }
        } catch (e) {
          return { host, diffs: null, error: e instanceof Error ? e.message : "preview failed", skipped: null }
        }
      }),
    )
    if (seq !== computeSeq.current) return
    setPlans(results)
    setComputedAt(new Date())
    const changing = results.filter((p) => !p.skipped && !p.error && realChanges(p.diffs).length > 0)
    setSel(new Set(changing.map((p) => p.host.id)))
    setOpen(Object.fromEntries(changing.slice(0, 1).map((p) => [p.host.id, true])))
    setPhase("review")
  }, [scopeHosts, moduleFilter])

  // A plan opened from a URL computes itself once the hosts are known.
  const autoComputed = useRef(false)
  useEffect(() => {
    if (autoComputed.current || !defined || !hosts || phase !== "define") return
    autoComputed.current = true
    void compute()
  }, [defined, hosts, phase, compute])

  /* derived from the selection */
  const changing = plans.filter((p) => !p.skipped && !p.error && realChanges(p.diffs).length > 0)
  const unchanged = plans.filter((p) => !p.skipped && !p.error && realChanges(p.diffs).length === 0)
  const skipped = plans.filter((p) => p.skipped)
  const errored = plans.filter((p) => p.error)
  const targets = changing.filter((p) => sel.has(p.host.id))
  const excluded = changing.length - targets.length
  const selChanges = targets.flatMap((p) => realChanges(p.diffs))
  const selSsh = modules.includes("firewall") && selChanges.some((c) => SSH_RE.test(c.summary))
  const changeCount = changing.reduce((n, p) => n + realChanges(p.diffs).length, 0)
  const pveInScope = scopeHosts.filter((h) => (vmMappings ?? []).some((vm) => vm.host_id === h.id)).length
  const unknownBackend = modules.includes("firewall") ? scopeHosts.filter((h) => h.firewall_backend === "unknown") : []

  const acks = [
    { k: "radius", label: `${targets.length} host${targets.length === 1 ? "" : "s"} will change · ${selChanges.length} change${selChanges.length === 1 ? "" : "s"} across ${modulesLabel}` },
    selSsh ? { k: "ssh", label: "this run touches SSH (22/tcp) — the control-plane rule is re-injected, but read the diff" } : null,
    excluded > 0 ? { k: "partial", label: `${excluded} changing host${excluded === 1 ? " is" : "s are"} excluded and will stay behind` } : null,
  ].filter((a): a is { k: string; label: string } => a !== null)
  const allAck = targets.length > 0 && acks.every((a) => ack[a.k]) && typed === String(targets.length)

  const toggle = (id: number) =>
    setSel((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      setTyped("")
      return n
    })

  /* apply — one coalesced job per selected host, then poll them */
  const apply = async () => {
    setConfirm(false)
    setPhase("applying")
    const ids: Record<number, number> = {}
    const initial: typeof jobs = {}
    await Promise.all(
      targets.map(async (p) => {
        try {
          const r = await apiFetch<{ job_id: number; status: string }>(`/api/sync/hosts/${p.host.id}/bulk`, { method: "POST", json: { module_filter: moduleFilter } })
          ids[p.host.id] = r.job_id
          initial[p.host.id] = { status: "queued" }
        } catch (e) {
          initial[p.host.id] = { status: "rejected", error: e instanceof ApiError ? e.message : "could not queue the sync" }
        }
      }),
    )
    setJobIds(ids)
    setJobs(initial)
    const jobList = Object.values(ids)
    if (jobList.length) registerSync({ label: `Plan · ${modulesLabel} → ${scopeLabel}`, jobIds: jobList })
    else {
      showError("No sync could be queued")
      setPhase("done")
    }
  }

  useEffect(() => {
    if (phase !== "applying") return
    const ids = Object.values(jobIds)
    if (ids.length === 0) return
    let stop = false
    const tick = async () => {
      try {
        const rows = await apiFetch<SyncJob[]>(`/api/sync/jobs?ids=${ids.join(",")}`)
        if (stop) return
        setJobs((prev) => {
          const next = { ...prev }
          for (const [hostId, jobId] of Object.entries(jobIds)) {
            const j = rows.find((r) => r.id === jobId)
            if (j) next[Number(hostId)] = j
          }
          return next
        })
        if (rows.length === ids.length && rows.every((r) => TERMINAL.has(r.status))) {
          setPhase("done")
          void queryClient.invalidateQueries({ queryKey: ["hosts"] })
          void queryClient.invalidateQueries({ queryKey: ["hosts-summary"] })
        }
      } catch {
        /* transient — try again next tick */
      }
    }
    void tick()
    const t = setInterval(() => void tick(), 2000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [phase, jobIds, queryClient])

  const jobStatus = (hostId: number) => {
    const j = jobs[hostId]
    if (!j) return null
    return j.status
  }
  const applied = targets.filter((p) => jobStatus(p.host.id) === "success").length
  const failed = targets.filter((p) => ["failed", "cancelled", "rejected"].includes(jobStatus(p.host.id) ?? "")).length

  const reset = () => {
    setPhase("define")
    setPlans([])
    setSel(new Set())
    setAck({})
    setTyped("")
    setJobs({})
    setJobIds({})
    autoComputed.current = true
  }

  const syncUrl = () => {
    const q = new URLSearchParams()
    if (groupId !== null) q.set("scope", `group:${groupId}`)
    else if (hostIds.length) q.set("hosts", hostIds.join(","))
    if (!allModules) q.set("modules", modules.join(","))
    router.replace(`/plans?${q.toString()}`)
  }

  const phaseTag =
    phase === "review" ? (
      <Tag tone="hold">awaiting review</Tag>
    ) : phase === "computing" ? (
      <Tag tone="sync">computing</Tag>
    ) : phase === "applying" ? (
      <Tag tone="sync">applying</Tag>
    ) : phase === "done" ? (
      <Tag tone={failed ? "danger" : "ok"}>{failed ? "applied with failures" : "applied"}</Tag>
    ) : null

  return (
    <>
      <PageHead
        crumbs={[{ label: "operations" }, { label: "plans" }]}
        title={
          <>
            <span className="mono">plan</span>
            {defined && <Tag>{modulesLabel}</Tag>}
            {defined && (
              <Tag tone="accent" title={group ? "open the group this plan came from" : "the hosts this plan covers"} onClick={() => (group ? router.push(`/groups/${group.id}`) : router.push("/hosts"))}>
                {scopeLabel} →
              </Tag>
            )}
            {phaseTag}
          </>
        }
        sub={
          phase === "define" ? (
            "Pick a scope and the modules to plan. Computing runs a dry run on every host in scope; nothing is applied until you review the diff and arm Apply."
          ) : phase === "review" ? (
            <>
              Computed {computedAt ? computedAt.toLocaleTimeString() : "just now"} · covers <span className="mono num">{scopeHosts.length}</span> host{scopeHosts.length === 1 ? "" : "s"}, <span className="mono num">{changing.length}</span> changing,{" "}
              <span className="mono num" style={{ color: excluded ? "var(--warn)" : "var(--text-2)" }}>{targets.length}</span> selected to run · nothing applied yet · this URL is shareable, so a second pair of eyes can review before anyone clicks Apply
            </>
          ) : phase === "computing" ? (
            `Running the dry run on ${scopeHosts.length} host${scopeHosts.length === 1 ? "" : "s"}…`
          ) : (
            <>
              Run scoped to <span className="mono num">{targets.length}</span> of <span className="mono num">{changing.length}</span> changing hosts · one coalesced playbook per host
            </>
          )
        }
        actions={
          phase === "review" ? (
            <>
              <button type="button" className="btn btn-sm" onClick={reset}>
                Discard
              </button>
              <button type="button" className="btn btn-sm" onClick={() => void compute()}>
                Re-plan
              </button>
              <button type="button" className="btn btn-sm btn-primary" disabled={!allAck} onClick={() => setConfirm(true)} title={allAck ? undefined : "acknowledge the checks in the side panel first"}>
                Review &amp; apply…
              </button>
            </>
          ) : phase === "done" ? (
            <>
              <button type="button" className="btn btn-sm" onClick={reset}>
                New plan
              </button>
              <button type="button" className="btn btn-sm btn-primary" onClick={() => router.push("/overview")}>
                Back to Overview
              </button>
            </>
          ) : null
        }
      />

      {phase === "define" && (
        <div className="scroll flex-1 p-3.5">
          <div className="grid gap-3" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", alignItems: "start" }}>
            <Panel title="scope" meta="who this plan touches">
              <div className="flex flex-col gap-2.5 p-3">
                <label className="flex flex-col gap-1">
                  <span className="tt">group</span>
                  <select
                    className="inp mono"
                    value={groupId ?? ""}
                    onChange={(e) => {
                      setGroupId(e.target.value ? Number(e.target.value) : null)
                      if (e.target.value) setHostIds([])
                    }}
                  >
                    <option value="">— pick a group —</option>
                    {[...(groups ?? [])]
                      .sort((a, b) => b.priority - a.priority)
                      .map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.name} · p{g.priority} · {(hosts ?? []).filter((h) => h.group_ids.includes(g.id)).length} hosts
                        </option>
                      ))}
                  </select>
                </label>
                <div className="tt">or hand-pick hosts</div>
                <div className="scroll flex max-h-[260px] flex-col gap-px rounded-r border border-line bg-surface-2 p-1">
                  {(hosts ?? []).map((h) => (
                    <label key={h.id} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-[3px] hover:bg-surface-3">
                      <input
                        type="checkbox"
                        checked={hostIds.includes(h.id)}
                        onChange={(e) => {
                          setGroupId(null)
                          setHostIds((ids) => (e.target.checked ? [...ids, h.id] : ids.filter((x) => x !== h.id)))
                        }}
                        style={{ accentColor: "var(--accent)" }}
                      />
                      <span className="mono flex-1 text-[11.5px]">{h.hostname}</span>
                      <Status s={h.sync_status} dim />
                    </label>
                  ))}
                  {(hosts ?? []).length === 0 && <span className="p-2 text-[11.5px] text-text-3">No hosts yet.</span>}
                </div>
              </div>
            </Panel>
            <div className="flex flex-col gap-3">
              <Panel title="modules" meta={`${modules.length} of ${SYNCABLE_MODULES.length}`}>
                <div className="flex flex-col gap-1 p-3">
                  {SYNCABLE_MODULES.map((m) => (
                    <label key={m.id} className="flex cursor-pointer items-center gap-2 py-[3px]">
                      <input
                        type="checkbox"
                        checked={modules.includes(m.syncModule!)}
                        onChange={(e) => setModules((ms) => (e.target.checked ? [...ms, m.syncModule!] : ms.filter((x) => x !== m.syncModule)))}
                        style={{ accentColor: "var(--accent)" }}
                      />
                      <span className="flex-1 text-xs text-text">{m.label}</span>
                      <span className="text-[10.5px] text-text-faint">{m.blurb}</span>
                    </label>
                  ))}
                  <div className="mt-1 flex gap-1.5">
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => setModules(SYNCABLE_MODULES.map((m) => m.syncModule!))}>
                      all
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => setModules([])}>
                      none
                    </button>
                  </div>
                  <span className="text-[11px] text-text-3">CA certificates are applied by their own action, not the coalesced sync.</span>
                </div>
              </Panel>
              <Panel title="compute">
                <div className="flex flex-col gap-2.5 p-3">
                  <div className="flex items-baseline gap-2">
                    <span className="mono num text-2xl font-semibold">{scopeHosts.length}</span>
                    <span className="text-[11.5px] text-text-2">host{scopeHosts.length === 1 ? "" : "s"} in scope · {modulesLabel}</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-primary justify-center"
                    disabled={!defined || scopeHosts.length === 0 || modules.length === 0}
                    onClick={() => {
                      syncUrl()
                      void compute()
                    }}
                  >
                    Compute plan — dry run on {scopeHosts.length} host{scopeHosts.length === 1 ? "" : "s"}
                  </button>
                  <span className="text-[10.5px] leading-[1.45] text-text-3">The dry run reads each host over SSH and compares it to desired state. It changes nothing.</span>
                </div>
              </Panel>
            </div>
          </div>
        </div>
      )}

      {phase === "computing" && (
        <div className="flex flex-1 flex-col">
          <Empty title={`Dry run on ${scopeHosts.length} host${scopeHosts.length === 1 ? "" : "s"}…`} note="Each host is read over SSH and compared to what its groups declare. Large fleets take a moment." />
        </div>
      )}

      {(phase === "review" || phase === "applying" || phase === "done") && (
        <div className="flex min-h-0 flex-1">
          <div className="scroll flex min-w-0 flex-1 flex-col gap-3 p-3.5">
            {phase === "applying" && (
              <Panel title="applying" meta={`${targets.filter((p) => TERMINAL.has(jobStatus(p.host.id) ?? "") || jobStatus(p.host.id) === "rejected").length} of ${targets.length} hosts finished`}>
                {targets.map((p) => {
                  const s = jobStatus(p.host.id)
                  const j = jobs[p.host.id]
                  return (
                    <div key={p.host.id} className="flex items-center gap-2.5 border-b border-line-faint px-[11px] py-[7px]">
                      <button type="button" className="mono w-[120px] shrink-0 border-0 bg-transparent text-left text-xs text-text underline decoration-line-strong underline-offset-[3px]" onClick={() => router.push(`/hosts/${p.host.id}`)}>
                        {p.host.hostname}
                      </button>
                      <Status s={s === "success" ? "in_sync" : s === "failed" || s === "rejected" || s === "cancelled" ? "error" : "pending"} />
                      <span className="mono trunc flex-1 text-[11px] text-text-3">
                        {s === "success" ? `${realChanges(p.diffs).length} change${realChanges(p.diffs).length === 1 ? "" : "s"} applied` : j && "error" in j ? j.error : j && "error_message" in j && j.error_message ? j.error_message : s === "running" ? "ansible is running…" : j && "pending_reason" in j && j.pending_reason ? j.pending_reason : "waiting for the host…"}
                      </span>
                    </div>
                  )
                })}
              </Panel>
            )}

            {phase === "done" && (
              <Panel title="result" meta={`${modulesLabel} → ${scopeLabel}`}>
                <div className="flex flex-col gap-[11px] p-[13px]">
                  <div className="flex flex-wrap gap-[18px]">
                    {(
                      [
                        ["applied", applied, "ok"],
                        ["failed", failed, failed ? "danger" : "idle"],
                        ["excluded", excluded, excluded ? "warn" : "idle"],
                        ["unchanged", unchanged.length, "idle"],
                        ["skipped", skipped.length + errored.length, "idle"],
                      ] as [string, number, Tone][]
                    ).map(([l, n, t]) => (
                      <div key={l} className="flex flex-col gap-[3px]">
                        <span className="tt">{l}</span>
                        <span className="mono num text-xl font-semibold" style={{ color: toneVar(t) }}>
                          {n}
                        </span>
                      </div>
                    ))}
                  </div>
                  {targets
                    .filter((p) => ["failed", "cancelled", "rejected"].includes(jobStatus(p.host.id) ?? ""))
                    .map((p) => {
                      const j = jobs[p.host.id]
                      const msg = j && "error" in j ? j.error : j && "error_message" in j ? j.error_message : null
                      return (
                        <div key={p.host.id} className="rounded-r border border-danger bg-danger-soft p-2.5 text-xs text-text">
                          <b className="mono">{p.host.hostname}</b> failed{msg ? <> — <span className="mono">{msg}</span></> : ""}. The host was left on its previous state; a failed play does not half-apply.
                          <div className="mt-2 flex gap-[7px]">
                            <button type="button" className="btn btn-sm" onClick={() => router.push(`/assistant`)}>
                              Ask the assistant
                            </button>
                            <button type="button" className="btn btn-sm" onClick={() => router.push(`/hosts/${p.host.id}`)}>
                              Open {p.host.hostname}
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  {excluded > 0 && (
                    <div className="flex flex-wrap items-center gap-[9px] rounded-r border border-line bg-surface-2 p-2.5 text-xs">
                      <Dot tone="warn" />
                      <span>
                        {excluded} host{excluded === 1 ? "" : "s"} excluded from this run and still pending this change.
                      </span>
                      <button
                        type="button"
                        className="btn btn-sm ml-auto"
                        onClick={() => {
                          setSel(new Set(changing.filter((p) => !sel.has(p.host.id)).map((p) => p.host.id)))
                          setPhase("review")
                          setAck({})
                          setTyped("")
                          setJobs({})
                          setJobIds({})
                        }}
                      >
                        Plan the remaining {excluded}
                      </button>
                    </div>
                  )}
                  <span className="text-[11.5px] text-text-3">Recorded in the audit trail with a per-module outcome for every host.</span>
                </div>
              </Panel>
            )}

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
                        ) : changes.length === 0 ? (
                          <Tag tone="ok">no change</Tag>
                        ) : (
                          <span className="flex gap-[5px]">
                            {(["add", "remove", "update"] as const).map((op) => {
                              const n = changes.filter((c) => c.op === op).length
                              return n ? (
                                <Tag key={op} tone={OP[op].tone}>
                                  {OP[op].s}
                                  {n}
                                </Tag>
                              ) : null
                            })}
                            {(p.diffs ?? []).filter((d) => d.error).length > 0 && <Tag tone="danger">{(p.diffs ?? []).filter((d) => d.error).length} module error</Tag>}
                          </span>
                        )}
                        <span className="tt ml-auto">
                          {selectable && !on ? <span className="text-warn">excluded</span> : p.error ? p.error : `${changes.length} change${changes.length === 1 ? "" : "s"}`}
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
          </div>

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
                {errored.length ? `, ${errored.length} could not be previewed` : ""}.
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <span className="tt">pre-flight checks</span>
              {(
                [
                  modules.includes("firewall")
                    ? { label: "SSH lockout prevention", state: "pass", note: "LabDog injects its control-plane SSH rule into every firewall apply; it cannot be removed by a group or a host." }
                    : null,
                  skipped.length
                    ? { label: "Reachability", state: "warn", note: `${skipped.map((p) => p.host.hostname).join(", ")} ${skipped.length === 1 ? "has" : "have"} no SSH key — skipped, not failed` }
                    : errored.length
                      ? { label: "Reachability", state: "warn", note: `${errored.map((p) => p.host.hostname).join(", ")} could not be previewed — see the diff for the error` }
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
                <button type="button" className="btn btn-primary justify-center" disabled={!allAck} onClick={() => setConfirm(true)}>
                  {targets.length ? `Apply to ${targets.length} host${targets.length === 1 ? "" : "s"}` : "Select at least one host"}
                </button>
                <span className="text-[10.5px] leading-[1.45] text-text-3">Apply runs one coalesced playbook per host. Hosts already busy queue behind the running operation.</span>
              </div>
            )}
          </aside>
        </div>
      )}

      {confirm && (
        <Modal
          title={`Apply plan — ${modulesLabel}`}
          meta={`${scopeLabel} · ${targets.length} of ${scopeHosts.length} hosts`}
          onClose={() => setConfirm(false)}
          w={520}
          footer={
            <>
              <span className="tt mr-auto">this is the last stop</span>
              <button type="button" className="btn" onClick={() => setConfirm(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" onClick={() => void apply()}>
                Apply now
              </button>
            </>
          }
        >
          <div className="text-[12.5px] leading-[1.6]">LabDog will queue one coalesced Ansible playbook per selected host. Hosts with an operation already running wait their turn in the per-host queue.</div>
          <div className="flex flex-col gap-[7px]">
            {(
              [
                [`${targets.length} of ${changing.length} changing hosts are selected${excluded ? ` — ${excluded} excluded, left untouched` : ""}`, excluded ? "warn" : "accent"],
                [`${selChanges.length} change${selChanges.length === 1 ? "" : "s"} across ${modulesLabel}`, "hold"],
                [selSsh ? "SSH (22/tcp) is touched — the control-plane rule is re-injected on apply" : "nothing on 22/tcp changes in this run", selSsh ? "warn" : "ok"],
                [pveInScope ? `${pveInScope} of ${scopeHosts.length} hosts in scope are Proxmox guests — syncs do not snapshot them` : "no host in this scope is a Proxmox guest — rollback unavailable", "idle"],
              ] as [string, Tone][]
            ).map(([l, t]) => (
              <div key={l} className="flex items-start gap-2 text-xs">
                <div className="mt-1">
                  <Dot tone={t} />
                </div>
                <span>{l}</span>
              </div>
            ))}
            <div className="mono scroll max-h-24 rounded-r border border-line px-[9px] py-1.5 text-[11.5px] text-text-2">
              {targets.map((p) => (
                <div key={p.host.id}>{p.host.hostname}</div>
              ))}
            </div>
          </div>
        </Modal>
      )}
    </>
  )
}
