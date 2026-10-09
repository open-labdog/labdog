import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// A schedule is an action with a cron; same library, same detail.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/actions?tab=schedules" />
    </Suspense>
  )
}
