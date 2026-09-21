"use client"

import { useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { ActionDefinition, Host } from "@/lib/types"
import { ActionRunDialog } from "@/components/action-run-dialog"

/**
 * "Run action…" from a target. It opens the same dialog an action's own
 * run button opens (components/action-run-dialog.tsx); the difference is
 * the entry point, so the dialog also picks the action, offering every
 * one the target supports. Built-in pseudo-actions (sync, drift check,
 * collect state) have their own surfaces and are left out.
 */
export function RunActionButton({
  scope,
  targetId,
  targetLabel,
  host,
  className = "btn btn-sm",
  children = "Run action…",
}: {
  scope: "host" | "group"
  targetId: number
  targetLabel?: string
  /** The host, when the target is one — its OS codename pre-fills upgrade actions. */
  host?: Host
  className?: string
  children?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [picked, setPicked] = useState<ActionDefinition | null>(null)

  const { data: catalog } = useQuery<ActionDefinition[]>({
    queryKey: ["actions-catalog"],
    queryFn: () => apiFetch<ActionDefinition[]>("/api/actions/"),
    staleTime: 60_000,
  })
  const actions = (catalog ?? []).filter((a) => !a.key.startsWith("_builtin.") && (scope === "host" ? a.supports_host : a.supports_group))
  const current = picked && actions.some((a) => a.key === picked.key) ? picked : (actions[0] ?? null)

  return (
    <>
      <button
        type="button"
        className={className}
        disabled={actions.length === 0}
        title={actions.length === 0 ? (catalog ? `no action supports a ${scope}` : "loading actions…") : `pick an action to run on this ${scope}`}
        onClick={() => setOpen(true)}
      >
        {children}
      </button>
      <ActionRunDialog
        action={open ? current : null}
        actions={actions}
        onPickAction={setPicked}
        scope={scope}
        targetId={targetId}
        targetLabel={targetLabel}
        open={open}
        onClose={() => setOpen(false)}
        hostOsCodename={scope === "host" ? host?.os_codename : undefined}
      />
    </>
  )
}
