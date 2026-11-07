"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { ActionDefinition, ActionRun } from "@/lib/types"
import { ActionRunDialog } from "@/components/action-run-dialog"

/**
 * "Re-run…" on a finished run's page. Opens the ordinary run dialog
 * against the same host or group, with the form filled in from the run's
 * own parameters and parallelism, so the operator can change any of it
 * before starting the new run.
 *
 * Disabled, with the reason as its title, when the run can't be repeated
 * from here: a fleet run (only a schedule dispatches those — the API's
 * create endpoint takes a host or a group), a run whose target was
 * deleted, or an action no pack supplies any more.
 */
export function RerunActionButton({ run }: { run: ActionRun }) {
  const [open, setOpen] = useState(false)

  const { data: catalog } = useQuery<ActionDefinition[]>({
    queryKey: ["actions-catalog"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    staleTime: 60_000,
  })
  const action = catalog?.find((a) => a.key === run.action_key) ?? null
  const scope: "host" | "group" | null = run.host_id !== null ? "host" : run.group_id !== null ? "group" : null
  const targetId = run.host_id ?? run.group_id

  const blocker =
    run.target_kind === "fleet"
      ? "fleet runs are started by a schedule — run it again from Scheduled actions"
      : scope === null || targetId === null
        ? `the ${run.target_kind} this run targeted was deleted`
        : !catalog
          ? "loading actions…"
          : !action
            ? `no pack supplies ${run.action_key} any more`
            : null

  return (
    <>
      <button
        type="button"
        className="btn btn-sm"
        disabled={blocker !== null}
        title={blocker ?? "run this action again with the same target and settings"}
        data-testid="rerun-action"
        onClick={() => setOpen(true)}
      >
        Re-run…
      </button>
      {open && action && scope && targetId !== null && (
        <ActionRunDialog
          action={action}
          scope={scope}
          targetId={targetId}
          targetLabel={run.target_label.replace(/^group:\s*/i, "")}
          open
          onClose={() => setOpen(false)}
          rerunOf={run}
        />
      )}
    </>
  )
}
