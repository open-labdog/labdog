"use client"

import { useEffect } from "react"
import { useParams, useRouter, useSearchParams } from "next/navigation"

/**
 * Client-side redirect for routes the IA moved. The production build is a
 * static export, so there is no server to answer with a 301 — the old
 * page renders nothing and replaces itself. `keepQuery` carries the
 * search string across for routes whose parameters still mean something
 * at the destination (`?tab=`, `?session=`).
 *
 * `:name` in `to` is filled from the route's dynamic segments, for the
 * stubs under `[id]` that point into the page that now embeds them
 * (`/groups/:id/rules` → `/groups/7?tab=config&module=firewall`). A
 * string rather than a function so a server `page.tsx` can pass it.
 *
 * Every page using this must be wrapped in a Suspense boundary by its
 * server `page.tsx` — `useSearchParams` requires one in a static export.
 */
export function Redirect({ to, keepQuery = false }: { to: string; keepQuery?: boolean }) {
  const router = useRouter()
  const search = useSearchParams()
  const params = useParams()
  useEffect(() => {
    const target = to.replace(/:([a-zA-Z_]+)/g, (_, k: string) => {
      const v = params?.[k]
      return v == null ? `:${k}` : Array.isArray(v) ? v.join("/") : String(v)
    })
    const q = keepQuery ? search.toString() : ""
    const sep = target.includes("?") ? "&" : "?"
    router.replace(q ? `${target}${sep}${q}` : target)
  }, [router, search, params, to, keepQuery])
  return null
}
