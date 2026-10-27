import { Suspense } from "react"

import AlertsPage from "./client-page"

/** The Suspense boundary is required: `useSearchParams` in the client page
 *  opts the route into client-side rendering, and the static export fails
 *  without a boundary around it. */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <AlertsPage />
    </Suspense>
  )
}
