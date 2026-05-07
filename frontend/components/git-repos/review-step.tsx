"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Tooltip } from "@/components/ui/tooltip"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess } from "@/lib/toast"
import {
  DetectedPackRow,
  type PackSelection,
} from "@/components/git-repos/detected-pack-row"
import {
  DetectedGitopsRow,
  type GitopsSelection,
} from "@/components/git-repos/detected-gitops-row"
import type {
  ActivateKeyResolution,
  HostGroup,
  RepoActivateRequest,
  RepoActivateResponse,
  RepoScanResponse,
} from "@/lib/types"

type SelectionsState = {
  packs: Record<string, PackSelection>
  gitops: Record<string, GitopsSelection>
  /** Operator overrides for per-contested-key winners. Only keys the
   * operator changed appear here — others fall back to the default
   * (the new pack contributing the key). Encoded as:
   *   "new:<pack_path>"   — a pack from this activation
   *   "existing:<id>"     — an existing DB pack (the prior winner)
   *   "bundled"           — the bundled pack
   */
  keyOverrides: Record<string, string>
}

function defaultKeyResolution(
  key: string,
  scan: RepoScanResponse,
  contributingPaths: string[],
): string {
  // Prefer the operator's just-added pack — they explicitly opted into
  // it, so default to "new pack wins". Picks the highest-listed
  // contributing pack so the choice is stable across re-renders.
  if (contributingPaths.length > 0) {
    return `new:${contributingPaths[0]}`
  }
  const winner = scan.existing_key_winners[key]
  if (!winner) return "bundled"
  return winner.source === "bundled" ? "bundled" : `existing:${winner.pack_id}`
}

function computeDefaultSelections(
  scan: RepoScanResponse,
  groups: HostGroup[],
): SelectionsState {
  const packs: Record<string, PackSelection> = {}
  for (const pack of scan.packs) {
    const hasErrors = pack.errors.length > 0
    packs[pack.path] = { checked: !hasErrors }
  }
  const gitops: Record<string, GitopsSelection> = {}
  for (const file of scan.gitops_files) {
    const hasErrors = file.errors.length > 0
    const matchByName = file.group_name
      ? groups.find((g) => g.name === file.group_name) ?? null
      : null
    gitops[file.path] = {
      checked: !hasErrors && matchByName !== null,
      host_group_id: matchByName?.id ?? null,
    }
  }
  return { packs, gitops, keyOverrides: {} }
}

function detectUnresolvedIntraConflicts(
  scan: RepoScanResponse,
  selections: SelectionsState,
): Set<string> {
  const offenders = new Set<string>()
  for (const conflict of scan.intra_repo_key_conflicts) {
    const checkedPaths = conflict.contributing_packs.filter(
      (p) => selections.packs[p]?.checked === true,
    )
    if (checkedPaths.length > 1) {
      checkedPaths.forEach((p) => offenders.add(p))
    }
  }
  return offenders
}

/** Per-key info: which checked packs from the new repo contribute the
 * key and what the existing winner is. Only keys with both an
 * existing-side owner AND at least one checked new pack are
 * surfaced — the operator must pick a winner before activating. */
function computeContestedKeys(
  scan: RepoScanResponse,
  selections: SelectionsState,
): Array<{
  key: string
  contributingPaths: string[]
  existingWinner: { source: "bundled" | "db_pack"; pack_id: number | null; pack_name: string }
}> {
  const out: Array<{
    key: string
    contributingPaths: string[]
    existingWinner: {
      source: "bundled" | "db_pack"
      pack_id: number | null
      pack_name: string
    }
  }> = []
  for (const [key, owner] of Object.entries(scan.existing_key_winners)) {
    const contributors = scan.packs.filter(
      (p) =>
        selections.packs[p.path]?.checked === true &&
        p.contributed_keys.includes(key),
    )
    if (contributors.length === 0) continue
    out.push({
      key,
      contributingPaths: contributors.map((p) => p.path),
      existingWinner: owner,
    })
  }
  out.sort((a, b) => a.key.localeCompare(b.key))
  return out
}

export function ReviewStep({
  repoId,
  scanResult,
  onActivated,
  onRescan,
}: {
  repoId: number
  scanResult: RepoScanResponse
  onActivated: (response: RepoActivateResponse) => void
  onRescan?: () => void
}) {
  const { data: groups, isLoading: groupsLoading } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  if (groupsLoading || !groups) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 text-sm text-slate-400">
        Preparing review…
      </div>
    )
  }

  return (
    <ReviewStepInner
      repoId={repoId}
      scanResult={scanResult}
      groups={groups}
      onActivated={onActivated}
      onRescan={onRescan}
    />
  )
}

function ReviewStepInner({
  repoId,
  scanResult,
  groups,
  onActivated,
  onRescan,
}: {
  repoId: number
  scanResult: RepoScanResponse
  groups: HostGroup[]
  onActivated: (response: RepoActivateResponse) => void
  onRescan?: () => void
}) {
  const [selections, setSelections] = useState<SelectionsState>(() =>
    computeDefaultSelections(scanResult, groups),
  )

  const conflictKeys = useMemo(() => {
    const set = new Set<string>()
    scanResult.intra_repo_key_conflicts.forEach((c) => set.add(c.key))
    return set
  }, [scanResult.intra_repo_key_conflicts])

  const unresolvedIntraConflictPaths = useMemo(
    () => detectUnresolvedIntraConflicts(scanResult, selections),
    [scanResult, selections],
  )

  const contested = useMemo(
    () => computeContestedKeys(scanResult, selections),
    [scanResult, selections],
  )

  // Resolved per-key picks: operator override if present, otherwise
  // the default winner (the new pack contributing the key). Derived
  // — toggling a pack in/out simply re-runs this without dropping
  // previously-set overrides for keys still contested.
  const keyResolutions = useMemo(() => {
    const result: Record<string, string> = {}
    for (const c of contested) {
      result[c.key] =
        selections.keyOverrides[c.key] ??
        defaultKeyResolution(c.key, scanResult, c.contributingPaths)
    }
    return result
  }, [contested, scanResult, selections.keyOverrides])

  const activateMutation = useApiMutation<
    RepoActivateResponse,
    RepoActivateRequest
  >({
    mutationFn: (body) =>
      apiFetch<RepoActivateResponse>(`/api/git-repos/${repoId}/activate`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    invalidateKeys: [
      ["git-repos"],
      ["action-packs"],
      ["action-resolutions"],
      ["actions-catalog"],
      ["groups"],
    ],
    onSuccess: (data) => {
      showSuccess(
        `Activated ${data.activated_packs.length} pack${data.activated_packs.length === 1 ? "" : "s"} and ${data.activated_gitops_bindings.length} group binding${data.activated_gitops_bindings.length === 1 ? "" : "s"}.`,
      )
      onActivated(data)
    },
  })

  const checkedPacks = scanResult.packs.filter(
    (p) => selections.packs[p.path]?.checked === true,
  )
  const checkedGitops = scanResult.gitops_files.filter(
    (f) => selections.gitops[f.path]?.checked === true,
  )

  const gitopsMissingGroup = checkedGitops.some(
    (f) => selections.gitops[f.path]?.host_group_id == null,
  )
  const gitopsBoundElsewhere = checkedGitops.some((f) => {
    const groupId = selections.gitops[f.path]?.host_group_id
    if (groupId == null) return false
    const group = groups.find((g) => g.id === groupId)
    return (
      group != null &&
      group.git_repository_id != null &&
      group.git_repository_id !== repoId
    )
  })

  const hasUnresolvedIntra = unresolvedIntraConflictPaths.size > 0
  const nothingChecked = checkedPacks.length === 0 && checkedGitops.length === 0
  const missingKeyDecisions = contested.filter(
    (c) => !keyResolutions[c.key],
  )

  let activateBlockedReason: string | null = null
  if (hasUnresolvedIntra) {
    const conflictedKeys = scanResult.intra_repo_key_conflicts
      .filter(
        (c) =>
          c.contributing_packs.filter(
            (p) => selections.packs[p]?.checked === true,
          ).length > 1,
      )
      .map((c) => c.key)
    activateBlockedReason = `Resolve key conflict${conflictedKeys.length === 1 ? "" : "s"}: ${conflictedKeys.join(", ")}.`
  } else if (missingKeyDecisions.length > 0) {
    activateBlockedReason = `Pick a winner for: ${missingKeyDecisions.map((c) => c.key).join(", ")}.`
  } else if (gitopsMissingGroup) {
    activateBlockedReason = "Pick a host group for every checked GitOps file."
  } else if (gitopsBoundElsewhere) {
    activateBlockedReason =
      "One of the chosen groups is already bound to a different repository."
  } else if (nothingChecked) {
    activateBlockedReason = "Check at least one pack or GitOps file to activate."
  }

  function setPackSelection(path: string, partial: Partial<PackSelection>) {
    setSelections((prev) => {
      const current = prev.packs[path] ?? { checked: false }
      return {
        ...prev,
        packs: { ...prev.packs, [path]: { ...current, ...partial } },
      }
    })
  }

  function setGitopsSelection(path: string, partial: Partial<GitopsSelection>) {
    setSelections((prev) => {
      const current = prev.gitops[path] ?? {
        checked: false,
        host_group_id: null,
      }
      return {
        ...prev,
        gitops: { ...prev.gitops, [path]: { ...current, ...partial } },
      }
    })
  }

  function setKeyResolution(action_key: string, value: string) {
    setSelections((prev) => ({
      ...prev,
      keyOverrides: { ...prev.keyOverrides, [action_key]: value },
    }))
  }

  function buildKeyResolutions(): ActivateKeyResolution[] {
    return contested.map((c) => {
      const choice = keyResolutions[c.key]
      if (choice?.startsWith("new:")) {
        return {
          action_key: c.key,
          winner_pack_path: choice.slice("new:".length),
        }
      }
      if (choice?.startsWith("existing:")) {
        return {
          action_key: c.key,
          winner_existing_pack_id: Number(choice.slice("existing:".length)),
        }
      }
      return { action_key: c.key, winner_is_bundled: true }
    })
  }

  function handleActivate() {
    const body: RepoActivateRequest = {
      packs: checkedPacks.map((p) => ({ path: p.path, name: p.name })),
      gitops_bindings: checkedGitops
        .map((f) => {
          const id = selections.gitops[f.path]?.host_group_id
          return id == null ? null : { file_path: f.path, host_group_id: id }
        })
        .filter(
          (x): x is { file_path: string; host_group_id: number } => x !== null,
        ),
      key_resolutions: buildKeyResolutions(),
    }
    activateMutation.mutate(body)
  }

  return (
    <div className="space-y-6">
      {scanResult.scan_errors.length > 0 && (
        <div className="rounded-lg border border-red-500/40 bg-red-950/30 p-4">
          <p className="text-sm font-medium text-red-300">
            Scan reported infrastructure errors
          </p>
          <ul className="mt-2 space-y-1 text-xs text-red-200">
            {scanResult.scan_errors.map((err, idx) => (
              <li key={idx}>
                <span className="font-mono">{err.file}</span> — {err.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-200">
            Action packs
            <span className="ml-2 text-slate-500 tabular-nums">
              ({scanResult.packs.length})
            </span>
          </h2>
          {onRescan && (
            <Button type="button" variant="outline" size="sm" onClick={onRescan}>
              Re-scan
            </Button>
          )}
        </div>
        {scanResult.packs.length === 0 ? (
          <p className="text-sm text-slate-500">No packs detected.</p>
        ) : (
          <div className="space-y-3">
            {scanResult.packs.map((pack) => {
              const selection = selections.packs[pack.path] ?? { checked: false }
              return (
                <DetectedPackRow
                  key={pack.path}
                  pack={pack}
                  selection={selection}
                  existingWinners={scanResult.existing_key_winners}
                  conflictKeys={conflictKeys}
                  inUnresolvedConflict={unresolvedIntraConflictPaths.has(
                    pack.path,
                  )}
                  onToggle={(checked) =>
                    setPackSelection(pack.path, { checked })
                  }
                />
              )
            })}
          </div>
        )}
      </section>

      {contested.length > 0 && (
        <section>
          <h2 className="mb-1 text-sm font-medium text-slate-200">
            Resolve action-key conflicts
            <span className="ml-2 text-slate-500 tabular-nums">
              ({contested.length})
            </span>
          </h2>
          <p className="text-xs text-slate-500 mb-3">
            For each key that already has an owner and would be contributed
            by a new pack, choose which pack should win.
          </p>
          <div className="space-y-3">
            {contested.map((c) => {
              const choice = keyResolutions[c.key]
              const existingValue =
                c.existingWinner.source === "bundled"
                  ? "bundled"
                  : `existing:${c.existingWinner.pack_id}`
              return (
                <div
                  key={c.key}
                  className="rounded-lg border border-slate-700 bg-slate-900 p-3"
                  data-testid="contested-key-row"
                  data-action-key={c.key}
                >
                  <p className="font-mono text-sm text-white">{c.key}</p>
                  <div className="mt-2 space-y-1.5">
                    {c.contributingPaths.map((path) => {
                      const value = `new:${path}`
                      const pack = scanResult.packs.find((p) => p.path === path)
                      return (
                        <label
                          key={value}
                          className="flex items-center gap-2 text-sm cursor-pointer rounded px-2 py-1 hover:bg-slate-800"
                        >
                          <input
                            type="radio"
                            name={`winner-${c.key}`}
                            checked={choice === value}
                            onChange={() => setKeyResolution(c.key, value)}
                          />
                          <span className="text-slate-200 flex-1">
                            {pack?.name ?? path}{" "}
                            <span className="text-slate-500 text-xs">
                              (new — {path || "repo root"})
                            </span>
                          </span>
                        </label>
                      )
                    })}
                    <label className="flex items-center gap-2 text-sm cursor-pointer rounded px-2 py-1 hover:bg-slate-800">
                      <input
                        type="radio"
                        name={`winner-${c.key}`}
                        checked={choice === existingValue}
                        onChange={() => setKeyResolution(c.key, existingValue)}
                      />
                      <span className="text-slate-200 flex-1">
                        {c.existingWinner.pack_name}{" "}
                        <span className="text-slate-500 text-xs">
                          (existing —{" "}
                          {c.existingWinner.source === "bundled"
                            ? "bundled"
                            : "DB pack"}
                          )
                        </span>
                      </span>
                    </label>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-3 text-sm font-medium text-slate-200">
          GitOps files
          <span className="ml-2 text-slate-500 tabular-nums">
            ({scanResult.gitops_files.length})
          </span>
        </h2>
        {scanResult.gitops_files.length === 0 ? (
          <p className="text-sm text-slate-500">No GitOps files detected.</p>
        ) : (
          <div className="space-y-3">
            {scanResult.gitops_files.map((file) => {
              const selection = selections.gitops[file.path] ?? {
                checked: false,
                host_group_id: null,
              }
              return (
                <DetectedGitopsRow
                  key={file.path}
                  file={file}
                  selection={selection}
                  groups={groups}
                  currentRepoId={repoId}
                  onToggle={(checked) =>
                    setGitopsSelection(file.path, { checked })
                  }
                  onGroupChange={(id) =>
                    setGitopsSelection(file.path, { host_group_id: id })
                  }
                />
              )
            })}
          </div>
        )}
      </section>

      {activateMutation.error && (
        <p className="text-sm text-red-400">{activateMutation.error.message}</p>
      )}

      <div className="flex items-center justify-between gap-3 pt-2">
        <p className="text-xs text-slate-500">
          {checkedPacks.length} pack{checkedPacks.length === 1 ? "" : "s"} •{" "}
          {checkedGitops.length} group binding
          {checkedGitops.length === 1 ? "" : "s"} selected
        </p>
        {activateBlockedReason ? (
          <Tooltip content={activateBlockedReason}>
            <Button type="button" disabled data-testid="activate-button">
              Activate
            </Button>
          </Tooltip>
        ) : (
          <Button
            type="button"
            onClick={handleActivate}
            disabled={activateMutation.isPending}
            data-testid="activate-button"
          >
            {activateMutation.isPending ? "Activating..." : "Activate"}
          </Button>
        )}
      </div>
    </div>
  )
}
