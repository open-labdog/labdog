"use client"

import { Modal } from "@/components/ld"
import type { ClaimSummary } from "../_hooks/use-claim-all"

/** Confirm "win all keys" for one pack, with what it would change. */
export function ClaimAllDialog({ claim, claiming, onClose, onClaim }: { claim: ClaimSummary; claiming: boolean; onClose: () => void; onClaim: () => void }) {
  return (
    <Modal
      title={`Make ${claim.pack.name} the winner for all its keys`}
      w={480}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ml-auto" onClick={onClose} disabled={claiming}>
            {claim.contested + claim.uncontested === 0 ? "Close" : "Cancel"}
          </button>
          {claim.contested + claim.uncontested > 0 && (
            <button type="button" className="btn btn-primary" onClick={onClaim} disabled={claiming}>
              {claiming ? "Pinning…" : "Pin all keys"}
            </button>
          )}
        </>
      }
    >
      {claim.contested + claim.uncontested === 0 ? (
        <div className="text-[12.5px] text-text-2">
          <span className="mono text-text">{claim.pack.name}</span> hasn&apos;t contributed any action keys yet, so there is nothing to pin. Sync the pack first, or check that its manifest is valid and
          discoverable.
        </div>
      ) : (
        <>
          <div className="text-[12.5px] text-text-2">
            This pack contributes to <span className="mono num text-text">{claim.contested + claim.uncontested}</span> action key{claim.contested + claim.uncontested === 1 ? "" : "s"}.
          </div>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-[11.5px] text-text-3">
            <li>
              <span className="mono num text-text">{claim.uncontested}</span> uncontested — a no-op for the resolver, but pinned explicitly so future contestants don&apos;t auto-claim them
            </li>
            <li>
              <span className="mono num text-text">{claim.contested}</span> contested: <span className="text-ok">{claim.pinnedHere} already pinned here</span>,{" "}
              <span className="text-warn">{claim.pinnedElsewhere} pinned elsewhere (overwritten)</span>, {claim.contested - claim.pinnedHere - claim.pinnedElsewhere} unpinned (pinned here)
            </li>
          </ul>
        </>
      )}
    </Modal>
  )
}
