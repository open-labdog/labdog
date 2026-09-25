"use client"

import { useState } from "react"

import { Dot, Tag } from "@/components/ld"
import { AI_APPROVAL, AI_CLASSIFICATION, def } from "@/lib/status"
import type { AIApprovalRequest } from "@/lib/types"

interface Props {
  approval: AIApprovalRequest
  hostName?: string
  onDecide: (approve: boolean, note: string) => void
  pending?: boolean
}

/**
 * The one place a person authorises the agent to change a host — the
 * design's approval gate.
 *
 * Three deliberate choices about what it shows.
 *
 * **The command is the headline**, in monospace, unwrapped and unabridged.
 * It is `command_preview` from the server rather than something rebuilt
 * from `arguments` here — what is displayed and what would run must not be
 * able to disagree, and rebuilding it in a second place is how they would.
 * (The design tucks an "exact command" under a disclosure beneath a
 * friendlier summary; here there is no second form to disclose, so the
 * exact command stays the headline.)
 *
 * **The classifier's reason outranks the model's.** `reason` is why LabDog
 * called this a write and is shown as the primary justification; `summary`
 * is the model's own stated purpose, shown after and labelled as its
 * claim. The model is the thing being authorised, so its account of its
 * own intentions is evidence, not a verdict.
 *
 * **Rejecting takes a note and approving does not.** A rejection without a
 * reason teaches the model nothing and it will propose something similar;
 * the note is passed back to it. An approval needs no explanation because
 * the outcome speaks for itself.
 */
export function ApprovalCard({ approval, hostName, onDecide, pending }: Props) {
  const [note, setNote] = useState("")
  const [showNote, setShowNote] = useState(false)

  if (approval.status !== "pending") {
    return <DecidedApproval approval={approval} />
  }

  const expires = approval.expires_at ? new Date(approval.expires_at) : null
  const cls = def(AI_CLASSIFICATION, approval.classification)

  return (
    <div className="flex flex-col gap-[9px] rounded-r-lg border border-hold bg-hold-soft p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Tag tone={cls.tone}>{cls.label}</Tag>
        <span className="text-[12.5px] font-semibold text-text">Waiting for your decision</span>
        {hostName && <Tag>{hostName}</Tag>}
        {expires && (
          <span className="tt ml-auto" title={expires.toISOString()}>
            expires {expires.toLocaleString()}
          </span>
        )}
      </div>

      <p className="m-0 text-[11.5px] text-text-2">The assistant wants to run this. Nothing has been changed yet.</p>

      <pre className="mono m-0 overflow-x-auto rounded-r border border-line bg-surface px-[9px] py-[7px] text-xs text-text">
        {approval.command_preview}
      </pre>

      <p className="m-0 text-[11.5px] leading-[1.5] text-text-2">
        <b className="font-semibold text-text">Why it stopped here:</b> {approval.reason}
      </p>
      {approval.summary && (
        <p className="m-0 text-[11.5px] leading-[1.5] text-text-3">
          <span className="text-text-faint">It says this is because:</span> {approval.summary}
        </p>
      )}

      {/* Whether this can be undone is part of what is being decided, so
          it is stated before the buttons rather than discovered after. */}
      <p className={`m-0 text-[11.5px] leading-[1.5] ${approval.snapshot_expected ? "text-text-3" : "font-medium text-text"}`}>
        {approval.snapshot_expected
          ? "A snapshot will be taken first, so this can be rolled back."
          : "No snapshot will be taken — this host has no Proxmox VM mapping, or snapshots are switched off. There will be no rollback point."}
      </p>
      {expires && (
        <p className="m-0 text-[11px] text-text-3">
          After it expires it is treated as not approved and the session finishes without it.
        </p>
      )}

      {showNote && (
        <textarea
          className="inp"
          rows={2}
          aria-label="why not"
          placeholder="Why not? This is passed back to the assistant."
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      )}

      <div className="flex flex-wrap gap-[7px]">
        <button type="button" className="btn btn-sm btn-primary" disabled={pending} onClick={() => onDecide(true, "")}>
          Approve and run
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={pending}
          onClick={() => {
            if (!showNote) {
              setShowNote(true)
              return
            }
            onDecide(false, note)
          }}
        >
          {showNote ? "Confirm rejection" : "Reject"}
        </button>
      </div>
    </div>
  )
}

const DECIDED_LABEL: Record<string, string> = {
  approved: "Approved — LabDog ran this",
  rejected: "Rejected — not run",
  expired: "Expired — nobody decided, so it was not run",
}

/**
 * A decided request stays in the transcript rather than disappearing.
 * "The assistant asked to restart nginx and I said no" is part of the
 * record of what happened, and removing it would leave the model's next
 * turn responding to something the operator can no longer see.
 */
function DecidedApproval({ approval }: { approval: AIApprovalRequest }) {
  const tone = def(AI_APPROVAL, approval.status).tone
  return (
    <div className="flex flex-col gap-1.5 rounded-r border border-line bg-surface p-2.5">
      <span className="flex items-center gap-2 text-[11.5px] font-medium text-text-2">
        <Dot tone={tone} />
        {DECIDED_LABEL[approval.status] ?? approval.status}
      </span>
      <pre className="mono m-0 overflow-x-auto text-[11px] text-text-2">{approval.command_preview}</pre>
      {approval.decision_note && <p className="m-0 text-[11px] text-text-3">{approval.decision_note}</p>}
    </div>
  )
}
