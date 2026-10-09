import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// About is a section of Settings, not a route with its own sidebar link.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/settings?section=system" />
    </Suspense>
  )
}
