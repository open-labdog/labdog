"use client"

import { useCallback, useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { moduleByAnyName } from "@/lib/modules"
import type { Host, HostGroup, VMMapping } from "@/lib/types"
import { Empty, PageHead, Tag } from "@/components/ld"
import { ApplyConfirm } from "./_dialogs/apply-confirm"
import { useApplyJobs } from "./_hooks/use-apply-jobs"
import { usePlanComputation } from "./_hooks/use-plan-computation"
import { usePlanScope } from "./_hooks/use-plan-scope"
import { ApplyProgress, ApplyResult } from "./_steps/apply-step"
import { DefineStep } from "./_steps/define-step"
import { DiffPanel, PlanSidebar, type Ack } from "./_steps/review-step"
import { breakdown, SSH_RE, type Phase } from "./model"

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
 *
 * The steps live in `_steps/`, the confirmation in `_dialogs/`, and the
 * state behind them in `_hooks/`: the definition (`usePlanScope`), the dry
 * run (`usePlanComputation`) and the run (`useApplyJobs`). This file owns
 * the phase, the acknowledgements, and how the pieces are put together.
 */
export default function PlanPage() {
  const router = useRouter()

  const { data: hosts } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const { data: vmMappings } = useQuery<VMMapping[]>({
    queryKey: ["vm-mappings"],
    queryFn: () => apiFetch<VMMapping[]>("/api/proxmox/vm-mappings"),
    retry: false,
  })

  const scope = usePlanScope(hosts, groups)
  const { modules, defined, group, scopeHosts, scopeLabel, moduleFilter, modulesLabel } = scope

  const [phase, setPhase] = useState<Phase>("define")
  const [ack, setAck] = useState<Record<string, boolean>>({})
  const [typed, setTyped] = useState("")
  const [confirm, setConfirm] = useState(false)
  const { jobs, jobStatus, queueJobs, clearJobs } = useApplyJobs(phase, setPhase)

  /** Untick every acknowledgement, so Apply has to be armed again. */
  const disarm = useCallback(() => {
    setAck({})
    setTyped("")
  }, [])
  const clearRun = useCallback(() => {
    clearJobs()
    disarm()
  }, [clearJobs, disarm])
  const { plans, computedAt, open, setOpen, sel, setSel, compute, clearPlan, previewed } = usePlanComputation({
    scopeHosts,
    moduleFilter,
    defined,
    hosts,
    phase,
    setPhase,
    onCompute: clearRun,
  })

  /* derived from the selection */
  const plan = breakdown(plans, sel)
  const { changing, unchanged, skipped, errored, targets, excluded, selChanges } = plan
  const selSsh = modules.includes("firewall") && selChanges.some((c) => SSH_RE.test(c.summary))
  const pveInScope = scopeHosts.filter((h) => (vmMappings ?? []).some((vm) => vm.host_id === h.id)).length
  const unknownBackend = modules.includes("firewall") ? scopeHosts.filter((h) => h.firewall_backend === "unknown") : []

  const acks = [
    { k: "radius", label: `${targets.length} host${targets.length === 1 ? "" : "s"} will change · ${selChanges.length} change${selChanges.length === 1 ? "" : "s"} across ${modulesLabel}` },
    selSsh ? { k: "ssh", label: "this run touches SSH (22/tcp) — the control-plane rule is re-injected, but read the diff" } : null,
    excluded > 0 ? { k: "partial", label: `${excluded} changing host${excluded === 1 ? " is" : "s are"} excluded and will stay behind` } : null,
  ].filter((a): a is Ack => a !== null)
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
    await queueJobs(targets, moduleFilter, `Plan · ${modulesLabel} → ${scopeLabel}`)
  }

  const applied = targets.filter((p) => jobStatus(p.host.id) === "success").length
  const failed = targets.filter((p) => ["failed", "cancelled", "rejected"].includes(jobStatus(p.host.id) ?? "")).length

  const reset = () => {
    setPhase("define")
    clearPlan()
    disarm()
    clearJobs()
  }

  const planRemaining = () => {
    setSel(new Set(changing.filter((p) => !sel.has(p.host.id)).map((p) => p.host.id)))
    setPhase("review")
    disarm()
    clearJobs()
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
            <span>plan</span>
            {defined && <Tag mono>{modulesLabel}</Tag>}
            {defined && (
              <Tag mono
                tone="accent"
                title={group ? "open the group this plan came from" : "the hosts this plan covers"}
                onClick={() => (group ? router.push(`/groups/${group.id}?tab=config${modules.length === 1 ? `&module=${moduleByAnyName(modules[0])?.id ?? ""}` : ""}`) : router.push("/hosts"))}
              >
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
        <DefineStep
          hosts={hosts}
          groups={groups}
          scope={scope}
          onCompute={() => {
            scope.syncUrl()
            void compute()
          }}
        />
      )}

      {phase === "computing" && (
        <div className="flex flex-1 flex-col">
          <Empty
            title={`Dry run on ${scopeHosts.length} host${scopeHosts.length === 1 ? "" : "s"}…`}
            note={`Each host is read over SSH and compared to what its groups declare, a few at a time. ${previewed} of ${scopeHosts.length} read so far.`}
          />
        </div>
      )}

      {(phase === "review" || phase === "applying" || phase === "done") && (
        <div className="flex min-h-0 flex-1">
          <div className="scroll flex min-w-0 flex-1 flex-col gap-3 p-3.5">
            {phase === "applying" && <ApplyProgress targets={targets} jobs={jobs} jobStatus={jobStatus} />}
            {phase === "done" && (
              <ApplyResult
                meta={`${modulesLabel} → ${scopeLabel}`}
                counts={{ applied, failed, excluded, unchanged: unchanged.length, skipped: skipped.length + errored.length }}
                targets={targets}
                jobs={jobs}
                jobStatus={jobStatus}
                onPlanRemaining={planRemaining}
              />
            )}
            <DiffPanel phase={phase} plans={plans} plan={plan} open={open} setOpen={setOpen} sel={sel} setSel={setSel} toggle={toggle} />
          </div>
          <PlanSidebar
            phase={phase}
            plan={plan}
            modules={modules}
            scopeHosts={scopeHosts}
            selSsh={selSsh}
            pveInScope={pveInScope}
            unknownBackend={unknownBackend}
            acks={acks}
            ack={ack}
            setAck={setAck}
            typed={typed}
            setTyped={setTyped}
            allAck={allAck}
            onApply={() => setConfirm(true)}
          />
        </div>
      )}

      {confirm && (
        <ApplyConfirm
          modulesLabel={modulesLabel}
          scopeLabel={scopeLabel}
          scopeHostCount={scopeHosts.length}
          changingCount={changing.length}
          targets={targets}
          excluded={excluded}
          selChangeCount={selChanges.length}
          selSsh={selSsh}
          pveInScope={pveInScope}
          onClose={() => setConfirm(false)}
          onApply={() => void apply()}
        />
      )}
    </>
  )
}
