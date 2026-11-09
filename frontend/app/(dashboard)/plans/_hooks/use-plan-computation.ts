"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { apiFetch } from "@/lib/api"
import type { Host, ModuleDiff } from "@/lib/types"
import { realChanges, type HostPlan, type Phase } from "../model"

interface Options {
  scopeHosts: Host[]
  moduleFilter: string[] | null
  defined: boolean
  hosts: Host[] | undefined
  phase: Phase
  setPhase: (phase: Phase) => void
  /** Called as a computation starts: whatever the last run left behind is cleared. Keep it stable. */
  onCompute: () => void
}

/**
 * Computing a plan: the dry-run preview on every host in scope, the
 * results, and which hosts are selected and expanded. A plan opened from a
 * URL computes itself once the hosts are known.
 */
export function usePlanComputation({ scopeHosts, moduleFilter, defined, hosts, phase, setPhase, onCompute }: Options) {
  const [plans, setPlans] = useState<HostPlan[]>([])
  const [computedAt, setComputedAt] = useState<Date | null>(null)
  const [open, setOpen] = useState<Record<number, boolean>>({})
  const [sel, setSel] = useState<Set<number>>(new Set())
  const computeSeq = useRef(0)

  const compute = useCallback(async () => {
    const seq = ++computeSeq.current
    setPhase("computing")
    setPlans([])
    onCompute()
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
  }, [scopeHosts, moduleFilter, setPhase, onCompute])

  // A plan opened from a URL computes itself once the hosts are known.
  const autoComputed = useRef(false)
  useEffect(() => {
    if (autoComputed.current || !defined || !hosts || phase !== "define") return
    autoComputed.current = true
    void compute()
  }, [defined, hosts, phase, compute])

  /** Drop the computed plan. It is not recomputed by itself afterwards. */
  const clearPlan = () => {
    setPlans([])
    setSel(new Set())
    autoComputed.current = true
  }

  return { plans, computedAt, open, setOpen, sel, setSel, compute, clearPlan }
}
