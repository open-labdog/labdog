import { Suspense } from "react"

import OverviewPage from "./client-page"

/**
 * The Suspense boundary is required, not decorative: `useSearchParams` in
 * the client page opts the route into client-side rendering, and Next
 * fails the production build outright without a boundary around it.
 */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <OverviewPage />
    </Suspense>
  )
}
