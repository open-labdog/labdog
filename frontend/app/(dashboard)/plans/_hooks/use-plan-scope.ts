"use client"

import { useMemo, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { moduleByAnyName, SYNCABLE_MODULES, syncModuleLabel } from "@/lib/modules"
import type { Host, HostGroup } from "@/lib/types"

/**
 * The plan's definition: a scope (a group, or hand-picked hosts) and the
 * modules to plan. It starts from the URL when the URL carries one
 * (`?scope=group:3&modules=firewall`), otherwise from the form, and
 * `syncUrl` writes it back so the plan can be shared before anyone applies.
 */
export function usePlanScope(hosts: Host[] | undefined, groups: HostGroup[] | undefined) {
  const router = useRouter()
  const search = useSearchParams()

  const scopeParam = search.get("scope")
  const hostsParam = search.get("hosts")
  const modulesParam = search.get("modules")

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

  const syncUrl = () => {
    const q = new URLSearchParams()
    if (groupId !== null) q.set("scope", `group:${groupId}`)
    else if (hostIds.length) q.set("hosts", hostIds.join(","))
    if (!allModules) q.set("modules", modules.join(","))
    router.replace(`/plans?${q.toString()}`)
  }

  return { groupId, setGroupId, hostIds, setHostIds, modules, setModules, defined, group, scopeHosts, scopeLabel, moduleFilter, modulesLabel, syncUrl }
}

export type PlanScope = ReturnType<typeof usePlanScope>
