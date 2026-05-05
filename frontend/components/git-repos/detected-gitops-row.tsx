"use client"

import { AlertTriangleIcon, FileCogIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Tooltip } from "@/components/ui/tooltip"
import type { DetectedGitopsFile, HostGroup } from "@/lib/types"

export type GitopsSelection = { checked: boolean; host_group_id: number | null }

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
  const inUseElsewhere =
    selectedGroup !== null &&
    selectedGroup.git_repository_id !== null &&
    selectedGroup.git_repository_id !== currentRepoId

  const borderClass = inUseElsewhere
    ? "border-red-500/60"
    : hasErrors
    ? "border-amber-500/40"
    : "border-slate-700"
  const dimClass = hasErrors ? "opacity-60" : ""

  return (
    <div
      data-testid="detected-gitops-row"
      data-path={file.path}
      data-has-errors={hasErrors ? "true" : "false"}
      className={`rounded-lg border ${borderClass} bg-slate-900 p-4 ${dimClass}`}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          aria-label={`Bind gitops file ${file.path}`}
          className="mt-1 h-4 w-4 rounded border-slate-600 bg-slate-800 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
          checked={selection.checked}
          disabled={hasErrors}
          onChange={(e) => onToggle(e.target.checked)}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <FileCogIcon className="h-3.5 w-3.5 text-slate-500" />
            <span className="font-mono text-sm text-white truncate">{file.path}</span>
            {file.group_name && (
              <Tooltip content={`The file declares group: "${file.group_name}".`}>
                <Badge variant="outline" className="border-slate-600 text-slate-300">
                  group: {file.group_name}
                </Badge>
              </Tooltip>
            )}
            {inUseElsewhere && (
              <Tooltip content="This group is already bound to a different repository. Disable GitOps on it before re-binding.">
                <Badge className="bg-red-600 text-white">already bound</Badge>
              </Tooltip>
            )}
          </div>

          {hasErrors && (
            <ul className="mt-2 space-y-1">
              {file.errors.map((err, idx) => (
                <li key={idx} className="flex items-start gap-1.5 text-xs text-amber-300">
                  <AlertTriangleIcon className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  <span>
                    <span className="font-mono text-amber-200">{err.file}</span> — {err.message}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex flex-col items-end gap-1 min-w-[180px]">
          <label className="text-xs text-slate-500" htmlFor={`group-${file.path}`}>
            Bind to group
          </label>
          <select
            id={`group-${file.path}`}
            aria-label={`Bind ${file.path} to host group`}
            className="rounded-lg border border-input bg-transparent px-2 py-1 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring disabled:opacity-50 dark:bg-input/30"
            value={selection.host_group_id ?? ""}
            disabled={hasErrors || !selection.checked}
            onChange={(e) => onGroupChange(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">Select a group…</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )
}
