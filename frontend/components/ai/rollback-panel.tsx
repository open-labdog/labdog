"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { Confirm, Dot, Tag } from "@/components/ld"
import { apiFetch, ApiError } from "@/lib/api"
import { AI_ROLLBACK, def } from "@/lib/status"
import { formatTimestamp } from "@/lib/utils"
import type { AIRollback, AIRollbackTarget, AISessionDetail } from "@/lib/types"

const note = "text-[11px] leading-[1.5] text-text-3"

/**
 * Putting a host back the way it was before this session changed it.
 *
 * Only one snapshot is offered per host — the one taken before the
 * session's first change there. Restoring a later one would leave the
 * earlier changes in place and call them undone. Below the buttons, every
 * rollback of this session, including the automatic one after a fix made
 * its host worse, and the ones LabDog refused, with why.
 */
export function RollbackPanel({ session }: { session: AISessionDetail }) {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState<AIRollbackTarget | null>(null)

  const rollBack = useMutation({
    mutationFn: (hostId: number) =>
      apiFetch<AIRollback>(`/api/ai/sessions/${session.id}/rollback`, { method: "POST", json: { host_id: hostId } }),
    onSuccess: (rollback) => {
      toast.success(`Rolling ${rollback.hostname} back to ${rollback.snapshot_name}. It restarts, which takes a few minutes.`)
    },
    onError: (err: unknown) => {
      // A refusal says exactly why — LabDog runs there, a sync is working
      // on it, the snapshot has expired — so it is passed on verbatim.
      toast.error(err instanceof ApiError || err instanceof Error ? err.message : "Could not roll back")
    },
    onSettled: () => {
      setAsking(null)
      queryClient.invalidateQueries({ queryKey: ["ai-session", session.id] })
    },
  })

  const targets = session.rollback_targets ?? []
  const history = session.rollbacks ?? []
  if (targets.length === 0 && history.length === 0) return null

  return (
    <div className="flex flex-col gap-[7px]">
      <span className="tt">roll back</span>
      {targets.map((t) => (
        <div key={t.host_id} className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="mono trunc flex-1 text-[11.5px] text-text" title={t.snapshot_name ?? undefined}>
              {t.hostname}
            </span>
            <button
              type="button"
              className="btn btn-sm btn-danger"
              disabled={t.unavailable_reason !== null || rollBack.isPending}
              title={t.unavailable_reason ?? `Restore ${t.snapshot_name}`}
              onClick={() => setAsking(t)}
            >
              Roll back
            </button>
          </div>
          {t.unavailable_reason && <span className={note}>{t.unavailable_reason}</span>}
        </div>
      ))}
      {history.map((r) => {
        const d = def(AI_ROLLBACK, r.status)
        return (
          <div key={r.id} className="flex flex-col gap-0.5" title={r.detail ?? undefined}>
            <span className="flex items-center gap-2 text-[11.5px] text-text">
              <Dot tone={d.tone} />
              <span className="flex-1">{d.label}</span>
              <Tag>{r.trigger}</Tag>
            </span>
            <span className={note}>
              {r.hostname} · {formatTimestamp(r.started_at)}
              {r.detail ? ` — ${r.detail}` : ""}
            </span>
          </div>
        )
      })}
      <Confirm
        open={asking !== null}
        onOpenChange={(open) => !open && setAsking(null)}
        title={asking ? `Roll ${asking.hostname} back?` : "Roll back?"}
        description={
          asking
            ? `Proxmox restores ${asking.snapshot_name}, taken ${asking.snapshot_taken_at ? formatTimestamp(asking.snapshot_taken_at) : "before the first change"}. ` +
              "The machine restarts, and everything written on it since then is lost — not only this session's changes. " +
              "LabDog then marks the host out of sync."
            : ""
        }
        confirmLabel="Roll back"
        variant="destructive"
        loading={rollBack.isPending}
        onConfirm={() => {
          if (asking) rollBack.mutate(asking.host_id)
        }}
      />
    </div>
  )
}
