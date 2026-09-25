import { Suspense } from "react"

import PendingReviewClientPage from "./client-page"

export async function generateStaticParams() {
  return [{ id: "placeholder" }]
}

/** Kept for deep links; the same queue is a tab of Discovery. */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <PendingReviewClientPage />
    </Suspense>
  )
}
