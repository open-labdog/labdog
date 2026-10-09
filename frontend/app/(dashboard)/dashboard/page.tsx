import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// The dashboard became Overview: fleet state and what is waiting are one question.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/overview" />
    </Suspense>
  )
}
