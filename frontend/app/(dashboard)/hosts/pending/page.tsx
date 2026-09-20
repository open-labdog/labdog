import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// Queue 1 of 3 for the same decision — the approval queue is the Discovery screen's first tab.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/discovery" />
    </Suspense>
  )
}
