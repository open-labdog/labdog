"use client"

import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { showError, showSuccess } from "@/lib/toast"
import type { ActionDefinition, ActionPack, ClaimAllKeysResponse, ContestedActionKey } from "@/lib/types"

/** What "win all keys" would do for one pack, counted before asking. */
export interface ClaimSummary {
  pack: ActionPack
  pinnedHere: number
  pinnedElsewhere: number
  uncontested: number
  contested: number
}

/** "Win all keys": the confirmation's counts, and pinning every key a pack contributes. */
export function useClaimAll(catalogActions: ActionDefinition[] | undefined, contestedByKey: Record<string, ContestedActionKey>) {
  const [claimDialog, setClaimDialog] = useState<ClaimSummary | null>(null)
  const [claiming, setClaiming] = useState(false)
  const queryClient = useQueryClient()

  function openClaimDialog(pack: ActionPack) {
    // Pre-compute the diff for the confirmation dialog.
    const keysThisPackContributes = (catalogActions ?? []).filter((a) =>
      a.pack_name === pack.name ||
      a.overridden_from.includes(pack.name),
    )
    let pinnedHere = 0
    let pinnedElsewhere = 0
    let uncontested = 0
    let contested = 0
    for (const action of keysThisPackContributes) {
      const c = contestedByKey[action.key]
      if (!c) {
        // Uncontested — this pack is the sole contributor and the
        // claim is a no-op for the resolver (but still pins
        // explicitly for future contestants).
        uncontested += 1
        continue
      }
      contested += 1
      if (c.resolution?.pack_id === pack.id) {
        pinnedHere += 1
      } else if (c.resolution !== null) {
        pinnedElsewhere += 1
      }
    }
    setClaimDialog({ pack, pinnedHere, pinnedElsewhere, uncontested, contested })
  }

  async function handleClaim() {
    if (!claimDialog) return
    setClaiming(true)
    try {
      const result = await apiFetch<ClaimAllKeysResponse>(
        `/api/action-packs/${claimDialog.pack.id}/claim-all-keys`,
        { method: "POST" },
      )
      showSuccess(
        `Pinned ${claimDialog.pack.name}: ${result.created} new, ${result.updated} updated, ${result.skipped} unchanged.`,
      )
      await queryClient.invalidateQueries({ queryKey: ["action-resolutions"] })
      await queryClient.invalidateQueries({ queryKey: ["actions-catalog"] })
      setClaimDialog(null)
    } catch (err) {
      showError(err instanceof Error ? err.message : "Claim failed")
    } finally {
      setClaiming(false)
    }
  }

  return { claimDialog, claiming, openClaimDialog, closeClaimDialog: () => setClaimDialog(null), handleClaim }
}
