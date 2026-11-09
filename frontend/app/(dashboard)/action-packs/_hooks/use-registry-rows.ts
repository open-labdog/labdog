"use client"

import { useMemo } from "react"
import type { ActionDefinition, ActionPack, ContestedActionKey } from "@/lib/types"

/** One row of the action registry: an action key and, when several packs declare it, the contest. */
export interface RegistryRow {
  action: ActionDefinition
  contested: ContestedActionKey | null
}

/** The lookups the packs screen derives from its three queries. */
export function useRegistryRows(packs: ActionPack[] | undefined, contested: ContestedActionKey[] | undefined, catalogActions: ActionDefinition[] | undefined) {
  const orderedPacks = useMemo(() => {
    return [...(packs ?? [])].sort((a, b) => a.name.localeCompare(b.name))
  }, [packs])

  const packsById = useMemo(() => {
    const m: Record<number, ActionPack> = {}
    for (const p of packs ?? []) m[p.id] = p
    return m
  }, [packs])

  const contestedByKey = useMemo(() => {
    const m: Record<string, ContestedActionKey> = {}
    for (const c of contested ?? []) m[c.action_key] = c
    return m
  }, [contested])

  // How many action keys does each pack contribute (as either the
  // winner or an overridden contestant)? Used to disable the bulk-pin
  // button on packs that aren't a candidate for anything yet.
  const packKeyCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const a of catalogActions ?? []) {
      if (a.pack_name) m[a.pack_name] = (m[a.pack_name] ?? 0) + 1
      for (const overridden of a.overridden_from) {
        m[overridden] = (m[overridden] ?? 0) + 1
      }
    }
    return m
  }, [catalogActions])

  // Build the action registry view. One row per action key.
  // Built-in pseudo-actions (_builtin.*) are management-page noise —
  // they're never pack-supplied and never contested. Skip them here.
  const registryRows = useMemo<RegistryRow[]>(() => {
    const rows = (catalogActions ?? [])
      .filter((a) => !a.key.startsWith("_builtin."))
      .map((a) => {
        const contestedRow = contestedByKey[a.key] ?? null
        return { action: a, contested: contestedRow }
      })
    rows.sort((x, y) => x.action.key.localeCompare(y.action.key))
    return rows
  }, [catalogActions, contestedByKey])

  return { orderedPacks, packsById, contestedByKey, packKeyCounts, registryRows }
}
