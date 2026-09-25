"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useActivityStream, type ActivityItem, type ActivityKind, type ActivityStatus } from "@/lib/activity"
import { shortAgo } from "@/lib/fleet"
import { JOB_STATUS, def } from "@/lib/status"
import { Dot, Filter, PageHead, Table, Tag } from "@/components/ld"

const KIND_LABEL: Record<ActivityKind, string> = { apply: "apply", action: "action", schedule: "scheduled", collect: "collect state", drift: "drift check" }
/** The stream's five words, in the order the filter lists them. */
const STATUSES: ActivityStatus[] = ["ok", "failed", "running", "queued", "cancelled"]

/**
 * Runs — one stream. Applies, action runs and scheduled runs were three
 * lists rendering the same object; this is the one place that answers
 * "what has been happening to my fleet".
 */
export default function RunsPage() {
  const router = useRouter()
  const [kind, setKind] = useState("all")
  const [status, setStatus] = useState("all")
  const { items, isLoading, error } = useActivityStream(100)

  const rows = useMemo(() => items.filter((i) => (kind === "all" || i.kind === kind) && (status === "all" || i.status === status)), [items, kind, status])
  const count = (f: (i: ActivityItem) => boolean) => items.filter(f).length

  return (
    <>
      <PageHead
        crumbs={[{ label: "operations" }]}
        title={
          <>
            Runs <span className="mono num text-[12.5px] font-normal text-text-faint">{rows.length} most recent</span>
          </>
        }
        sub="One stream: applies, action runs, scheduled runs and state collections, newest first. Open a run for its per-host transcript."
        actions={
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => router.push("/audit")}>
            audit trail →
          </button>
        }
      >
        <div className="flex flex-wrap items-center gap-[7px]">
          <Filter label="kind" value={kind} onChange={setKind} options={(Object.keys(KIND_LABEL) as ActivityKind[]).map((k) => ({ k, label: KIND_LABEL[k], n: count((i) => i.kind === k) }))} />
          <Filter label="status" value={status} onChange={setStatus} options={STATUSES.map((k) => ({ k, label: def(JOB_STATUS, k).label, n: count((i) => i.status === k) }))} />
          <span className="tt ml-auto">the last 100 sync jobs and action runs</span>
        </div>
      </PageHead>
      {error ? (
        <div className="p-6 text-center text-xs text-danger">Failed to load runs</div>
      ) : (
        <Table
          cols={[
            { k: "t", label: "when", w: "72px", sortable: false, cell: (a) => <span className="mono num text-[11px]" title={new Date(a.at).toLocaleString()}>{shortAgo(a.at)} ago</span> },
            { k: "kind", label: "kind", w: "104px", sortable: false, cell: (a) => <Tag tone={a.status === "failed" ? "danger" : undefined}>{KIND_LABEL[a.kind]}</Tag> },
            {
              k: "status",
              label: "status",
              w: "96px",
              sortable: false,
              cell: (a) => (
                <span className="inline-flex items-center gap-1.5 text-[11.5px] font-medium" style={{ color: `var(--${def(JOB_STATUS, a.status).tone})` }}>
                  <Dot tone={def(JOB_STATUS, a.status).tone} pulse={a.status === "running"} />
                  {def(JOB_STATUS, a.status).label}
                </span>
              ),
            },
            { k: "title", label: "what", w: "minmax(200px,1.5fr)", sortable: false, cell: (a) => <span className="text-text">{a.title}</span> },
            { k: "detail", label: "detail", w: "minmax(220px,1.8fr)", sortable: false, cell: (a) => <span className="mono text-[11px]" style={{ color: a.status === "failed" ? "var(--danger-ink)" : undefined }}>{a.detail || "—"}</span> },
            { k: "who", label: "actor", w: "80px", sortable: false, cell: (a) => <span className="mono text-[11px]">{a.who}</span> },
            { k: "s", label: "", w: "76px", right: true, sortable: false, cell: (a) => <span className="tt text-ld-accent">{a.href ? "open →" : a.hostId != null ? "host →" : ""}</span> },
          ]}
          rows={rows}
          keyOf={(a) => a.id}
          onRowClick={(a) => {
            if (a.href) router.push(a.href)
            else if (a.hostId != null) router.push(`/hosts/${a.hostId}`)
          }}
          rowTone={(a) => (a.status === "failed" ? "danger" : undefined)}
          loading={isLoading}
          empty="No runs yet. Applies and action runs land here as they happen."
        />
      )}
    </>
  )
}
