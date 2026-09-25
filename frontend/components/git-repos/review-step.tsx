"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Banner, Panel } from "@/components/ld"
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
    return <div className="p-3 text-xs text-text-3">Preparing review…</div>
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

  const n = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`

  return (
    <div className="flex flex-col gap-3">
      {scanResult.scan_errors.length > 0 && (
        <Banner tone="danger">
          <span className="font-medium">Scan reported infrastructure errors.</span>{" "}
          {scanResult.scan_errors.map((err, idx) => (
            <span key={idx}>
              <span className="mono">{err.file}</span> — {err.message}
              {idx < scanResult.scan_errors.length - 1 ? "; " : ""}
            </span>
          ))}
        </Banner>
      )}

      <Panel
        title="action packs"
        meta={n(scanResult.packs.length, "pack")}
        actions={
          onRescan && (
            <button type="button" className="btn btn-sm" onClick={onRescan}>
              Re-scan
            </button>
          )
        }
      >
        {scanResult.packs.length === 0 ? (
          <div className="p-[11px] text-[11.5px] text-text-3">No packs detected.</div>
        ) : (
          <div className="flex flex-col gap-1.5 p-[11px]">
            {scanResult.packs.map((pack) => {
              const selection = selections.packs[pack.path] ?? { checked: false }
              return (
                <DetectedPackRow
                  key={pack.path}
                  pack={pack}
                  selection={selection}
                  existingWinners={scanResult.existing_key_winners}
                  conflictKeys={conflictKeys}
                  inUnresolvedConflict={unresolvedIntraConflictPaths.has(pack.path)}
                  onToggle={(checked) => setPackSelection(pack.path, { checked })}
                />
              )
            })}
          </div>
        )}
      </Panel>

      {contested.length > 0 && (
        <Panel title="resolve action-key conflicts" meta={n(contested.length, "key")}>
          <div className="flex flex-col gap-1.5 p-[11px]">
            <div className="text-[11.5px] text-text-3">For each key that already has an owner and would be contributed by a new pack, choose which pack wins.</div>
            {contested.map((c) => {
              const choice = keyResolutions[c.key]
              const existingValue = c.existingWinner.source === "bundled" ? "bundled" : `existing:${c.existingWinner.pack_id}`
              return (
                <div key={c.key} className="rounded-r border border-line bg-surface-2 px-2.5 py-2" data-testid="contested-key-row" data-action-key={c.key}>
                  <div className="mono text-xs text-text">{c.key}</div>
                  <div className="mt-1.5 flex flex-col gap-1">
                    {c.contributingPaths.map((path) => {
                      const value = `new:${path}`
                      const pack = scanResult.packs.find((p) => p.path === path)
                      return (
                        <label key={value} className="row-hover flex cursor-pointer items-center gap-2 rounded-r px-1.5 py-1 text-xs">
                          <input type="radio" name={`winner-${c.key}`} checked={choice === value} onChange={() => setKeyResolution(c.key, value)} />
                          <span className="flex-1 text-text">
                            {pack?.name ?? path} <span className="text-[11px] text-text-3">(new — {path || "repo root"})</span>
                          </span>
                        </label>
                      )
                    })}
                    <label className="row-hover flex cursor-pointer items-center gap-2 rounded-r px-1.5 py-1 text-xs">
                      <input type="radio" name={`winner-${c.key}`} checked={choice === existingValue} onChange={() => setKeyResolution(c.key, existingValue)} />
                      <span className="flex-1 text-text">
                        {c.existingWinner.pack_name} <span className="text-[11px] text-text-3">(existing — {c.existingWinner.source === "bundled" ? "bundled" : "DB pack"})</span>
                      </span>
                    </label>
                  </div>
                </div>
              )
            })}
          </div>
        </Panel>
      )}

      <Panel title="gitops files" meta={n(scanResult.gitops_files.length, "file")}>
        {scanResult.gitops_files.length === 0 ? (
          <div className="p-[11px] text-[11.5px] text-text-3">No GitOps files detected.</div>
        ) : (
          <div className="flex flex-col gap-1.5 p-[11px]">
            {scanResult.gitops_files.map((file) => {
              const selection = selections.gitops[file.path] ?? { checked: false, host_group_id: null }
              return (
                <DetectedGitopsRow
                  key={file.path}
                  file={file}
                  selection={selection}
                  groups={groups}
                  currentRepoId={repoId}
                  onToggle={(checked) => setGitopsSelection(file.path, { checked })}
                  onGroupChange={(id) => setGitopsSelection(file.path, { host_group_id: id })}
                />
              )
            })}
          </div>
        )}
      </Panel>

      {activateMutation.error && <Banner tone="danger">{activateMutation.error.message}</Banner>}

      <div className="flex flex-wrap items-center gap-3">
        <span className="tt">
          {n(checkedPacks.length, "pack")} · {n(checkedGitops.length, "group binding")} selected
        </span>
        {activateBlockedReason && <span className="text-[11.5px] text-warn">{activateBlockedReason}</span>}
        <button
          type="button"
          className="btn btn-primary ml-auto"
          onClick={handleActivate}
          disabled={!!activateBlockedReason || activateMutation.isPending}
          title={activateBlockedReason ?? undefined}
          data-testid="activate-button"
        >
          {activateMutation.isPending ? "Activating…" : "Activate"}
        </button>
      </div>
    </div>
  )
}
