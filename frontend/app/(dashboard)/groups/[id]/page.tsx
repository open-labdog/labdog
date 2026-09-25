import { Suspense } from "react"

import ClientPage from "./client-page"

export async function generateStaticParams() {
  return [{ id: "placeholder" }]
}

/**
 * The Suspense boundary is required, not decorative: the client page reads
 * `useSearchParams` (its tab and module live in the URL), which opts the
 * route into client-side rendering and fails the export without one.
 */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <ClientPage />
    </Suspense>
  )
}
