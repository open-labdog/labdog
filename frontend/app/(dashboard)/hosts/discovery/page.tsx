import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// Discovery was promoted out from under Hosts — it finds things that are not yet hosts.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/discovery?tab=schedules" />
    </Suspense>
  )
}
