"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess, showError } from "@/lib/toast"
import { Banner, Confirm, Panel } from "@/components/ld"
import type { ActionDefinition, ActionPack, ActionPackSyncResponse, ContestedActionKey, GitRepository } from "@/lib/types"
import { ClaimAllDialog } from "./_dialogs/claim-all"
import { PackFormDialog } from "./_dialogs/pack-form"
import { useClaimAll } from "./_hooks/use-claim-all"
import { usePackForm } from "./_hooks/use-pack-form"
import { useRegistryRows } from "./_hooks/use-registry-rows"
import { PackSourcesTable } from "./_tables/pack-sources"
import { RegistryTable } from "./_tables/registry"

/**
 * Action packs — the body of the Actions screen's Packs tab. The registry
 * (which pack owns each action key) is the primary surface; the sources
 * table underneath is where packs are added, synced and removed.
 *
 * The two tables live in `_tables/`, the add/edit and win-all-keys dialogs
 * in `_dialogs/`, and the state behind them in `_hooks/`.
 */
export default function ActionPacksPage() {
  const packForm = usePackForm()
  const [syncingId, setSyncingId] = useState<number | null>(null)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [confirmState, setConfirmState] = useState<{
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
  } | null>(null)

  const queryClient = useQueryClient()

  const {
    data: packs,
    isLoading,
    error,
  } = useQuery<ActionPack[]>({
    queryKey: ["action-packs"],
    queryFn: () => apiFetch<ActionPack[]>("/api/action-packs"),
  })

  const { data: gitRepos } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })

  const { data: contested } = useQuery<ContestedActionKey[]>({
    queryKey: ["action-resolutions"],
    queryFn: () => apiFetch<ContestedActionKey[]>("/api/action-resolutions"),
  })

  const { data: catalogActions, isLoading: catalogLoading } = useQuery<ActionDefinition[]>({
    queryKey: ["actions-catalog"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    staleTime: 30_000,
  })

  const { orderedPacks, packsById, contestedByKey, packKeyCounts, registryRows } = useRegistryRows(packs, contested, catalogActions)
  const claim = useClaimAll(catalogActions, contestedByKey)

  const deleteMutation = useApiMutation<unknown, number, ActionPack>({
    mutationFn: (packId) =>
      apiFetch(`/api/action-packs/${packId}`, { method: "DELETE" }),
    invalidateKeys: [["action-packs"], ["actions-catalog"], ["action-resolutions"]],
    successMessage: "Action pack deleted",
    optimisticUpdate: {
      queryKey: ["action-packs"],
      updater: (old, packId) => old.filter((p) => p.id !== packId),
    },
  })

  function handleDelete(pack: ActionPack) {
    setConfirmState({
      title: "Delete action pack",
      description: `${pack.name}'s checkout is removed, the actions it provided disappear from the registry, and keys pinned to it become unresolved — unrunnable until you pick a new winner. The linked Git repository, if any, is not affected.`,
      action: async () => {
        setConfirmState((prev) => (prev ? { ...prev, loading: true } : null))
        try {
          await deleteMutation.mutateAsync(pack.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  async function handleSync(pack: ActionPack) {
    setSyncingId(pack.id)
    try {
      const result = await apiFetch<ActionPackSyncResponse>(
        `/api/action-packs/${pack.id}/sync`,
        { method: "POST" },
      )
      if (result.success) {
        showSuccess(
          result.current_sha
            ? `Synced @ ${result.current_sha.slice(0, 8)}`
            : "Sync successful",
        )
      } else {
        showError(`Sync failed: ${result.message}`)
      }
      await queryClient.invalidateQueries({ queryKey: ["action-packs"] })
      await queryClient.invalidateQueries({ queryKey: ["actions-catalog"] })
      await queryClient.invalidateQueries({ queryKey: ["action-resolutions"] })
    } catch (err) {
      showError(err instanceof Error ? err.message : "Sync failed")
    } finally {
      setSyncingId(null)
    }
  }

  const unresolvedRows = registryRows.filter((r) => r.action.unresolved)

  return (
    <div className="flex flex-col gap-3">
      <div className="text-[11.5px] text-text-3">
        Each action key has at most one source pack. When several packs declare the same key, pick a winner per key — there is no global pack ordering. To bulk-add packs from one repository, use the scan wizard under{" "}
        <Link href="/git-repos" className="text-ld-accent">
          Settings › Git repositories
        </Link>
        .
      </div>

      {unresolvedRows.length > 0 && (
        <Banner tone="warn">
          <span className="font-medium">
            {unresolvedRows.length} action {unresolvedRows.length === 1 ? "key needs" : "keys need"} a decision
          </span>{" "}
          — pick a winning pack for each; until then these actions are blocked: <span className="mono">{unresolvedRows.map((r) => r.action.key).join(", ")}</span>
        </Banner>
      )}

      {/* ---- Action Registry — the primary surface ---- */}
      <Panel title="action registry" meta="every action key the live registry knows, and which pack owns it">
        <RegistryTable rows={registryRows} packsById={packsById} expandedRow={expandedRow} setExpandedRow={setExpandedRow} loading={catalogLoading} />
      </Panel>

      {/* ---- Pack Sources — management-only ---- */}
      <Panel
        title="pack sources"
        meta="where the packs come from"
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={packForm.openCreate}>
            Add pack…
          </button>
        }
      >
        {error && <Banner tone="danger">Could not load action packs: {error.message}</Banner>}
        <PackSourcesTable
          packs={orderedPacks}
          loading={isLoading}
          packKeyCounts={packKeyCounts}
          syncingId={syncingId}
          deletePending={deleteMutation.isPending}
          onSync={handleSync}
          onClaim={claim.openClaimDialog}
          onEdit={packForm.openEdit}
          onDelete={handleDelete}
        />
      </Panel>

      {packForm.dialogOpen && packForm.form && (
        <PackFormDialog
          editing={packForm.editing}
          form={packForm.form}
          formError={packForm.formError}
          formSaving={packForm.formSaving}
          gitRepos={gitRepos}
          setF={packForm.setF}
          onClose={packForm.closeDialog}
          onSave={() => void packForm.handleSave()}
        />
      )}

      {claim.claimDialog && <ClaimAllDialog claim={claim.claimDialog} claiming={claim.claiming} onClose={claim.closeClaimDialog} onClaim={() => void claim.handleClaim()} />}

      {confirmState && (
        <Confirm
          open
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel="Delete"
          variant="destructive"
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}
    </div>
  )
}
