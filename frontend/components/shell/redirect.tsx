"use client"

import { useEffect } from "react"
import { useRouter, useSearchParams } from "next/navigation"

/**
 * Client-side redirect for routes the IA moved. The production build is a
 * static export, so there is no server to answer with a 301 — the old
 * page renders nothing and replaces itself. `keepQuery` carries the
 * search string across for routes whose parameters still mean something
 * at the destination (`?tab=`, `?session=`).
 *
 * Every page using this must be wrapped in a Suspense boundary by its
 * server `page.tsx` — `useSearchParams` requires one in a static export.
 */
export function Redirect({ to, keepQuery = false }: { to: string; keepQuery?: boolean }) {
  const router = useRouter()
  const search = useSearchParams()
  useEffect(() => {
    const q = keepQuery ? search.toString() : ""
    const sep = to.includes("?") ? "&" : "?"
    router.replace(q ? `${to}${sep}${q}` : to)
  }, [router, search, to, keepQuery])
  return null
}
