import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/discovery?tab=schedules" />
    </Suspense>
  )
}
