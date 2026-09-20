"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQueries, useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { queueHostStateCollection } from "@/lib/collect-state"
import { shortAgo } from "@/lib/fleet"
import { moduleByAnyName, MODULES, type ModuleDef } from "@/lib/modules"
import { showError, showSuccess } from "@/lib/toast"
import type { DriftCoverage, Host, ModuleCurrentState } from "@/lib/types"
import { Dot, Empty, Filter, PageHead, Panel, Seg, Tag } from "@/components/ld"

interface Finding {
  id: string
  host: Host
  mod: ModuleDef | undefined
  moduleType: string
  sev: "high" | "med"
  status: string
  checkedAt: string | null
  note: string | null
}

/**
 * Drift — what the fleet actually looks like versus what it was told to
 * look like, as a findings list rather than a chart. A finding is one
 * (host, module) whose last collection disagreed with desired state.
 * Remediate writes a plan for exactly that host and module; nothing here
 * applies anything directly.
 */
export default function DriftPage() {
  const router = useRouter()
  const [group, setGroup] = useState<"host" | "module">("host")
  const [sev, setSev] = useState("all")
  const [checking, setChecking] = useState(false)

  const { data: hosts, isLoading } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
    refetchInterval: 30_000,
  })
  const { data: coverage } = useQuery<DriftCoverage>({
    queryKey: ["dashboard", "drift-coverage"],
    queryFn: () => apiFetch<DriftCoverage>("/api/dashboard/drift-coverage"),
  })

  // A host's status is the roll-up; the per-module verdicts live on its
  // module status rows. Only hosts that are not clean are worth asking.
  const suspects = useMemo(() => (hosts ?? []).filter((h) => h.sync_status === "out_of_sync" || h.sync_status === "error"), [hosts])
  const states = useQueries({
    queries: suspects.map((h) => ({
      queryKey: ["host-current-state", h.id],
      queryFn: () => apiFetch<ModuleCurrentState[]>(`/api/hosts/${h.id}/current-state`),
      refetchInterval: 30_000,
    })),
  })
  const statesLoading = states.some((s) => s.isLoading)

  const findings = useMemo<Finding[]>(() => {
    const out: Finding[] = []
    suspects.forEach((h, i) => {
      const rows = states[i]?.data ?? []
      const bad = rows.filter((r) => r.sync_status === "out_of_sync" || r.sync_status === "error")
      if (bad.length === 0 && states[i]?.data) {
        // The host says drifted but no module row does — the roll-up is
        // stale or the module-level view has not caught up. Say so rather
        // than hide it.
        out.push({ id: `${h.id}:host`, host: h, mod: undefined, moduleType: "host", sev: h.sync_status === "error" ? "high" : "med", status: h.sync_status, checkedAt: h.last_drift_check_at, note: "host-level status only — re-check to get per-module findings" })
        return
      }
      for (const r of bad) {
        out.push({
          id: `${h.id}:${r.module_type}`,
          host: h,
          mod: moduleByAnyName(r.module_type),
          moduleType: r.module_type,
          sev: r.sync_status === "error" ? "high" : "med",
          status: r.sync_status,
          checkedAt: r.collected_at,
          note: r.error_message,
        })
      }
    })
    return out.sort((a, b) => (a.sev === b.sev ? (b.checkedAt ?? "").localeCompare(a.checkedAt ?? "") : a.sev === "high" ? -1 : 1))
  }, [suspects, states])

  const shown = findings.filter((f) => sev === "all" || f.sev === sev)
  const grouped = useMemo(() => {
    const m = new Map<string, Finding[]>()
    for (const f of shown) {
      const k = group === "host" ? f.host.hostname : f.mod?.id ?? f.moduleType
      m.set(k, [...(m.get(k) ?? []), f])
    }
    return [...m.entries()]
  }, [shown, group])

  const remediate = (fs: Finding[]) => {
    const hostIds = [...new Set(fs.map((f) => f.host.id))]
    const mods = [...new Set(fs.map((f) => f.mod?.syncModule).filter((m): m is string => !!m))]
    const q = new URLSearchParams({ hosts: hostIds.join(",") })
    if (mods.length) q.set("modules", mods.join(","))
    router.push(`/plans?${q.toString()}`)
  }

  const recheck = async (targets: Host[]) => {
    setChecking(true)
    const results = await Promise.allSettled(targets.map((h) => queueHostStateCollection(h.id)))
    setChecking(false)
    const failed = results.filter((r) => r.status === "rejected").length
    if (failed) showError(`State collection queued for ${targets.length - failed} of ${targets.length} hosts`)
    else showSuccess(`State collection queued for ${targets.length} host${targets.length === 1 ? "" : "s"}`)
  }

  const driftOff = coverage !== undefined && coverage.hosts_total > 0 && coverage.any_enabled_hosts === 0
  const hostCount = new Set(shown.map((f) => f.host.id)).size

  return (
    <>
      <PageHead
        crumbs={[{ label: "operations" }]}
        title={
          <>
            Drift{" "}
            <span className="mono num text-[12.5px] font-normal text-text-faint">
              {shown.length} finding{shown.length === 1 ? "" : "s"} · {hostCount} host{hostCount === 1 ? "" : "s"}
            </span>
          </>
        }
        sub="What the fleet actually looks like versus what it was told to look like. Remediate writes a plan you review; re-check reads the host again."
        actions={
          <>
            <button type="button" className="btn btn-sm" disabled={checking || (hosts ?? []).length === 0} onClick={() => void recheck(hosts ?? [])}>
              {checking ? "Queuing…" : "Re-check fleet"}
            </button>
            <button type="button" className="btn btn-sm btn-primary" disabled={shown.length === 0} onClick={() => remediate(shown)}>
              Remediate {shown.length || ""}
            </button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <Seg sm value={group} onChange={(k) => setGroup(k as "host" | "module")} options={[{ k: "host", label: "by host" }, { k: "module", label: "by module" }]} />
          <Filter label="severity" value={sev} onChange={setSev} options={[{ k: "high", label: "high — collection failed", n: findings.filter((f) => f.sev === "high").length }, { k: "med", label: "drifted", n: findings.filter((f) => f.sev === "med").length }]} />
          <span className="tt ml-auto">sorted by severity, then most recent check</span>
        </div>
      </PageHead>

      <div className="scroll flex flex-1 flex-col gap-[11px] p-3.5">
        {driftOff && (
          <div className="flex flex-wrap items-center gap-2 rounded-r border border-warn bg-warn-soft px-3 py-2 text-xs text-text">
            <Dot tone="warn" />
            Drift checking is off on all {coverage.hosts_total} hosts — the sweep runs and finds nothing.{" "}
            <Link href="/hosts?drift=off" className="underline">
              Enable it on Hosts
            </Link>
          </div>
        )}
        {(isLoading || statesLoading) && findings.length === 0 && <div className="p-3 text-xs text-text-3">Reading module state…</div>}
        {!isLoading && !statesLoading && grouped.length === 0 && (
          <Empty
            title="No drift"
            note={driftOff ? "Nothing is being checked, so nothing can be found." : "Every host matched its desired state at its last check."}
            action={
              <button type="button" className="btn btn-sm" onClick={() => router.push("/hosts")}>
                Hosts
              </button>
            }
          />
        )}
        {grouped.map(([k, items]) => {
          const hostsHere = [...new Map(items.map((f) => [f.host.id, f.host])).values()]
          return (
            <Panel
              key={k}
              title={group === "host" ? k : MODULES.find((m) => m.id === k)?.label ?? k}
              meta={`${items.length} finding${items.length === 1 ? "" : "s"}${group === "module" ? ` · ${hostsHere.length} host${hostsHere.length === 1 ? "" : "s"}` : ""}`}
              actions={
                <>
                  {group === "host" && (
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => router.push(`/hosts/${items[0].host.id}`)}>
                      open host →
                    </button>
                  )}
                  <button type="button" className="btn btn-sm btn-ghost" disabled={checking} onClick={() => void recheck(hostsHere)}>
                    Re-check
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => remediate(items)}>
                    Remediate all
                  </button>
                </>
              }
            >
              {items.map((f) => (
                <div key={f.id} className="flex items-start gap-2.5 border-b border-line-faint px-[11px] py-2">
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-[7px]">
                      <Tag tone={f.sev === "high" ? "danger" : "warn"}>{f.sev === "high" ? "collection failed" : "drifted"}</Tag>
                      <Tag>{group === "host" ? f.mod?.label ?? f.moduleType : f.host.hostname}</Tag>
                      {f.mod && <span className="text-[11px] text-text-3">{f.mod.blurb}</span>}
                      <span className="mono num ml-auto text-[10.5px] text-text-faint">{f.checkedAt ? `checked ${shortAgo(f.checkedAt)} ago` : "never checked"}</span>
                    </div>
                    {f.note && <div className="mono text-[11.5px]" style={{ color: f.sev === "high" ? "var(--danger)" : "var(--text-3)" }}>{f.note}</div>}
                    {!f.note && f.mod && (
                      <div className="text-[11.5px] text-text-3">
                        The host&apos;s {f.mod.label.toLowerCase()} no longer matches what its groups declare.{" "}
                        <Link href={`/hosts/${f.host.id}?tab=${f.mod.hostTab}`} className="underline">
                          Compare desired and effective →
                        </Link>
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    <button type="button" className="btn btn-sm" onClick={() => remediate([f])} disabled={!f.mod?.syncModule && f.mod !== undefined}>
                      Remediate
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => router.push(`/hosts/${f.host.id}${f.mod ? `?tab=${f.mod.hostTab}` : ""}`)}>
                      Inspect
                    </button>
                  </div>
                </div>
              ))}
            </Panel>
          )
        })}
      </div>
    </>
  )
}
