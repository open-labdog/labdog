"use client"

import { useMemo, useState } from "react"
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import type { SessionView } from "@/components/ai/session-list"
import { apiFetch, ApiError } from "@/lib/api"
import { plural } from "@/lib/fleet"
import type { AISession } from "@/lib/types"
import type { ArchiveOld } from "../_dialogs/archive-old"

/** Sessions per page of the list. */
const PAGE_SIZE = 50

interface BulkResult {
  done: number[]
  skipped: { session_id: number; reason: string }[]
}

/** A bulk result in one toast: what was done, and why the rest was not. */
function reportBulk(result: BulkResult, verb: string) {
  const done = `${plural(result.done.length, "session")} ${verb}`
  if (result.skipped.length === 0) {
    toast.success(done)
    return
  }
  // The reasons repeat across a batch ("still counts towards the
  // safeguards…"), so each is said once, with how many it kept.
  const reasons = new Map<string, number>()
  for (const s of result.skipped) reasons.set(s.reason, (reasons.get(s.reason) ?? 0) + 1)
  toast.warning(`${done}, ${result.skipped.length} kept`, {
    description: [...reasons].map(([reason, n]) => (n > 1 ? `${n}× ${reason}` : reason)).join(" "),
    duration: 15_000,
  })
}

interface Options {
  selectedId: number | null
  setSelectedId: (id: number | null) => void
  setError: (error: string | null) => void
}

/**
 * The session list: its pages per view, select mode with bulk archive,
 * unarchive and delete, archiving by age, and deleting one session.
 * Deleting the open session closes it.
 */
export function useSessionList({ selectedId, setSelectedId, setError }: Options) {
  const queryClient = useQueryClient()
  const [deleting, setDeleting] = useState<AISession | null>(null)
  const [view, setView] = useState<SessionView>("active")
  const [selecting, setSelecting] = useState(false)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false)
  const [archiveOld, setArchiveOld] = useState<ArchiveOld | null>(null)

  // A page at a time, by an id cursor: the list is polled while sessions
  // are added, which would shift an offset. Keyed by view, and every
  // `["ai-sessions"]` invalidation below reaches both.
  const sessionPages = useInfiniteQuery<AISession[]>({
    queryKey: ["ai-sessions", view],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => {
      const before = pageParam as number | undefined
      return apiFetch<AISession[]>(`/api/ai/sessions?view=${view}&limit=${PAGE_SIZE}${before === undefined ? "" : `&before_id=${before}`}`)
    },
    getNextPageParam: (last) => (last.length === PAGE_SIZE ? last[last.length - 1].id : undefined),
    refetchInterval: 10_000,
  })
  const sessions = useMemo(() => sessionPages.data?.pages.flat(), [sessionPages.data])

  const changeView = (next: SessionView) => {
    setView(next)
    setPicked(new Set())
  }
  const togglePicked = (id: number) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const changeSelecting = (on: boolean) => {
    setSelecting(on)
    if (!on) setPicked(new Set())
  }

  /** Archive, unarchive or delete in bulk; the server says what it skipped. */
  const bulk = useMutation({
    mutationFn: ({ path, body }: { path: "archive" | "unarchive" | "delete"; body: object; verb: string }) =>
      apiFetch<BulkResult>(`/api/ai/sessions/${path}`, { method: "POST", json: body }),
    onSuccess: (result, { path, verb }) => {
      reportBulk(result, verb)
      setPicked(new Set())
      setConfirmBulkDelete(false)
      setArchiveOld(null)
      if (path === "delete" && selectedId !== null && result.done.includes(selectedId)) setSelectedId(null)
      queryClient.invalidateQueries({ queryKey: ["ai-sessions"] })
      if (selectedId !== null) queryClient.invalidateQueries({ queryKey: ["ai-session", selectedId] })
    },
    onError: (e: unknown) => {
      setConfirmBulkDelete(false)
      toast.error(e instanceof ApiError ? e.message : "Could not update the sessions.")
    },
  })
  const pickedIds = [...picked]

  const deleteSession = useMutation({
    mutationFn: (id: number) => apiFetch(`/api/ai/sessions/${id}`, { method: "DELETE" }),
    onSuccess: (_data, id) => {
      if (id === selectedId) setSelectedId(null)
      setDeleting(null)
      queryClient.invalidateQueries({ queryKey: ["ai-sessions"] })
      setError(null)
    },
    onError: (e: unknown) => {
      setDeleting(null)
      setError(e instanceof ApiError ? e.message : "Could not delete the session.")
    },
  })

  return {
    sessionPages,
    sessions,
    view,
    changeView,
    selecting,
    changeSelecting,
    picked,
    setPicked,
    togglePicked,
    pickedIds,
    bulk,
    confirmBulkDelete,
    setConfirmBulkDelete,
    archiveOld,
    setArchiveOld,
    deleting,
    setDeleting,
    deleteSession,
  }
}
