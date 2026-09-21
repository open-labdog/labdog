"use client"

import { Tag } from "@/components/ld"
import type { DetectedGitopsFile, HostGroup } from "@/lib/types"

export type GitopsSelection = { checked: boolean; host_group_id: number | null }

/**
 * One GitOps file the scan found: a checkbox to bind it, the group it
 * names, and the select that decides which host group it feeds.
 */
export function DetectedGitopsRow({
  file,
  selection,
  groups,
  currentRepoId,
  onToggle,
  onGroupChange,
}: {
  file: DetectedGitopsFile
  selection: GitopsSelection
  groups: HostGroup[]
  currentRepoId: number
  onToggle: (checked: boolean) => void
  onGroupChange: (id: number | null) => void
}) {
  const hasErrors = file.errors.length > 0
  const selectedGroup = groups.find((g) => g.id === selection.host_group_id) ?? null
  const inUseElsewhere = selectedGroup !== null && selectedGroup.git_repository_id !== null && selectedGroup.git_repository_id !== currentRepoId
  const border = inUseElsewhere ? "var(--danger)" : hasErrors ? "var(--warn)" : "var(--border)"

  return (
    <div
      data-testid="detected-gitops-row"
      data-path={file.path}
      data-has-errors={hasErrors ? "true" : "false"}
      className="flex flex-wrap items-start gap-2.5 rounded-r border bg-surface-2 px-2.5 py-2"
      style={{ borderColor: border, opacity: hasErrors ? 0.7 : 1 }}
    >
      <label className="flex min-w-0 flex-1 items-start gap-2.5">
        <input type="checkbox" aria-label={`Bind gitops file ${file.path}`} className="mt-0.5" checked={selection.checked} disabled={hasErrors} onChange={(e) => onToggle(e.target.checked)} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mono trunc text-xs text-text">{file.path}</span>
            {file.group_name && <Tag title={`the file declares group: "${file.group_name}"`}>group: {file.group_name}</Tag>}
            {inUseElsewhere && (
              <Tag tone="danger" title="this group is already bound to a different repository — disable GitOps on it before re-binding">
                already bound
              </Tag>
            )}
          </div>
          {hasErrors && (
            <ul className="m-0 mt-1.5 flex list-none flex-col gap-0.5 p-0 text-[11px] text-warn">
              {file.errors.map((err, idx) => (
                <li key={idx}>
                  <span className="mono">{err.file}</span> — {err.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      </label>
      <label className="flex flex-col gap-1" style={{ width: 200 }}>
        <span className="tt">bind to group</span>
        <select
          id={`group-${file.path}`}
          aria-label={`Bind ${file.path} to host group`}
          className="inp mono"
          value={selection.host_group_id ?? ""}
          disabled={hasErrors || !selection.checked}
          onChange={(e) => onGroupChange(e.target.value === "" ? null : Number(e.target.value))}
        >
          <option value="">— pick a group —</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
