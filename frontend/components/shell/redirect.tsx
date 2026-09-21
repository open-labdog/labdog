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
 * `to` can be a function of the route's dynamic segments, for the stubs
 * under `[id]` that point into the page that now embeds them
 * (`/groups/7/rules` → `/groups/7?tab=config&module=firewall`).
 *
 * Every page using this must be wrapped in a Suspense boundary by its
 * server `page.tsx` — `useSearchParams` requires one in a static export.
 */
export function Redirect({
  to,
  keepQuery = false,
}: {
  to: string | ((params: Record<string, string>) => string)
  keepQuery?: boolean
}) {
  const router = useRouter()
  const search = useSearchParams()
  const params = useParams()
  useEffect(() => {
    const flat: Record<string, string> = {}
    for (const [k, v] of Object.entries(params ?? {})) flat[k] = Array.isArray(v) ? v.join("/") : String(v)
    const target = typeof to === "function" ? to(flat) : to
    const q = keepQuery ? search.toString() : ""
    const sep = target.includes("?") ? "&" : "?"
    router.replace(q ? `${target}${sep}${q}` : target)
  }, [router, search, params, to, keepQuery])
  return null
}
