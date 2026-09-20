import { Suspense } from "react"
import { MODULES } from "@/lib/modules"

import ConfigPage from "./client-page"

/**
 * One page per module, prerendered. The production build is a static
 * export and the backend's SPA fallback only substitutes *numeric*
 * dynamic segments, so `/config/firewall/` must exist as a real file —
 * this is what makes it one.
 */
export function generateStaticParams() {
  return MODULES.map((m) => ({ module: m.id }))
}

export const dynamicParams = false

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ConfigPage />
    </Suspense>
  )
}
