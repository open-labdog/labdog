"use client"

import { useState } from "react"
import { AlertTriangle, Check, X } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { AIApprovalRequest } from "@/lib/types"

interface Props {
  approval: AIApprovalRequest
  hostName?: string
  onDecide: (approve: boolean, note: string) => void
  pending?: boolean
}

/**
 * The one place a person authorises the agent to change a host.
 *
 * Three deliberate choices about what it shows.
 *
 * **The command is the headline**, in monospace, unwrapped and unabridged.
 * It is `command_preview` from the server rather than something rebuilt
 * from `arguments` here — what is displayed and what would run must not be
 * able to disagree, and rebuilding it in a second place is how they would.
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

  return (
    <div className="rounded-md border border-amber-600/60 bg-amber-950/20 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-amber-400" aria-hidden />
        <span className="text-sm font-semibold text-amber-200">
          Waiting for your decision
        </span>
        <Badge className="bg-amber-600 text-white">write</Badge>
        {hostName && (
          <Badge variant="outline" className="text-xs">
            {hostName}
          </Badge>
        )}
      </div>

      <p className="mt-2 text-sm text-slate-300">
        The assistant wants to run this. Nothing has been changed yet.
      </p>

      <pre className="mt-2 overflow-x-auto rounded bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100">
        {approval.command_preview}
      </pre>

      <p className="mt-2 text-xs text-slate-400">{approval.reason}</p>
      {approval.summary && (
        <p className="mt-1 text-xs text-slate-400">
          <span className="text-slate-500">It says this is because:</span>{" "}
          {approval.summary}
        </p>
      )}

      {/* Whether this can be undone is part of what is being decided, so
          it is stated before the buttons rather than discovered after. */}
      <p
        className={`mt-2 text-xs ${
          approval.snapshot_expected ? "text-slate-400" : "text-amber-300"
        }`}
      >
        {approval.snapshot_expected
          ? "A snapshot will be taken first, so this can be rolled back."
          : "No snapshot will be taken — this host has no Proxmox VM mapping, or snapshots are switched off. There will be no rollback point."}
      </p>
      {expires && (
        <p className="mt-1 text-xs text-slate-500">
          Expires {expires.toLocaleString()} — after that it is treated as not
          approved and the session finishes without it.
        </p>
      )}

      {showNote && (
        <Textarea
          className="mt-3 text-sm"
          rows={2}
          placeholder="Why not? This is passed back to the assistant."
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={pending}
          onClick={() => onDecide(true, "")}
          className="bg-emerald-600 text-white hover:bg-emerald-500"
        >
          <Check className="mr-1 h-4 w-4" aria-hidden />
          Approve and run
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={pending}
          onClick={() => {
            if (!showNote) {
              setShowNote(true)
              return
            }
            onDecide(false, note)
          }}
        >
          <X className="mr-1 h-4 w-4" aria-hidden />
          {showNote ? "Confirm rejection" : "Reject"}
        </Button>
      </div>
    </div>
  )
}

const DECIDED_LABEL: Record<string, string> = {
  approved: "Approved — LabDog ran this",
  rejected: "Rejected — not run",
  expired: "Expired — nobody decided, so it was not run",
}

const DECIDED_STYLE: Record<string, string> = {
  approved: "border-emerald-700/60 bg-emerald-950/20",
  rejected: "border-slate-700 bg-slate-900/60",
  expired: "border-slate-700 bg-slate-900/60",
}

/**
 * A decided request stays in the transcript rather than disappearing.
 * "The assistant asked to restart nginx and I said no" is part of the
 * record of what happened, and removing it would leave the model's next
 * turn responding to something the operator can no longer see.
 */
function DecidedApproval({ approval }: { approval: AIApprovalRequest }) {
  return (
    <div className={`rounded-md border p-3 ${DECIDED_STYLE[approval.status] ?? ""}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-slate-300">
          {DECIDED_LABEL[approval.status] ?? approval.status}
        </span>
      </div>
      <pre className="mt-2 overflow-x-auto rounded bg-slate-950 px-2 py-1 font-mono text-xs text-slate-300">
        {approval.command_preview}
      </pre>
      {approval.decision_note && (
        <p className="mt-2 text-xs text-slate-400">{approval.decision_note}</p>
      )}
    </div>
  )
}
