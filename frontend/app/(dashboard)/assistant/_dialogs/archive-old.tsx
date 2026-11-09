"use client"

import { Field, Modal } from "@/components/ld"

/** "Archive old sessions": finished before this date, optionally only alert investigations. */
export interface ArchiveOld {
  before: string
  alertsOnly: boolean
}

/** Today minus `days`, as a date input's value. */
export function daysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

export function ArchiveOldDialog({
  archiveOld,
  setArchiveOld,
  pending,
  onCancel,
  onArchive,
}: {
  archiveOld: ArchiveOld
  setArchiveOld: (next: ArchiveOld) => void
  pending: boolean
  onCancel: () => void
  onArchive: (body: object) => void
}) {
  return (
    <Modal
      title="Archive old sessions"
      description="Archived sessions leave this list and keep everything else: the transcript, the rollback, and what the alert safeguards count. Running sessions are left alone."
      onClose={() => !pending && onCancel()}
      w={440}
      onSubmit={(e) => {
        e.preventDefault()
        onArchive({
          finished_before: new Date(`${archiveOld.before}T00:00:00`).toISOString(),
          ...(archiveOld.alertsOnly ? { mode: "alert_investigation" } : {}),
        })
      }}
      footer={
        <>
          <button type="button" className="btn ml-auto" disabled={pending} onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={pending || !archiveOld.before}>
            {pending ? "Archiving…" : "Archive"}
          </button>
        </>
      }
    >
      <Field label="finished before" htmlFor="archive-before">
        <input
          id="archive-before"
          type="date"
          className="inp mono"
          style={{ width: 160 }}
          value={archiveOld.before}
          max={daysAgo(0)}
          onChange={(e) => setArchiveOld({ ...archiveOld, before: e.target.value })}
        />
      </Field>
      <label className="flex items-center gap-2 text-[12px] text-text-2">
        <input type="checkbox" checked={archiveOld.alertsOnly} onChange={(e) => setArchiveOld({ ...archiveOld, alertsOnly: e.target.checked })} />
        alert investigations only
      </label>
    </Modal>
  )
}
