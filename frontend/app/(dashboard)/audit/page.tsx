"use client"

import { useMemo, useState } from "react"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { def, AUDIT_ACTION } from "@/lib/status"
import { Banner, CodeBlock, Filter, Modal, PageHead, Table, Tag } from "@/components/ld"
import type { AuditLogEntry } from "@/lib/types"

interface TranscriptRow {
  id: number
  session_id: string
  host_id: number | null
  user_id: number | null
  command_text: string
  recorded_at: string
}

function TranscriptModal({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const { data, isLoading, error } = useQuery<TranscriptRow[]>({
    queryKey: ["ssh-transcript", sessionId],
    queryFn: () => apiFetch<TranscriptRow[]>(`/api/audit-log/ssh-sessions/${sessionId}/transcript`),
    retry: false,
  })
  const joined = data?.map((r) => r.command_text).join("\n") ?? ""

  return (
    <Modal title="SSH session transcript" w={760} onClose={onClose}>
      <Banner tone="sync">This transcript shows what the operator typed (stdin only) — not the host output. Control characters may appear as-is.</Banner>
      {isLoading && <span className="text-[11.5px] text-text-3">Loading transcript…</span>}
      {error && <span className="text-[11.5px] text-text-3">No transcript captured for this session.</span>}
      {!isLoading && !error && data?.length === 0 && <span className="text-[11.5px] text-text-3">No transcript rows found for this session.</span>}
      {!isLoading && !error && data && data.length > 0 && <CodeBlock maxH="60vh">{joined}</CodeBlock>}
    </Modal>
  )
}

/** Rows per request. The endpoint caps `limit` at 200; 100 keeps a page
 *  worth of filtering material in hand without a slow first paint. */
const PAGE_SIZE = 100

export default function AuditPage() {
  const [transcriptSessionId, setTranscriptSessionId] = useState<string | null>(null)
  const [actionFilter, setActionFilter] = useState("all")
  const [entityFilter, setEntityFilter] = useState("all")

  // Cursor-paginated (BUG-75). This used to fetch once with no `limit`,
  // which meant the backend default of 50 — everything older than the
  // fiftieth entry was unreachable, and the column filters searched only
  // those fifty. The cursor is the id of the last row seen.
  const { data, isLoading, error, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery<AuditLogEntry[]>({
    queryKey: ["audit-log"],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => {
      const cursor = pageParam as number | undefined
      return apiFetch<AuditLogEntry[]>(`/api/audit-log?limit=${PAGE_SIZE}${cursor === undefined ? "" : `&cursor=${cursor}`}`)
    },
    getNextPageParam: (lastPage) => (lastPage.length === PAGE_SIZE ? lastPage[lastPage.length - 1].id : undefined),
    retry: false,
  })

  const entries = useMemo(() => data?.pages.flat() ?? [], [data])

  // The action vocabulary is fixed (lib/status.ts AUDIT_ACTION), so every
  // kind is always offered — not just the ones on the currently-loaded
  // page, which may hold none of a kind that is simply rarer — with a
  // count for however many of each have loaded so far (0 if none yet).
  const actionOptions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const e of entries) counts.set(e.action, (counts.get(e.action) ?? 0) + 1)
    return Object.keys(AUDIT_ACTION).map((k) => ({ k, label: def(AUDIT_ACTION, k).label, n: counts.get(k) ?? 0 }))
  }, [entries])
  const entityOptions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const e of entries) counts.set(e.entity_type, (counts.get(e.entity_type) ?? 0) + 1)
    return [...counts.entries()].map(([k, n]) => ({ k, label: k.replace(/_/g, " "), n }))
  }, [entries])

  const filtered = entries.filter((e) => (actionFilter === "all" || e.action === actionFilter) && (entityFilter === "all" || e.entity_type === entityFilter))

  return (
    <>
      <PageHead crumbs={[{ label: "operations", href: "/plans" }]} title="Audit" sub="Track all changes made to firewall configuration">
        <div className="flex flex-wrap items-center gap-[7px]">
          <Filter label="action" value={actionFilter} onChange={setActionFilter} options={actionOptions} />
          <Filter label="entity" value={entityFilter} onChange={setEntityFilter} options={entityOptions} />
        </div>
      </PageHead>

      {error && <Banner tone="danger" flush>Could not load the audit log: {error instanceof Error ? error.message : "unknown error"}</Banner>}

      <Table<AuditLogEntry>
        cols={[
          { k: "when", label: "when", w: "160px", sortable: false, cell: (e) => <span className="mono text-[11px] text-text-3">{new Date(e.created_at).toLocaleString()}</span> },
          { k: "user", label: "user", w: "minmax(120px,1fr)", sortable: false, cell: (e) => <span className="text-text-2">{e.user_email ?? (e.user_id ? `user #${e.user_id}` : "system")}</span> },
          { k: "action", label: "action", w: "100px", sortable: false, cell: (e) => <Tag tone={def(AUDIT_ACTION, e.action).tone}>{e.action.replace(/_/g, " ")}</Tag> },
          { k: "entity", label: "entity", w: "minmax(120px,1fr)", sortable: false, cell: (e) => <Tag>{e.entity_type.replace(/_/g, " ")}{e.entity_id ? ` #${e.entity_id}` : ""}</Tag> },
          { k: "ip", label: "ip address", w: "120px", sortable: false, cell: (e) => <span className="mono text-[11px] text-text-3">{e.ip_address ?? "—"}</span> },
          {
            k: "transcript", label: "", w: "120px", right: true, sortable: false,
            cell: (e) => {
              const sid = e.action === "session_start" && e.entity_type === "ssh_session" && e.after_state?.session_id ? String(e.after_state.session_id) : null
              return sid ? <button type="button" className="btn btn-sm btn-ghost" onClick={() => setTranscriptSessionId(sid)}>view transcript</button> : null
            },
          },
        ]}
        rows={filtered}
        keyOf={(e) => e.id}
        loading={isLoading}
        empty={error ? "Could not load the audit log." : "No audit entries found."}
        footer={
          entries.length > 0 && (
            <span className="flex items-center gap-2.5">
              <span>{entries.length} {entries.length === 1 ? "entry" : "entries"} loaded{hasNextPage ? " so far" : ""}</span>
              {hasNextPage && <button type="button" className="btn btn-sm btn-ghost" disabled={isFetchingNextPage} onClick={() => fetchNextPage()}>{isFetchingNextPage ? "loading…" : "load more"}</button>}
            </span>
          )
        }
      />

      {transcriptSessionId && <TranscriptModal sessionId={transcriptSessionId} onClose={() => setTranscriptSessionId(null)} />}
    </>
  )
}
