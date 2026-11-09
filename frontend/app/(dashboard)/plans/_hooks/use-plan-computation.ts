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
 * Previews in flight at once. Each one holds an API worker for as long as
 * the host takes to answer over SSH, so a whole fleet sent at once took the
 * API's database pool with it (BUG-153). Over HTTP/2 the browser's
 * six-per-host limit does not apply, so the page has to set its own.
 */
const PREVIEW_CONCURRENCY = 6

/** `fn` over `items` with at most `limit` running at once, results in input order. */
async function mapWithLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length)
  let next = 0
  const worker = async () => {
    while (next < items.length) {
      const i = next++
      results[i] = await fn(items[i])
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return results
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
  /** Hosts whose preview has come back in the computation under way. */
  const [previewed, setPreviewed] = useState(0)
  const computeSeq = useRef(0)

  const compute = useCallback(async () => {
    const seq = ++computeSeq.current
    setPhase("computing")
    setPlans([])
    setPreviewed(0)
    onCompute()
    const results = await mapWithLimit(scopeHosts, PREVIEW_CONCURRENCY, async (host): Promise<HostPlan> => {
      // A superseded computation stops sending; its results are dropped below.
      if (seq !== computeSeq.current) return { host, diffs: null, error: null, skipped: "superseded" }
      if (!host.ssh_key_id) return { host, diffs: null, error: null, skipped: "no SSH key — cannot be reached" }
      try {
        const diffs = await apiFetch<ModuleDiff[]>(`/api/sync/hosts/${host.id}/preview`, { method: "POST", json: { module_filter: moduleFilter } })
        return { host, diffs, error: null, skipped: null }
      } catch (e) {
        return { host, diffs: null, error: e instanceof Error ? e.message : "preview failed", skipped: null }
      } finally {
        if (seq === computeSeq.current) setPreviewed((n) => n + 1)
      }
    })
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

  return { plans, computedAt, open, setOpen, sel, setSel, compute, clearPlan, previewed }
}
