import { Suspense } from "react"

import AssistantPage from "./client-page"

/**
 * The Suspense boundary is required, not decorative: `useSearchParams` in
 * the client page opts the route into client-side rendering, and Next
 * fails the production build outright without a boundary around it.
 */
export default function Page() {
  return (
    <Suspense fallback={<p className="m-0 p-4 text-xs text-text-3">Loading the assistant…</p>}>
      <AssistantPage />
    </Suspense>
  )
}
