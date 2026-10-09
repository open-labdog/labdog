import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// Packs are where actions come from — operational, not an integration.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/actions?tab=packs" />
    </Suspense>
  )
}
