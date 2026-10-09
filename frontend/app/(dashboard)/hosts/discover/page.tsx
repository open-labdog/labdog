import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// "Discover" and "Discovery" are one place now; the scan form is a tab.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/discovery?tab=scan" />
    </Suspense>
  )
}
